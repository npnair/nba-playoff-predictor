"""How much better or worse each player has played in past playoffs than in the regular season.

For every postseason in the window, a player's shift is his playoff Box Plus/Minus minus his
regular season BPM that same year. Playoff opponents are tougher, so most players dip; each season's
shifts are centered on that season's minutes-weighted average dip, which leaves only the part that is
specific to the player (a "riser" plays above the usual playoff level, a "faller" below it).

Shifts are combined across seasons weighted by playoff minutes, with older postseasons counted less,
then shrunk toward zero because playoff samples are small and noisy: a player with PRIOR_MINUTES of
weighted playoff time keeps half of his raw shift. The result (playoff_adj) is added to the player's
BPM in the bracket's starting-five comparison. Players with no playoff history get 0.
"""

from __future__ import annotations

HISTORY_SEASONS = 5      # postseasons looked at, most recent first
RECENCY = 0.8            # each older postseason counts this much less
PRIOR_MINUTES = 1500     # shrinkage: weighted playoff minutes needed to keep half the raw shift
MAX_ADJ = 2.5            # cap on the adjustment, in BPM points
FIELDS = ["bref_id", "player", "seasons", "po_games", "po_minutes", "raw_shift", "playoff_adj"]


def season_shifts(regular: list[dict], playoffs: list[dict]) -> list[dict]:
    """Each playoff player's BPM change that season, centered on the season's average change."""
    reg = {p["bref_id"]: p for p in regular if p.get("bpm") is not None}
    rows = []
    for p in playoffs:
        r = reg.get(p["bref_id"])
        if r is None or p.get("bpm") is None or (p.get("mp") or 0) <= 0:
            continue
        rows.append({"bref_id": p["bref_id"], "player": p["player"], "mp": p["mp"], "g": p.get("g") or 0,
                     "diff": p["bpm"] - r["bpm"]})
    total = sum(x["mp"] for x in rows)
    mean = sum(x["mp"] * x["diff"] for x in rows) / total if total else 0.0
    for x in rows:
        x["shift"] = x["diff"] - mean
    return rows


def build_history(seasons: dict[int, tuple[list[dict], list[dict]]]) -> list[dict]:
    """seasons: {year: (regular season rows, playoff rows)} from bref.parse_advanced."""
    if not seasons:
        return []
    latest = max(seasons)
    acc: dict[str, dict] = {}
    for year in sorted(seasons):
        regular, playoffs = seasons[year]
        decay = RECENCY ** (latest - year)
        for x in season_shifts(regular, playoffs):
            a = acc.setdefault(x["bref_id"], {"bref_id": x["bref_id"], "player": x["player"], "seasons": 0,
                                              "po_games": 0, "po_minutes": 0, "w": 0.0, "ws": 0.0})
            a["player"] = x["player"]
            a["seasons"] += 1
            a["po_games"] += int(x["g"])
            a["po_minutes"] += int(x["mp"])
            a["w"] += x["mp"] * decay
            a["ws"] += x["mp"] * decay * x["shift"]
    rows = []
    for a in acc.values():
        raw = a["ws"] / a["w"] if a["w"] else 0.0
        adj = max(-MAX_ADJ, min(MAX_ADJ, raw * a["w"] / (a["w"] + PRIOR_MINUTES)))
        rows.append({"bref_id": a["bref_id"], "player": a["player"], "seasons": a["seasons"],
                     "po_games": a["po_games"], "po_minutes": a["po_minutes"],
                     "raw_shift": round(raw, 2), "playoff_adj": round(adj, 2)})
    return sorted(rows, key=lambda r: -r["playoff_adj"])
