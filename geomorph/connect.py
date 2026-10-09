"""Connections: native doors, patched doors, access sealing, walkways, graphs."""
from __future__ import annotations

from collections import deque

from . import filler as F
from .edges import DOOR, WALL, passable
from .placement import DIRS, OPPOSITE, LevelGrid, Placed


def tile_graph(grid: LevelGrid) -> dict:
    """id(tile) -> neighbouring tiles joined by a native matched door."""
    adj: dict = {}
    for a, b, door, contact in grid.connections():
        if door > 0:
            adj.setdefault(id(a), []).append(b)
            adj.setdefault(id(b), []).append(a)
    return adj


def shared_boundary(a: Placed, b: Placed):
    """``(side_of_a, [(squares_along...)])`` for touching tiles, or None.

    Returns the side of ``a`` facing ``b`` and the list of coordinates along the
    shared boundary as ``(index_in_a, index_in_b, world_coord)``.
    """
    if a.x + a.w == b.x:
        side, rng_ = "E", range(max(a.y, b.y), min(a.y + a.h, b.y + b.h))
        return side, [(y - a.y, y - b.y, y) for y in rng_]
    if b.x + b.w == a.x:
        side, rng_ = "W", range(max(a.y, b.y), min(a.y + a.h, b.y + b.h))
        return side, [(y - a.y, y - b.y, y) for y in rng_]
    if a.y + a.h == b.y:
        side, rng_ = "S", range(max(a.x, b.x), min(a.x + a.w, b.x + b.w))
        return side, [(x - a.x, x - b.x, x) for x in rng_]
    if b.y + b.h == a.y:
        side, rng_ = "N", range(max(a.x, b.x), min(a.x + a.w, b.x + b.w))
        return side, [(x - a.x, x - b.x, x) for x in rng_]
    return None


def door_squares(a: Placed, b: Placed):
    """World coordinates (along the shared boundary) where both sides carry a door."""
    sb = shared_boundary(a, b)
    if not sb:
        return None, []
    side, pts = sb
    out = [w for ia, ib, w in pts
           if passable(a.side_cls(side)[ia]) and passable(b.side_cls(OPPOSITE[side])[ib])]
    return side, out


def wall_squares(a: Placed, b: Placed):
    sb = shared_boundary(a, b)
    if not sb:
        return None, []
    side, pts = sb
    out = [w for ia, ib, w in pts
           if a.side_cls(side)[ia] == WALL and b.side_cls(OPPOSITE[side])[ib] == WALL]
    return side, out


def boundary_point(a: Placed, side, coord):
    """World (x, y) of the boundary line crossing at ``coord`` along ``side``."""
    if side == "E":
        return a.x + a.w, coord + 0.5
    if side == "W":
        return a.x, coord + 0.5
    if side == "S":
        return coord + 0.5, a.y + a.h
    return coord + 0.5, a.y


def door_piece(level, side, coord, a: Placed, kind="door", label=""):
    """A door glyph straddling the wall between two tiles at ``coord``."""
    bx, by = boundary_point(a, side, coord)
    if side in ("E", "W"):
        return F.piece(kind, level, bx - 0.5, by - 1.0, 1, 2, label=label)
    return F.piece(kind, level, bx - 1.0, by - 0.5, 2, 1, label=label)


def patch_pieces(level, side, coords, a: Placed):
    """Wall patches that cover doors which access rules close off."""
    out = []
    for c in coords:
        bx, by = boundary_point(a, side, c)
        if side in ("E", "W"):
            out.append(F.piece("patch", level, bx - 0.5, by - 0.6, 1, 1.2))
        else:
            out.append(F.piece("patch", level, bx - 0.6, by - 0.5, 1.2, 1))
    return out


