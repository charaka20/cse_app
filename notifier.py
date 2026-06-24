"""
notifier.py
-----------
Telegram push interface.

Functions:
    send_alert(message)           — Standard stock movement alert
    send_health_alert(error_msg)  — System failure warning

Uses Telegram HTML parse mode for bold/link formatting.
Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment.
"""

import logging
import os
import requests

logger = logging.getLogger(__name__)

TG_URL = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = 15

ACTION_EMOJI = {"B": "🟢", "S": "🔴", "H": "⚪"}
ACTION_LABEL = {"B": "BUY ↑", "S": "SELL ↓", "H": "HOLD"}


def send_alert(announcement: dict, records: list[dict]) -> bool:
    """
    Send a formatted CSE disclosure alert.

    Args:
        announcement: {"id", "company", "description", "pdf_url"}
        records:      enriched list from logic_mapper
    """
    whale_flag = any(r.get("whale") for r in records)
    lines = []

    # Header
    if whale_flag:
        lines.append("🐋 <b>WHALE MOVEMENT DETECTED</b>")
        lines.append("")

    lines += [
        "📌 <b>CSE Disclosure</b>",
        f"🏢 <b>Company:</b> {_esc(announcement.get('company', '?'))}",
        f"📄 <b>Type:</b> {_esc(announcement.get('description', '?'))}",
        f"🆔 <b>ID:</b> <code>{_esc(str(announcement.get('id', '?')))}</code>",
    ]

    pdf = announcement.get("pdf_url", "")
    if pdf:
        lines.append(f'📎 <a href="{pdf}">View PDF</a>')

    lines.append("")
    lines.append("<b>── Extracted Records ──</b>")

    if records:
        for r in records:
            a = r.get("a", "H")
            line = (
                f"{ACTION_EMOJI.get(a, '⚪')} "
                f"<b>{_esc(r.get('n', '?'))}</b> — "
                f"{_esc(r.get('p', '—'))} [{ACTION_LABEL.get(a, 'HOLD')}]"
            )
            if r.get("whale"):
                line += (
                    f"\n   ↳ 🐋 <i>{_esc(r['whale'])}"
                    + (f" / {_esc(r['group'])}" if r.get("group") else "")
                    + "</i>"
                )
            lines.append(line)
    else:
        lines.append("<i>No structured data extracted.</i>")

    lines.append("")
    lines.append("<i>CSE Tracker · Colombo Stock Exchange</i>")

    return _send("\n".join(lines))


def send_health_alert(error_msg: str) -> bool:
    """Send a system failure warning to Telegram."""
    msg = (
        "🚨 <b>CSE Tracker — System Alert</b>\n\n"
        "⚠️ Scraper or pipeline failure detected.\n\n"
        f"<b>Error:</b>\n<code>{_esc(error_msg[:1000])}</code>\n\n"
        "📋 Check GitHub Actions logs."
    )
    return _send(msg)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------
def _send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat:
        logger.error("Telegram credentials missing from environment.")
        return False

    try:
        r = requests.post(
            TG_URL.format(token=token),
            json={
                "chat_id": chat,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        ok = r.json().get("ok", False)
        if not ok:
            logger.error("Telegram error: %s", r.json().get("description"))
        return ok
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def _esc(s: str) -> str:
    """Escape HTML special chars for Telegram HTML parse mode."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
