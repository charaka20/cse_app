"""
scraper.py
----------
Monitors the Colombo Stock Exchange (CSE) announcements feed.

CSE API uses POST requests (not GET) to the following endpoints:
  Primary:  POST https://www.cse.lk/api/approvedAnnouncement
  Fallback: POST https://www.cse.lk/api/getFinancialAnnouncement

Both return JSON arrays of announcement objects.

Target announcement types:
  - "DEALINGS BY DIRECTORS"
  - "Interim Financial Statements"

On any unhandled exception, a Telegram health alert is fired immediately.
"""

import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CSE_API_BASE = "https://www.cse.lk/api/"

# CSE uses POST endpoints (reverse-engineered from the cse.lk web portal)
CSE_ENDPOINTS = [
    "approvedAnnouncement",       # General approved announcements (primary)
    "getFinancialAnnouncement",   # Financial announcements (fallback)
]

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
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.cse.lk",
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

    On failure: fires a Telegram health alert and returns an empty list.
    """
    try:
        announcements = []

        for endpoint in CSE_ENDPOINTS:
            logger.info("Trying CSE endpoint: %s", endpoint)
            items = _fetch_from_endpoint(endpoint)
            if items:
                announcements = items
                logger.info(
                    "Endpoint '%s' returned %d items.", endpoint, len(items)
                )
                break

        if not announcements:
            logger.warning(
                "All CSE endpoints returned empty. Market may be closed "
                "or no announcements today."
            )
            return []

        filtered = _filter_relevant(announcements)
        logger.info(
            "Scraper found %d relevant announcement(s) out of %d total.",
            len(filtered),
            len(announcements),
        )
        return filtered

    except Exception as exc:  # pylint: disable=broad-except
        error_msg = (
            f"[CSE Scraper] ⚠️ API layout may have changed.\n"
            f"Exception: {type(exc).__name__}: {exc}"
        )
        logger.error(error_msg, exc_info=True)
        _fire_health_alert(error_msg)
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _fetch_from_endpoint(endpoint: str) -> list[dict]:
    """
    POST to a CSE API endpoint and return normalised announcement list.
    The CSE API accepts an empty JSON body {} for listing all announcements.
    """
    url = CSE_API_BASE + endpoint

    # Try with empty body first, then with common filter params
    payloads = [
        {},
        {"pageNo": 0, "pageSize": 50},
        {"pageNo": "0", "pageSize": "50", "language": "en"},
    ]

    for payload in payloads:
        try:
            resp = requests.post(
                url,
                json=payload,
                headers=REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if resp.status_code == 200:
                data = resp.json()
                items = _unwrap_response(data)
                if items:
                    return [_normalise_item(item) for item in items
                            if _has_pdf(item)]
            else:
                logger.debug(
                    "Endpoint %s returned HTTP %d with payload %s",
                    endpoint, resp.status_code, payload
                )

        except requests.exceptions.RequestException as exc:
            logger.debug("Request failed for %s: %s", endpoint, exc)
            continue

    return []


def _unwrap_response(data) -> list:
    """
    CSE API may return:
      - A plain list:            [{...}, {...}]
      - A wrapped dict:          {"announcements": [...]}
      - A paginated dict:        {"data": [...], "total": N}
    """
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("announcements", "data", "results", "content",
                    "responseData", "list", "items"):
            if isinstance(data.get(key), list):
                return data[key]

    return []


def _normalise_item(item: dict) -> dict:
    """Map a raw API item to our canonical announcement schema."""
    ann_id = str(
        item.get("id")
        or item.get("announcementId")
        or item.get("announcement_id")
        or item.get("annId")
        or ""
    )
    title = (
        item.get("subject")
        or item.get("title")
        or item.get("announcementType")
        or item.get("type")
        or ""
    )
    company = (
        item.get("companyName")
        or item.get("company")
        or item.get("symbol")
        or item.get("stockSymbol")
        or ""
    )
    raw_pdf = (
        item.get("fileUrl")
        or item.get("pdfUrl")
        or item.get("pdf_url")
        or item.get("attachmentUrl")
        or item.get("fileLink")
        or item.get("url")
        or ""
    )
    pdf_url = _make_absolute(raw_pdf)

    # Derive a stable ID from the PDF URL if no ID field found
    if not ann_id and pdf_url:
        ann_id = _derive_id_from_url(pdf_url)

    return {
        "id": ann_id,
        "title": title,
        "company": company,
        "pdf_url": pdf_url,
        "_raw": item,  # keep raw data for debugging
    }


def _filter_relevant(announcements: list[dict]) -> list[dict]:
    """Keep only announcements whose title matches our target keywords."""
    relevant = []
    for ann in announcements:
        title_upper = ann.get("title", "").upper()
        if any(kw.upper() in title_upper for kw in TARGET_KEYWORDS):
            relevant.append(ann)
    return relevant


def _has_pdf(item: dict) -> bool:
    """Return True if the raw API item has any PDF URL field populated."""
    pdf_keys = ("fileUrl", "pdfUrl", "pdf_url", "attachmentUrl",
                 "fileLink", "url")
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
