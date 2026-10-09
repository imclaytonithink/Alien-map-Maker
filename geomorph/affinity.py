"""Smart grouping: rooms that belong together are generated close to each other.

``data/affinity.json`` lists groups of function tags (medical with lab and morgue, cargo with loading docks, dining
with the galley, quarters with freshers...) and pairs that should be far apart. A layout is scored by summing, over
every pair of rooms, ``weight * proximity(distance)``: distance is measured between room centres in grid squares
(a level change counts as 25 squares), so the pull works across the whole building rather than only for touching
neighbours. ``grouping`` (0..1) scales how hard the generator tries.
"""
from __future__ import annotations

import json
import math

from . import DATA_DIR

_DATA = None
_PAIRS = None
SCALE = 30.0            # squares over which attraction fades (about 1.5 tiles)
LEVEL_COST = 25.0


def data():
    global _DATA
    if _DATA is None:
        _DATA = json.loads((DATA_DIR / "affinity.json").read_text(encoding="utf-8"))
    return _DATA


def reload():
    global _DATA, _PAIRS
    _DATA = _PAIRS = None


def pair_table() -> dict:
    """{(tag_a, tag_b): weight}: positive attracts, negative repels (symmetric)."""
    global _PAIRS
    if _PAIRS is not None:
        return _PAIRS
    d = data()
    t: dict = {}

    def put(a, b, w):
        for k in ((a, b), (b, a)):
            if abs(w) > abs(t.get(k, 0.0)) or (w > 0 > t.get(k, 0.0)):
                t[k] = w
    for g in d["groups"]:
        tags = g["tags"]
        for i, a in enumerate(tags):
            for b in tags[i + 1:]:
                if g["weight"] > t.get((a, b), 0.0):
                    put(a, b, g["weight"])
    for r in d.get("repel", []):
        a, b = r["tags"]
        t[(a, b)] = t[(b, a)] = -abs(r["weight"])
    return _PAIRS if _PAIRS is not None else _store(t)


def _store(t):
    global _PAIRS
    _PAIRS = t
    return t


def tags_weight(ta, tb) -> float:
    """Attraction between two rooms given their tags (the strongest pull, less the strongest push)."""
    t = pair_table()
    same = data().get("same_function", 0.9)
    pull, push = 0.0, 0.0
    for a in ta:
        for b in tb:
            if a == b:
                pull = max(pull, same)
                continue
            w = t.get((a, b), 0.0)
            if w > 0:
                pull = max(pull, w)
            elif w < 0:
                push = max(push, -w)
    return pull - 0.9 * push


def proximity(d: float) -> float:
    return math.exp(-d / SCALE)


def dist(a, b) -> float:
    """a, b = (cx, cy, level)"""
    return math.hypot(a[0] - b[0], a[1] - b[1]) + LEVEL_COST * abs(a[2] - b[2])


def local(i, pos, tagsets, grouping=1.0) -> float:
    """Sum of pair scores between room ``i`` and every other room."""
    ti = tagsets[i]
    if not ti or grouping <= 0:
        return 0.0
    total = 0.0
    pi = pos[i]
    for j, tj in enumerate(tagsets):
        if j == i or not tj:
            continue
        w = tags_weight(ti, tj)
        if w:
            total += w * proximity(dist(pi, pos[j]))
    return grouping * total


def total(pos, tagsets, grouping=1.0) -> float:
    s = 0.0
    for i in range(len(tagsets)):
        s += local(i, pos, tagsets, grouping)
    return s / 2


def cluster_spread(pos, tagsets, tag) -> float:
    """Mean distance between rooms having ``tag`` (smaller = tighter group); used by tests."""
    idx = [i for i, t in enumerate(tagsets) if tag in t]
    if len(idx) < 2:
        return 0.0
    ds = [dist(pos[a], pos[b]) for k, a in enumerate(idx) for b in idx[k + 1:]]
    return sum(ds) / len(ds)
