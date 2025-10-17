#!/usr/bin/env python3
"""
pdf_repair.py
Robust PDF repair utility using PyMuPDF (fitz) + pytesseract OCR fallback.

Features:
 - Backup original PDF
 - Attempt structural repair by opening and re-saving with PyMuPDF
 - For pages missing text: render page -> run Tesseract OCR -> produce searchable PDF page
 - Merge repaired pages into final PDF
 - Optionally extract embedded images to a folder
 - Optionally remove blank pages
 - Re-write metadata if requested
 - Generate a JSON repair report

Usage:
    python pdf_repair.py input.pdf -o repaired.pdf --ocr --extract-images out_images --dpi 300

Notes:
 - Requires: PyMuPDF (fitz), Pillow, pytesseract
 - Tesseract OCR engine must be installed on your system and available in PATH
 - pip install pymupdf Pillow pytesseract
"""

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import datetime
from pathlib import Path
from typing import List, Dict

try:
    import fitz  # PyMuPDF
except Exception as e:
    print("Missing dependency: pymupdf. Install via `pip install pymupdf`", file=sys.stderr)
    raise

try:
    from PIL import Image
except Exception:
    print("Missing dependency: Pillow. Install via `pip install Pillow`", file=sys.stderr)
    raise

try:
    import pytesseract
    from pytesseract import Output
except Exception:
    pytesseract = None  # OCR optional

# -------------------------
# Helper: report object
# -------------------------
def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"

