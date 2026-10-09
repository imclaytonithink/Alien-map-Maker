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


def _strip_depth(p, side, c0, width):
    """Open distance inside tile ``p`` before its first visible wall on the strip [c0, c0+width) (None: no wall there)."""
    try:
        from . import decor, floors
        rows = decor.tile_floors().get(p.tile.id)
        if not rows:
            return 0.0                               # filler box: its edge is the wall
        grid = floors.transform(floors.decode(rows), p.o.rot, p.o.mirror)
        base = p.y if side in ("E", "W") else p.x
        return floors.solid_depth(grid, side, c0 - base, c0 - base + width, none_if_empty=True)
    except AttributeError:
        return 0.0


def walkway_between(grid: LevelGrid, a: Placed, b: Placed, kind="walkway", width=2):
    """Draw a corridor between two tiles that do not touch, so that it really connects.

    It runs square to both facing walls and on until each tile's *visible* wall (tile art often leaves empty floor
    inside its plan edge). Where the tiles overlap it looks for a straight run that hits a wall at both ends,
    preferring existing doors; a door glyph is cut into each wall that has none. If no straight run reaches both
    buildings (one has its room off to the side) the corridor jogs once in the gap.
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
    horiz = sa in ("E", "W")
    lo_a = int(a.y if horiz else a.x)
    n_a = int(a.h if horiz else a.w)
    lo_b = int(b.y if horiz else b.x)
    n_b = int(b.h if horiz else b.w)

    def door_coords(p, side):
        base = p.y if side in ("E", "W") else p.x
        return {base + i for i, c in enumerate(p.side_cls(side)) if c == DOOR}
    da, db = door_coords(a, sa), door_coords(b, sb)
    ov_lo, ov_hi = max(lo_a, lo_b), min(lo_a + n_a, lo_b + n_b)

    def candidates(lo, hi):
        res = []
        for c in range(int(lo), int(hi) - width + 1):
            res.append((c, _strip_depth(a, sa, c, width), _strip_depth(b, sb, c, width)))
        return res
    straight = [(c, x, y) for c, x, y in candidates(ov_lo, ov_hi) if x is not None and y is not None]
    out = []

    def run(c0, ia, ib):
        """One straight corridor at strip [c0, c0+width) from tile a's wall to tile b's wall."""
        if horiz:
            left, right = (a, b) if sa == "E" else (b, a)
            il, ir = (ia, ib) if sa == "E" else (ib, ia)
            xa, xb = left.x + left.w - il, right.x + ir
            out.append(F.piece(kind, lvl, xa, c0, max(0.5, xb - xa), width))
            return (left, "E", xa, il, c0), (right, "W", xb, ir, c0)
        top, bot = (a, b) if sa == "S" else (b, a)
        it, ibt = (ia, ib) if sa == "S" else (ib, ia)
        ya, yb = top.y + top.h - it, bot.y + ibt
        out.append(F.piece(kind, lvl, c0, ya, width, max(0.5, yb - ya)))
        return (top, "S", ya, it, c0), (bot, "N", yb, ibt, c0)

    def door_at(p, side, w_, ins, c0):
        if ins > 0 or not (set(range(c0, c0 + width)) & door_coords(p, side)):
            if side in ("E", "W"):
                out.append(F.piece("door", lvl, w_ - 0.5, c0, 1, width))
            else:
                out.append(F.piece("door", lvl, c0, w_ - 0.5, width, 1))
    if straight:
        def cost(item):
            c0, ia, ib = item
            span = set(range(c0, c0 + width))
            both = bool(span & da and span & db)
            return (0 if both else 1, ia + ib, abs(c0 + width / 2 - (ov_lo + ov_hi) / 2))
        c0, ia, ib = min(straight, key=cost)
        for p, side, w_, ins, cc in run(c0, ia, ib):
            door_at(p, side, w_, ins, cc)
        return out, (sa, c0), (sb, c0)
    # one jog: each end at its own best strip (the first strip that reaches a wall, nearest the other end's centre)

    def best_strip(p, side, lo, n, target):
        opts = []
        for c in range(int(lo), int(lo) + int(n) - width + 1):
            d = _strip_depth(p, side, c, width)
            if d is not None:
                opts.append((abs(c + width / 2 - target), c, d))
        if not opts:
            c = int(lo + n // 2 - width // 2)
            return c, 0.0
        _, c, d = min(opts)
        return c, d
    target = (bcy if horiz else bcx)
    ca, ia = best_strip(a, sa, lo_a, n_a, target)
    cb, ib = best_strip(b, sb, lo_b, n_b, ca + width / 2)
    if horiz:
        left, right = (a, b) if sa == "E" else (b, a)
        cl, il, cr, ir = (ca, ia, cb, ib) if sa == "E" else (cb, ib, ca, ia)
        xa, xb = left.x + left.w - il, right.x + ir
        mid = (left.x + left.w + right.x) / 2
        out.append(F.piece(kind, lvl, xa, cl, max(0.5, mid + width / 2 - xa), width))
        out.append(F.piece(kind, lvl, mid - width / 2, min(cl, cr), width, abs(cl - cr) + width))
        out.append(F.piece(kind, lvl, mid - width / 2, cr, max(0.5, xb - mid + width / 2), width))
        door_at(left, "E", xa, il, cl)
        door_at(right, "W", xb, ir, cr)
    else:
        top, bot = (a, b) if sa == "S" else (b, a)
        ct, it, cbt, ibt = (ca, ia, cb, ib) if sa == "S" else (cb, ib, ca, ia)
        ya, yb = top.y + top.h - it, bot.y + ibt
        mid = (top.y + top.h + bot.y) / 2
        out.append(F.piece(kind, lvl, ct, ya, width, max(0.5, mid + width / 2 - ya)))
        out.append(F.piece(kind, lvl, min(ct, cbt), mid - width / 2, abs(ct - cbt) + width, width))
        out.append(F.piece(kind, lvl, cbt, mid - width / 2, width, max(0.5, yb - mid + width / 2)))
        door_at(top, "S", ya, it, ct)
        door_at(bot, "N", yb, ibt, cbt)
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
