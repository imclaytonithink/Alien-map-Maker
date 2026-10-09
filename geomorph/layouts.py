"""Topology templates: turn an archetype's scale preset into slots and filler.

A *slot* is a 20x20-square place for one standard tile (or a filler room).
Every topology returns a :class:`Layout` with slots, explicit links
(corridors/tunnels/roads), procedural filler (ground, rock, roads, fences,
domes, shafts, stairs) and vertical structures that sit at the *same X/Y on
every level*.

Level index 0 is the top-most level. For underground sites a ``Surface`` level
is added above the building levels, showing only the small visible head
structure.
"""
from __future__ import annotations

import math

from . import filler as F
from .model import Layout, Link, Slot

T = 20          # tile size in squares
PAD = 6         # empty margin around everything
GAP = 6         # gap between campus buildings (walkway length)
ROAD = 4        # street width


def _slot(lay, level, x, y, **kw):
    s = Slot(idx=len(lay.slots), level=level, x=int(x), y=int(y), **kw)
    lay.slots.append(s)
    return s


def contacts(lay: Layout):
    """Pairs of slots on the same level whose tiles touch (>= 4 shared squares)."""
    out = []
    by_level = {}
    for s in lay.slots:
        by_level.setdefault(s.level, []).append(s)
    for lvl, ss in by_level.items():
        for i, a in enumerate(ss):
            for b in ss[i + 1:]:
                if a.x + a.w == b.x or b.x + b.w == a.x:
                    ov = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
                elif a.y + a.h == b.y or b.y + b.h == a.y:
                    ov = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
                else:
                    continue
                if ov >= 4:
                    out.append((a.idx, b.idx))
    return out


def classify_positions(lay: Layout):
    """core / edge / corner by how many sides of the slot face outside."""
    by_level = {}
    for s in lay.slots:
        by_level.setdefault(s.level, []).append(s)
    for lvl, ss in by_level.items():
        occ = {(s.x, s.y) for s in ss}
        xs = sorted({s.x for s in ss})
        ys = sorted({s.y for s in ss})
        for s in ss:
            exposed = 0
            for dx, dy in ((T, 0), (-T, 0), (0, T), (0, -T)):
                if (s.x + dx, s.y + dy) not in occ:
                    exposed += 1
            if not s.pos or s.pos == "any":
                s.pos = "core" if exposed == 0 else ("corner" if exposed >= 2 else "edge")


def _grow(params, keys, need_fn, need, limit=40):
    p = dict(params)
    i = 0
    while need_fn(p) < need and i < limit:
        k = keys[i % len(keys)]
        p[k] = p[k] + 1
        i += 1
    return p


def _level_names(n, underground):
    names = [f"Level {i + 1}" for i in range(n)]
    if underground:
        names = ["Surface"] + names
    return names


def _bounds(lay):
    xs0 = [s.x for s in lay.slots] + [f["x"] for f in lay.filler]
    ys0 = [s.y for s in lay.slots] + [f["y"] for f in lay.filler]
    xs1 = [s.x + s.w for s in lay.slots] + [f["x"] + f["w"] for f in lay.filler]
    ys1 = [s.y + s.h for s in lay.slots] + [f["y"] + f["h"] for f in lay.filler]
    lay.bounds = (min(xs0), min(ys0), max(xs1), max(ys1))


def _ground(lay, level, margin=PAD, kind="ground", box=None):
    x0, y0, x1, y1 = box or _slots_box(lay, level)
    lay.filler.insert(0, F.piece(kind, level, x0 - margin, y0 - margin,
                                 x1 - x0 + 2 * margin, y1 - y0 + 2 * margin))


def _slots_box(lay, level):
    ss = [s for s in lay.slots if s.level == level] or lay.slots
    return (min(s.x for s in ss), min(s.y for s in ss),
            max(s.x + s.w for s in ss), max(s.y + s.h for s in ss))


