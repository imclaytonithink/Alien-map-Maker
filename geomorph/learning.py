"""Taste: the generator learns which tiles you like from your ratings and from the maps you keep.

* Rate a map with the thumbs buttons: every tile in it gets a vote (up or down).
* A map you place on the canvas or export counts as a gentle vote of its own, up when it scored well
  (85+) and down when it scored badly (under 65).
* A tile's net taste is (up - down) / (up + down + 3), so a single vote barely matters and many agreeing
  votes matter a lot. It adds a bonus (or a penalty) to that tile's score when the generator picks tiles,
  and it ranks "best of N" candidates. Nothing else changes: the rules for wings, noses, access and
  the rest are never bent by taste.

The preferences live in one small JSON file (path set with :func:`use`; None means no learning), so they can be
inspected, backed up or deleted. :func:`reset` clears them.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

PATH: Path | None = None
STRENGTH = 1.2          # score added to a tile at net taste +1 (a tile's tag score counts 3 per point)
SOFT_WEIGHT = 0.4       # a kept map counts this much of a full vote
GOOD, BAD = 85, 65      # quality scores that make a kept map a soft up / down vote
HISTORY = 200

_cache: dict | None = None


def use(path) -> None:
    """Point learning at a preferences file (or None to switch it off)."""
    global PATH, _cache
    new = Path(path) if path else None
    if new != PATH:
        PATH, _cache = new, None


def _empty() -> dict:
    return {"version": 1, "tiles": {}, "history": []}


def _load() -> dict:
    global _cache
    if _cache is None:
        data = _empty()
        if PATH is not None:
            try:
                data = json.loads(PATH.read_text(encoding="utf-8"))
                data.setdefault("tiles", {})
                data.setdefault("history", [])
            except (OSError, ValueError):
                data = _empty()
        _cache = data
    return _cache


def _save() -> None:
    if PATH is None or _cache is None:
        return
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(_cache, indent=1), encoding="utf-8")
    except OSError:
        pass


def net_by_tile() -> dict:
    """{tile id: taste in (-1, 1)} for tiles with any votes (empty when learning is off)."""
    if PATH is None:
        return {}
    out = {}
    for tid, v in _load()["tiles"].items():
        up, down = v.get("up", 0.0), v.get("down", 0.0)
        net = (up - down) / (up + down + 3.0)
        if abs(net) > 1e-9:
            out[tid] = net
    return out


def taste(res) -> float:
    """Average taste of the distinct tiles in a result (0 = neutral). Used to rank candidates."""
    net = net_by_tile()
    ids = {p.tile.id for g in res.grids for p in g.placed}
    return sum(net.get(t, 0.0) for t in ids) / len(ids) if ids else 0.0


def record(res, rating: int | None = None, soft: bool = False) -> bool:
    """Vote with a result: ``rating`` +1 / -1 for a full vote, or ``soft=True`` to vote by its quality score.
    Returns True when a vote was recorded."""
    if PATH is None:
        return False
    if rating is None:
        score = (getattr(res, "quality", None) or {}).get("score")
        if not soft or score is None or BAD <= score < GOOD:
            return False
        rating, weight = (1 if score >= GOOD else -1), SOFT_WEIGHT
    else:
        weight = 1.0
    data = _load()
    for tid in {p.tile.id for g in res.grids for p in g.placed}:
        v = data["tiles"].setdefault(tid, {"up": 0.0, "down": 0.0})
        v["up" if rating > 0 else "down"] = round(v["up" if rating > 0 else "down"] + weight, 3)
    meta = res.meta or {}
    data["history"].append({"t": int(time.time()), "kind": res.kind, "what": meta.get("archetype") or meta.get("ship_type", ""),
                            "score": (getattr(res, "quality", None) or {}).get("score"), "vote": rating, "weight": weight})
    del data["history"][:-HISTORY]
    _save()
    return True


def summary() -> dict:
    data = _load() if PATH is not None else _empty()
    votes = [h for h in data["history"]]
    net = net_by_tile()
    ranked = sorted(net.items(), key=lambda kv: kv[1])
    return {"maps": len(votes), "up": sum(1 for h in votes if h["vote"] > 0), "down": sum(1 for h in votes if h["vote"] < 0),
            "tiles": len(net), "favourites": [t for t, n in ranked[::-1][:5] if n > 0],
            "avoided": [t for t, n in ranked[:5] if n < 0]}


def reset() -> None:
    global _cache
    _cache = _empty()
    _save()
