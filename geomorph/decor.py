"""Symbol decor: furnish open rooms with real-size symbols, optionally after something bad happened.

* Scale: a symbol's size is its measured opaque box at 300 px per grid square, never stretched.
* Where: only enclosed room floor from the tile's floor map (``floors.py``); corridors stay clear and items keep to
  the walls (or the middle for tables), so nothing blocks a door.
* What: the room's function (tile tags) picks the Symbols folders via ``data/symbol_map.json``.
* Incidents (alien-flavoured, generic): signs of a struggle, ransacked, overrun — displaced and rotated items,
  debris, barricaded doors, scorch/acid burns, drag marks and resin. Applied everywhere, only to
  lockdown/quarantine/threat zones, or to a random share of rooms.
"""
from __future__ import annotations

import json
import math

from . import DATA_DIR
from . import filler as F
from .floors import SUB, decode, transform
from .overlays import categorize  # noqa: F401  (kept for symmetry with craft overlays)
from .placement import DIRS
from .symbols import SYM_PPS

_MAP = None
_FLOORS = None
MIN_SIDE = 4          # cells (2 squares): smaller pockets are not rooms worth furnishing
INCIDENTS = ("none", "struggle", "ransacked", "overrun")


def symbol_map():
    global _MAP
    if _MAP is None:
        _MAP = json.loads((DATA_DIR / "symbol_map.json").read_text(encoding="utf-8"))
    return _MAP


def tile_floors():
    global _FLOORS
    if _FLOORS is None:
        p = DATA_DIR / "tile_floor.json"
        _FLOORS = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _FLOORS


from .floors import free_rects  # noqa: E402  (shared with the floor-map builder)


def _categories(tile, only=None):
    m = symbol_map()["tags"]
    ranked = sorted(tile.tags.items(), key=lambda kv: -kv[1])
    cats = []
    for tag, wgt in ranked:
        if wgt < 0.5:
            continue
        for c in m.get(tag, []):
            if c not in cats and (not only or c in only):
                cats.append(c)
    return cats


