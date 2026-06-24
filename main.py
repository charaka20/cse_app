"""
main.py
-------
CSE Tracker — Orchestrator

Runs the full pipeline for each new CSE announcement:
  1. Scrape CSE for relevant announcements.
  2. Check SQLite (cse_state.db) for already-processed IDs → skip if seen.
  3. Download & parse the PDF → extract relevant page text.
  4. Send to Gemini LLM → extract structured shareholder/director records.
  5. Cross-reference against whale dictionary → flag large-player movements.
  6. Push formatted Telegram alert.
  7. Save announcement ID to SQLite to prevent reprocessing.

Usage:
  python main.py             # Normal production run
  python main.py --dry-run   # Scrape + dedup check only; no LLM, no Telegram alerts

Environment variables (set via GitHub Actions secrets or local .env):
  GEMINI_API_KEY
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging setup — must happen before importing project modules
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# Project module imports
# ---------------------------------------------------------------------------
import scraper
import parser as pdf_parser
import llm_extractor
import logic_mapper
import notifier

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_PATH = Path(__file__).parent / "cse_state.db"
DB_TABLE = "processed_announcements"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def init_db(conn: sqlite3.Connection) -> None:
    """Create the deduplication table if it doesn't exist."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DB_TABLE} (
            id          TEXT PRIMARY KEY,
            title       TEXT,
            company     TEXT,
            processed_at TEXT
        )
        """
    )
    conn.commit()
    logger.debug("SQLite DB initialised at: %s", DB_PATH)


def is_processed(conn: sqlite3.Connection, ann_id: str) -> bool:
    """Return True if this announcement ID has already been processed."""
    row = conn.execute(
        f"SELECT 1 FROM {DB_TABLE} WHERE id = ?", (ann_id,)
    ).fetchone()
    return row is not None


def mark_processed(conn: sqlite3.Connection, announcement: dict) -> None:
    """Insert the announcement ID into the processed table."""
    conn.execute(
        f"""
        INSERT OR IGNORE INTO {DB_TABLE} (id, title, company, processed_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            announcement["id"],
            announcement.get("title", ""),
            announcement.get("company", ""),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    logger.info("Marked announcement %s as processed.", announcement["id"])


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def process_announcement(
    conn: sqlite3.Connection,
    announcement: dict,
    dry_run: bool = False,
) -> bool:
    """
    Run the full processing pipeline for a single announcement.

    Returns:
        True  — announcement was successfully processed (or dry-run checked).
        False — a recoverable error occurred; ID not marked as processed.
    """
    ann_id = announcement["id"]
    company = announcement.get("company", "?")
    title = announcement.get("title", "?")
    pdf_url = announcement.get("pdf_url", "")

    logger.info(
        "Processing [%s] %s — %s",
        ann_id,
        company,
        title,
    )

    if not pdf_url:
        logger.warning("Announcement %s has no PDF URL — skipping.", ann_id)
        mark_processed(conn, announcement)
        return True

    # ── Step 1: Parse PDF ──────────────────────────────────────────────────
    logger.info("Downloading and parsing PDF: %s", pdf_url)
    extracted_text = pdf_parser.extract_relevant_text(pdf_url)

    if not extracted_text:
        logger.warning(
            "No relevant text extracted from PDF for announcement %s. "
            "Possible scanned PDF without OCR support, or no matching pages.",
            ann_id,
        )
        # Still mark as processed to avoid retrying indefinitely
        mark_processed(conn, announcement)
        return True

    # ── Step 2: LLM Extraction ────────────────────────────────────────────
    logger.info("Sending extracted text to Gemini LLM.")
    raw_records = llm_extractor.extract_records(extracted_text)

    if not raw_records:
        logger.warning(
            "LLM returned no records for announcement %s.", ann_id
        )
        # Still mark as processed
        mark_processed(conn, announcement)
        return True

    # ── Step 3: Whale Enrichment ──────────────────────────────────────────
    enriched_records = logic_mapper.enrich(raw_records)
    has_whale = logic_mapper.is_whale_movement(enriched_records)

    # ── Step 4: Send Telegram Alert ───────────────────────────────────────
    if not dry_run:
        success = notifier.send_alert(
            announcement=announcement,
            records=enriched_records,
            has_whale=has_whale,
        )
        if not success:
            logger.error(
                "Failed to send Telegram alert for announcement %s. "
                "Will NOT mark as processed — will retry next run.",
                ann_id,
            )
            return False

    # ── Step 5: Persist to SQLite ─────────────────────────────────────────
    mark_processed(conn, announcement)
    return True


def run(dry_run: bool = False) -> None:
    """Main entry point for the tracker pipeline."""
    logger.info(
        "═══════════════════════════════════════════════\n"
        "  CSE Tracker started — %s%s\n"
        "═══════════════════════════════════════════════",
        datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        " [DRY RUN]" if dry_run else "",
    )

    # ── Open DB ────────────────────────────────────────────────────────────
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)

        # ── Scrape announcements ───────────────────────────────────────────
        logger.info("Fetching CSE announcements...")
        announcements = scraper.get_announcements()

        if not announcements:
            logger.info("No relevant announcements found this run.")
            return

        logger.info("Found %d relevant announcement(s).", len(announcements))

        new_count = 0
        skipped_count = 0
        processed_count = 0
        failed_count = 0

        for announcement in announcements:
            ann_id = announcement.get("id", "")

            if not ann_id:
                logger.warning("Announcement missing ID — skipping: %s", announcement)
                continue

            # ── Deduplication check ────────────────────────────────────────
            if is_processed(conn, ann_id):
                logger.debug("Announcement %s already processed — skipping.", ann_id)
                skipped_count += 1
                continue

            new_count += 1

            if dry_run:
                logger.info(
                    "[DRY RUN] Would process: [%s] %s — %s",
                    ann_id,
                    announcement.get("company"),
                    announcement.get("title"),
                )
                continue

            # ── Process ────────────────────────────────────────────────────
            try:
                success = process_announcement(conn, announcement, dry_run=False)
                if success:
                    processed_count += 1
                else:
                    failed_count += 1
            except Exception as exc:  # pylint: disable=broad-except
                logger.error(
                    "Unhandled error processing announcement %s: %s",
                    ann_id,
                    exc,
                    exc_info=True,
                )
                failed_count += 1
                # Don't mark as processed so it retries next run

    # ── Summary ────────────────────────────────────────────────────────────
    logger.info(
        "Run complete — Scraped: %d | Skipped: %d | New: %d | "
        "Processed: %d | Failed: %d",
        len(announcements),
        skipped_count,
        new_count,
        processed_count,
        failed_count,
    )

    if dry_run:
        notifier.send_dry_run_summary(
            announcements_found=len(announcements),
            skipped=skipped_count,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="CSE Tracker — Colombo Stock Exchange Announcement Monitor"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run scraper and dedup check only. "
            "Skip PDF parsing, LLM extraction, and Telegram alerts. "
            "Sends a single Telegram summary instead."
        ),
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
