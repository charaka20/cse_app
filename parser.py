"""
parser.py
---------
Downloads a PDF from a URL and extracts only the pages that are relevant
to shareholder / director analysis.

Pipeline:
  1. Download PDF into an in-memory buffer (no disk I/O required).
  2. Open with pdfplumber and extract text page by page.
  3. Keep only pages whose text contains target keywords.
  4. If a kept page yields near-empty text → OCR fallback via pytesseract.
  5. Return the concatenated filtered text.

Target page keywords:
  - "Top 20 Shareholders"
  - "Major Shareholders"
  - "Directors"
  - "Dealings"
  - "Shares Held"
"""

import io
import logging
import os
from typing import Optional

import pdfplumber
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PAGE_KEYWORDS = [
    "top 20 shareholders",
    "major shareholders",
    "twenty largest shareholders",
    "directors",
    "dealings",
    "shares held",
    "shareholding",
]

# A page is considered "blank" if it has fewer than this many characters
# after pdfplumber extraction (likely a scanned/image page).
BLANK_THRESHOLD = 50

REQUEST_TIMEOUT = 30  # seconds for PDF download
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Whether OCR fallback is available (pytesseract + pdf2image)
_OCR_AVAILABLE: Optional[bool] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def extract_relevant_text(pdf_url: str) -> str:
    """
    Download the PDF at `pdf_url` and return filtered page text.

    Returns:
        str: Concatenated text from relevant pages.
             Empty string if nothing relevant was found or download failed.
    """
    pdf_bytes = _download_pdf(pdf_url)
    if not pdf_bytes:
        return ""

    return _extract_from_bytes(pdf_bytes)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _download_pdf(url: str) -> Optional[bytes]:
    """Download PDF, return raw bytes or None on failure."""
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "pdf" not in content_type.lower() and not url.lower().endswith(".pdf"):
            logger.warning(
                "URL does not appear to be a PDF (Content-Type: %s): %s",
                content_type,
                url,
            )
        return resp.content
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to download PDF from %s: %s", url, exc)
        return None


def _extract_from_bytes(pdf_bytes: bytes) -> str:
    """
    Open PDF bytes with pdfplumber, filter relevant pages,
    apply OCR fallback for blank pages, return combined text.
    """
    relevant_chunks: list[str] = []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""

                # Check if this page is relevant
                if not _is_relevant_page(page_text):
                    continue

                # Page is relevant but near-blank → try OCR
                if len(page_text.strip()) < BLANK_THRESHOLD:
                    logger.info(
                        "Page %d has relevant keywords but sparse text "
                        "(%d chars) — attempting OCR fallback.",
                        page_num,
                        len(page_text.strip()),
                    )
                    ocr_text = _ocr_page(pdf_bytes, page_num - 1)
                    if ocr_text:
                        relevant_chunks.append(
                            f"--- Page {page_num} (OCR) ---\n{ocr_text}"
                        )
                    else:
                        logger.warning(
                            "OCR returned no text for page %d. "
                            "This page may be a scanned image without text.",
                            page_num,
                        )
                else:
                    relevant_chunks.append(
                        f"--- Page {page_num} ---\n{page_text}"
                    )

    except Exception as exc:  # pylint: disable=broad-except
        logger.error("pdfplumber failed to open PDF: %s", exc)
        # If pdfplumber fails entirely (e.g. corrupted/encrypted PDF),
        # attempt a full OCR pass on all pages as last resort.
        logger.info("Attempting full-document OCR fallback.")
        full_ocr = _ocr_full_document(pdf_bytes)
        if full_ocr:
            # Filter OCR text by keyword relevance
            for chunk in full_ocr:
                if _is_relevant_page(chunk):
                    relevant_chunks.append(chunk)

    combined = "\n\n".join(relevant_chunks)
    logger.info(
        "Parser extracted %d relevant page(s), %d total characters.",
        len(relevant_chunks),
        len(combined),
    )
    return combined


def _is_relevant_page(text: str) -> bool:
    """Return True if the page text contains at least one target keyword."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in PAGE_KEYWORDS)


# ---------------------------------------------------------------------------
# OCR helpers
# ---------------------------------------------------------------------------
def _ocr_available() -> bool:
    """Check once whether pytesseract and pdf2image are importable."""
    global _OCR_AVAILABLE  # pylint: disable=global-statement
    if _OCR_AVAILABLE is None:
        try:
            import pytesseract  # noqa: F401
            import pdf2image  # noqa: F401
            _OCR_AVAILABLE = True
            logger.debug("OCR fallback: pytesseract + pdf2image available.")
        except ImportError:
            _OCR_AVAILABLE = False
            logger.warning(
                "OCR fallback unavailable: pytesseract or pdf2image not installed."
            )
    return _OCR_AVAILABLE


def _ocr_page(pdf_bytes: bytes, page_index: int) -> str:
    """
    Convert a single PDF page to an image and run Tesseract OCR on it.

    Args:
        pdf_bytes:  Raw PDF bytes.
        page_index: 0-based page index.

    Returns:
        OCR'd text string (may be empty on failure).
    """
    if not _ocr_available():
        return ""
    try:
        import pytesseract
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(
            pdf_bytes,
            first_page=page_index + 1,
            last_page=page_index + 1,
            dpi=300,
        )
        if not images:
            return ""
        return pytesseract.image_to_string(images[0], lang="eng")
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("OCR failed for page %d: %s", page_index, exc)
        return ""


def _ocr_full_document(pdf_bytes: bytes) -> list[str]:
    """
    OCR all pages in the document.

    Returns:
        List of per-page OCR text strings.
    """
    if not _ocr_available():
        return []
    try:
        import pytesseract
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(pdf_bytes, dpi=300)
        return [pytesseract.image_to_string(img, lang="eng") for img in images]
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Full-document OCR failed: %s", exc)
        return []