class Decorator:
    def __init__(self, symbols: dict, rng, density=0.5, incident="none", where="all", cats=None):
        self.syms = symbols
        self.rng = rng
        self.density = max(0.0, min(1.0, float(density)))
        self.incident = incident if incident in INCIDENTS else "none"
        self.where = where
        self.only = set(cats) if cats else None
        by = {}
        for s in symbols.values():
            if s.role == "item" and max(s.w, s.h) >= 0.4:
                by.setdefault(s.cat, []).append(s)
        self.items = by

    # -- one room rectangle -------------------------------------------------
    def furnish(self, tile, p, rect, level, zone, incident):
        """Place items in ``rect`` (cells of tile ``p``); returns decor dicts and filler pieces."""
        rng = self.rng
        cats = [c for c in _categories(tile, self.only) if self.items.get(c)] or \
            [c for c in ("Furniture, Consoles, & Equipment",) if self.items.get(c)]
        if not cats:
            return [], []
        x, y, w, h = rect
        cw = cell_w = 1.0 / SUB
        used = [[False] * w for _ in range(h)]
        placed, extra = [], []

        def fits(ix, iy, iw, ih):
            if ix < 0 or iy < 0 or ix + iw > w or iy + ih > h:
                return False
            return not any(used[yy][xx] for yy in range(iy, iy + ih) for xx in range(ix, ix + iw))

        def take(ix, iy, iw, ih):
            for yy in range(iy, iy + ih):
                for xx in range(ix, ix + iw):
                    used[yy][xx] = True

        def put(sym, ix, iy, rot, flip=False, kind="item"):
            sw, sh = (sym.h, sym.w) if rot % 180 else (sym.w, sym.h)
            cx = p.x + (x + ix) * cell_w + sw / 2
            cy = p.y + (y + iy) * cell_w + sh / 2
            placed.append({"sym": sym.id, "level": level, "cx": round(cx, 3), "cy": round(cy, 3), "rot": rot,
                           "flip": bool(flip), "zone": zone, "kind": kind, "cat": sym.cat})

        target = max(1, round((w * h) / (SUB * SUB) * 0.18 * self.density * 2))
        style_map = symbol_map()["style"]
        tries = 0
        n = 0
        while n < target and tries < target * 12:
            tries += 1
            cat = rng.choice(cats)
            sym = rng.choice(self.items[cat])
            rot = rng.choice((0, 90))
            sw, sh = (sym.h, sym.w) if rot else (sym.w, sym.h)
            cw_, ch_ = max(1, math.ceil(sw * SUB)), max(1, math.ceil(sh * SUB))
            if cw_ > w - 2 or ch_ > h - 2:
                continue
            style = style_map.get(cat, style_map["default"])
            if style == "center" or (style == "mixed" and rng.random() < 0.4):
                ix, iy = (w - cw_) // 2 + rng.randint(-1, 1), (h - ch_) // 2 + rng.randint(-1, 1)
            else:                                    # against a wall, leaving the middle for walking
                side = rng.choice("NESW")
                if side in "NS":
                    ix = rng.randint(0, w - cw_)
                    iy = 0 if side == "N" else h - ch_
                    rot = 0 if sw >= sh else 90
                else:
                    iy = rng.randint(0, h - ch_)
                    ix = 0 if side == "W" else w - cw_
                    rot = 90 if sw >= sh else 0
                sw, sh = (sym.h, sym.w) if rot else (sym.w, sym.h)
                cw_, ch_ = max(1, math.ceil(sw * SUB)), max(1, math.ceil(sh * SUB))
                ix = min(ix, w - cw_)
                iy = min(iy, h - ch_)
            if fits(ix, iy, cw_, ch_):
                take(ix, iy, cw_, ch_)
                put(sym, ix, iy, rot, flip=rng.random() < 0.3)
                n += 1
        if incident != "none":
            self._incident(placed, extra, incident, p, rect, level, used)
        return placed, extra

    def _incident(self, placed, extra, kind, p, rect, level, used):
        rng = self.rng
        x, y, w, h = rect
        ox, oy = p.x + x / SUB, p.y + y / SUB
        rw, rh = w / SUB, h / SUB
        loss = {"struggle": 0.10, "ransacked": 0.40, "overrun": 0.50}[kind]
        shift = {"struggle": 0.15, "ransacked": 0.45, "overrun": 0.6}[kind]
        keep = []
        for it in placed:
            if rng.random() < loss:
                continue
            if rng.random() < (0.35 if kind == "struggle" else 0.8):
                it["rot"] = (it["rot"] + rng.choice((-1, 1)) * rng.randint(12, 170)) % 360
                it["cx"] = round(it["cx"] + rng.uniform(-shift, shift), 3)
                it["cy"] = round(it["cy"] + rng.uniform(-shift, shift), 3)
                it["flip"] = it["flip"] or rng.random() < 0.3
                it["disturbed"] = True
            keep.append(it)
        placed[:] = keep
        nd = {"struggle": (1, 3), "ransacked": (3, 7), "overrun": (4, 8)}[kind]
        for _ in range(rng.randint(*nd)):
            s = rng.choice((1, 1, 2))
            extra.append(F.piece("rubble", level, ox + rng.uniform(0, max(0.1, rw - s)), oy + rng.uniform(0, max(0.1, rh - s)),
                                 s, s, decor=True))
        if kind == "overrun":
            for _ in range(rng.randint(2, 4)):
                s = rng.uniform(1.0, 2.2)
                extra.append(F.piece("scorch", level, ox + rng.uniform(0, max(0.1, rw - s)),
                                     oy + rng.uniform(0, max(0.1, rh - s)), s, s, decor=True))
            for _ in range(rng.randint(1, 2)):          # resin in the corners
                s = rng.uniform(0.8, 1.6)
                cx = ox + (0.1 if rng.random() < 0.5 else max(0.1, rw - s - 0.1))
                cy = oy + (0.1 if rng.random() < 0.5 else max(0.1, rh - s - 0.1))
                extra.append(F.piece("resin", level, cx, cy, s, s, decor=True))
            # a drag trail from the middle of the room toward a corner
            if rw >= 3 and rh >= 3:
                if rng.random() < 0.5:
                    extra.append(F.piece("drag", level, ox + rw * 0.2, oy + rh / 2 - 0.4, rw * 0.6, 0.8, decor=True))
                else:
                    extra.append(F.piece("drag", level, ox + rw / 2 - 0.4, oy + rh * 0.2, 0.8, rh * 0.6, decor=True))
        elif kind == "ransacked" and rng.random() < 0.5:
            extra.append(F.piece("scorch", level, ox + rng.uniform(0, max(0.1, rw - 1.2)), oy + rng.uniform(0, max(0.1, rh - 1.2)),
                                 1.2, 1.2, decor=True))

    def barricade(self, p, level, zone, rect):
        """Stack lockers/crates across the door of a room (overrun rooms)."""
        pool = [s for c in symbol_map()["barricade"] for s in self.items.get(c, [])]
        if not pool:
            return []
        x, y, w, h = rect
        out = []
        for side in "NESW":
            cls = p.side_cls(side)
            for i, c in enumerate(cls):
                if c != 1:
                    continue
                # door square in world cells; the room must touch it
                if side in "NS":
                    dx_cell, dy_cell = i * SUB, (0 if side == "N" else p.h * SUB - 1)
                else:
                    dx_cell, dy_cell = (0 if side == "W" else p.w * SUB - 1), i * SUB
                if x - 2 <= dx_cell <= x + w + 1 and y - 2 <= dy_cell <= y + h + 1:
                    for k in range(self.rng.randint(1, 3)):
                        sym = self.rng.choice(pool)
                        cx = p.x + dx_cell / SUB + 0.5 + self.rng.uniform(-0.3, 0.3)
                        cy = p.y + dy_cell / SUB + 0.5 + self.rng.uniform(-0.3, 0.3)
                        shiftx = {"W": 0.8, "E": -0.8}.get(side, 0)
                        shifty = {"N": 0.8, "S": -0.8}.get(side, 0)
                        out.append({"sym": sym.id, "level": level, "cx": round(cx + shiftx, 3), "cy": round(cy + shifty, 3),
                                    "rot": self.rng.choice((0, 90, 20, 340)), "flip": False, "zone": zone,
                                    "kind": "barricade", "cat": sym.cat, "disturbed": True})
                    return out
        return out


