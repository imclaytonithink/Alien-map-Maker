"""Ship generator: the four layout modes on a linear (bow-to-stern) hull.

Layout (bow = up): a block of ``C x R`` standard tiles, wrapped in a ring of
Edge tiles with Corner tiles at the four corners, an End tile (bridge) on the
nose, an End tile (engineering) on the tail and optional mirrored aerofin End
tiles port and starboard. Tonnage uses the guide's convention that two squares
are one displacement ton.
"""
from __future__ import annotations

from . import filler as F
from .placement import LevelGrid, OPPOSITE, orientations, rot_side, side_of_dir
from .tiling import TilePicker

SHIP_TYPES = {
    "Merchant": {"extra": {"cargo": 3, "vehicle_bay": 1}, "palette": {"cargo": 4, "staterooms": 2, "workshop": 1, "recreation": 1, "vehicle_bay": 1, "galley": 1}},
    "Luxury Liner": {"extra": {"passenger": 3, "hydroponics": 1, "medical": 1, "escape": 1, "vehicle_bay": 1}, "palette": {"passenger": 4, "recreation": 3, "staterooms": 3, "galley": 1, "hydroponics": 1, "medical": 1, "concourse": 1}},
    "Scout": {"extra": {"sensors": 2, "hangar": 1}, "palette": {"sensors": 2, "lab": 1, "staterooms": 2, "hangar": 1, "workshop": 1, "cargo": 1}},
    "Research": {"extra": {"lab": 3, "sensors": 1, "office": 1}, "palette": {"lab": 4, "office": 2, "sensors": 2, "staterooms": 2, "medical": 1, "recreation": 1}},
    "Military": {"extra": {"weapons": 2, "hangar": 1, "sensors": 1, "barracks": 2, "armory": 1}, "palette": {"weapons": 3, "barracks": 3, "hangar": 2, "sensors": 1, "armory": 1, "staterooms": 1, "workshop": 1}},
    "Colony / Generational": {"extra": {"lowberth": 3, "cargo": 2, "vehicle_bay": 1, "hydroponics": 1}, "palette": {"lowberth": 4, "cargo": 3, "hydroponics": 2, "staterooms": 2, "vehicle_bay": 1, "workshop": 1, "medical": 1}},
    "Medical / Rescue": {"extra": {"medical": 3, "lab": 1, "passenger": 1}, "palette": {"medical": 4, "lab": 2, "passenger": 2, "staterooms": 2, "hangar": 1, "escape": 1}},
}
BASE_REQUIRED = {"bridge": 1, "engineering": 1, "fuel": 1, "staterooms": 1}
MODES = ("planned", "random", "selective", "movie")
# where along the hull (0 bow .. 1 stern) each function likes to sit
LONG_PREF = {"bridge": 0.0, "sensors": 0.1, "weapons": 0.3, "office": 0.25, "staterooms": 0.4, "passenger": 0.4,
             "medical": 0.4, "lab": 0.45, "recreation": 0.4, "concourse": 0.4, "hydroponics": 0.5, "galley": 0.45,
             "barracks": 0.4, "armory": 0.35, "escape": 0.5, "lowberth": 0.5, "cargo": 0.65, "hangar": 0.7,
             "vehicle_bay": 0.7, "fuel": 0.8, "workshop": 0.85, "engineering": 1.0, "power": 1.0}
PAD = 6


