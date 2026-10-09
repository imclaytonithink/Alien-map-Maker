"""Zone graph + scoring: put zone instances on slots.

The assignment is scored on adjacency satisfaction (archetype preferences and
the global adjacency table), the access gradient, reachability from the
entrance under the gating rules, and level/position constraints; then improved
by local search ("scoring and repair"). Deterministic by seed.
"""
from __future__ import annotations

import json
from collections import deque

from . import DATA_DIR
from .layouts import contacts
from .model import TIER, Layout, ZoneInst

_ADJ = None


def adjacency_rules():
    global _ADJ
    if _ADJ is None:
        with open(DATA_DIR / "adjacency.json", encoding="utf-8") as fh:
            d = json.load(fh)
        _ADJ = ({frozenset(p) for p in d["forbid"]}, {frozenset(p) for p in d["prefer"]})
    return _ADJ


def door_allowed(a: ZoneInst, b: ZoneInst) -> bool:
    """Gating: may a door connect these two zones?

    Tier 3+ (secure/containment) can only be entered from lower tiers through a
    checkpoint zone, and no one crosses three tiers without one either.
    """
    lo, hi = sorted((a.tier, b.tier))
    cp = a.checkpoint or b.checkpoint
    if cp:
        return True
    if hi >= 3 and lo <= 2:
        return False
    if hi - lo >= 3:
        return False
    return True


def building_levels(lay: Layout):
    lv = [s.level for s in lay.slots if s.role not in ("headframe",)]
    return (min(lv), max(lv)) if lv else (0, 0)


def constraint_ok(z: ZoneInst, s, lo, hi, relax=False) -> bool:
    if z.level == "top" and s.level != lo and not relax:
        return False
    if z.level == "bottom" and s.level != hi and not relax:
        return False
    if isinstance(z.level, int) and s.level != z.level and not relax:
        return False
    if z.position != "any" and not relax:
        if z.position == "core" and s.pos != "core":
            return False
        if z.position == "edge" and s.pos not in ("edge", "corner"):
            return False
        if z.position == "corner" and s.pos != "corner":
            return False
    if z.height > 1 and s.level - (z.height - 1) < lo:    # needs levels above for the void
        return False
    return True


def adjacency_pairs(lay: Layout):
    """(slot a, slot b, weight): contacts, links and vertical neighbours.

    Plain stacked neighbours only count for scoring (a room above another is
    "near" it); real connections for reachability are contacts, explicit links
    and aligned vertical cores (see :func:`graph_edges`).
    """
    pairs = {}
    for a, b in contacts(lay):
        pairs[(min(a, b), max(a, b))] = 1.0
    for l in lay.links:
        pairs[(min(l.a, l.b), max(l.a, l.b))] = 1.0
    cells = {}
    for s in lay.slots:
        cells.setdefault((s.x, s.y), []).append(s)
    for k, ss in cells.items():
        ss = sorted(ss, key=lambda s: s.level)
        for a, b in zip(ss, ss[1:]):
            if b.level == a.level + 1:
                pairs[(min(a.idx, b.idx), max(a.idx, b.idx))] = max(pairs.get((a.idx, b.idx), 0), 0.5)
    return [(a, b, w) for (a, b), w in pairs.items()]


def graph_edges(lay: Layout, pairs=None):
    """Real connections used for reachability (not mere stacking)."""
    es = set()
    for a, b in contacts(lay):
        es.add((min(a, b), max(a, b)))
    for l in lay.links:
        es.add((min(l.a, l.b), max(l.a, l.b)))
    cores = {}
    for s in lay.slots:
        if s.role == "vertical":
            cores.setdefault((s.x, s.y), []).append(s)
    for ss in cores.values():
        ss.sort(key=lambda q: q.level)
        for x, y in zip(ss, ss[1:]):
            es.add((min(x.idx, y.idx), max(x.idx, y.idx)))
    return es


def reachable(lay: Layout, edges, entrance_idx: int):
    zones = {s.idx: s.zone for s in lay.slots}
    adj = {}
    for a, b in edges:
        za, zb = zones.get(a), zones.get(b)
        if za is None or zb is None:
            continue
        if not door_allowed(za, zb):
            continue
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen = {entrance_idx}
    dq = deque([entrance_idx])
    while dq:
        n = dq.popleft()
        for m in adj.get(n, ()):
            if m not in seen:
                seen.add(m)
                dq.append(m)
    return seen


def entrance_slot(lay: Layout):
    for s in lay.slots:
        if s.zone and s.zone.entrance:
            return s.idx
    for s in lay.slots:
        if s.zone and s.zone.tier == 0:
            return s.idx
    return lay.slots[0].idx if lay.slots else 0