class RepairReport:
    def __init__(self, input_path: Path):
        self.data = {
            "input_path": str(input_path),
            "timestamp": now_iso(),
            "actions": [],
            "errors": [],
            "pages": []
        }

    def add_action(self, msg: str):
        entry = {"time": now_iso(), "msg": msg}
        self.data["actions"].append(entry)
        print("[ACTION]", msg)

    def add_error(self, msg: str):
        entry = {"time": now_iso(), "msg": msg}
        self.data["errors"].append(entry)
        print("[ERROR]", msg, file=sys.stderr)

    def add_page_entry(self, page_num: int, entry: dict):
        # append or create page entry
        entry_full = {"page": page_num, "time": now_iso(), **entry}
        self.data["pages"].append(entry_full)

    def save(self, path: Path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            self.add_action(f"Saved JSON report to {path}")
        except Exception as e:
            print("Failed saving report:", e, file=sys.stderr)

# -------------------------
# Utilities
# -------------------------
def backup_file(src: Path, report: RepairReport) -> Path:
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    dst = src.with_name(src.stem + f".backup.{timestamp}" + src.suffix)
    shutil.copy2(src, dst)
    report.add_action(f"Backed up original to {dst}")
    return dst

def ensure_output_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

# -------------------------
# Core functions
# -------------------------
def try_simple_repair(input_path: Path, report: RepairReport) -> Path:
    """
    Attempt to open and re-save the doc using PyMuPDF
    This often fixes xref and minor structural issues.
    Returns path to intermediate repaired file.
    """
    report.add_action("Attempting simple PyMuPDF open+save repair.")
    repaired = input_path.with_name(input_path.stem + ".resaved" + input_path.suffix)
    try:
        doc = fitz.open(str(input_path))
        # read metadata
        meta = doc.metadata
        report.add_action(f"Original metadata: {meta}")
        # Save with garbage=4 to do cleanup
        doc.save(str(repaired), garbage=4, deflate=True)
        doc.close()
        report.add_action(f"Saved intermediate repaired PDF to {repaired}")
        return repaired
    except Exception as e:
        report.add_error(f"Simple resave failed: {e}")
        # ensure file not created
        if repaired.exists():
            try:
                repaired.unlink()
            except Exception:
                pass
        raise

def extract_images_from_doc(doc: fitz.Document, out_dir: Path, report: RepairReport) -> List[Path]:
    """
    Extract embedded images to out_dir. Returns list of saved image paths.
    """
    saved = []
    out_dir.mkdir(parents=True, exist_ok=True)
    report.add_action(f"Extracting images to {out_dir}")
    img_index = 0
    for i in range(len(doc)):
        page = doc[i]
        image_list = page.get_images(full=True)
        if not image_list:
            continue
        for img in image_list:
            xref = img[0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            ext = base_image.get("ext", "png")
            fname = out_dir / f"page{i+1}_img{img_index}.{ext}"
            with open(fname, "wb") as f:
                f.write(image_bytes)
            saved.append(fname)
            img_index += 1
            report.add_action(f"Extracted image to {fname}")
    report.add_action(f"Total images extracted: {len(saved)}")
    return saved

def page_has_text(page: fitz.Page) -> bool:
    """Return True if page has meaningful text (non-whitespace)."""
    try:
        txt = page.get_text("text")
        if txt and txt.strip():
            return True
        return False
    except Exception:
        return False

def ocr_page_to_pdf_bytes(page: fitz.Page, dpi: int = 300, lang: str = "eng") -> bytes:
    """
    Render the page to an image (Pillow) and run pytesseract to produce a searchable PDF page (bytes).
    Returns PDF bytes produced by tesseract's PDF engine.
    """
    if pytesseract is None:
        raise RuntimeError("pytesseract not available for OCR fallback")

    # Render page
    mat = fitz.Matrix(dpi/72, dpi/72)  # scale
    pix = page.get_pixmap(matrix=mat, alpha=False)  # RGB
    img_bytes = pix.tobytes("png")
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    # pytesseract can produce pdf bytes
    pdf_bytes = pytesseract.image_to_pdf_or_hocr(img, extension='pdf', lang=lang)
    return pdf_bytes

def build_repaired_pdf(input_pdf_path: Path, output_pdf_path: Path, use_ocr: bool, dpi: int,
                       extract_images: bool, out_images_dir: Path, remove_blank: bool,
                       ocr_lang: str, report: RepairReport) -> None:
    """
    Main orchestration:
    - open source doc
    - iterate pages: if page has text -> append original page; else -> OCR to PDF page and append
    - optionally extract images
    - save final PDF
    """
    report.add_action(f"Opening source PDF {input_pdf_path}")
    src_doc = fitz.open(str(input_pdf_path))  # may raise
    report.add_action(f"Source has {len(src_doc)} pages")

    if extract_images:
        try:
            extract_images_from_doc(src_doc, out_images_dir, report)
        except Exception as e:
            report.add_error(f"Image extraction failed: {e}")

    # new doc to accumulate pages
    new_doc = fitz.open()  # empty

    for i in range(len(src_doc)):
        try:
            page = src_doc[i]
            page_info = {"page_index": i+1}
            text_ok = page_has_text(page)
            if text_ok:
                # Append the original page by inserting that single page
                new_doc.insert_pdf(src_doc, from_page=i, to_page=i)
                page_info["action"] = "copied"
                page_info["text_chars"] = len(page.get_text("text") or "")
                report.add_action(f"Page {i+1}: copied (has text)")
            else:
                page_info["action"] = "no_text"
                report.add_action(f"Page {i+1}: no text detected")
                if use_ocr:
                    try:
                        pdf_bytes = ocr_page_to_pdf_bytes(page, dpi=dpi, lang=ocr_lang)
                        temp_pdf = fitz.open("pdf", pdf_bytes)
                        # insert pdf page(s) - usually one
                        new_doc.insert_pdf(temp_pdf)
                        page_info["ocr"] = "applied"
                        report.add_action(f"Page {i+1}: OCR applied and page inserted (dpi={dpi})")
                    except Exception as e:
                        page_info["ocr"] = f"failed: {e}"
                        report.add_error(f"Page {i+1} OCR failed: {e}")
                        # fallback: insert rendered image as page
                        try:
                            mat = fitz.Matrix(dpi/72, dpi/72)
                            pix = page.get_pixmap(matrix=mat, alpha=False)
                            img_xref = new_doc.insert_image(new_doc.new_page(width=pix.width, height=pix.height).rect, stream=pix.tobytes("png"))
                            # We inserted page already as image; mark
                            page_info["fallback_image_inserted"] = True
                            report.add_action(f"Page {i+1}: fallback image-insert used")
                        except Exception as e2:
                            page_info["fallback_error"] = str(e2)
                            report.add_error(f"Page {i+1} fallback image insert failed: {e2}")
                else:
                    # No OCR: we can insert an image version to preserve visual content
                    try:
                        mat = fitz.Matrix(dpi/72, dpi/72)
                        pix = page.get_pixmap(matrix=mat, alpha=False)
                        new_page = new_doc.new_page(width=pix.width, height=pix.height)
                        new_page.insert_image(new_page.rect, stream=pix.tobytes("png"))
                        page_info["image_inserted"] = True
                        report.add_action(f"Page {i+1}: inserted as image (no OCR mode)")
                    except Exception as e:
                        page_info["image_error"] = str(e)
                        report.add_error(f"Page {i+1} image insertion failed: {e}")
            # decide to drop blank pages if requested
            if remove_blank:
                # after insertion, check the last page of new_doc for text
                last_page = new_doc[-1]
                try:
                    last_text = last_page.get_text("text") or ""
                    if not last_text.strip():
                        # consider blank -> remove
                        new_doc.delete_page(-1)
                        page_info["removed_blank"] = True
                        report.add_action(f"Page {i+1}: removed blank page after processing")
                except Exception:
                    # be conservative - keep it
                    pass

            # record page info
            report.add_page_entry(i+1, page_info)

        except Exception as e:
            report.add_error(f"Processing page {i+1} failed: {e}")
            # continue with next page

    # metadata handling - copy original metadata and allow some sanity repair
    try:
        meta = src_doc.metadata or {}
        report.add_action(f"Copying metadata: {meta}")
        # ensure keys exist
        meta_fixed = {
            "title": meta.get("title") or (input_pdf_path.stem + " (repaired)"),
            "author": meta.get("author") or "RepairedByScript",
            "subject": meta.get("subject") or "",
            "keywords": meta.get("keywords") or "",
            "creator": meta.get("creator") or meta.get("producer") or "pdf_repair.py",
            "producer": meta.get("producer") or meta.get("creator") or ""
        }
        new_doc.set_metadata(meta_fixed)
    except Exception as e:
        report.add_error(f"Failed to set metadata on final doc: {e}")

    # Save final doc
    try:
        ensure_output_parent(output_pdf_path)
        # Use garbage cleanup to attempt final repair
        new_doc.save(str(output_pdf_path), garbage=4, deflate=True)
        report.add_action(f"Saved final repaired PDF to {output_pdf_path}")
    except Exception as e:
        report.add_error(f"Failed saving final PDF: {e}")
        raise
    finally:
        src_doc.close()
        new_doc.close()

# -------------------------
# CLI
# -------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Repair PDF using PyMuPDF + OCR fallback.")
    p.add_argument("input", type=str, help="Input PDF path")
    p.add_argument("-o", "--output", type=str, default=None, help="Output repaired PDF path")
    p.add_argument("--ocr", action="store_true", help="Use OCR fallback for pages with no text (requires Tesseract)")
    p.add_argument("--dpi", type=int, default=300, help="DPI for rendering pages for OCR (default 300)")
    p.add_argument("--extract-images", type=str, default=None, help="Directory to save extracted images")
    p.add_argument("--remove-blank", action="store_true", help="Remove pages that remain blank after processing")
    p.add_argument("--ocr-lang", type=str, default="eng", help="Tesseract OCR language (default eng)")
    p.add_argument("--report", type=str, default=None, help="Path to save JSON repair report (default: <input>.repair_report.json)")
    return p.parse_args()

def main():
    args = parse_args()
    inp = Path(args.input)
    if not inp.exists():
        print("Input file not found:", inp, file=sys.stderr)
        sys.exit(2)

    out = Path(args.output) if args.output else inp.with_name(inp.stem + ".repaired" + inp.suffix)
    report_path = Path(args.report) if args.report else inp.with_suffix(".repair_report.json")
    report = RepairReport(inp)

    # backup
    try:
        backup_file(inp, report)
    except Exception as e:
        report.add_error(f"Backup failed: {e}")

    # try simple resave repair first
    try:
        intermediate = try_simple_repair(inp, report)
        working_input = intermediate
    except Exception:
        # fallback to original file if simple repair failed
        working_input = inp
        report.add_action("Proceeding with original input due to resave failure.")

    # open working input and build repaired doc
    try:
        out_images_dir = Path(args.extract_images) if args.extract_images else None
        build_repaired_pdf(working_input, out, use_ocr=args.ocr, dpi=args.dpi,
                           extract_images=bool(out_images_dir), out_images_dir=out_images_dir or Path("."),
                           remove_blank=args.remove_blank, ocr_lang=args.ocr_lang, report=report)
    except Exception as e:
        report.add_error(f"Overall repair failed: {e}")

    # save report
    try:
        report.save(report_path)
    except Exception as e:
        print("Failed to save report:", e, file=sys.stderr)

    print("Repair complete. Report saved to", report_path)
    print("Output:", out)

if __name__ == "__main__":
    main()

