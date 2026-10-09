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
            out.append(F.piece("patch", level, bx - 0.15, by - 0.5, 0.3, 1))
        else:
            out.append(F.piece("patch", level, bx - 0.5, by - 0.15, 1, 0.3))
    return out


def walkway_between(grid: LevelGrid, a: Placed, b: Placed, kind="walkway", width=2):
    """Draw a straight corridor between two tiles that do not touch.

    The corridor runs square to both facing walls at a position where the two tiles overlap, preferring a spot
    where both walls already have a door; where a wall has none a door is cut into it (a door glyph on the wall).
    Tiles that do not overlap at all (diagonal neighbours) are joined with a single elbow.
    Returns ``(pieces, (side_a, coord_a), (side_b, coord_b))``.
    """
    acx, acy = a.x + a.w / 2, a.y + a.h / 2
    bcx, bcy = b.x + b.w / 2, b.y + b.h / 2
    dx, dy = bcx - acx, bcy - acy
    if abs(dx) >= abs(dy):
        sa, sb = ("E", "W") if dx > 0 else ("W", "E")
    else:
        sa, sb = ("S", "N") if dy > 0 else ("N", "S")
    lvl = a.level
    out = []
    horiz = sa in ("E", "W")                       # corridor runs left-right
    if horiz:
        lo, hi = max(a.y, b.y), min(a.y + a.h, b.y + b.h)
    else:
        lo, hi = max(a.x, b.x), min(a.x + a.w, b.x + b.w)

    def door_coords(p, side):
        cls = p.side_cls(side)
        base = p.y if side in ("E", "W") else p.x
        return {base + i for i, c in enumerate(cls) if c == DOOR}
    da, db = door_coords(a, sa), door_coords(b, sb)
    if hi - lo >= width:
        best = None
        for c in range(int(lo), int(hi) - width + 1):
            span = set(range(c, c + width))
            both = bool(span & da and span & db)
            near = min([abs(c + (width - 1) / 2 - d) for d in (da | db)] or [99])
            cost = (0 if both else 1, near + abs(c + width / 2 - (lo + hi) / 2) * 0.1)
            if best is None or cost < best[0]:
                best = (cost, c, both)
        _, c, both = best
        if horiz:
            xa = a.x + a.w if sa == "E" else b.x + b.w
            xb = b.x if sa == "E" else a.x
            out.append(F.piece(kind, lvl, xa, c, max(0.5, xb - xa), width))
            walls = ((a, sa, a.x + a.w if sa == "E" else a.x), (b, sb, b.x if sb == "W" else b.x + b.w))
            for p, side, wx in walls:
                if not (set(range(c, c + width)) & door_coords(p, side)):
                    out.append(F.piece("door", lvl, wx - 0.5, c, 1, width))
        else:
            ya = a.y + a.h if sa == "S" else b.y + b.h
            yb = b.y if sa == "S" else a.y
            out.append(F.piece(kind, lvl, c, ya, width, max(0.5, yb - ya)))
            walls = ((a, sa, a.y + a.h if sa == "S" else a.y), (b, sb, b.y if sb == "N" else b.y + b.h))
            for p, side, wy in walls:
                if not (set(range(c, c + width)) & door_coords(p, side)):
                    out.append(F.piece("door", lvl, c, wy - 0.5, width, 1))
        return out, (sa, c), (sb, c)

    # no overlap: one elbow from a door of ``a`` to a door of ``b``
    def pick(p, side, toward):
        cs = door_coords(p, side) or {(p.y if side in ("E", "W") else p.x) + len(p.side_cls(side)) // 2}
        pts = [(c, boundary_point(p, side, c)) for c in cs]
        return min(pts, key=lambda q: abs(q[1][0] - toward[0]) + abs(q[1][1] - toward[1]))
    ca, pa = pick(a, sa, (bcx, bcy))
    cb, pb = pick(b, sb, pa)

    def seg(x0, y0, x1, y1):
        if abs(x1 - x0) >= abs(y1 - y0):
            xa_, xb_ = sorted((x0, x1))
            out.append(F.piece(kind, lvl, xa_, y0 - width / 2, max(0.5, xb_ - xa_), width))
        else:
            ya_, yb_ = sorted((y0, y1))
            out.append(F.piece(kind, lvl, x0 - width / 2, ya_, width, max(0.5, yb_ - ya_)))
    if horiz:
        seg(pa[0], pa[1], pb[0], pa[1])
        seg(pb[0], pa[1], pb[0], pb[1])
    else:
        seg(pa[0], pa[1], pa[0], pb[1])
        seg(pa[0], pb[1], pb[0], pb[1])
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