# ---------------------------------------------------------------------------
# Stacked structure: one footprint, several levels, aligned core and shafts
# ---------------------------------------------------------------------------
def stacked(rng, p, env, need, ctx):
    underground = env == "underground"

    def free(q):
        cells = q["cols"] * q["rows"]
        vc = 1 if cells >= 3 else 0
        return (cells - vc) * q["levels"]
    q = _grow({"cols": p["cols"], "rows": p["rows"], "levels": p["levels"]},
              ["cols", "rows", "levels"], free, need)
    cols, rows, nlev = q["cols"], q["rows"], q["levels"]
    off = 1 if underground else 0
    lay = Layout(levels=nlev + off, level_names=_level_names(nlev, underground), slots=[], topology="stacked")
    x0 = y0 = PAD
    cells = [(i, j) for j in range(rows) for i in range(cols)]
    vc = None
    if len(cells) >= 3:
        edge = [c for c in cells if c[0] in (0, cols - 1) or c[1] in (0, rows - 1)]
        vc = rng.choice(edge)
    for lvl in range(nlev):
        for (i, j) in cells:
            s = _slot(lay, lvl + off, x0 + i * T, y0 + j * T)
            if (i, j) == vc:
                s.role = "vertical"
    vx, vy = (x0 + vc[0] * T, y0 + vc[1] * T) if vc else (x0, y0)
    lay.vertical.append({"kind": "core", "x": vx, "y": vy, "w": T if vc else 4, "h": T if vc else 4,
                         "levels": list(range(off, nlev + off)), "source": "tile" if vc else "filler"})
    if not vc:                              # tiny footprint: stairs + lift as filler, aligned
        col = [s for s in lay.slots if (s.x, s.y) == (x0, y0)]
        col.sort(key=lambda q: q.level)
        for a_, b_ in zip(col, col[1:]):
            lay.links.append(Link(a_.idx, b_.idx, "stairs"))
        for lvl in range(off, nlev + off):
            lay.filler.append(F.piece("stairs", lvl, vx + 1, vy + 1, 2, 4, label="Stairs"))
            lay.filler.append(F.piece("lift", lvl, vx + 4, vy + 1, 2, 2, label="Lift"))
        lay.vertical[0].update({"x": vx + 1, "y": vy + 1, "w": 5, "h": 4})
    # utility shafts at the same spots every level (corners of the footprint)
    shafts = ctx.get("shafts") or []
    pts = [(x0 + 1, y0 + 1), (x0 + cols * T - 3, y0 + 1), (x0 + 1, y0 + rows * T - 3),
           (x0 + cols * T - 3, y0 + rows * T - 3)]
    for k, name in enumerate(shafts[:4]):
        lay.markers.append({"type": "shaft", "name": name, "x": pts[k][0], "y": pts[k][1],
                            "levels": list(range(off, nlev + off))})
    if underground:
        lay.filler.append(F.piece("ground", 0, x0 - PAD, y0 - PAD, cols * T + 2 * PAD, rows * T + 2 * PAD))
        lay.filler.append(F.piece("rock", 0, x0 - PAD, y0 - PAD, cols * T + 2 * PAD, rows * T + 2 * PAD))
        lay.notes.append("Surface level shows only the entrance structure; everything else is underground.")
    for lvl in range(off, nlev + off):
        lay.filler.insert(0, F.piece("rock" if underground else "ground", lvl, x0 - 2, y0 - 2,
                                     cols * T + 4, rows * T + 4))
    return lay


