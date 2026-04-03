# 📄 PDF to Markdown Converter (pdf2md)

A **CPU‑only**, open‑source tool that converts PDF files into clean, structured Markdown.  
Built for the **Sugarclass Trainee SWE Screening Task** – meets all core requirements and includes multiple bonuses.

## ✨ Features

| Core Requirements | Status | Bonus Features | Status |
|------------------|--------|----------------|--------|
| Headings (`#`, `##`) | ✅ | Tables | ✅ |
| Paragraphs | ✅ | Code blocks | ✅ |
| Lists (bulleted & numbered) | ✅ | OCR fallback for scanned PDFs | ✅ |
| Bold / italic formatting | ✅ | Smart formatting cleanup | ✅ |
| CLI tool | ✅ | Lightweight LLM cleanup | ❌ *not implemented* |

- **CPU only** – no GPU required.
- **Python** – easy to install and run.
- **Free & open‑source** – all libraries are MIT/BSD licensed.

## 📦 Steps to run the program

1. Clone the repository
git clone https://github.com/CodesByDipKhan/PDFtoMarkdownConverter.git
2. Install Python dependencies
pip install pdfplumber PyMuPDF pytesseract pillow pdf2image
3. Basic conversion (text‑based PDF):
python Converter.py input.pdf output.md

**How It Works**
The converter analyses the PDF’s visual layout and font metadata – it does not rely on embedded structural tags.

Extract words with position & font info – uses pdfplumber.

Calibrate font sizes – scans the whole document to determine “body”, “h2” and “h1” size thresholds.

Detect tables – uses pdfplumber.find_tables(), converts to GitHub‑flavoured Markdown, and removes table text from normal flow.

Group words into lines – based on vertical proximity (tolerance adapts to median font size).

Classify each line:

Heading – font size ≥ h1/h2 threshold and short line length.

Code block – monospaced font or ≥4 leading spaces.

List – starts with •, -, *, 1., etc.

Paragraph – everything else; applies inline bold/italic detection via font names.

Render Markdown – merges paragraphs across lines, wraps code blocks, adds blank lines appropriately.

OCR fallback – if a page has almost no extractable text and --ocr is used, it renders the page to an image and runs Tesseract.

**Limitations**
Multi‑column layouts – text may be read in the wrong order (left‑to‑right, top‑to‑bottom). The tool does not perform column detection.

Heading inference – based purely on font size and boldness. Decorative or irregular fonts may produce wrong levels.

Tables – only simple tables are supported; merged cells (colspan/rowspan) are not handled.

OCR – slower and less accurate on low‑quality scans. Requires Tesseract to be installed separately.

No image extraction – embedded images are ignored (except OCR renders the whole page as an image).

No LLM cleanup – the “lightweight LLM” bonus was not implemented; all cleanup is rule‑based.