def score(lay: Layout, pairs, edges) -> float:
    forbid_t, prefer_t = adjacency_rules()
    total = 0.0
    for a, b, w in pairs:
        za, zb = lay.slots[a].zone, lay.slots[b].zone
        if za is None or zb is None:
            continue
        s = 0.0
        if zb.base in za.prefer or za.base in zb.prefer:
            s += 3
        if zb.base in za.forbid or za.base in zb.forbid:
            s -= 6
        for ta in za.tags:
            for tb in zb.tags:
                p = frozenset((ta, tb))
                if len(p) == 2:
                    if p in forbid_t:
                        s -= 1.5
                    elif p in prefer_t:
                        s += 0.5
        d = abs(za.tier - zb.tier)
        if d >= 2 and not (za.checkpoint or zb.checkpoint):
            s -= 1.0 * d
        elif d == 0:
            s += 0.2
        total += s * w
    ent = entrance_slot(lay)
    seen = reachable(lay, edges, ent)
    unreachable = sum(1 for s in lay.slots if s.zone and s.idx not in seen)
    total -= 8.0 * unreachable
    return total


def assign_zones(lay: Layout, zones: list, rng, restarts=3, iters=None, grouping=0.6):
    """Assign zone instances to free slots. Returns a report dict."""
    free = [s for s in lay.slots if s.zone is None and not s.reserved and s.role not in ("vertical",)]
    lo, hi = building_levels(lay)
    report = {"relaxed": [], "unplaced": []}
    pairs = adjacency_pairs(lay)
    edges = graph_edges(lay, pairs)

    def initial():
        for s in free:
            s.zone = None
        slots = list(free)
        rng.shuffle(slots)
        order = sorted(zones, key=lambda z: -((z.level != "any") + (z.position != "any") + (z.height > 1)))
        for z in order:
            cands = [s for s in slots if s.zone is None and constraint_ok(z, s, lo, hi)]
            if not cands:
                cands = [s for s in slots if s.zone is None and constraint_ok(z, s, lo, hi, relax=True)]
                if cands:
                    report["relaxed"].append(z.id)
            if not cands:
                report["unplaced"].append(z.id)
                continue
            cands[0].zone = z

    from . import affinity
    pos = [(s.cx, s.cy, s.level) for s in lay.slots]

    def tagsets():
        return [set(s.zone.tags) if s.zone is not None else set() for s in lay.slots]

    best = None
    n_iter = iters if iters is not None else (300 + 25 * len(free))
    for _ in range(restarts):
        report["relaxed"], report["unplaced"] = [], []
        initial()
        base = score(lay, pairs, edges)
        ts = tagsets()
        aff = affinity.total(pos, ts, grouping) if grouping > 0 else 0.0
        movable = list(free)
        for _i in range(n_iter):
            if len(movable) < 2:
                break
            a, b = rng.sample(movable, 2)
            za, zb = a.zone, b.zone
            if za is None and zb is None:
                continue
            if za is not None and not constraint_ok(za, b, lo, hi, relax=za.id in report["relaxed"]):
                continue
            if zb is not None and not constraint_ok(zb, a, lo, hi, relax=zb.id in report["relaxed"]):
                continue
            aff_before = (affinity.local(a.idx, pos, ts, grouping) + affinity.local(b.idx, pos, ts, grouping)) \
                if grouping > 0 else 0.0
            a.zone, b.zone = zb, za
            ts[a.idx], ts[b.idx] = ts[b.idx], ts[a.idx]
            aff_after = (affinity.local(a.idx, pos, ts, grouping) + affinity.local(b.idx, pos, ts, grouping)) \
                if grouping > 0 else 0.0
            new_base = score(lay, pairs, edges)
            d_aff = aff_after - aff_before
            if new_base + d_aff >= base:
                base, aff = new_base, aff + d_aff
            else:
                a.zone, b.zone = za, zb
                ts[a.idx], ts[b.idx] = ts[b.idx], ts[a.idx]
        cur = base + aff
        if best is None or cur > best[0]:
            best = (cur, {s.idx: s.zone for s in free}, list(report["relaxed"]), list(report["unplaced"]))
    cur, zmap, rel, unp = best
    for s in free:
        s.zone = zmap[s.idx]
    report["relaxed"], report["unplaced"], report["score"] = rel, unp, round(cur, 2)
    for s in lay.slots:
        if s.zone is not None and s.zone.filler_only:
            s.kind = "filler"
    return report


def reroll_zone(lay: Layout, zone_id: str, rng, iters=200):
    """Swap one zone to the best other slot (the 'reroll this zone' action)."""
    pairs = adjacency_pairs(lay)
    edges = graph_edges(lay, pairs)
    lo, hi = building_levels(lay)
    src = next((s for s in lay.slots if s.zone and s.zone.id == zone_id), None)
    if src is None:
        return False
    best_delta, best_s = None, None
    base = score(lay, pairs, edges)
    others = [s for s in lay.slots if s is not src and s.zone is not None and s.role not in ("vertical",)
              and s.placed is None or (s is not src and s.zone is not None and s.role not in ("vertical",))]
    rng.shuffle(others)
    for o in others[:iters]:
        if not constraint_ok(src.zone, o, lo, hi) or not constraint_ok(o.zone, src, lo, hi):
            continue
        src.zone, o.zone = o.zone, src.zone
        sc = score(lay, pairs, edges)
        src.zone, o.zone = o.zone, src.zone
        if best_delta is None or sc > best_delta:
            best_delta, best_s = sc, o
    if best_s is None or best_delta < base - 3:
        return False
    src.zone, best_s.zone = best_s.zone, src.zone
    return True