def apply(res, rng, symbols: dict, opts: dict):
    """Furnish ``res`` (a generation Result). ``opts``: enabled, density, incident, where, cats."""
    if not opts or not opts.get("enabled") or not symbols:
        res.decor = []
        return res
    dec = Decorator(symbols, rng, opts.get("density", 0.5), opts.get("incident", "none"), opts.get("where", "all"),
                    opts.get("cats"))
    floors = tile_floors()
    ov = res.overlays or {}
    hot = set(ov.get("lockdown", [])) | set(ov.get("quarantine", [])) | set(ov.get("power_failure", []))
    hot |= {m.get("zone") for m in res.markers if m.get("type") in ("threat", "breach", "damage")}
    out = []
    for g in res.grids:
        for p in g.placed:
            rows = floors.get(p.tile.id)
            if not rows or p.tile.type == "wing":
                continue
            grid = transform(decode(rows), p.o.rot, p.o.mirror)
            rects = free_rects(grid)
            if not rects:
                continue
            kind = dec.incident
            if kind != "none":
                if dec.where == "overlay" and p.zone not in hot:
                    kind = "none"
                elif dec.where == "random" and rng.random() > 0.3:
                    kind = "none"
            for rect in rects:
                items, extra = dec.furnish(p.tile, p, rect, g.index, p.zone, kind)
                out.extend(items)
                for f in extra:
                    g.filler.append(f)
                if kind == "overrun":
                    out.extend(dec.barricade(p, g.index, p.zone, rect))
    res.decor = out
    res.meta["decor"] = {"items": len(out), "incident": dec.incident, "where": dec.where}
    return res