def walkway_between(grid: LevelGrid, a: Placed, b: Placed, kind="walkway", width=2):
    """Draw a corridor from a door of ``a`` to a door of ``b`` (they do not touch).

    Picks the facing sides, the door squares nearest each other and joins them
    with an axis-aligned path (one elbow at most). Returns the pieces added.
    """
    acx, acy = a.x + a.w / 2, a.y + a.h / 2
    bcx, bcy = b.x + b.w / 2, b.y + b.h / 2
    dx, dy = bcx - acx, bcy - acy
    if abs(dx) >= abs(dy):
        sa, sb = ("E", "W") if dx > 0 else ("W", "E")
    else:
        sa, sb = ("S", "N") if dy > 0 else ("N", "S")

    def best_door(p, side, toward):
        idxs = [i for i, c in enumerate(p.side_cls(side)) if c == DOOR]
        if not idxs:
            idxs = [len(p.side_cls(side)) // 2]
        pts = []
        for i in idxs:
            coord = (p.y + i) if side in ("E", "W") else (p.x + i)
            pts.append((coord, boundary_point(p, side, coord)))
        return min(pts, key=lambda q: abs(q[1][0] - toward[0]) + abs(q[1][1] - toward[1]))
    ca, pa = best_door(a, sa, (bcx, bcy))
    cb, pb = best_door(b, sb, pa)
    out = []
    lvl = a.level

    def seg(x0, y0, x1, y1):
        # axis-aligned rectangle of ``width`` squares centred on the line
        if abs(x1 - x0) >= abs(y1 - y0):
            xa, xb = sorted((x0, x1))
            out.append(F.piece(kind, lvl, xa, y0 - width / 2, max(0.5, xb - xa), width))
        else:
            ya, yb = sorted((y0, y1))
            out.append(F.piece(kind, lvl, x0 - width / 2, ya, width, max(0.5, yb - ya)))
    if sa in ("E", "W"):
        if abs(pa[1] - pb[1]) < 0.01:
            seg(pa[0], pa[1], pb[0], pb[1])
        else:
            mx = (pa[0] + pb[0]) / 2
            seg(pa[0], pa[1], mx, pa[1])
            seg(mx, pa[1], mx, pb[1])
            seg(mx, pb[1], pb[0], pb[1])
    else:
        if abs(pa[0] - pb[0]) < 0.01:
            seg(pa[0], pa[1], pb[0], pb[1])
        else:
            my = (pa[1] + pb[1]) / 2
            seg(pa[0], pa[1], pa[0], my)
            seg(pa[0], my, pb[0], my)
            seg(pb[0], my, pb[0], pb[1])
    return out, (sa, ca), (sb, cb)


def reachable_set(adj: dict, start_id):
    seen = {start_id}
    dq = deque([start_id])
    while dq:
        n = dq.popleft()
        for m in adj.get(n, ()):
            if id(m) not in seen:
                seen.add(id(m))
                dq.append(id(m))
    return seen


class Box:
    """A non-tile room (filler slot) that corridors can attach to (door mid-side)."""

    def __init__(self, x, y, w, h, level):
        self.x, self.y, self.w, self.h, self.level = x, y, w, h, level

    def side_cls(self, side):
        n = self.w if side in ("N", "S") else self.h
        cls = [WALL] * n
        cls[n // 2] = DOOR
        return cls


def contact_info(a: Placed, b: Placed):
    """Describe how two touching tiles meet.

    Returns ``None`` for hull-to-hull contacts (no door expected) else a dict
    with the facing side of ``a``, native matched door coordinates, single-sided
    door coordinates (a door facing a wall) and wall-to-wall coordinates.
    """
    sb = shared_boundary(a, b)
    if not sb:
        return None
    side, pts = sb
    if side in a.o.hull or OPPOSITE[side] in b.o.hull:
        return None
    native, lone, walls = [], [], []
    for ia, ib, w in pts:
        ca, cb = a.side_cls(side)[ia], b.side_cls(OPPOSITE[side])[ib]
        if passable(ca) and passable(cb):
            native.append(w)
        elif passable(ca) or passable(cb):
            lone.append((w, "a" if passable(ca) else "b"))
        else:
            walls.append(w)
    return {"side": side, "native": native, "lone": lone, "walls": walls}


def realize_contacts(grid: LevelGrid, zones: dict, allowed=None):
    """Decide every tile-to-tile contact on one level.

    * allowed contact with a native matched door: stays as built
    * allowed contact without one: a door is *patched* onto the shared wall
    * contact the access rules forbid: its doors are sealed with wall patches
    * single-sided doors (a door facing a wall) are always sealed

    Returns ``(links, fillers)``; links are ``(a, b, state)`` with state
    ``native | patched | sealed | wall``.
    """
    links, pieces = [], []
    for a, b, door, contact in grid.connections():
        info = contact_info(a, b)
        if info is None:
            continue
        side = info["side"]
        za, zb = zones.get(a.zone), zones.get(b.zone)
        ok = True if (allowed is None or za is None or zb is None) else allowed(za, zb)
        start = len(pieces)
        for w, who in info["lone"]:
            pieces.extend(patch_pieces(grid.index, side, [w], a))
        if ok:
            if info["native"]:
                links.append((a, b, "native"))
            elif info["walls"]:
                mid = info["walls"][len(info["walls"]) // 2]
                # prefer a wall square that is not at a tile corner
                inner = [w for w in info["walls"] if True]
                pieces.append(door_piece(grid.index, side, mid, a))
                links.append((a, b, "patched"))
            else:
                links.append((a, b, "wall"))
        else:
            if info["native"]:
                pieces.extend(patch_pieces(grid.index, side, info["native"], a))
            links.append((a, b, "sealed"))
        for pc in pieces[start:]:
            pc["zones"] = [a.zone, b.zone]
    return links, pieces


def level_components(grid: LevelGrid, links):
    """Connected components of tiles joined by open links (native / patched)."""
    adj = {}
    for a, b, state in links:
        if state in ("native", "patched"):
            adj.setdefault(id(a), []).append(b)
            adj.setdefault(id(b), []).append(a)
    seen, comps = set(), []
    for p in grid.placed:
        if id(p) in seen:
            continue
        comp, stack = [], [p]
        while stack:
            q = stack.pop()
            if id(q) in seen:
                continue
            seen.add(id(q))
            comp.append(q)
            stack.extend(adj.get(id(q), []))
        comps.append(comp)
    return comps
