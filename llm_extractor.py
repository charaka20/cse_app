"""
llm_extractor.py
----------------
Sends filtered PDF text to Google Gemini (gemini-1.5-flash) and
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
    GEMINI_API_KEY — your Google AI Studio API key

Notes:
  - Uses response_mime_type="application/json" for deterministic JSON output.
  - Falls back gracefully to [] on any API/parse error.
  - Token-aware: the prompt is kept minimal; only relevant page text is sent.
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL_NAME = "gemini-1.5-flash"
MAX_INPUT_CHARS = 30_000   # Safety ceiling; Gemini flash handles ~1M tokens
                            # but we want fast, cheap responses.

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
    Send `text` to Gemini and return a list of extracted records.

    Args:
        text: Filtered PDF text from parser.py.

    Returns:
        List of dicts with keys "n", "p", "a".
        Returns [] on any failure.
    """
    if not text or not text.strip():
        logger.warning("llm_extractor received empty text — skipping LLM call.")
        return []

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error(
            "GEMINI_API_KEY environment variable is not set. "
            "Cannot call Gemini API."
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

    return _call_gemini(api_key, truncated_text)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _call_gemini(api_key: str, document_text: str) -> list[dict]:
    """Call Gemini API and parse the JSON response."""
    try:
        import google.generativeai as genai  # noqa: PLC0415

        genai.configure(api_key=api_key)

        model = genai.GenerativeModel(
            model_name=MODEL_NAME,
            system_instruction=SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.0,      # Deterministic extraction
                max_output_tokens=4096,
            ),
        )

        prompt = USER_PROMPT_TEMPLATE.format(document_text=document_text)
        response = model.generate_content(prompt)

        raw_text = response.text.strip()
        logger.debug("Gemini raw response (%d chars): %s", len(raw_text), raw_text[:500])

        return _parse_and_validate(raw_text)

    except ImportError:
        logger.error(
            "google-generativeai package is not installed. "
            "Run: pip install google-generativeai"
        )
        return []
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Gemini API call failed: %s", exc, exc_info=True)
        return []


def _parse_and_validate(raw_json: str) -> list[dict]:
    """
    Parse the JSON string from Gemini and validate the schema.

    Returns a clean list of {"n": str, "p": str, "a": str} dicts.
    Invalid records are dropped with a warning.
    """
    # Strip markdown code fences if Gemini added them despite instructions
    cleaned = raw_json.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last fence lines
        cleaned = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error(
            "Failed to parse Gemini JSON response: %s\nRaw: %s",
            exc,
            raw_json[:1000],
        )
        return []

    if not isinstance(data, list):
        logger.error(
            "Expected a JSON array from Gemini, got %s.", type(data).__name__
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
        "LLM extracted %d valid record(s) from %d raw record(s).",
        len(valid_records),
        len(data),
    )
    return valid_records