def hull_slots(C, R, fins=True):
    """All slots of a C x R ship: standard block, edge ring, corners, ends, fins."""
    S = 20
    ox = oy = PAD + 20
    out = []
    for r in range(R):
        for c in range(C):
            out.append({"kind": "standard", "x": ox + c * S, "y": oy + r * S, "w": 20, "h": 20,
                        "out": set(), "row": (r + 0.5) / R, "col": c})
    cb = C // 2
    # top / bottom rings
    for c in range(C):
        if c == cb:
            continue
        out.append({"kind": "edge", "x": ox + c * S, "y": oy - 10, "w": 20, "h": 10, "out": {"N"}, "row": 0.0, "col": c})
        out.append({"kind": "edge", "x": ox + c * S, "y": oy + R * S, "w": 20, "h": 10, "out": {"S"}, "row": 1.0, "col": c})
    # port / starboard rings (skip a middle row where a fin sits)
    fin_row = R // 2 if (fins and R >= 2) else None
    for r in range(R):
        if r == fin_row:
            continue
        out.append({"kind": "edge", "x": ox - 10, "y": oy + r * S, "w": 10, "h": 20, "out": {"W"}, "row": (r + 0.5) / R, "col": -1})
        out.append({"kind": "edge", "x": ox + C * S, "y": oy + r * S, "w": 10, "h": 20, "out": {"E"}, "row": (r + 0.5) / R, "col": C})
    for (cx, cy, o, col) in ((ox - 10, oy - 10, {"N", "W"}, -1), (ox + C * S, oy - 10, {"N", "E"}, C),
                             (ox - 10, oy + R * S, {"S", "W"}, -1), (ox + C * S, oy + R * S, {"S", "E"}, C)):
        out.append({"kind": "corner", "x": cx, "y": cy, "w": 10, "h": 10, "out": o,
                    "row": 0.0 if "N" in o else 1.0, "col": col})
    out.append({"kind": "end", "x": ox + cb * S, "y": oy - 20, "w": 20, "h": 20, "out": {"N", "E", "W"}, "conn": "S",
                "row": 0.0, "col": cb, "role": "bow"})
    out.append({"kind": "end", "x": ox + cb * S, "y": oy + R * S, "w": 20, "h": 20, "out": {"S", "E", "W"}, "conn": "N",
                "row": 1.0, "col": cb, "role": "stern"})
    if fin_row is not None:
        out.append({"kind": "end", "x": ox - 20, "y": oy + fin_row * S, "w": 20, "h": 20, "out": {"N", "S", "W"},
                    "conn": "E", "row": (fin_row + 0.5) / R, "col": -2, "role": "fin"})
        out.append({"kind": "end", "x": ox + C * S, "y": oy + fin_row * S, "w": 20, "h": 20, "out": {"N", "S", "E"},
                    "conn": "W", "row": (fin_row + 0.5) / R, "col": C + 1, "role": "fin"})
    return out


def slots_area(slots):
    return sum(s["w"] * s["h"] for s in slots)


def choose_dims(tonnage, symmetric=True, fins=True):
    target = tonnage * 2           # two squares = one ton
    best = None
    for C in range(1, 6):
        if symmetric and C % 2 == 0:
            continue
        for R in range(1, 10):
            if R < C:
                continue
            a = slots_area(hull_slots(C, R, fins))
            err = abs(a - target) + (0 if R >= C else 50)
            if best is None or err < best[0]:
                best = (err, C, R)
    return best[1], best[2]


def tags_for_slots(slots, ship_type, rng, mode):
    """Planned: guarantee required rooms, bias function to hull position."""
    cfg = SHIP_TYPES[ship_type]
    need = dict(BASE_REQUIRED)
    for k, v in cfg["extra"].items():
        need[k] = need.get(k, 0) + v
    tags = {i: None for i in range(len(slots))}
    for i, s in enumerate(slots):
        if s.get("role") == "bow":
            tags[i] = ["bridge"]
        elif s.get("role") == "stern":
            tags[i] = ["engineering", "power"]
    open_ = [i for i, t in tags.items() if t is None]
    rng.shuffle(open_)
    required = []
    for tag, n in need.items():
        if tag in ("bridge", "engineering"):
            n = max(0, n - 1)
        required += [tag] * n
    # best position per required room, greedy with noise
    for tag in required:
        if not open_:
            break
        pref = LONG_PREF.get(tag, 0.5)
        pick = min(open_, key=lambda i: abs(slots[i]["row"] - pref) + rng.random() * 0.35)
        tags[pick] = [tag]
        open_.remove(pick)
    palette = cfg["palette"]
    names = list(palette)
    for i in open_:
        row = slots[i]["row"]
        wts = [palette[n] / (0.25 + abs(LONG_PREF.get(n, 0.5) - row)) for n in names]
        tags[i] = [rng.choices(names, weights=wts)[0]]
    return tags


def _allowed(slot):
    kind, out = slot["kind"], frozenset(slot["out"])
    if kind == "standard":
        return None

    def ok(tile, o):
        return o.hull == out
    return ok