# ---------------------------------------------------------------------------
# Campus: buildings on a surface map joined by walkways
# ---------------------------------------------------------------------------
def campus(rng, p, env, need, ctx):
    lo, hi = p["buildings"]
    count = max(lo, need)
    count = min(max(count, lo), max(hi, need))
    nlev = max(1, p.get("levels", 1))
    n = count
    ncol = max(2, math.ceil(math.sqrt(n * 1.3)))
    nrow = math.ceil(n / ncol)
    lay = Layout(levels=1, level_names=["Surface"], slots=[], topology="campus")
    pitch = T + GAP
    base = max(PAD, ctx.get("ground_margin", PAD))      # room for the ground around (farmland needs more)
    order = [(i, j) for j in range(nrow) for i in range(ncol)][:n]
    pos = {}
    for k, (i, j) in enumerate(order):
        s = _slot(lay, 0, base + i * pitch, base + j * pitch)
        pos[(i, j)] = s
    # links between grid neighbours (walkways drawn after tiles are placed)
    for (i, j), s in pos.items():
        for di, dj in ((1, 0), (0, 1)):
            t = pos.get((i + di, j + dj))
            if t:
                lay.links.append(Link(s.idx, t.idx, "walkway"))
    _ground(lay, 0, margin=ctx.get("ground_margin", PAD))
    ctx["perimeter_box"] = _slots_box(lay, 0)
    if nlev > 1:                           # large campuses: a second, covered level
        lay.levels = 2
        lay.level_names = ["Surface", "Lower / tunnels"]
        for (i, j), s in list(pos.items())[: max(2, n // 3)]:
            s2 = _slot(lay, 1, s.x, s.y)
            lay.links.append(Link(s.idx, s2.idx, "stairs"))
        lay.filler.append(F.piece("rock", 1, base - 2, base - 2, ncol * pitch, nrow * pitch))
        lay.vertical.append({"kind": "stairs", "x": base, "y": base, "w": 4, "h": 4, "levels": [0, 1],
                             "source": "filler"})
    return lay


# ---------------------------------------------------------------------------
# Street grid: blocks and streets with buildings on lots
# ---------------------------------------------------------------------------
def street(rng, p, env, need, ctx):
    cols, rows = p["blocks"]
    q = _grow({"cols": cols, "rows": rows}, ["cols", "rows"], lambda q: q["cols"] * q["rows"], need)
    cols, rows = q["cols"], q["rows"]
    lay = Layout(levels=1, level_names=["Streets"], slots=[], topology="street")
    pitch = T + ROAD
    x0 = y0 = PAD + ROAD
    grid = {}
    for j in range(rows):
        for i in range(cols):
            grid[(i, j)] = _slot(lay, 0, x0 + i * pitch, y0 + j * pitch)
    w = cols * pitch + ROAD
    h = rows * pitch + ROAD
    lay.filler.append(F.piece("ground", 0, PAD - 2, PAD - 2, w + 4, h + 4))
    for i in range(cols + 1):
        lay.filler.append(F.piece("road", 0, PAD + i * pitch, PAD, ROAD, h))
    for j in range(rows + 1):
        lay.filler.append(F.piece("road", 0, PAD, PAD + j * pitch, w, ROAD))
    for (i, j), s in grid.items():
        for di, dj in ((1, 0), (0, 1)):
            t = grid.get((i + di, j + dj))
            if t:
                lay.links.append(Link(s.idx, t.idx, "road"))
    ctx["perimeter_box"] = (PAD, PAD, PAD + w, PAD + h)
    ctx["dome"] = ctx.get("dome")
    return lay


# ---------------------------------------------------------------------------
# Branching tunnels / vertical shaft (mines)
# ---------------------------------------------------------------------------
def branching(rng, p, env, need, ctx):
    nlev, chambers = p["levels"], p["chambers"]
    # slots: surface head-frame (1) + per underground level 1 hub + chambers/levels
    def total(q):
        return 1 + q["levels"] * 1 + q["chambers"]
    q = _grow({"levels": nlev, "chambers": chambers}, ["chambers", "levels"], total, need)
    nlev, chambers = q["levels"], q["chambers"]
    lay = Layout(levels=nlev + 1, level_names=["Surface"] + [f"Level {i + 1}" for i in range(nlev)],
                 slots=[], topology="branching")
    sx = sy = PAD + 4 * T
    sw = 4
    # head-frame on the surface over the shaft
    head = _slot(lay, 0, sx - T // 2 + 2, sy - T // 2 + 2, role="headframe")
    head.pos = "core"
    lay.filler.append(F.piece("shaft", 0, sx, sy, sw, sw, label="Shaft"))
    lay.vertical.append({"kind": "shaft", "x": sx, "y": sy, "w": sw, "h": sw,
                         "levels": list(range(0, nlev + 1)), "source": "filler"})
    per_level = [chambers // nlev + (1 if i < chambers % nlev else 0) for i in range(nlev)]
    reach = 0
    for li in range(nlev):
        lvl = li + 1
        lay.filler.append(F.piece("shaft", lvl, sx, sy, sw, sw, label="Shaft"))
        hub = _slot(lay, lvl, sx - T // 2 + 2, sy - T // 2 + 2, role="hub")
        hub.pos = "core"
        k = per_level[li]
        # chambers alternate along the main drift (E/W) and side drifts (N/S)
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        placed = []
        for c in range(k):
            dx, dy = dirs[c % 4]
            ring = 1 + c // 4
            dist = (T + GAP) * ring
            cx = hub.x + dx * dist
            cy = hub.y + dy * dist
            cs = _slot(lay, lvl, cx, cy, pos="edge")
            parent = hub.idx
            if ring > 1:                     # chain behind the nearer chamber on that arm
                for pc in placed:
                    if pc[0] == (dx, dy) and pc[1] == ring - 1:
                        parent = pc[2]
            placed.append(((dx, dy), ring, cs.idx))
            lay.links.append(Link(parent, cs.idx, "tunnel"))
        reach = max(reach, k)
    # the shaft ties the head-frame and every level's hub together
    hubs = [s for s in lay.slots if s.role == "hub"]
    prev = head
    for hs in hubs:
        lay.links.append(Link(prev.idx, hs.idx, "shaft"))
        prev = hs
    x0, y0, x1, y1 = _slots_box(lay, 1)
    for lvl in range(1, nlev + 1):
        lay.filler.insert(0, F.piece("rock", lvl, x0 - PAD, y0 - PAD, x1 - x0 + 2 * PAD, y1 - y0 + 2 * PAD))
    lay.filler.insert(0, F.piece("ground", 0, sx - 3 * T // 2, sy - 3 * T // 2, 3 * T, 3 * T))
    lay.markers.append({"type": "shaft", "name": "Main shaft", "x": sx, "y": sy, "levels": list(range(nlev + 1))})
    ctx["perimeter_box"] = None
    return lay


# ---------------------------------------------------------------------------
# Hub-and-spoke / modular spine / ring (stations)
# ---------------------------------------------------------------------------
def hub(rng, p, env, need, ctx):
    modules = max(p["modules"], need)
    lay = Layout(levels=1, level_names=["Deck"], slots=[], topology="hub")
    c = PAD + 3 * T
    hubs = _slot(lay, 0, c, c, role="hub", pos="core")
    arms = 4
    per = math.ceil((modules - 1) / arms)
    dirs = [(0, -1), (1, 0), (0, 1), (-1, 0)]
    made = 1
    for ring in range(1, per + 1):
        for k, (dx, dy) in enumerate(dirs):
            if made >= modules:
                break
            dist = (T + GAP) * ring
            s = _slot(lay, 0, c + dx * dist, c + dy * dist, pos="edge")
            parent = hubs.idx
            if ring > 1:
                parent = [q.idx for q in lay.slots if (q.x, q.y) == (c + dx * dist - dx * (T + GAP),
                                                                    c + dy * dist - dy * (T + GAP))][0]
            lay.links.append(Link(parent, s.idx, "walkway"))
            made += 1
    ctx["perimeter_box"] = None
    return lay


def spine(rng, p, env, need, ctx):
    modules = max(p["modules"], need)
    lay = Layout(levels=1, level_names=["Deck"], slots=[], topology="spine")
    n_top = math.ceil(modules / 2)
    n_bot = modules - n_top
    y_top = PAD
    y_spine = y_top + T
    y_bot = y_spine + ROAD
    spine_len = n_top * T
    lay.filler.append(F.piece("walkway", 0, PAD, y_spine, spine_len, ROAD, label="Spine"))
    top_slots, bot_slots = [], []
    for i in range(n_top):
        top_slots.append(_slot(lay, 0, PAD + i * T, y_top, pos="edge"))
    for i in range(n_bot):
        bot_slots.append(_slot(lay, 0, PAD + i * T, y_bot, pos="edge"))
    for a, b in zip(top_slots, top_slots[1:]):
        lay.links.append(Link(a.idx, b.idx, "walkway"))
    for a, b in zip(bot_slots, bot_slots[1:]):
        lay.links.append(Link(a.idx, b.idx, "walkway"))
    for i, a in enumerate(top_slots):                # spine ties each opposing pair together
        if i < len(bot_slots):
            lay.links.append(Link(a.idx, bot_slots[i].idx, "walkway"))
    for s in (top_slots[:1] + top_slots[-1:] + bot_slots[:1] + bot_slots[-1:]):
        s.pos = "corner"
    ctx["perimeter_box"] = None
    return lay


def ring(rng, p, env, need, ctx):
    modules = max(p["modules"], need, 4)
    lay = Layout(levels=1, level_names=["Deck"], slots=[], topology="ring")
    # choose a cols x rows loop with 2c + 2r - 4 >= modules
    cols = 2
    rows = 2
    while 2 * cols + 2 * rows - 4 < modules:
        if cols <= rows:
            cols += 1
        else:
            rows += 1
    pitch = T + GAP
    ring_cells = ([(i, 0) for i in range(cols)] + [(cols - 1, j) for j in range(1, rows)]
                  + [(i, rows - 1) for i in range(cols - 2, -1, -1)] + [(0, j) for j in range(rows - 2, 0, -1)])
    ring_cells = ring_cells[:max(modules, 4)]
    slots = []
    for (i, j) in ring_cells:
        s = _slot(lay, 0, PAD + i * pitch, PAD + j * pitch, pos="edge")
        if (i, j) in ((0, 0), (cols - 1, 0), (0, rows - 1), (cols - 1, rows - 1)):
            s.pos = "corner"
        slots.append(s)
    for a, b in zip(slots, slots[1:] + slots[:1]):
        if a is not b:
            lay.links.append(Link(a.idx, b.idx, "walkway"))
    ctx["perimeter_box"] = None
    return lay


TOPOLOGY_BUILDERS = {"stacked": stacked, "campus": campus, "street": street, "branching": branching,
                     "hub": hub, "spine": spine, "ring": ring}


def build(topology, rng, preset, env, need, ctx):
    lay = TOPOLOGY_BUILDERS[topology](rng, preset, env, need, ctx)
    classify_positions(lay)
    lay.topology = topology
    return lay
