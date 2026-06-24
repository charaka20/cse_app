"""
llm_extractor.py
----------------
Sends filtered PDF text to DeepSeek API (deepseek-chat) and
extracts a structured JSON payload describing shareholder/director records.

Output JSON schema (minified to save tokens):
    [
        {
            "n": "Shareholder or Director Name",
            "p": "Percentage or Share Count (string)",
            "a": "B|S|H"   # B=Buy/Increase, S=Sell/Decrease, H=Hold
        },
        ...
    ]

Environment variable required:
    DEEPSEEK_API_KEY — your DeepSeek API key from https://platform.deepseek.com

Notes:
  - DeepSeek uses an OpenAI-compatible API, so we use the openai Python client.
  - Falls back gracefully to [] on any API/parse error.
  - Token-aware: the prompt is kept minimal; only relevant page text is sent.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL_NAME = "deepseek-chat"
MAX_INPUT_CHARS = 30_000   # Safety ceiling to keep responses fast and cheap

SYSTEM_PROMPT = (
    "You are a financial data extraction engine for the Colombo Stock Exchange (CSE). "
    "Your sole job is to extract shareholder and/or director dealings data from "
    "the document text provided. Output ONLY a minified JSON array. "
    "No markdown, no explanation, no extra text — just the raw JSON array."
)

USER_PROMPT_TEMPLATE = """
Extract all shareholder and director records from the following CSE document text.

For each person or entity, return a JSON object with EXACTLY these keys:
  "n"  — Full name of the shareholder or director (string)
  "p"  — Number of shares held OR percentage of total shares (string, include % or share count as found)
  "a"  — Action code: "B" if shares increased/bought, "S" if shares decreased/sold, "H" if unchanged/held

Rules:
- If the document is a "Top 20 Shareholders" list, set "a" to "H" unless a change is explicitly mentioned.
- If the document is a "Dealings by Directors" disclosure, infer "B" or "S" from context.
- Include ALL named entities (individuals and companies).
- If you cannot determine the action, default to "H".
- Output ONLY the JSON array. No preamble, no markdown code fences.

Document text:
---
{document_text}
---
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def extract_records(text: str) -> list[dict]:
    """
    Send `text` to DeepSeek and return a list of extracted records.

    Args:
        text: Filtered PDF text from parser.py.

    Returns:
        List of dicts with keys "n", "p", "a".
        Returns [] on any failure.
    """
    if not text or not text.strip():
        logger.warning("llm_extractor received empty text — skipping LLM call.")
        return []

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        logger.error(
            "DEEPSEEK_API_KEY environment variable is not set. "
            "Cannot call DeepSeek API."
        )
        return []

    # Truncate to stay within safe input limits
    truncated_text = text[:MAX_INPUT_CHARS]
    if len(text) > MAX_INPUT_CHARS:
        logger.warning(
            "Input text truncated from %d to %d characters for LLM.",
            len(text),
            MAX_INPUT_CHARS,
        )

    return _call_deepseek(api_key, truncated_text)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _call_deepseek(api_key: str, document_text: str) -> list[dict]:
    """
    Call DeepSeek via OpenAI-compatible client and parse the JSON response.
    DeepSeek's API is fully compatible with the openai Python package.
    """
    try:
        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
        )

        prompt = USER_PROMPT_TEMPLATE.format(document_text=document_text)

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.0,       # Deterministic extraction
            max_tokens=4096,
            response_format={"type": "json_object"},  # DeepSeek JSON mode
        )

        raw_text = response.choices[0].message.content.strip()
        logger.debug(
            "DeepSeek raw response (%d chars): %s", len(raw_text), raw_text[:500]
        )

        # DeepSeek JSON mode returns a top-level object; our schema is an array.
        # If the model wrapped the array, unwrap it.
        return _parse_and_validate(raw_text)

    except ImportError:
        logger.error(
            "openai package is not installed. Run: pip install openai"
        )
        return []
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("DeepSeek API call failed: %s", exc, exc_info=True)
        return []


def _parse_and_validate(raw_json: str) -> list[dict]:
    """
    Parse the JSON string from DeepSeek and validate the schema.

    Handles two shapes:
      - Direct array:  [{"n":...}, ...]
      - Wrapped object: {"records": [{"n":...}, ...]}

    Returns a clean list of {"n": str, "p": str, "a": str} dicts.
    Invalid records are dropped with a warning.
    """
    # Strip markdown code fences just in case
    cleaned = raw_json.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error(
            "Failed to parse DeepSeek JSON response: %s\nRaw: %s",
            exc,
            raw_json[:1000],
        )
        return []

    # Unwrap if DeepSeek returned {"records": [...]} or {"data": [...]}
    if isinstance(data, dict):
        for key in ("records", "data", "shareholders", "directors", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            logger.error(
                "Expected a JSON array from DeepSeek, got a dict with keys: %s",
                list(data.keys()),
            )
            return []

    if not isinstance(data, list):
        logger.error(
            "Expected a JSON array from DeepSeek, got %s.", type(data).__name__
        )
        return []

    valid_records: list[dict] = []
    valid_actions = {"B", "S", "H"}

    for i, record in enumerate(data):
        if not isinstance(record, dict):
            logger.warning("Record %d is not a dict — skipping.", i)
            continue

        n = str(record.get("n", "")).strip()
        p = str(record.get("p", "")).strip()
        a = str(record.get("a", "H")).strip().upper()

        if not n:
            logger.warning("Record %d has empty 'n' (name) — skipping.", i)
            continue

        if a not in valid_actions:
            logger.warning(
                "Record %d has invalid action '%s' — defaulting to 'H'.", i, a
            )
            a = "H"

        valid_records.append({"n": n, "p": p, "a": a})

    logger.info(
        "DeepSeek extracted %d valid record(s) from %d raw record(s).",
        len(valid_records),
        len(data),
    )
    return valid_records
