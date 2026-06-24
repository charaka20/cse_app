"""
notifier.py
-----------
Sends formatted alerts to a Telegram chat via the Bot API.

Two public functions:
  send_alert(message)         — Normal stock movement / disclosure alert.
  send_health_alert(message)  — System error / scraper failure warning.

Environment variables required:
  TELEGRAM_BOT_TOKEN  — Bot token from @BotFather (e.g. "123456:ABC-DEF...")
  TELEGRAM_CHAT_ID    — Chat/channel ID to send messages to (e.g. "-1001234567890")

Messages use Telegram HTML parse mode for formatting.
"""

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_TIMEOUT = 15  # seconds

ACTION_EMOJI = {
    "B": "🟢",   # Buy / Increase
    "S": "🔴",   # Sell / Decrease
    "H": "⚪",   # Hold / No change
}
ACTION_LABEL = {
    "B": "BUY / ↑",
    "S": "SELL / ↓",
    "H": "HOLD",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def send_alert(
    announcement: dict,
    records: list[dict],
    has_whale: bool = False,
) -> bool:
    """
    Send a formatted CSE disclosure alert to Telegram.

    Args:
        announcement:  Dict with keys "id", "title", "company", "pdf_url".
        records:       List of enriched dicts from logic_mapper.enrich().
        has_whale:     Whether any whale movement was detected.

    Returns:
        True if message was sent successfully, False otherwise.
    """
    message = _format_alert(announcement, records, has_whale)
    return _send(message)


def send_health_alert(error_message: str) -> bool:
    """
    Send a system health warning to Telegram.

    Args:
        error_message: Description of the failure.

    Returns:
        True if message was sent successfully, False otherwise.
    """
    message = (
        "🚨 <b>CSE Tracker — System Alert</b>\n\n"
        "⚠️ The scraper encountered an error and may have missed announcements.\n\n"
        f"<b>Error:</b>\n<code>{_escape_html(error_message[:1000])}</code>\n\n"
        "📋 Check GitHub Actions logs for the full traceback."
    )
    return _send(message)


def send_dry_run_summary(announcements_found: int, skipped: int) -> bool:
    """
    Send a dry-run summary (no real processing). Used with --dry-run flag.
    """
    message = (
        "🔍 <b>CSE Tracker — Dry Run Summary</b>\n\n"
        f"📋 Announcements found: <b>{announcements_found}</b>\n"
        f"⏩ Already processed (skipped): <b>{skipped}</b>\n"
        f"🆕 New (would process): <b>{announcements_found - skipped}</b>\n\n"
        "<i>No alerts sent — dry run mode.</i>"
    )
    return _send(message)


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------
def _format_alert(
    announcement: dict,
    records: list[dict],
    has_whale: bool,
) -> str:
    """Build a richly formatted Telegram HTML message."""
    company = _escape_html(announcement.get("company", "Unknown Company"))
    title = _escape_html(announcement.get("title", "Announcement"))
    pdf_url = announcement.get("pdf_url", "")
    ann_id = _escape_html(str(announcement.get("id", "")))

    # Header
    whale_banner = "🐋 <b>WHALE MOVEMENT DETECTED</b>\n\n" if has_whale else ""
    lines = [
        f"{whale_banner}"
        f"📌 <b>CSE Disclosure Alert</b>\n"
        f"🏢 <b>Company:</b> {company}\n"
        f"📄 <b>Type:</b> {title}\n"
        f"🆔 <b>ID:</b> <code>{ann_id}</code>",
    ]

    if pdf_url:
        lines.append(f'📎 <a href="{pdf_url}">View PDF</a>')

    lines.append("\n<b>── Extracted Data ──</b>")

    # Records table
    if records:
        for rec in records:
            name = _escape_html(rec.get("n", "Unknown"))
            pct = _escape_html(rec.get("p", "—"))
            action_code = rec.get("a", "H")
            emoji = ACTION_EMOJI.get(action_code, "⚪")
            label = ACTION_LABEL.get(action_code, "HOLD")
            whale = rec.get("whale")
            group = rec.get("group")

            line = f"{emoji} <b>{name}</b> — {pct} [{label}]"
            if whale:
                line += f"\n   ↳ 🐋 <i>{_escape_html(whale)}"
                if group:
                    line += f" / {_escape_html(group)}"
                line += "</i>"
            lines.append(line)
    else:
        lines.append("<i>No structured data extracted from this disclosure.</i>")

    # Footer
    lines.append(
        "\n<i>Powered by CSE Tracker · Colombo Stock Exchange</i>"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal transport
# ---------------------------------------------------------------------------
def _send(message: str) -> bool:
    """POST a message to the Telegram Bot API."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token:
        logger.error(
            "TELEGRAM_BOT_TOKEN is not set. Cannot send Telegram message."
        )
        return False
    if not chat_id:
        logger.error(
            "TELEGRAM_CHAT_ID is not set. Cannot send Telegram message."
        )
        return False

    url = TELEGRAM_API_BASE.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        result = resp.json()
        if result.get("ok"):
            logger.info("Telegram message sent successfully.")
            return True
        else:
            logger.error(
                "Telegram API returned ok=false: %s", result.get("description")
            )
            return False
    except requests.exceptions.HTTPError as exc:
        logger.error("Telegram HTTP error: %s — Response: %s", exc, exc.response.text)
        return False
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to send Telegram message: %s", exc, exc_info=True)
        return False


def _escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )
