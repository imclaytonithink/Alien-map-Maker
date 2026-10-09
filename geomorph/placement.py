"""Grid placement engine: rotation, mirroring, edge matching, no overlaps.

Coordinates are grid squares (5 ft). A tile's *footprint* is its plan area
(``w x h`` squares, without the transparent image border). Orientation is
``(rot, mirror)``: the image is mirrored left-right first, then rotated
clockwise by ``rot`` degrees — the same order the canvas applies a piece's
flip and rotation, so exported pieces draw exactly what the engine validated.

Edge data per side (see ``edges.py``) is a list of classes, one per square, read
in canonical direction (N/S: left to right, E/W: top to bottom):
``WALL`` (0), ``DOOR`` (1) or ``VOID`` (2, an outer hull side).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .edges import DOOR, VOID, WALL, passable
from .registry import BORDER_SQUARES, PX_PER_SQUARE, SIDES, Tile

DIRS = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}
OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}
ROTS = (0, 90, 180, 270)


def rot_side(side: str, rot: int) -> str:
    return SIDES[(SIDES.index(side) + rot // 90) % 4]


def side_of_dir(dx, dy) -> str:
    return {(0, -1): "N", (1, 0): "E", (0, 1): "S", (-1, 0): "W"}[(dx, dy)]


def hull_sides(tile: Tile) -> set:
    """Sides of a tile that face outside (edge/corner/end tiles).

    Connecting sides are flat (no see-through squares) and carry the doors; hull
    sides are chamfered/open or, when the hull is a flat wall, door-less.
    """
    if tile.type in ("standard", "megamorph") or not tile.edges:
        return set()
    avg = {s: sum(tile.edges[s]["raw"]) / max(1, len(tile.edges[s]["raw"])) for s in SIDES}
    doors = {s: sum(1 for c in tile.edges[s]["cls"] if c == DOOR) for s in SIDES}
    voids = {s: sum(1 for v in tile.edges[s]["raw"] if v >= 0.6) for s in SIDES}

    def rank(s):
        return (voids[s] > 0, -doors[s], avg[s])
    if tile.type == "edge":
        longs = ("N", "S") if tile.w >= tile.h else ("E", "W")
        conn = min(longs, key=rank)
        return {s for s in longs if s != conn}
    if tile.type == "end":
        conn = min(SIDES, key=rank)
        return {s for s in SIDES if s != conn}
    best, pick = None, None
    for i in range(4):
        a_, b_ = SIDES[i], SIDES[(i + 1) % 4]
        key = (voids[a_] > 0 or voids[b_] > 0, voids[a_] + voids[b_], -(doors[a_] + doors[b_]), avg[a_] + avg[b_])
        if best is None or key < best:
            best, pick = key, {SIDES[(i + 2) % 4], SIDES[(i + 3) % 4]}
    return pick


@dataclass(frozen=True)
class Orientation:
    rot: int
    mirror: bool
    w: int
    h: int
    edges: tuple          # ((side, cls-tuple), ...)
    hull: frozenset

    def cls(self, side):
        return dict(self.edges)[side]


_orient_cache: dict = {}


def orientations(tile: Tile) -> list:
    """All distinct orientations of a tile (mirror x 4 rotations)."""
    cached = _orient_cache.get(id(tile))
    if cached is not None and cached[0] is tile:
        return cached[1]
    base = {s: tuple(tile.edges[s]["cls"]) if tile.edges else (WALL,) * (tile.w if s in "NS" else tile.h)
            for s in SIDES}
    hull0 = hull_sides(tile)
    for s in hull0:                                   # a hull side never connects
        base[s] = (VOID,) * len(base[s])
    out, seen = [], set()
    for mirror in (False, True):
        e = dict(base)
        h = set(hull0)
        if mirror:
            e = {"N": e["N"][::-1], "S": e["S"][::-1], "E": e["W"], "W": e["E"]}
            h = {"E" if s == "W" else "W" if s == "E" else s for s in h}
        for rot in ROTS:
            ee, hh, w, hgt = dict(e), set(h), tile.w, tile.h
            for _ in range(rot // 90):             # one clockwise quarter turn
                ee = {"N": ee["W"][::-1], "E": ee["N"], "S": ee["E"][::-1], "W": ee["S"][::-1]}
                hh = {SIDES[(SIDES.index(s) + 1) % 4] for s in hh}
                w, hgt = hgt, w
            key = (w, hgt, tuple(sorted(ee.items())), tuple(sorted(hh)))
            if key in seen:
                continue
            seen.add(key)
            out.append(Orientation(rot, mirror, w, hgt, tuple((s, tuple(ee[s])) for s in SIDES),
                                   frozenset(hh)))
    _orient_cache[id(tile)] = (tile, out)
    return out


def orientation_for(tile: Tile, rot: int, mirror: bool) -> Orientation:
    """The orientation with this exact rotation/mirror (used when loading saved layouts)."""
    for o in orientations(tile):
        if o.rot == rot and o.mirror == bool(mirror):
            return o
    # deduplicated away because it equals another orientation: rebuild it
    base = {s: tuple(tile.edges[s]["cls"]) for s in SIDES}
    for s in hull_sides(tile):
        base[s] = (VOID,) * len(base[s])
    e, h, w, hgt = dict(base), set(hull_sides(tile)), tile.w, tile.h
    if mirror:
        e = {"N": e["N"][::-1], "S": e["S"][::-1], "E": e["W"], "W": e["E"]}
        h = {"E" if s == "W" else "W" if s == "E" else s for s in h}
    for _ in range((rot % 360) // 90):
        e = {"N": e["W"][::-1], "E": e["N"], "S": e["E"][::-1], "W": e["S"][::-1]}
        h = {SIDES[(SIDES.index(s) + 1) % 4] for s in h}
        w, hgt = hgt, w
    return Orientation(rot % 360, bool(mirror), w, hgt, tuple((s, tuple(e[s])) for s in SIDES), frozenset(h))


@dataclass
class Placed:
    tile: Tile
    x: int
    y: int
    o: Orientation
    zone: str = ""
    level: int = 0
    key: int = 0              # numbered-key marker, assigned in dressing

    @property
    def w(self): return self.o.w
    @property
    def h(self): return self.o.h

    def cells(self):
        return ((self.x + i, self.y + j) for j in range(self.h) for i in range(self.w))

    def side_cls(self, side):
        return self.o.cls(side)

    def to_json(self):
        return {"tile": self.tile.id, "x": self.x, "y": self.y, "rot": self.o.rot,
                "mirror": self.o.mirror, "zone": self.zone, "level": self.level, "key": self.key}


@dataclass
class Fit:
    ok: bool = True
    matches: int = 0          # door meets door
    mism: int = 0             # door meets wall
    hull_bad: int = 0         # hull side pressed against a tile
    hull_out: int = 0         # hull side facing empty space (good)
    dangling: int = 0         # door facing empty space
    touching: int = 0         # neighbour tiles touched
    neighbours: list = field(default_factory=list)

    def score(self):
        return (self.matches * 2.0 - self.mism * 3.0 - self.hull_bad * 5.0
                + self.hull_out * 0.2 - self.dangling * 0.3)

    @property
    def perfect(self):
        return self.ok and self.mism == 0 and self.hull_bad == 0


class LevelGrid:
    """One deck: occupancy map + the tiles on it."""

    def __init__(self, index=0, name="", cols=200, rows=200):
        self.index = index
        self.name = name or f"Level {index + 1}"
        self.cols, self.rows = cols, rows
        self.occ: dict = {}
        self.placed: list = []
        self.filler: list = []          # procedural pieces (see filler.py)

    def free(self, x, y, w, h):
        return all((x + i, y + j) not in self.occ for j in range(h) for i in range(w))

    def in_bounds(self, x, y, w, h):
        return x >= 0 and y >= 0 and x + w <= self.cols and y + h <= self.rows

    def fit(self, x, y, o: Orientation, cur_type: str = "standard") -> Fit:
        f = Fit()
        if not self.in_bounds(x, y, o.w, o.h) or not self.free(x, y, o.w, o.h):
            f.ok = False
            return f
        neigh = {}
        for side, (dx, dy) in DIRS.items():
            cls = o.cls(side)
            n = o.w if side in "NS" else o.h
            for i in range(n):
                cx = x + (i if side in "NS" else (o.w if side == "E" else -1))
                cy = y + (i if side in "EW" else (o.h if side == "S" else -1))
                nb = self.occ.get((cx, cy))
                mine = cls[i]
                if nb is None:
                    if mine == VOID:
                        f.hull_out += 1
                    elif passable(mine):
                        f.dangling += 1
                    continue
                idx = (cx - nb.x) if side in "NS" else (cy - nb.y)
                theirs = nb.side_cls(OPPOSITE[side])[idx]
                neigh[id(nb)] = nb
                mine_hull = side in o.hull
                their_hull = OPPOSITE[side] in nb.o.hull
                if mine_hull and their_hull:
                    continue                       # two hull sides flush together: fine
                if mine_hull or their_hull:
                    # hull pressed against another tile's interior side is wrong;
                    # against ring/end neighbours (chamfered ends) it is harmless
                    if (mine_hull and nb.tile.type in ("standard", "megamorph")) or \
                            (their_hull and cur_type in ("standard", "megamorph")):
                        f.hull_bad += 1
                    continue
                if passable(mine) and passable(theirs):
                    f.matches += 1
                elif passable(mine) != passable(theirs):
                    f.mism += 1
        f.neighbours = list(neigh.values())
        f.touching = len(neigh)
        return f

    def place(self, tile, x, y, o: Orientation, zone="") -> Placed:
        p = Placed(tile, x, y, o, zone, self.index)
        for c in p.cells():
            assert c not in self.occ, "overlap"
            self.occ[c] = p
        self.placed.append(p)
        return p

    def remove(self, p: Placed):
        for c in p.cells():
            self.occ.pop(c, None)
        self.placed.remove(p)

    def bounds(self):
        if not self.placed:
            return (0, 0, 0, 0)
        return (min(p.x for p in self.placed), min(p.y for p in self.placed),
                max(p.x + p.w for p in self.placed), max(p.y + p.h for p in self.placed))

    # -- connections ---------------------------------------------------
    def connections(self):
        """Tile-to-tile links: (a, b, native_door_squares). Wall-only contacts omitted."""
        links = {}
        for p in self.placed:
            for side in ("E", "S"):                      # each contact once
                dx, dy = DIRS[side]
                n = p.w if side in "NS" else p.h
                for i in range(n):
                    cx = p.x + (i if side == "S" else p.w)
                    cy = p.y + (i if side == "E" else p.h)
                    nb = self.occ.get((cx, cy))
                    if nb is None:
                        continue
                    idx = (cx - nb.x) if side == "S" else (cy - nb.y)
                    mine = p.side_cls(side)[i]
                    theirs = nb.side_cls(OPPOSITE[side])[idx]
                    k = (id(p), id(nb))
                    ent = links.setdefault(k, [p, nb, 0, 0])
                    ent[3] += 1
                    if side in p.o.hull or OPPOSITE[side] in nb.o.hull:
                        continue
                    if passable(mine) and passable(theirs):
                        ent[2] += 1
        return [(a, b, door, contact) for a, b, door, contact in links.values()]


# -- piece export --------------------------------------------------------
def tile_piece(placed: Placed, cell: float, layer="Generated", asset_path=None, px=None):
    """Canvas piece dict for a placed tile.

    The image is ``(w+4) x (h+4)`` squares at 300 px per square (2-square clear
    border), so the visible footprint is the plan area and the border overhangs.
    """
    t = placed.tile
    S, B = PX_PER_SQUARE, BORDER_SQUARES
    w_px = (t.w + 2 * B) * S
    h_px = (t.h + 2 * B) * S
    scale = cell / S
    cw, ch = placed.w * cell, placed.h * cell            # footprint after rotation
    cx = placed.x * cell + cw / 2.0
    cy = placed.y * cell + ch / 2.0
    ww, hh = w_px * scale, h_px * scale
    return {"asset_path": asset_path or t.image, "name": f"{t.number} {t.title}".strip(),
            "x": cx - ww / 2.0, "y": cy - hh / 2.0, "w": w_px, "h": h_px, "scale": scale,
            "rotation": placed.o.rot, "flip_h": placed.o.mirror, "flip_v": False,
            "layer_name": layer, "snap": True, "opacity": 1.0,
            "tile": t.id, "zone": placed.zone, "level": placed.level}
