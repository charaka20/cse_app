"""
parser.py
---------
Minimum-Storage PDF extraction layer.

Rules:
  - ALL PDF data flows through io.BytesIO (zero disk writes).
  - Only pages containing target keywords are kept — rest discarded immediately.
  - OCR fires ONLY when pdfplumber returns blank text (conditional, not default).
"""

import io
import logging
import requests
import pdfplumber

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.cse.lk/",
}

# Only pages containing these keywords are kept — all others discarded
PAGE_KEYWORDS = [
    "top 20 shareholders",
    "major shareholders",
    "directors",
    "dealings",
    "shares held",
    "twenty largest shareholders",
]

BLANK_THRESHOLD = 40  # chars; below this → assume scanned image, try OCR


def extract_text(pdf_url: str) -> str:
    """
    Download PDF into RAM buffer, extract only high-signal pages.

    Returns:
        Filtered text string ready for LLM. Empty string on failure.
    """
    raw = _download(pdf_url)
    if not raw:
        return ""

    buf = io.BytesIO(raw)
    raw = None  # release raw bytes from RAM immediately

    return _extract(buf, pdf_url)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------
def _download(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception as exc:
        logger.error("PDF download failed [%s]: %s", url, exc)
        return None


def _extract(buf: io.BytesIO, url: str) -> str:
    kept = []

    try:
        with pdfplumber.open(buf) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""

                # Discard immediately if no keyword match
                if not _relevant(text):
                    continue

                # Blank text on relevant page → conditional OCR
                if len(text.strip()) < BLANK_THRESHOLD:
                    logger.info("Page %d blank — triggering OCR.", i)
                    text = _ocr_page(buf, i)

                if text.strip():
                    kept.append(f"[p{i}]\n{text.strip()}")

    except Exception as exc:
        logger.error("pdfplumber failed [%s]: %s", url, exc)
        # Full-document OCR fallback
        kept = _ocr_all(buf)

    buf.close()

    result = "\n\n".join(kept)
    logger.info("Parser: %d relevant page(s), %d chars.", len(kept), len(result))
    return result


def _relevant(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in PAGE_KEYWORDS)


def _ocr_page(buf: io.BytesIO, page_num: int) -> str:
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
        buf.seek(0)
        imgs = convert_from_bytes(buf.read(), first_page=page_num,
                                  last_page=page_num, dpi=300)
        return pytesseract.image_to_string(imgs[0]) if imgs else ""
    except Exception as exc:
        logger.error("OCR page %d failed: %s", page_num, exc)
        return ""


def _ocr_all(buf: io.BytesIO) -> list[str]:
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
        buf.seek(0)
        imgs = convert_from_bytes(buf.read(), dpi=300)
        pages = []
        for i, img in enumerate(imgs, 1):
            t = pytesseract.image_to_string(img)
            if _relevant(t):
                pages.append(f"[p{i}]\n{t.strip()}")
        return pages
    except Exception as exc:
        logger.error("Full OCR failed: %s", exc)
        return []
