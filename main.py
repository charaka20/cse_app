"""
main.py
-------
Orchestrator for the CSE Tracker pipeline.

Flow per announcement:
  1. Scrape CSE → get announcement list
  2. Check SQLite → skip if ID already processed
  3. Download + parse PDF → extract relevant page text
  4. DeepSeek LLM → extract structured records
  5. logic_mapper → enrich with whale identities
  6. Telegram → push alert
  7. SQLite → mark ID as processed
  8. Wipe RAM buffers immediately

Usage:
    python main.py             # Full production run
    python main.py --dry-run   # Scrape + dedup only, no LLM or alerts
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

# ── Load .env for local development ───────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import scraper
import parser as pdf_parser
import llm_extractor
import logic_mapper
import notifier

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_PATH = Path(__file__).parent / "cse_state.db"
TABLE   = "processed"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id           TEXT PRIMARY KEY,
            company      TEXT,
            description  TEXT,
            processed_at TEXT
        )
    """)
    conn.commit()


def is_seen(conn: sqlite3.Connection, ann_id: str) -> bool:
    return conn.execute(
        f"SELECT 1 FROM {TABLE} WHERE id=?", (ann_id,)
    ).fetchone() is not None


def mark_seen(conn: sqlite3.Connection, ann: dict) -> None:
    conn.execute(
        f"INSERT OR IGNORE INTO {TABLE} (id,company,description,processed_at) VALUES (?,?,?,?)",
        (
            ann["id"],
            ann.get("company", ""),
            ann.get("description", ""),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def process(conn: sqlite3.Connection, ann: dict, dry_run: bool) -> None:
    """Run the full pipeline for one announcement."""
    ann_id = ann["id"]
    logger.info("Processing [%s] %s — %s", ann_id, ann.get("company"), ann.get("description"))

    pdf_url = ann.get("pdf_url", "")
    if not pdf_url:
        logger.warning("No PDF URL — marking seen and skipping.")
        mark_seen(conn, ann)
        return

    # Step 1: Parse PDF
    text = pdf_parser.extract_text(pdf_url)
    if not text:
        logger.warning("No relevant text extracted — marking seen.")
        mark_seen(conn, ann)
        return

    # Step 2: LLM extraction
    records = llm_extractor.extract(text)
    text = None  # wipe RAM

    # Step 3: Whale enrichment
    records = logic_mapper.enrich(records or [])

    # Step 4: Telegram alert
    if not dry_run:
        sent = notifier.send_alert(ann, records)
        if not sent:
            logger.error("Alert failed — NOT marking as seen (will retry next run).")
            return

    # Step 5: Persist
    mark_seen(conn, ann)
    records = None  # wipe RAM
    logger.info("Done [%s].", ann_id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(dry_run: bool = False) -> None:
    logger.info(
        "═══ CSE Tracker started %s%s ═══",
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        " [DRY RUN]" if dry_run else "",
    )

    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)

        # Scrape
        announcements = scraper.get_announcements()
        if not announcements:
            logger.info("No relevant announcements found.")
            return

        new = skipped = failed = done = 0

        for ann in announcements:
            ann_id = ann.get("id", "")
            if not ann_id:
                continue

            # ── Dedup check ────────────────────────────────────────────────
            if is_seen(conn, ann_id):
                logger.debug("Seen [%s] — skipping.", ann_id)
                skipped += 1
                continue

            new += 1

            if dry_run:
                logger.info("[DRY RUN] Would process: [%s] %s", ann_id, ann.get("company"))
                continue

            try:
                process(conn, ann, dry_run=False)
                done += 1
            except Exception as exc:
                logger.error("Unhandled error [%s]: %s", ann_id, exc, exc_info=True)
                failed += 1
                # Do NOT mark_seen — will retry next run

    logger.info(
        "═══ Run complete | Found:%d New:%d Done:%d Skipped:%d Failed:%d ═══",
        len(announcements), new, done, skipped, failed,
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Scrape + dedup only. No LLM, no alerts.")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
