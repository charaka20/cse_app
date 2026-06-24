"""
llm_extractor.py
----------------
Token-minimizing AI layer using DeepSeek API.

Output schema (single-char keys to minimize generation tokens):
    [{"n": "Name", "p": "Percentage/Volume", "a": "B|S|H"}]
    a = Action: B=Buy/Increase, S=Sell/Decrease, H=Hold/Unchanged

Environment variable required: DEEPSEEK_API_KEY
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

DEEPSEEK_BASE = "https://api.deepseek.com"
MODEL = "deepseek-chat"
MAX_CHARS = 28_000  # hard ceiling — truncate beyond this

# Minified system prompt — every word costs tokens
SYSTEM = (
    "Extract CSE shareholder/director data. "
    "Output ONLY a JSON array. No text, no markdown. "
    'Schema: [{"n":"Name","p":"Shares/%","a":"B|S|H"}] '
    "a=B(bought/increased),S(sold/decreased),H(held/unchanged)."
)

USER_TMPL = "Extract from this CSE document:\n---\n{text}\n---"


def extract(text: str) -> list[dict]:
    """
    Send filtered text to DeepSeek and return parsed records.

    Returns [] on any failure.
    """
    if not text or not text.strip():
        logger.warning("LLM: empty input, skipping.")
        return []

    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        logger.error("DEEPSEEK_API_KEY not set.")
        return []

    # Truncate to ceiling — saves tokens on large financials
    if len(text) > MAX_CHARS:
        logger.warning("LLM: input truncated %d→%d chars.", len(text), MAX_CHARS)
        text = text[:MAX_CHARS]

    return _call(key, text)


def _call(key: str, text: str) -> list[dict]:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=DEEPSEEK_BASE)

        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user",   "content": USER_TMPL.format(text=text)},
            ],
            temperature=0.0,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )

        raw = resp.choices[0].message.content.strip()
        logger.debug("DeepSeek raw: %s", raw[:300])
        return _parse(raw)

    except ImportError:
        logger.error("openai package missing. Run: pip install openai")
        return []
    except Exception as exc:
        logger.error("DeepSeek call failed: %s", exc)
        return []


def _parse(raw: str) -> list[dict]:
    """Parse and validate the JSON response from DeepSeek."""
    # Strip accidental code fences
    s = raw.strip()
    if s.startswith("```"):
        s = "\n".join(l for l in s.split("\n") if not l.strip().startswith("```"))

    try:
        data = json.loads(s.strip())
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed: %s | raw: %s", e, raw[:500])
        return []

    # DeepSeek JSON mode may wrap array in a dict
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                data = v
                break
        else:
            logger.error("Expected array, got dict keys: %s", list(data.keys()))
            return []

    if not isinstance(data, list):
        logger.error("Expected list, got %s", type(data).__name__)
        return []

    valid = []
    for rec in data:
        if not isinstance(rec, dict):
            continue
        n = str(rec.get("n", "")).strip()
        p = str(rec.get("p", "")).strip()
        a = str(rec.get("a", "H")).strip().upper()
        if not n:
            continue
        if a not in ("B", "S", "H"):
            a = "H"
        valid.append({"n": n, "p": p, "a": a})

    logger.info("LLM: %d valid record(s) extracted.", len(valid))
    return valid
