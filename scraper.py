"""
scraper.py
----------
HTML scraping layer for CSE announcements.
Targets: https://cse.lk/announcements
Filters: "DEALINGS BY DIRECTORS" | "Interim Financial Statements"

On DOM layout failure → notifier.send_health_alert() fires immediately.
"""

import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

URL = "https://www.cse.lk/announcements"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cse.lk/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

FILTERS = [
    "dealings by directors",
    "interim financial statements",
]


def get_announcements() -> list[dict]:
    """
    Scrape the CSE announcements page and return filtered rows.

    Returns list of:
        {"id": str, "company": str, "description": str, "pdf_url": str}
    """
    try:
        resp = requests.get(URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        # ── Locate the announcements table ─────────────────────────────────
        # CSE renders a <table> with announcement rows — find it by content
        table = (
            soup.find("table", {"id": lambda x: x and "announcement" in x.lower()})
            or soup.find("table", class_=lambda x: x and "announcement" in x.lower())
            or soup.find("table")   # last resort: first table on page
        )

        if not table:
            raise ValueError("Announcements table not found in DOM.")

        rows = table.find_all("tr")
        if not rows:
            raise ValueError("No <tr> rows found inside announcements table.")

        results = []

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue  # skip header rows or empty rows

            # ── Extract fields from cells ──────────────────────────────────
            # Typical CSE table layout: [ID | Date | Company | Description | PDF]
            # We try multiple index positions to be layout-resilient.
            ann_id   = _text(cells[0])
            company  = _text(cells[1]) if len(cells) > 1 else ""
            desc     = _text(cells[2]) if len(cells) > 2 else ""

            # If company column looks like a date, shift right
            if _looks_like_date(company) and len(cells) > 3:
                company = _text(cells[2])
                desc    = _text(cells[3]) if len(cells) > 3 else ""

            # ── Filter by description ──────────────────────────────────────
            if not any(f in desc.lower() for f in FILTERS):
                continue

            # ── Extract PDF link ───────────────────────────────────────────
            pdf_tag = row.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
            if not pdf_tag:
                # Also look for any link containing "pdf" or "download"
                pdf_tag = row.find(
                    "a",
                    href=lambda h: h and ("pdf" in h.lower() or "download" in h.lower()),
                )

            if not pdf_tag:
                continue  # no PDF attached, skip

            pdf_url = _make_absolute(pdf_tag["href"])

            # Use PDF filename as ID fallback if cell ID is blank
            if not ann_id:
                ann_id = pdf_url.rstrip("/").split("/")[-1]

            results.append({
                "id":          ann_id,
                "company":     company,
                "description": desc,
                "pdf_url":     pdf_url,
            })

        logger.info(
            "Scraper: %d relevant announcement(s) found from %d total rows.",
            len(results), len(rows),
        )
        return results

    except Exception as exc:
        _alert(
            f"[CSE Scraper] ⚠️ DOM layout broken or page unreachable.\n"
            f"{type(exc).__name__}: {exc}"
        )
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _text(tag) -> str:
    return tag.get_text(separator=" ", strip=True) if tag else ""


def _looks_like_date(s: str) -> bool:
    """Rough check: does this string look like a date (e.g. '2026-06-24')?"""
    import re
    return bool(re.match(r"\d{2,4}[-/]\d{1,2}[-/]\d{1,2}", s.strip()))


def _make_absolute(href: str) -> str:
    if href.startswith("http"):
        return href
    return "https://www.cse.lk" + href if href.startswith("/") else href


def _alert(msg: str) -> None:
    try:
        from notifier import send_health_alert
        send_health_alert(msg)
    except Exception as e:
        logger.error("Health alert failed: %s", e)
