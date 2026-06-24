"""
logic_mapper.py
---------------
Local data enrichment layer — zero API calls, zero token spend.

All investor profiles live in a local Python dict.
Cross-references LLM output against known whale proxies/vehicles.
"""

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WHALE MAP — key: proxy/vehicle name fragment (lowercase)
#             val: {"whale": "Beneficial Owner", "group": "Conglomerate"}
#
# MAINTENANCE: Update 2–3x per year as new SPVs/vehicles are discovered.
# ---------------------------------------------------------------------------
WHALE_MAP: dict[str, dict] = {

    # ── Dhammika Perera ────────────────────────────────────────────────────
    "vallibel one":        {"whale": "Dhammika Perera", "group": "Vallibel Group"},
    "vallibel finance":    {"whale": "Dhammika Perera", "group": "Vallibel Group"},
    "royal ceramics":      {"whale": "Dhammika Perera", "group": "Vallibel Group"},
    "rocell":              {"whale": "Dhammika Perera", "group": "Vallibel Group"},
    "lbi finance":         {"whale": "Dhammika Perera", "group": "Vallibel Group"},
    "hayleys":             {"whale": "Dhammika Perera", "group": "Hayleys Group"},
    "dipped products":     {"whale": "Dhammika Perera", "group": "Hayleys Group"},
    "dhammika perera":     {"whale": "Dhammika Perera", "group": "Vallibel Group"},

    # ── Harry Jayawardena ──────────────────────────────────────────────────
    "melstacorp":          {"whale": "Harry Jayawardena", "group": "Melstacorp Group"},
    "distilleries":        {"whale": "Harry Jayawardena", "group": "Melstacorp Group"},
    "aitken spence":       {"whale": "Harry Jayawardena", "group": "Aitken Spence Group"},
    "browns":              {"whale": "Harry Jayawardena", "group": "Browns Group"},
    "harry jayawardena":   {"whale": "Harry Jayawardena", "group": "Melstacorp Group"},
    "milford exports":     {"whale": "Harry Jayawardena", "group": "Melstacorp Group"},

    # ── Nimal Perera ───────────────────────────────────────────────────────
    "nimal perera":        {"whale": "Nimal Perera", "group": "Ceylinco Group"},
    "nimal ananda perera": {"whale": "Nimal Perera", "group": "Ceylinco Group"},
    "ceylinco":            {"whale": "Nimal Perera", "group": "Ceylinco Group"},
    "seylan":              {"whale": "Nimal Perera", "group": "Ceylinco Group"},

    # ── Ashok Pathirage ────────────────────────────────────────────────────
    "softlogic":           {"whale": "Ashok Pathirage", "group": "Softlogic Group"},
    "ashok pathirage":     {"whale": "Ashok Pathirage", "group": "Softlogic Group"},

    # ── Ishara Nanayakkara ─────────────────────────────────────────────────
    "lolc":                {"whale": "Ishara Nanayakkara", "group": "LOLC Group"},
    "lanka orix":          {"whale": "Ishara Nanayakkara", "group": "LOLC Group"},
    "ishara":              {"whale": "Ishara Nanayakkara", "group": "LOLC Group"},

    # ── EPF / Government ───────────────────────────────────────────────────
    "employees provident": {"whale": "EPF (Govt)", "group": "Central Bank"},
    "epf":                 {"whale": "EPF (Govt)", "group": "Central Bank"},
    "bank of ceylon":      {"whale": "Govt of Sri Lanka", "group": "State Banks"},
    "peoples bank":        {"whale": "Govt of Sri Lanka", "group": "State Banks"},
    "people's bank":       {"whale": "Govt of Sri Lanka", "group": "State Banks"},

    # ── John Keells Group ──────────────────────────────────────────────────
    "john keells":         {"whale": "JKH Group", "group": "John Keells Holdings"},
    "jkh":                 {"whale": "JKH Group", "group": "John Keells Holdings"},

    # ── Cargills / CT Holdings ────────────────────────────────────────────
    "cargills":            {"whale": "CT Holdings / Page Family", "group": "Cargills Group"},
    "ct holdings":         {"whale": "CT Holdings / Page Family", "group": "Cargills Group"},

    # ── Richard Pieris ─────────────────────────────────────────────────────
    "richard pieris":      {"whale": "Richard Pieris Group", "group": "Arpico Group"},
    "arpico":              {"whale": "Richard Pieris Group", "group": "Arpico Group"},

    # ── Carson Cumberbatch ────────────────────────────────────────────────
    "bukit darah":         {"whale": "Carson Cumberbatch", "group": "Carson Group"},
    "carson cumberbatch":  {"whale": "Carson Cumberbatch", "group": "Carson Group"},

    # ── Expolanka ─────────────────────────────────────────────────────────
    "expolanka":           {"whale": "SG Holdings (Japan)", "group": "Expolanka Group"},

    # ── Nawaloka ──────────────────────────────────────────────────────────
    "nawaloka":            {"whale": "Jayantha Dharmadasa", "group": "Nawaloka Group"},

    # ── Hirdaramani ───────────────────────────────────────────────────────
    "hirdaramani":         {"whale": "Hirdaramani Group", "group": "Hirdaramani Group"},
}


def enrich(records: list[dict]) -> list[dict]:
    """
    Cross-reference each LLM record against WHALE_MAP.
    Adds 'whale' and 'group' fields where matched.

    Args:
        records: list of {"n", "p", "a"} from llm_extractor

    Returns:
        Same list with optional "whale" and "group" fields added.
    """
    whale_hits = 0
    enriched = []

    for rec in records:
        name_lower = rec.get("n", "").lower()
        match = _match(name_lower)

        if match:
            rec = {**rec, "whale": match["whale"], "group": match["group"]}
            whale_hits += 1
            logger.info("🐋 %s → %s", rec["n"], match["whale"])
        else:
            rec = {**rec, "whale": None, "group": None}

        enriched.append(rec)

    logger.info("logic_mapper: %d whale(s) in %d record(s).", whale_hits, len(enriched))
    return enriched


def has_whale(records: list[dict]) -> bool:
    return any(r.get("whale") for r in records)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------
def _match(name_lower: str) -> dict | None:
    """Partial-match search. Longest key wins to avoid false positives."""
    hits = [
        (len(k), v)
        for k, v in WHALE_MAP.items()
        if k in name_lower or name_lower in k
    ]
    if not hits:
        return None
    hits.sort(key=lambda x: x[0], reverse=True)
    return hits[0][1]
