"""Plain-language quality check of a generated map: is it a good seed or a bad one?

Counts rooms nobody can reach, through-spaces that lead nowhere, how much of the
furnishable floor got furniture and how many outdoor features were placed, then
folds them into one 0-100 score. Pure data in, pure data out (no Qt), so the
generator dialog, the CLI, the tests and any future "pick the best of N" can all
use it.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import floors, validate

DATA = Path(__file__).resolve().parent / "data"

# A room with a single way in is normal for these. A *through-space* with a
# single way in is a stub that leads nowhere.
THROUGH_TAGS = {"concourse", "multipurpose", "vertical", "interstitial", "docking"}
THROUGH_BASES = ("core", "hub", "corridor", "street", "plaza", "walkway", "road", "junction", "spine", "atrium")
NOT_ROOMS = ("wing", "corner")      # hull pieces, not rooms: they have no doors of their own

PENALTY = {"unreachable": 20, "dead_end": 4, "issue": 5, "gap": 3}
CAP = {"unreachable": 60, "dead_end": 20, "issue": 20, "gap": 15}


@lru_cache(maxsize=1)
def _floor_table() -> dict:
    try:
        return json.loads((DATA / "tile_floor.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _furnishable(tile_id: str, chars=(".",)) -> bool:
    """True when the tile has a free floor patch big enough for the decorator to use."""
    code = _floor_table().get(tile_id)
    if not code:
        return False
    return bool(floors.free_rects(floors.decode(code), min_side=4, limit=1, chars=chars))


def _is_through(zone, tile) -> bool:
    base = (getattr(zone, "base", "") or "").lower()
    if any(b in base for b in THROUGH_BASES):
        return True
    tags = set(getattr(zone, "tags", None) or [])
    if not tags and tile is not None:
        tags = set(tile.tags)
    return bool(tags & THROUGH_TAGS)


def assess(res) -> dict:
    """Return the quality report for ``res`` (also fine to call again after a re-roll)."""
    adj = validate.zone_graph(res)
    zones = res.zones
    tile_of = {p.zone: p.tile for g in res.grids for p in g.placed if p.zone}
    rooms = [z for z in zones if z in tile_of and tile_of[z].type not in NOT_ROOMS]

    # --- reachability: from the entrance on a site, from the biggest part on a ship ---
    if res.kind == "site":
        start = validate.entrance_zone_id(res)
        reach = validate._bfs(adj, start) if start in adj else set()
    else:
        reach, best = set(), set()
        for z in rooms:
            if z in reach:
                continue
            comp = validate._bfs(adj, z)
            reach |= comp
            if len(comp) > len(best):
                best = comp
        reach = best
    unreachable = sorted(z for z in rooms if z not in reach)

    # --- dead ends: through-spaces with one way in and out ---
    start = validate.entrance_zone_id(res) if res.kind == "site" else None
    dead = sorted(z for z in rooms
                  if z not in unreachable and z != start and len(adj.get(z, ())) == 1
                  and _is_through(zones[z], tile_of.get(z)))

    # --- furnishing ---
    decor = [d for d in (getattr(res, "decor", None) or []) if d.get("kind") == "item"]
    decor_on = bool((res.options or {}).get("decor", {}) and (res.options["decor"].get("enabled")))
    furnished_n = furnishable = 0
    chars = (".",) if res.meta.get("environment") in ("vacuum", "hostile", "orbital") and res.kind == "site" else (".", "r")
    if decor_on:
        by_room = {}
        for d in decor:
            by_room[d.get("zone")] = by_room.get(d.get("zone"), 0) + 1
        for z in rooms:
            t = tile_of.get(z)
            if t is not None and t.type == "standard" and _furnishable(t.id, chars):
                furnishable += 1
                furnished_n += 1 if by_room.get(z) else 0
    furnished_pct = round(100.0 * furnished_n / furnishable) if furnishable else None
    outdoor = sum(1 for d in (getattr(res, "decor", None) or []) if d.get("kind") == "exterior")

    # --- variety: the same tile over and over looks lazy ---
    placed = [p.tile.id for g in res.grids for p in g.placed if p.tile.type not in NOT_ROOMS]
    variety_pct = round(100.0 * len(set(placed)) / len(placed)) if placed else 100

    # --- other problems the pipeline already knows about ---
    issues = [i for i in res.issues if not i.startswith("not connected to the entrance")]
    gaps = list(res.gaps or {})

    score = 100
    score -= min(CAP["unreachable"], PENALTY["unreachable"] * len(unreachable))
    score -= min(CAP["dead_end"], PENALTY["dead_end"] * len(dead))
    score -= min(CAP["issue"], PENALTY["issue"] * len(issues))
    score -= min(CAP["gap"], PENALTY["gap"] * len(gaps))
    if furnished_pct is not None and furnished_pct < 60:
        score -= round((60 - furnished_pct) * 0.3)
    if variety_pct < 70:
        score -= round((70 - variety_pct) * 0.25)
    score = max(0, min(100, score))
    label = "Good seed" if score >= 85 else "Fine, check the notes" if score >= 65 else "Try another seed"

    def nice(z):
        return zones[z].name if z in zones and getattr(zones[z], "name", None) else z

    return {
        "score": score, "label": label, "rooms": len(rooms),
        "unreachable": [nice(z) for z in unreachable], "unreachable_ids": unreachable,
        "dead_ends": [nice(z) for z in dead], "dead_end_ids": dead,
        "decor_on": decor_on, "furnished_pct": furnished_pct, "furnished": furnished_n, "furnishable": furnishable,
        "outdoor": outdoor, "variety_pct": variety_pct, "issues": issues, "gaps": gaps,
    }


def summary(q: dict) -> str:
    """One plain sentence, e.g. 'Good seed (92): 2 dead ends, 0 unreachable rooms, 78% furnished.'"""
    parts = [f"{len(q['dead_ends'])} dead end{'s' if len(q['dead_ends']) != 1 else ''}",
             f"{len(q['unreachable'])} unreachable room{'s' if len(q['unreachable']) != 1 else ''}"]
    if q["decor_on"]:
        parts.append("no furnishable rooms" if q["furnished_pct"] is None else f"{q['furnished_pct']}% furnished")
    if q["outdoor"]:
        parts.append(f"{q['outdoor']} outdoor feature{'s' if q['outdoor'] != 1 else ''}")
    return f"{q['label']} ({q['score']}): " + ", ".join(parts) + "."


def compare(before: dict | None, after: dict | None) -> str:
    """What changed between two reports, in a plain line: 'Score 88 -> 93, dead ends 2 -> 1, furnished 61% -> 74%'."""
    if not before or not after:
        return ""
    parts = []
    if before["score"] != after["score"]:
        parts.append(f"Score {before['score']} \u2192 {after['score']}")
    for label, a, b in (("dead ends", len(before["dead_ends"]), len(after["dead_ends"])),
                        ("unreachable rooms", len(before["unreachable"]), len(after["unreachable"]))):
        if a != b:
            parts.append(f"{label} {a} \u2192 {b}")
    if before["furnished_pct"] != after["furnished_pct"] and None not in (before["furnished_pct"], after["furnished_pct"]):
        parts.append(f"furnished {before['furnished_pct']}% \u2192 {after['furnished_pct']}%")
    if before["variety_pct"] != after["variety_pct"]:
        parts.append(f"variety {before['variety_pct']}% \u2192 {after['variety_pct']}%")
    return ", ".join(parts) if parts else f"Score unchanged at {after['score']}"
