"""PDF text extraction with a scanned-page fallback path."""
from __future__ import annotations

from pathlib import Path

import pymupdf

MIN_TEXT_LEN = 120


class PdfResult:
    """Text extracted from a PDF plus quality flags."""

    def __init__(self, text: str, pages: int, scanned: bool, note: str = "") -> None:
        self.text = text
        self.pages = pages
        self.scanned = scanned
        self.note = note

    @property
    def ok(self) -> bool:
        return len(self.text.strip()) >= MIN_TEXT_LEN


def extract_pdf_text(path: str | Path) -> PdfResult:
    """Extract text; if it looks scanned, try OCR when tesseract is installed."""
    doc = pymupdf.open(str(path))
    try:
        chunks = [page.get_text() for page in doc]
        text = "\n".join(chunks).strip()
        scanned = len(text) < MIN_TEXT_LEN
        note = ""
        if scanned:
            text = _ocr_attempt(doc) or text
            if len(text.strip()) < MIN_TEXT_LEN:
                note = "scanned or text-poor PDF; OCR unavailable"
        return PdfResult(text=text, pages=doc.page_count, scanned=scanned, note=note)
    finally:
        doc.close()


def _ocr_attempt(doc: "pymupdf.Document") -> str:
    """Render pages and OCR them if pytesseract + tesseract binary exist."""
    try:
        import io

        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    parts: list[str] = []
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            parts.append(pytesseract.image_to_string(img))
    except Exception:
        return ""
    return "\n".join(parts).strip()
