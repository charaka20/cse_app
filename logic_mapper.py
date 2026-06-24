"""
logic_mapper.py
---------------
Cross-references extracted shareholder/director names against a curated
dictionary of known Sri Lankan market "whales" and their proxy vehicles.

Structure of WHALE_MAP:
    {
        "Entity or Proxy Name (lowercase key)": {
            "whale":    "Beneficial Owner Name",
            "group":    "Conglomerate / Group Name",
            "sector":   "Primary business sector",
            "notes":    "Optional context",
        }
    }

The enrich() function adds a "whale" field to each LLM record where a
match is found, enabling downstream alerts to flag significant movements.

MAINTENANCE NOTE:
  Sri Lankan holding structures evolve. New SPVs and investment vehicles
  are opened periodically. Update this file ~2–3x per year as new proxies
  are discovered via CSE announcements or financial news.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Whale / Proxy Dictionary
# ---------------------------------------------------------------------------
# Keys are normalised to lowercase for case-insensitive matching.
# Partial-match logic is used (substring search), so "vallibel" will match
# "Vallibel One PLC", "Vallibel Finance", etc.
# ---------------------------------------------------------------------------
WHALE_MAP: dict[str, dict] = {
    # ── Dhammika Perera ────────────────────────────────────────────────────
    "vallibel one": {
        "whale": "Dhammika Perera",
        "group": "Vallibel Group",
        "sector": "Diversified / Manufacturing",
        "notes": "Primary listed holding vehicle",
    },
    "vallibel finance": {
        "whale": "Dhammika Perera",
        "group": "Vallibel Group",
        "sector": "Finance",
    },
    "royal ceramics": {
        "whale": "Dhammika Perera",
        "group": "Vallibel Group",
        "sector": "Manufacturing / Tiles",
        "notes": "Also known as Rocell",
    },
    "lbi finance": {
        "whale": "Dhammika Perera",
        "group": "Vallibel Group",
        "sector": "Finance",
    },
    "dipped products": {
        "whale": "Dhammika Perera",
        "group": "Hayleys / Vallibel Group",
        "sector": "Rubber / Manufacturing",
    },
    "hayleys": {
        "whale": "Dhammika Perera",
        "group": "Hayleys Group",
        "sector": "Diversified",
        "notes": "Executive Chairman; primary vehicle post-2023 acquisition",
    },
    "softlogic": {
        "whale": "Ashok Pathirage",
        "group": "Softlogic Group",
        "sector": "Retail / Healthcare / Finance",
    },
    "dhammika perera": {
        "whale": "Dhammika Perera",
        "group": "Vallibel Group",
        "sector": "Diversified",
        "notes": "Direct personal holding",
    },

    # ── Harry Jayawardena ──────────────────────────────────────────────────
    "melstacorp": {
        "whale": "Harry Jayawardena",
        "group": "Melstacorp / Distilleries Group",
        "sector": "Beverages / Diversified",
        "notes": "Primary holding company",
    },
    "distilleries company": {
        "whale": "Harry Jayawardena",
        "group": "Melstacorp Group",
        "sector": "Beverages",
    },
    "aitken spence": {
        "whale": "Harry Jayawardena",
        "group": "Aitken Spence Group",
        "sector": "Diversified / Tourism / Logistics",
    },
    "browns": {
        "whale": "Harry Jayawardena",
        "group": "Browns Group",
        "sector": "Healthcare / Power",
    },
    "harry jayawardena": {
        "whale": "Harry Jayawardena",
        "group": "Melstacorp Group",
        "sector": "Diversified",
        "notes": "Direct personal holding",
    },

    # ── Employees' Provident Fund (EPF) ────────────────────────────────────
    "employees provident fund": {
        "whale": "EPF / Government",
        "group": "Central Bank of Sri Lanka",
        "sector": "Institutional",
        "notes": "Largest single institutional investor on CSE",
    },
    "epf": {
        "whale": "EPF / Government",
        "group": "Central Bank of Sri Lanka",
        "sector": "Institutional",
    },

    # ── John Keells Holdings ───────────────────────────────────────────────
    "john keells": {
        "whale": "John Keells Group",
        "group": "John Keells Holdings PLC",
        "sector": "Diversified / Blue-chip",
        "notes": "Sri Lanka's largest listed conglomerate",
    },
    "jkh": {
        "whale": "John Keells Group",
        "group": "John Keells Holdings PLC",
        "sector": "Diversified",
    },

    # ── Cargills Ceylon ────────────────────────────────────────────────────
    "cargills": {
        "whale": "CT Holdings / Page family",
        "group": "Cargills Group",
        "sector": "Retail / Food",
    },
    "ct holdings": {
        "whale": "CT Holdings / Page family",
        "group": "Cargills Group",
        "sector": "Holding",
    },

    # ── Perpetual Treasuries / Arjun Aloysius ──────────────────────────────
    "perpetual treasuries": {
        "whale": "Arjun Aloysius",
        "group": "Perpetual Group",
        "sector": "Primary Dealer / Finance",
        "notes": "Bond-market linked entity; monitor for treasury/finance stocks",
    },

    # ── Lanka Orix Leasing / LOLC ──────────────────────────────────────────
    "lolc": {
        "whale": "Ishara Nanayakkara",
        "group": "LOLC Group",
        "sector": "Finance / Microfinance",
    },
    "lanka orix": {
        "whale": "Ishara Nanayakkara",
        "group": "LOLC Group",
        "sector": "Finance",
    },

    # ── Sanken Construction / Nanda Godahewa ───────────────────────────────
    "sanken": {
        "whale": "Nanda Godahewa",
        "group": "Sanken Group",
        "sector": "Construction",
    },

    # ── Commercial Bank / DFCC ────────────────────────────────────────────
    "bank of ceylon": {
        "whale": "Government of Sri Lanka",
        "group": "State Banks",
        "sector": "Banking",
    },
    "peoples bank": {
        "whale": "Government of Sri Lanka",
        "group": "State Banks",
        "sector": "Banking",
    },

    # ── Expolanka ──────────────────────────────────────────────────────────
    "expolanka": {
        "whale": "SG Holdings (Japan)",
        "group": "Expolanka Group",
        "sector": "Logistics / Freight",
        "notes": "Japanese strategic investor SG Holdings holds majority stake",
    },

    # ── Sampath Bank (Esufally family interest) ────────────────────────────
    "hirdaramani": {
        "whale": "Hirdaramani Group",
        "group": "Hirdaramani Group",
        "sector": "Apparel / Hospitality",
    },

    # ── Capital Alliance / CAL ─────────────────────────────────────────────
    "capital alliance": {
        "whale": "Capital Alliance Group",
        "group": "CAL",
        "sector": "Investment / Brokerage",
    },

    # ── Richard Pieris ─────────────────────────────────────────────────────
    "richard pieris": {
        "whale": "Richard Pieris Group",
        "group": "Arpico Group",
        "sector": "Rubber / Retail / Finance",
    },

    # ── Nawaloka ───────────────────────────────────────────────────────────
    "nawaloka": {
        "whale": "Jayantha Dharmadasa",
        "group": "Nawaloka Group",
        "sector": "Healthcare / Construction",
    },

    # ── Ashok Pathirage (Softlogic) ────────────────────────────────────────
    "ashok pathirage": {
        "whale": "Ashok Pathirage",
        "group": "Softlogic Group",
        "sector": "Retail / Healthcare",
        "notes": "Direct personal holding",
    },

    # ── Seylan Bank (Ceylinco Group) ───────────────────────────────────────
    "ceylinco": {
        "whale": "Lalith Kotelawala",
        "group": "Ceylinco Group",
        "sector": "Finance / Insurance",
    },

    # ── Bukit Darah (Carson Cumberbatch) ──────────────────────────────────
    "bukit darah": {
        "whale": "Carson Cumberbatch / Selvendran family",
        "group": "Carson Cumberbatch Group",
        "sector": "Diversified / Plantations / Beverages",
    },
    "carson cumberbatch": {
        "whale": "Carson Cumberbatch / Selvendran family",
        "group": "Carson Cumberbatch Group",
        "sector": "Diversified",
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def enrich(records: list[dict]) -> list[dict]:
    """
    Cross-reference each record's name against WHALE_MAP.

    Args:
        records: List of {"n": str, "p": str, "a": str} from llm_extractor.

    Returns:
        Same list, each dict optionally augmented with:
            "whale":  Beneficial owner name (str) or None
            "group":  Conglomerate group (str) or None
            "sector": Business sector (str) or None
    """
    enriched: list[dict] = []
    for record in records:
        name_lower = record.get("n", "").lower()
        match = _find_whale(name_lower)
        if match:
            record = {**record, **match}
            logger.info(
                "🐋 Whale detected: '%s' → %s (%s)",
                record["n"],
                match["whale"],
                match.get("group", ""),
            )
        else:
            record = {**record, "whale": None, "group": None, "sector": None}

        enriched.append(record)

    whale_count = sum(1 for r in enriched if r.get("whale"))
    logger.info(
        "logic_mapper: %d whale match(es) found in %d record(s).",
        whale_count,
        len(enriched),
    )
    return enriched


def is_whale_movement(records: list[dict]) -> bool:
    """Return True if any record in the enriched list is a whale match."""
    return any(r.get("whale") for r in records)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _find_whale(name_lower: str) -> Optional[dict]:
    """
    Partial-match search: return the whale info dict if any WHALE_MAP key
    is a substring of the entity name (or vice versa).

    Prioritises longer keys to avoid false positives (e.g. "browns" vs
    "browns investments").
    """
    matches = []
    for key, info in WHALE_MAP.items():
        if key in name_lower or name_lower in key:
            matches.append((len(key), info))

    if not matches:
        return None

    # Return the longest (most specific) match
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]
