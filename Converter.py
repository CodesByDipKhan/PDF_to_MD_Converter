from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Sequence

import fitz  
import pdfplumber


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    fontname: str
    size: float

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass
class LineBlock:
    y: float
    kind: str  
    text: str
    level: int = 0
    height: float = 0.0


def normalize_spaces(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def looks_like_bullet(text: str) -> bool:
    return bool(re.match(r"^[•●◦▪▸\-*]\s+\S+", text.strip()))


def looks_like_numbered_item(text: str) -> bool:
    return bool(re.match(r"^\d+[\.)]\s+\S+", text.strip()))


def strip_list_prefix(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^[•●◦▪▸\-*]\s+", "", text)
    text = re.sub(r"^\d+[\.)]\s+", "", text)
    return normalize_spaces(text)


def font_style(fontname: str) -> tuple[bool, bool, bool]:
    """Return (bold, italic, mono) booleans inferred from the font name."""
    name = (fontname or "").lower()
    bold = any(tok in name for tok in ("bold", "black", "semibold", "demibold"))
    italic = any(tok in name for tok in ("italic", "oblique", "slanted"))
    mono = any(tok in name for tok in ("mono", "courier", "code", "console", "terminal"))
    return bold, italic, mono


def markdown_wrap(text: str, bold: bool = False, italic: bool = False, mono: bool = False) -> str:
    text = normalize_spaces(text)
    if not text:
        return text
    if mono:
        return f"`{text}`"
    if bold and italic:
        return f"***{text}***"
    if bold:
        return f"**{text}**"
    if italic:
        return f"*{text}*"
    return text


def render_words_with_inline_formatting(words: Sequence[Word]) -> str:
    """Render a line of words while preserving simple bold/italic runs."""
    if not words:
        return ""

    ordered = sorted(words, key=lambda w: w.x0)
    chunks: list[str] = []
    current_words: list[str] = []
    current_style: tuple[bool, bool, bool] | None = None

    def flush() -> None:
        nonlocal current_words, current_style
        if not current_words:
            return
        text = " ".join(current_words)
        bold, italic, mono = current_style or (False, False, False)
        chunks.append(markdown_wrap(text, bold=bold, italic=italic, mono=mono))
        current_words = []

    for w in ordered:
        style = font_style(w.fontname)
        if current_style is None:
            current_style = style
        elif style != current_style:
            flush()
            current_style = style
        current_words.append(w.text)

    flush()
    return normalize_spaces(" ".join(chunks))



def classify_font_sizes(sizes: Sequence[float]) -> dict[str, float]:
    """Infer body / heading thresholds from the document's font-size distribution."""
    cleaned = [round(float(s), 1) for s in sizes if s and s > 0]
    if not cleaned:
        return {"body": 11.0, "h2": 14.0, "h1": 18.0}

    counts = Counter(cleaned)
    body = counts.most_common(1)[0][0]
    distinct = sorted(counts)
    larger = [s for s in distinct if s > body + 0.4]

    if len(larger) >= 2:
        h2 = larger[-2]
        h1 = larger[-1]
    elif len(larger) == 1:
        h1 = larger[0]
        h2 = max(body + 2.0, body + 1.0)
    else:
        h2 = body + 3.0
        h1 = body + 6.0

    if h2 >= h1:
        h2 = max(body + 1.5, h1 - 2.0)

    return {"body": body, "h2": h2, "h1": h1}



def word_inside_bbox(word: Word, bbox: tuple[float, float, float, float]) -> bool:
    x0, top, x1, bottom = bbox
    return x0 <= word.center_x <= x1 and top <= word.center_y <= bottom


def filter_words_outside_bboxes(
    words: Sequence[Word],
    bboxes: Sequence[tuple[float, float, float, float]],
) -> list[Word]:
    if not bboxes:
        return list(words)
    return [w for w in words if not any(word_inside_bbox(w, bbox) for bbox in bboxes)]


def group_words_into_lines(words: Sequence[Word]) -> list[list[Word]]:
    """Cluster words into visual lines by their top-y coordinate."""
    if not words:
        return []

    ordered = sorted(words, key=lambda w: (w.top, w.x0))
    median_size = median([w.size for w in ordered]) if ordered else 10.0
    y_tolerance = max(1.5, min(4.0, median_size * 0.25))

    lines: list[list[Word]] = []
    current: list[Word] = [ordered[0]]
    current_top: float = ordered[0].top

    for word in ordered[1:]:
        if abs(word.top - current_top) <= y_tolerance:
            current.append(word)
        else:
            lines.append(sorted(current, key=lambda w: w.x0))
            current = [word]
            current_top = word.top

    if current:
        lines.append(sorted(current, key=lambda w: w.x0))

    return lines



def table_to_markdown(table: list[list[object]]) -> str:
    if not table:
        return ""

    rows = [
        [normalize_spaces(str(cell or "").replace("\n", " ")) for cell in row]
        for row in table
    ]
    rows = [row for row in rows if any(cell for cell in row)]
    if not rows:
        return ""

    num_cols = max(len(r) for r in rows)
    rows = [r + [""] * (num_cols - len(r)) for r in rows]
    col_widths = [max(3, max(len(row[c]) for row in rows)) for c in range(num_cols)]

    def fmt_row(row: Sequence[str]) -> str:
        return "| " + " | ".join(row[c].ljust(col_widths[c]) for c in range(num_cols)) + " |"

    lines = [fmt_row(rows[0])]
    lines.append("| " + " | ".join("-" * w for w in col_widths) + " |")
    for row in rows[1:]:
        lines.append(fmt_row(row))
    return "\n".join(lines)


def assemble_markdown(blocks: Sequence[LineBlock]) -> str:
    out: list[str] = []
    paragraph_buffer: list[str] = []
    code_buffer: list[str] = []
    prev_line: LineBlock | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph_buffer
        if paragraph_buffer:
            text = normalize_spaces(" ".join(paragraph_buffer))
            if text:
                if out and out[-1] != "":
                    out.append("")
                out.append(text)
            paragraph_buffer = []

    def flush_code() -> None:
        nonlocal code_buffer
        if code_buffer:
            if out and out[-1] != "":
                out.append("")
            out.append("```")
            out.extend(code_buffer)
            out.append("```")
            out.append("")
            code_buffer = []

    for block in blocks:
        if block.kind == "code":
            flush_paragraph()
            code_buffer.append(block.text.rstrip())
            prev_line = block
            continue

        flush_code()

        if block.kind == "heading":
            flush_paragraph()
            if out and out[-1] != "":
                out.append("")
            out.append(f"{'#' * block.level} {block.text}")
            out.append("")

        elif block.kind == "table":
            flush_paragraph()
            if out and out[-1] != "":
                out.append("")
            out.append(block.text)
            out.append("")

        elif block.kind in {"bullet", "numbered"}:
            flush_paragraph()
            prefix = "-" if block.kind == "bullet" else "1."
            out.append(f"{prefix} {block.text}")

        else:  
            if prev_line and prev_line.kind == "paragraph":
                gap = block.y - (prev_line.y + prev_line.height)
                if gap <= max(4.0, prev_line.height * 1.5):
                    paragraph_buffer.append(block.text)
                else:
                    flush_paragraph()
                    paragraph_buffer.append(block.text)
            else:
                flush_paragraph()
                paragraph_buffer.append(block.text)

        prev_line = block

    flush_paragraph()
    flush_code()

    md = "\n".join(out)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    return md


def ocr_page(doc: fitz.Document, page_num: int) -> str:
    try:
        import io

        import pytesseract
        from PIL import Image
    except Exception as exc:
        return f"<!-- OCR unavailable: install pytesseract + pillow ({exc}) -->"

    try:
        page = doc[page_num]
        pix = page.get_pixmap(dpi=220)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(img)
    except Exception as exc:
        return f"<!-- OCR failed on page {page_num + 1}: {exc} -->"


def ocr_text_to_markdown(text: str) -> str:
    lines = [normalize_spaces(line) for line in text.splitlines()]
    paragraphs: list[str] = []
    current: list[str] = []

    for line in lines:
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)

    if current:
        paragraphs.append(" ".join(current))

    return "\n\n".join(paragraphs).strip()


def extract_page_blocks(
    page: pdfplumber.page.Page,
    thresholds: dict[str, float],
) -> list[LineBlock]:

    try:
        raw_words = page.extract_words(
            extra_attrs=["fontname", "size"],
            use_text_flow=True,
            keep_blank_chars=False,
        )
    except TypeError:
        raw_words = page.extract_words(extra_attrs=["fontname", "size"], use_text_flow=True)

    words: list[Word] = []
    for w in raw_words or []:
        try:
            words.append(
                Word(
                    text=str(w.get("text", "")),
                    x0=float(w.get("x0", 0.0)),
                    x1=float(w.get("x1", 0.0)),
                    top=float(w.get("top", 0.0)),
                    bottom=float(w.get("bottom", 0.0)),
                    fontname=str(w.get("fontname", "")),
                    size=float(w.get("size", 0.0)),
                )
            )
        except Exception:
            continue

    table_bboxes: list[tuple[float, float, float, float]] = []
    table_blocks: list[LineBlock] = []
    try:
        tables = page.find_tables() or []
    except Exception:
        tables = []

    for table in tables:
        bbox = getattr(table, "bbox", None)
        if bbox:
            bbox_f: tuple[float, float, float, float] = tuple(map(float, bbox))  # type: ignore[assignment]
            table_bboxes.append(bbox_f)
            try:
                extracted = table.extract()
            except Exception:
                extracted = None
            md_table = table_to_markdown(extracted or [])
            if md_table:
                tbl_height = bbox_f[3] - bbox_f[1]
                table_blocks.append(
                    LineBlock(y=bbox_f[1], kind="table", text=md_table, height=tbl_height)
                )

    if words:
        words = filter_words_outside_bboxes(words, table_bboxes)

    lines = group_words_into_lines(words)

    line_blocks: list[LineBlock] = []
    for line in lines:
        raw_text = " ".join(w.text for w in line)
        text_plain = normalize_spaces(raw_text)
        if not text_plain:
            continue

        top = min(w.top for w in line)
        bottom = max(w.bottom for w in line)
        height = max(1.0, bottom - top)
        avg_size = sum(w.size for w in line) / max(1, len(line))
        word_count = len(line)

        bold_ratio = sum(1 for w in line if font_style(w.fontname)[0]) / word_count
        mono_ratio = sum(1 for w in line if font_style(w.fontname)[2]) / word_count

        if avg_size >= thresholds["h1"] - 0.25 and word_count <= 18 and len(text_plain) <= 120:
            line_blocks.append(
                LineBlock(y=top, kind="heading", level=1, text=text_plain, height=height)
            )
            continue

        if (
            avg_size >= thresholds["h2"] - 0.25
            and word_count <= 20
            and len(text_plain) <= 140
        ) or (
            avg_size >= thresholds["body"] + 1.5
            and bold_ratio >= 0.6
            and len(text_plain) <= 140
        ):
            line_blocks.append(
                LineBlock(y=top, kind="heading", level=2, text=text_plain, height=height)
            )
            continue

        leading_spaces = len(raw_text) - len(raw_text.lstrip(" "))
        if mono_ratio >= 0.5 or leading_spaces >= 4:
            line_blocks.append(LineBlock(y=top, kind="code", text=text_plain, height=height))
            continue

        if looks_like_bullet(text_plain):
            line_blocks.append(
                LineBlock(y=top, kind="bullet", text=strip_list_prefix(text_plain), height=height)
            )
            continue

        if looks_like_numbered_item(text_plain):
            line_blocks.append(
                LineBlock(y=top, kind="numbered", text=strip_list_prefix(text_plain), height=height)
            )
            continue

        rendered = render_words_with_inline_formatting(line)
        rendered = normalize_spaces(rendered)
        if rendered:
            line_blocks.append(LineBlock(y=top, kind="paragraph", text=rendered, height=height))

    combined = sorted(table_blocks + line_blocks, key=lambda b: b.y)
    return combined


def pdf_to_markdown(
    input_path: str,
    output_path: str,
    use_ocr: bool = False,
    verbose: bool = False,
) -> None:
    src = Path(input_path)
    dst = Path(output_path)

    if not src.exists():
        print(f"Error: input file not found: {src}", file=sys.stderr)
        sys.exit(1)

    if src.suffix.lower() != ".pdf":
        print("Error: input must be a PDF file.", file=sys.stderr)
        sys.exit(1)

    if verbose:
        print(f"[pdf2md] Opening {src}")

    try:
        fitz_doc = fitz.open(str(src))
    except Exception as exc:
        print(f"Error: could not open PDF with PyMuPDF: {exc}", file=sys.stderr)
        sys.exit(1)

    pages_md: list[str] = []

    try:
        with pdfplumber.open(str(src)) as pdf:
            all_sizes: list[float] = []
            for page in pdf.pages:
                try:
                    raw = page.extract_words(extra_attrs=["size"], use_text_flow=True)
                    for w in raw:
                        size = w.get("size")
                        if size:
                            all_sizes.append(float(size))
                except Exception:
                    continue

            thresholds = classify_font_sizes(all_sizes)
            if verbose:
                print(f"[pdf2md] Font thresholds: {thresholds}")

            total_pages = len(pdf.pages)
            for idx, page in enumerate(pdf.pages):
                if verbose:
                    print(f"[pdf2md] Processing page {idx + 1}/{total_pages}")

                try:
                    blocks = extract_page_blocks(page, thresholds)
                except Exception as exc:
                    if verbose:
                        print(f"[pdf2md]   extraction error on page {idx + 1}: {exc}", file=sys.stderr)
                    blocks = []

                approx_text_len = sum(len(b.text) for b in blocks if b.kind != "table")

                if approx_text_len < 20:
                    if use_ocr:
                        if verbose:
                            print(f"[pdf2md]   OCR fallback on page {idx + 1}")
                        ocr_text = ocr_page(fitz_doc, idx)
                        ocr_md = ocr_text_to_markdown(ocr_text)
                        pages_md.append(
                            ocr_md if ocr_md else f"<!-- Page {idx + 1}: OCR produced no usable text -->"
                        )
                    else:
                        pages_md.append(f"<!-- Page {idx + 1}: no extractable text (try --ocr) -->")
                    continue

                page_md = assemble_markdown(blocks)
                if page_md.strip():
                    pages_md.append(page_md.strip())
                elif use_ocr:
                    if verbose:
                        print(f"[pdf2md]   OCR fallback on page {idx + 1}")
                    ocr_text = ocr_page(fitz_doc, idx)
                    ocr_md = ocr_text_to_markdown(ocr_text)
                    if ocr_md:
                        pages_md.append(ocr_md)

    finally:
        fitz_doc.close()

    final_parts = [part.strip() for part in pages_md if part and part.strip()]
    markdown = "\n\n---\n\n".join(final_parts).strip() + "\n"

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(markdown, encoding="utf-8")
    print(f"[pdf2md] Done: {dst}")



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2md",
        description="Convert a PDF file into structured Markdown (CPU-only, open-source).",
    )
    parser.add_argument("input", help="Path to the input PDF file")
    parser.add_argument("output", help="Path to the output Markdown file")
    parser.add_argument("--ocr", action="store_true", help="Enable OCR fallback for scanned pages")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed progress")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    pdf_to_markdown(args.input, args.output, use_ocr=args.ocr, verbose=args.verbose)


if __name__ == "__main__":
    main()