def generate_ship(registry, rng, tonnage=1000, ship_type="Merchant", mode="planned", symmetric=True,
                  fins=True, orientation="N", condition="Average"):
    """Build a ship. Returns ``(grid, info)`` where ``grid`` is a LevelGrid."""
    if ship_type not in SHIP_TYPES:
        ship_type = "Merchant"
    C, R = choose_dims(tonnage, symmetric, fins)
    slots = hull_slots(C, R, fins)
    picker = TilePicker(registry, rng)
    grid = LevelGrid(0, "Main deck", cols=PAD * 2 + 20 * (C + 4) + 20, rows=PAD * 2 + 20 * (R + 4) + 20)
    if mode == "random":
        tags = {i: None for i in range(len(slots))}
    else:
        tags = tags_for_slots(slots, ship_type, rng, mode)
    # placement order: inner block first (centre outwards), then ring, then ends
    order = sorted(range(len(slots)), key=lambda i: ({"standard": 0, "edge": 1, "corner": 2, "end": 3}[slots[i]["kind"]],
                                                     abs(slots[i].get("col", 0) - C / 2)))
    placed = {}
    issues = []
    mirror_of = {}
    cb = C // 2
    for i in order:
        s = slots[i]
        t = tags[i] or []
        allowed = _allowed(s)
        if s["kind"] == "end":
            ttype = "end"
            allowed = _end_allowed(s)
        else:
            ttype = s["kind"]
        pick = None
        # symmetric partner: reuse the mirrored tile when the port side was already placed
        if symmetric and (s["kind"] != "end" or s.get("role") == "fin"):
            partner = _partner(slots, i, C)
            if partner is not None and partner in placed:
                p0 = placed[partner]
                best = None
                for o in orientations(p0.tile):
                    if (o.w, o.h) != (s["w"], s["h"]) or o.mirror == p0.o.mirror:
                        continue
                    if allowed is not None and not allowed(p0.tile, o):
                        continue
                    f = grid.fit(s["x"], s["y"], o, p0.tile.type)
                    if f.ok and (best is None or f.score() > best[0]):
                        best = (f.score(), o, f)
                if best is not None:
                    pick = (p0.tile, best[1], best[2])
        if pick is None:
            if mode == "selective":
                pick = _selective_pick(picker, grid, s, t, ttype, allowed, rng)
            else:
                pick = picker.choose(grid, s["x"], s["y"], s["w"], s["h"], t, ttype,
                                     allowed_orients=allowed)
        if pick is None:
            issues.append(f"no tile fits {s['kind']} slot at {s['x']},{s['y']}")
            continue
        tile, o, f = pick
        placed[i] = grid.place(tile, s["x"], s["y"], o, zone=(t[0] if t else ""))
    info = {"C": C, "R": R, "tonnage_target": tonnage, "ship_type": ship_type, "mode": mode,
            "symmetric": symmetric, "issues": issues}
    info["tonnage"] = round(sum(p.w * p.h for p in grid.placed) / 2)
    if mode == "movie":
        _movie_crop(grid, rng, info)
    _repair_ship(grid, info)
    k = {"N": 0, "E": 1, "S": 2, "W": 3}.get(orientation, 0)
    if k:
        rotate_grid(grid, k)
    info["bow"] = orientation
    return grid, info


def _partner(slots, i, C):
    s = slots[i]
    col = s.get("col")
    if col is None:
        return None
    for j, q in enumerate(slots):
        if j == i or q["kind"] != s["kind"] or q["y"] != s["y"] or q["h"] != s["h"] or q["w"] != s["w"]:
            continue
        if q.get("role") == "fin" and s.get("role") == "fin":
            return j
        # mirror across the ship's centre line
        mid = slots[0]["x"] + C * 20 / 2
        if abs((q["x"] + q["w"] / 2) - (2 * mid - (s["x"] + s["w"] / 2))) < 1e-6 and q["x"] < s["x"]:
            return j
    return None


def _end_allowed(slot):
    out = frozenset(slot["out"])

    def ok(tile, o):
        return o.hull == out
    return ok


