"""
scraper.py
----------
Monitors the Colombo Stock Exchange (CSE) announcements feed.
Primary:  CSE JSON API  (structured, reliable)
Fallback: HTML scraping via BeautifulSoup (in case API endpoint changes)

Target announcement types:
  - "DEALINGS BY DIRECTORS"
  - "Interim Financial Statements"

On any unhandled exception, a Telegram health alert is fired immediately.
"""

import os
import logging
import requests
from bs4 import BeautifulSoup
from typing import Optional

# notifier import is deferred inside the except block to avoid circular imports
# during unit-testing scraper.py in isolation.

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CSE_API_URL = "https://www.cse.lk/api/announcements"
CSE_HTML_URL = (
    "https://www.cse.lk/pages/company-announcements/"
    "company-announcements.component.html"
)
PDF_BASE_URL = "https://www.cse.lk"

TARGET_KEYWORDS = [
    "DEALINGS BY DIRECTORS",
    "Interim Financial Statements",
    "interim financial statements",
    "dealings by directors",
]

REQUEST_TIMEOUT = 20  # seconds
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Referer": "https://www.cse.lk/",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_announcements() -> list[dict]:
    """
    Fetch and return filtered CSE announcements.

    Returns a list of dicts:
        {
            "id":       str,   # unique announcement identifier
            "title":    str,   # announcement subject/title
            "company":  str,   # listed company name
            "pdf_url":  str,   # absolute URL to the linked PDF
        }

    On failure: fires a Telegram health alert and returns an empty list
    so main.py can continue without crashing.
    """
    try:
        announcements = _fetch_via_api()
        if not announcements:
            logger.info("API returned no results, attempting HTML fallback.")
            announcements = _fetch_via_html()

        filtered = _filter_relevant(announcements)
        logger.info(
            "Scraper found %d relevant announcement(s) out of %d total.",
            len(filtered),
            len(announcements),
        )
        return filtered

    except Exception as exc:  # pylint: disable=broad-except
        error_msg = (
            f"[CSE Scraper] ⚠️ DOM/API layout may have changed.\n"
            f"Exception: {type(exc).__name__}: {exc}"
        )
        logger.error(error_msg, exc_info=True)
        _fire_health_alert(error_msg)
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _fetch_via_api() -> list[dict]:
    """
    Hit the CSE JSON API and normalise response into a flat list.
    The API returns paginated JSON; we grab the first page (latest ~50).
    """
    params = {
        "pageSize": 50,
        "pageNo": 0,
        "language": "en",
    }
    resp = requests.get(
        CSE_API_URL,
        params=params,
        headers=REQUEST_HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()

    # CSE API nests results differently depending on endpoint version;
    # handle both common shapes gracefully.
    items: list = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        # Try common wrapper keys in priority order
        for key in ("announcements", "data", "results", "content"):
            if key in payload and isinstance(payload[key], list):
                items = payload[key]
                break

    return [_normalise_api_item(item) for item in items if _has_pdf(item)]


def _normalise_api_item(item: dict) -> dict:
    """Map an API response dict to our canonical announcement schema."""
    # Field names observed from CSE API (may vary; fall back gracefully)
    ann_id = str(
        item.get("id")
        or item.get("announcementId")
        or item.get("announcement_id")
        or ""
    )
    title = (
        item.get("subject")
        or item.get("title")
        or item.get("announcementType")
        or ""
    )
    company = (
        item.get("companyName")
        or item.get("company")
        or item.get("symbol")
        or ""
    )
    raw_pdf = (
        item.get("fileUrl")
        or item.get("pdfUrl")
        or item.get("pdf_url")
        or item.get("attachmentUrl")
        or ""
    )
    pdf_url = _make_absolute(raw_pdf)

    return {
        "id": ann_id,
        "title": title,
        "company": company,
        "pdf_url": pdf_url,
    }


def _fetch_via_html() -> list[dict]:
    """
    BeautifulSoup fallback — parse the CSE announcements HTML page.
    Looks for <tr> rows containing announcement data and a PDF link.
    """
    resp = requests.get(
        CSE_HTML_URL,
        headers=REQUEST_HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    announcements: list[dict] = []
    # CSE uses a table with rows for each announcement
    for row in soup.select("table tr, .announcement-row, .announcement-item"):
        pdf_tag = row.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
        if not pdf_tag:
            continue

        cells = row.find_all("td")
        ann_id = ""
        title = ""
        company = ""

        if len(cells) >= 3:
            ann_id = cells[0].get_text(strip=True)
            company = cells[1].get_text(strip=True)
            title = cells[2].get_text(strip=True)
        elif len(cells) >= 1:
            title = cells[0].get_text(strip=True)

        # Try to extract ID from row attributes or data attributes
        ann_id = ann_id or row.get("data-id", "") or row.get("id", "")

        pdf_url = _make_absolute(pdf_tag["href"])
        if pdf_url:
            announcements.append(
                {
                    "id": ann_id or _derive_id_from_url(pdf_url),
                    "title": title,
                    "company": company,
                    "pdf_url": pdf_url,
                }
            )

    return announcements


def _filter_relevant(announcements: list[dict]) -> list[dict]:
    """Keep only announcements whose title matches our target keywords."""
    relevant = []
    for ann in announcements:
        title_upper = ann.get("title", "").upper()
        if any(kw.upper() in title_upper for kw in TARGET_KEYWORDS):
            relevant.append(ann)
    return relevant


def _has_pdf(item: dict) -> bool:
    """Return True if the raw API item has any PDF URL field."""
    pdf_keys = ("fileUrl", "pdfUrl", "pdf_url", "attachmentUrl")
    return any(item.get(k) for k in pdf_keys)


def _make_absolute(url: str) -> str:
    """Ensure the URL is absolute; prepend CSE base if it starts with '/'."""
    if not url:
        return ""
    if url.startswith("http"):
        return url
    return PDF_BASE_URL + url if url.startswith("/") else url


def _derive_id_from_url(url: str) -> str:
    """Last resort: use the PDF filename as a stable deduplication key."""
    return url.rstrip("/").split("/")[-1]


def _fire_health_alert(message: str) -> None:
    """Lazy-import notifier to avoid circular import issues."""
    try:
        from notifier import send_health_alert  # noqa: PLC0415
        send_health_alert(message)
    except Exception as notify_exc:  # pylint: disable=broad-except
        logger.error(
            "Failed to send health alert: %s", notify_exc, exc_info=True
        )
