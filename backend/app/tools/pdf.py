"""PDF text extraction with a scanned-page fallback path.

Uses PyMuPDF's 'blocks' extraction mode to preserve layout structure
(headers, bullet-lists, columns) better than the default 'text' mode,
which is critical for multi-column CVs.
"""
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
    """Extract text preserving layout; fall back to OCR for scanned PDFs."""
    doc = pymupdf.open(str(path))
    try:
        chunks: list[str] = []
        for page in doc:
            # 'blocks' gives (x0,y0,x1,y1, text, block_no, block_type) tuples
            # sorted by their vertical position — better than 'text' for CVs
            blocks = page.get_text("blocks", sort=True)          # type: ignore[call-arg]
            page_lines: list[str] = []
            for block in blocks:
                if block[6] != 0:   # skip image blocks (type != 0)
                    continue
                raw: str = block[4]
                # Normalise whitespace but preserve paragraph breaks
                raw = raw.replace("\r\n", "\n").replace("\r", "\n")
                page_lines.append(raw.strip())
            chunks.append("\n".join(filter(None, page_lines)))

        text = "\n\n".join(filter(None, chunks)).strip()

        # If blocks gave us very little (e.g. a linearised / XFA form),
        # fall back to simple text extraction
        if len(text) < MIN_TEXT_LEN:
            text = "\n".join(p.get_text() for p in doc).strip()

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
