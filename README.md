# PDF Repair Tool — PyMuPDF + OCR

A robust PDF repair utility that attempts to rebuild corrupted or damaged PDF files using `PyMuPDF` (fitz) and Tesseract OCR as a fallback for pages lacking text.

This project is perfect as a demo portfolio piece for freelancers offering **document repair**, **data recovery**, and **automation** services.

---

## 🚀 Features

- Backup original PDF before changes
- Attempt structural repair by opening and re-saving PDF (fixes many xref / corruption issues)
- For pages with missing text, render to image and run Tesseract OCR to create searchable PDF pages
- Optionally extract embedded images to a folder
- Optionally remove pages that remain blank after processing
- Rewrites basic metadata (title, author, subject)
- Generates JSON repair report with per-page details and errors/actions

---

## 🧰 Tech Stack

- Python 3.8+
- [PyMuPDF (fitz)](https://pymupdf.readthedocs.io) — PDF reading, rendering, and saving
- [Pillow](https://python-pillow.org) — image handling
- [pytesseract](https://github.com/madmaze/pytesseract) — Tesseract wrapper for OCR
- **Tesseract** OCR engine must be installed on your system

---

## ⚙️ Installation

1. Clone the repo:
```bash
git clone <repo-url>
cd pdf-repair-tool
```
## Create a virtualenv (recommended):
```bash
python -m venv venv
source venv/bin/activate   # macOS / Linux
venv\Scripts\activate.bat  # Windows
```

## Install Python dependencies:
```bash
pip install -r requirements.txt
```

## Install Tesseract:
```
Ubuntu/Debian: sudo apt install tesseract-ocr
Mac (brew): brew install tesseract
Windows: download installer from https://github.com/tesseract-ocr/tesseract and add to PATH.
```
---
## Usage
```bash
python pdf_repair.py input.pdf -o repaired.pdf --ocr --extract-images ./images --dpi 300
```

Options:

- -o/--output: path to save repaired PDF (default: <input>.repaired.pdf)

- --ocr: enable OCR fallback for pages without text (requires Tesseract)

- --dpi: DPI for page rendering before OCR (default 300)

- --extract-images DIR: extract embedded images into DIR

- --remove-blank: remove pages that remain blank after processing

- --ocr-lang LANG: tesseract language code (default eng)

- --report FILE: path to JSON report (default: <input>.repair_report.json)
---
## Example (repair without OCR, just structural clean)
```bash
python pdf_repair.py corrupted.pdf -o repaired.pdf
``` 
## Example (use OCR fallback and extract images):
```bash
python pdf_repair.py corrupted_with_scans.pdf -o repaired.pdf --ocr --extract-images ./extracted_images --dpi 300
```