def _selective_pick(picker, grid, s, tags, ttype, allowed, rng):
    """Random tile, but reject placements that make no sense (bad adjacencies, hull in the wrong place)."""
    from .assign import adjacency_rules
    forbid, _prefer = adjacency_rules()
    pool = [t for t in picker.reg.tiles.values() if t.type == ttype and {t.w, t.h} == {s["w"], s["h"]}]
    rng.shuffle(pool)
    first = None
    for tile in pool[:60]:
        for o in orientations(tile):
            if (o.w, o.h) != (s["w"], s["h"]) or (allowed and not allowed(tile, o)):
                continue
            f = grid.fit(s["x"], s["y"], o, tile.type)
            if not f.ok or not f.perfect:
                continue
            bad = False
            for nb in f.neighbours:
                for ta in tile.tags:
                    for tb in nb.tile.tags:
                        if frozenset((ta, tb)) in forbid and tile.tags[ta] >= 0.9 and nb.tile.tags[tb] >= 0.9:
                            bad = True
            if first is None:
                first = (tile, o, f)
            if not bad:
                picker.used[tile.id] = picker.used.get(tile.id, 0) + 1
                return tile, o, f
    return first or picker.choose(grid, s["x"], s["y"], s["w"], s["h"], tags, ttype, allowed_orients=allowed)


def _movie_crop(grid, rng, info):
    """Movie set: keep only a connected area around the action."""
    from .connect import tile_graph
    if not grid.placed:
        return
    adj = tile_graph(grid)
    start = rng.choice(grid.placed)
    keep = {id(start)}
    frontier = [start]
    target = max(3, round(len(grid.placed) * 0.4))
    while frontier and len(keep) < target:
        cur = frontier.pop(0)
        nbrs = [n for n in adj.get(id(cur), []) if id(n) not in keep]
        rng.shuffle(nbrs)
        for n in nbrs:
            if len(keep) >= target:
                break
            keep.add(id(n))
            frontier.append(n)
    removed = [p for p in grid.placed if id(p) not in keep]
    for p in removed:
        grid.remove(p)
    info["movie_removed"] = len(removed)
    xs0 = [p.x for p in grid.placed]; ys0 = [p.y for p in grid.placed]
    xs1 = [p.x + p.w for p in grid.placed]; ys1 = [p.y + p.h for p in grid.placed]
    if xs0:
        info["crop"] = (min(xs0) - 3, min(ys0) - 3, max(xs1) + 3, max(ys1) + 3)
    info["tonnage"] = round(sum(p.w * p.h for p in grid.placed) / 2)


def _repair_ship(grid, info):
    """Check connectivity and record repairs (patched doors added later in connect)."""
    from .connect import tile_graph
    adj = tile_graph(grid)
    if not grid.placed:
        return
    seen = set()
    stack = [grid.placed[0]]
    while stack:
        p = stack.pop()
        if id(p) in seen:
            continue
        seen.add(id(p))
        stack.extend(adj.get(id(p), []))
    info["connected"] = len(seen) == len(grid.placed)


def rotate_grid(grid, k):
    """Rotate every tile and filler piece clockwise by ``k`` quarter turns."""
    from .placement import Orientation
    for _ in range(k):
        W, H = grid.cols, grid.rows
        new_placed = []
        for p in grid.placed:
            o = p.o
            old_h = o.h
            edges = {s: c for s, c in o.edges}
            ee = {"N": edges["W"][::-1], "E": edges["N"], "S": edges["E"][::-1], "W": edges["S"][::-1]}
            hull = frozenset(rot_side(s, 90) for s in o.hull)
            p.o = Orientation((o.rot + 90) % 360, o.mirror, o.h, o.w, tuple((s, tuple(ee[s])) for s in ("N", "E", "S", "W")), hull)
            p.x, p.y = H - (p.y + old_h), p.x      # new box: x' = H - (y + h), y' = x
            new_placed.append(p)
        for f in grid.filler:
            f["x"], f["y"], f["w"], f["h"] = H - (f["y"] + f["h"]), f["x"], f["h"], f["w"]
        grid.cols, grid.rows = H, W
        grid.occ.clear()
        for p in grid.placed:
            for c in p.cells():
                grid.occ[c] = p
