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
import re

from . import DATA_DIR
from . import filler as F
from .floors import SUB, decode, transform
from .overlays import categorize  # noqa: F401  (kept for symmetry with craft overlays)
from .placement import DIRS
from .symbols import SYM_PPS

_MAP = None
_KITS = None
_FLOORS = None
MIN_SIDE = 4          # cells (2 squares): smaller pockets are not rooms worth furnishing
INCIDENTS = ("none", "struggle", "ransacked", "overrun")


def symbol_map():
    global _MAP
    if _MAP is None:
        _MAP = json.loads((DATA_DIR / "symbol_map.json").read_text(encoding="utf-8"))
    return _MAP


def symbol_kits():
    global _KITS
    if _KITS is None:
        _KITS = json.loads((DATA_DIR / "symbol_kits.json").read_text(encoding="utf-8"))["kits"]
    return _KITS


def tile_floors():
    global _FLOORS
    if _FLOORS is None:
        p = DATA_DIR / "tile_floor.json"
        _FLOORS = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _FLOORS


from .floors import free_rects, hall_bands  # noqa: E402  (shared with the floor-map builder)


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
            if 0.4 <= max(s.w, s.h) <= 3.6:
                by.setdefault(s.cat, []).append(s)
        self.items = by
        self._pools = {}

    # -- what belongs in this room -------------------------------------------
    def kit(self, tile, prefer=()):
        """Entries (with their symbol pools) for the room function that fits best.

        ``prefer`` are the functions the generator assigned to this room; they win over whatever else the (often
        multipurpose) tile happens to contain.
        """
        kits = symbol_kits()
        assigned = [t for t in prefer if t in kits]
        if assigned:                           # the function the generator gave this room decides, nothing else
            tags = [(1.0, t) for t in assigned]
        else:
            tags = [(w, t) for t, w in tile.tags.items() if w >= 0.5 and t in kits]
        if not tags:
            return []
        tag = self.rng.choices([t for _w, t in tags], weights=[w for w, _t in tags])[0]
        room_text = " ".join(list(tile.rooms) + [tile.title]).lower()
        out = []
        for e in kits[tag]:
            if e.get("if_room") and not re.search(e["if_room"], room_text):
                continue
            key = (e["cat"], e.get("name", ""))
            if key not in self._pools:
                rx = re.compile(e["name"], re.I) if e.get("name") else None
                self._pools[key] = [s for s in self.items.get(e["cat"], []) if rx is None or rx.search(s.name)]
            pool = self._pools[key]
            if pool and (not self.only or e["cat"] in self.only):
                out.append(dict(e, pool=pool))
        return out

    # -- one room rectangle -------------------------------------------------
    def furnish(self, tile, p, rect, level, zone, incident, prefer=(), messy=False, toward=None, nest=False):
        """Place items in ``rect`` (cells of tile ``p``); returns decor dicts and filler pieces."""
        rng = self.rng
        entries = self.kit(tile, prefer)
        if not entries:
            return [], []
        x, y, w, h = rect
        used = [[False] * w for _ in range(h)]
        placed, extra = [], []
        counts = {}

        def fits(ix, iy, iw, ih):
            if ix < 0 or iy < 0 or ix + iw > w or iy + ih > h:
                return False
            return not any(used[yy][xx] for yy in range(iy, iy + ih) for xx in range(ix, ix + iw))

        def take(ix, iy, iw, ih):
            for yy in range(iy, iy + ih):
                for xx in range(ix, ix + iw):
                    used[yy][xx] = True

        def dims(sym, rot):
            sw, sh = (sym.h, sym.w) if rot else (sym.w, sym.h)
            return max(1, math.ceil(sw * SUB - 1e-6)), max(1, math.ceil(sh * SUB - 1e-6)), sw, sh

        def put(sym, ix, iy, rot, ent, flip=False):
            cw_, ch_, sw, sh = dims(sym, rot)
            take(ix, iy, cw_, ch_)
            cx = p.x + (x + ix) / SUB + sw / 2
            cy = p.y + (y + iy) / SUB + sh / 2
            placed.append({"sym": sym.id, "level": level, "cx": round(cx, 3), "cy": round(cy, 3), "rot": rot,
                           "flip": bool(flip), "zone": zone, "kind": "item", "cat": sym.cat})
            counts[id(ent)] = counts.get(id(ent), 0) + 1

        def place_wall(ent, sym, side=None, start=None, inset=0):
            side = side or rng.choice("NESW")
            rot = 0 if (side in "NS") == (sym.w >= sym.h) else 90
            cw_, ch_, _sw, _sh = dims(sym, rot)
            if cw_ > w - 1 or ch_ > h - 1:
                return None
            if side in "NS":
                ix = start if start is not None else rng.randint(0, w - cw_)
                iy = inset if side == "N" else h - ch_ - inset
            else:
                iy = start if start is not None else rng.randint(0, h - ch_)
                ix = inset if side == "W" else w - cw_ - inset
            if fits(ix, iy, cw_, ch_):
                put(sym, ix, iy, rot, ent, flip=rng.random() < 0.3)
                return (side, ix, iy, cw_, ch_)
            return None

        def place_center(ent, sym):
            for _ in range(8):
                rot = rng.choice((0, 90))
                cw_, ch_, _sw, _sh = dims(sym, rot)
                if cw_ > w - 2 or ch_ > h - 2:
                    continue
                ix = (w - cw_) // 2 + rng.randint(-max(1, w // 5), max(1, w // 5))
                iy = (h - ch_) // 2 + rng.randint(-max(1, h // 5), max(1, h // 5))
                ix, iy = max(1, min(ix, w - cw_ - 1)), max(1, min(iy, h - ch_ - 1))
                if fits(ix, iy, cw_, ch_):
                    put(sym, ix, iy, rot, ent)
                    return True
            return False

        def place_corner(ent, sym):
            corner = rng.choice(("NW", "NE", "SW", "SE"))
            for _ in range(6):
                cw_, ch_, _sw, _sh = dims(sym, 0)
                ix = 0 if corner[1] == "W" else w - cw_
                iy = 0 if corner[0] == "N" else h - ch_
                # stack outward from the corner along the walls
                step = rng.randint(0, max(0, w // 3)), rng.randint(0, max(0, h // 3))
                ix = max(0, min(w - cw_, ix + (step[0] if corner[1] == "W" else -step[0])))
                iy = max(0, min(h - ch_, iy + (step[1] if corner[0] == "N" else -step[1])))
                if fits(ix, iy, cw_, ch_):
                    put(sym, ix, iy, 0, ent, flip=rng.random() < 0.3)
                    return True
            return False

        def place_row(ent, sym):
            side = rng.choice("NESW")
            first = place_wall(ent, sym, side)
            if not first:
                return False
            _s, ix, iy, cw_, ch_ = first
            while counts.get(id(ent), 0) < ent.get("max", 4):
                nxt = rng.choice(ent["pool"]) if rng.random() < 0.5 else sym
                if side in "NS":
                    ix += cw_
                    ok = place_wall(ent, nxt, side, ix)
                else:
                    iy += ch_
                    ok = place_wall(ent, nxt, side, iy)
                if not ok:
                    break
                _s, ix, iy, cw_, ch_ = ok
            return True

        def place_block(ent, sym):
            """Neat stock: one wall, rows of the same crate side by side (a second row behind the first when
            there is room), leaving an aisle through the middle."""
            side = rng.choice("NESW")
            first = place_wall(ent, sym, side, start=0 if rng.random() < 0.5 else None)
            if not first:
                return False
            _s, ix, iy, cw_, ch_ = first
            along = (w if side in "NS" else h)
            depth = ch_ if side in "NS" else cw_
            pos = (ix if side in "NS" else iy)
            step = cw_ if side in "NS" else ch_
            # the rest of the first row
            p0 = pos + step
            while counts.get(id(ent), 0) < ent.get("max", 6) and p0 + step <= along:
                if not place_wall(ent, sym, side, p0):
                    break
                p0 += step
            across = (h if side in "NS" else w)
            if across - 2 * depth >= 4 and counts.get(id(ent), 0) < ent.get("max", 6):    # aisle stays >= 2 squares
                p1 = pos
                while counts.get(id(ent), 0) < ent.get("max", 6) and p1 + step <= along:
                    if not place_wall(ent, sym, side, p1, inset=depth):
                        break
                    p1 += step
            return True

        def place_scatter(ent, sym):
            for _ in range(10):
                rot = rng.choice((0, 90))
                cw_, ch_, _sw, _sh = dims(sym, rot)
                if cw_ > w or ch_ > h:
                    continue
                ix, iy = rng.randint(0, w - cw_), rng.randint(0, h - ch_)
                if fits(ix, iy, cw_, ch_):
                    put(sym, ix, iy, rot, ent, flip=rng.random() < 0.5)
                    return True
            return False

        def place(ent):
            sym = rng.choice(ent["pool"])
            style = ent.get("style", "wall")
            if messy and ent["cat"] in ("Cargo", "Storage", "Ship's Locker") and style in ("block", "row", "corner", "wall"):
                return place_scatter(ent, sym)             # the scenario calls for a mess: crates everywhere
            if style == "block":
                return place_block(ent, sym)
            if style == "center":
                return place_center(ent, sym)
            if style == "corner":
                return place_corner(ent, sym)
            if style == "row":
                return place_row(ent, sym)
            return bool(place_wall(ent, sym))

        target = max(1, round((w * h) / (SUB * SUB) * 0.4 * self.density))
        for ent in entries:                               # the anchor piece(s) first (a bed in a cabin, tables in a mess)
            if ent.get("must"):
                for _ in range(6):
                    if place(ent):
                        break
        tries = 0
        while len(placed) < target and tries < target * 10:
            tries += 1
            open_ = [e for e in entries if counts.get(id(e), 0) < e.get("max", 3)]
            if not open_:
                break
            ent = rng.choices(open_, weights=[e.get("w", 1) for e in open_])[0]
            place(ent)
        if incident != "none":
            self._incident(placed, extra, incident, p, rect, level, used, toward, nest)
        return placed, extra

    def _incident(self, placed, extra, kind, p, rect, level, used, toward=None, nest=False):
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
            for _ in range(rng.randint(3, 5) if nest else rng.randint(1, 2)):   # resin in the corners (a nest: everywhere)
                s = rng.uniform(1.2, 2.4) if nest else rng.uniform(0.8, 1.6)
                cx = ox + (0.1 if rng.random() < 0.5 else max(0.1, rw - s - 0.1))
                cy = oy + (0.1 if rng.random() < 0.5 else max(0.1, rh - s - 0.1))
                extra.append(F.piece("resin", level, cx, cy, s, s, decor=True))
            # a drag trail from the middle of the room: toward the source when it is known, else toward a corner
            if rw >= 3 and rh >= 3 and not nest:
                if toward in ("E", "W"):
                    xs = ox + rw / 2 if toward == "E" else ox
                    extra.append(F.piece("drag", level, xs, oy + rh / 2 - 0.4, rw / 2, 0.8, decor=True))
                elif toward in ("N", "S"):
                    ys = oy + rh / 2 if toward == "S" else oy
                    extra.append(F.piece("drag", level, ox + rw / 2 - 0.4, ys, 0.8, rh / 2, decor=True))
                elif rng.random() < 0.5:
                    extra.append(F.piece("drag", level, ox + rw * 0.2, oy + rh / 2 - 0.4, rw * 0.6, 0.8, decor=True))
                else:
                    extra.append(F.piece("drag", level, ox + rw / 2 - 0.4, oy + rh * 0.2, 0.8, rh * 0.6, decor=True))
        elif kind == "ransacked" and rng.random() < 0.5:
            extra.append(F.piece("scorch", level, ox + rng.uniform(0, max(0.1, rw - 1.2)), oy + rng.uniform(0, max(0.1, rh - 1.2)),
                                 1.2, 1.2, decor=True))

    def barricade(self, p, level, zone, rect, sides=None, all_doors=False):
        """Stack lockers/crates across the door of a room (overrun rooms)."""
        pool = [s for c in symbol_map()["barricade"] for s in self.items.get(c, [])]
        if not pool:
            return []
        x, y, w, h = rect
        out = []
        for side in "NESW":
            if sides is not None and side not in sides:
                continue
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
                    if not all_doors:
                        return out
        return out


_EXT = None


def exterior_rules():
    global _EXT
    if _EXT is None:
        _EXT = json.loads((DATA_DIR / "symbol_exterior.json").read_text(encoding="utf-8"))
    return _EXT


def _landscaping(symbols):
    by = {}
    rx = re.compile(r"Landscaping 0*(\d+)")
    for s in symbols.values():
        m = rx.search(s.name)
        if m and s.cat == "Misc":
            by[int(m.group(1)) if m.group(1) else 0] = s
    return by


def apply_exterior(res, rng, symbols: dict, opts: dict):
    """Trees, bushes, boulders, benches, fountains and fields outdoors — only where a site would have them.

    * Non-ship sites, on levels that have open ground (campus, street, mine surface).
    * What grows depends on the environment (trees only in a breathable atmosphere; rocks and scrub on a hostile
      world; only rocks on an airless one; nothing for an orbital station) and on the kind of site (a company
      town gets trees and benches, a mine only rock and scrub, a garrison almost nothing, a farm gets fields).
    * It keeps off buildings, corridors, roads, pads, the gate and the approach to the entrance, and the fence line.
    """
    if res.kind != "site" or res.layout is None or not opts or not opts.get("exterior"):
        return res
    ext = exterior_rules()
    env = res.meta.get("environment", "breathable")
    allowed = set(ext["env"].get(env, []))
    arch_name = res.meta.get("archetype", "")
    group = (res.options or {}).get("_group") or ""
    style = ext["style_by_archetype"].get(arch_name) or ext["style_by_group"].get(group) or "civil"
    weights = {k: v for k, v in ext["style"][style].items() if k in allowed}
    if not weights:
        return res
    sym_by_num = _landscaping(symbols)
    groups = {k: [sym_by_num[n] for n in v if n in sym_by_num] for k, v in ext["groups"].items()}
    density = max(0.0, min(1.0, float(opts.get("exterior_density", opts.get("density", 0.5)))))
    out = []
    for g in res.grids:
        grounds = [f for f in g.filler if f["kind"] in ("ground", "rock") and f["w"] * f["h"] > 100]
        if not grounds or any(f["kind"] == "rock" for f in grounds if f["w"] * f["h"] > 100) and env == "underground" and g.index != 0:
            continue
        ground = max(grounds, key=lambda f: f["w"] * f["h"])
        gx0, gy0, gx1, gy1 = ground["x"] + 2, ground["y"] + 2, ground["x"] + ground["w"] - 2, ground["y"] + ground["h"] - 2
        gate = getattr(res.layout, "gate", {}) or {}
        yard = (gx0, gy0, gx1, gy1)                       # the whole open ground (fields may lie outside the fence)
        if gate.get("box"):                               # everything else stays inside the fence
            bx0, by0, bx1, by1 = gate["box"]
            gx0, gy0, gx1, gy1 = max(gx0, bx0 + 1), max(gy0, by0 + 1), min(gx1, bx1 - 1), min(gy1, by1 - 1)
        fence = (gx0, gy0, gx1, gy1)
        occ = set()

        def mark(x, y, w, h, margin=1):
            for cx in range(int(math.floor(x)) - margin, int(math.ceil(x + w)) + margin):
                for cy in range(int(math.floor(y)) - margin, int(math.ceil(y + h)) + margin):
                    occ.add((cx, cy))
        for p in g.placed:
            mark(p.x, p.y, p.w, p.h, 2)
        for f in g.filler:
            if f["kind"] in ("ground", "rock", "water", "dome"):
                continue
            mark(f["x"], f["y"], f["w"], f["h"], 1)
        ent = res.layout.entrance or {}
        if ent.get("level", 0) == g.index and "x" in ent:
            mark(ent["x"] - 3, ent["y"] - 3, 6, 6, 1)             # the approach to the entrance stays open
        if gate.get("x") is not None:
            mark(gate["x"] - 3, gate["y"] - 3, 10, 10, 0)
        near_building = {(c[0] + dx, c[1] + dy) for p in g.placed for c in p.cells() for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3))}

        def free(cx, cy, w, h):
            return all((x, y) not in occ for x in range(int(math.floor(cx - w / 2)) - 0, int(math.ceil(cx + w / 2)) + 0)
                       for y in range(int(math.floor(cy - h / 2)), int(math.ceil(cy + h / 2))))

        def place(sym, cx, cy, rot, gap=1, bounds=None):
            w, h = (sym.h, sym.w) if rot % 180 else (sym.w, sym.h)
            if rot % 90:
                w = h = max(sym.w, sym.h)
            bx0_, by0_, bx1_, by1_ = bounds or fence
            if cx - w / 2 < bx0_ or cy - h / 2 < by0_ or cx + w / 2 > bx1_ or cy + h / 2 > by1_:
                return False
            if not free(cx, cy, w, h):
                return False
            mark(cx - w / 2, cy - h / 2, w, h, gap)
            out.append({"sym": sym.id, "level": g.index, "cx": round(cx, 3), "cy": round(cy, 3), "rot": rot,
                        "flip": rng.random() < 0.5, "zone": "", "kind": "exterior", "cat": sym.cat})
            return True
        area = max(1, (gx1 - gx0) * (gy1 - gy0))
        counts = {"tree": area / 220, "grove": area / 900, "shrub": area / 90, "rock": area / 150, "stones": area / 600,
                  "bench": 3, "planter": 2, "hedge": area / 500, "fountain": 1, "field": area / 450}
        for kind, wgt in weights.items():
            pool = groups.get(kind) or []
            if not pool:
                continue
            n = int(round(counts[kind] * wgt * (0.3 + density * 1.4)))
            if kind == "fountain" and (density < 0.3 or style not in ("civil", "park")):
                n = 0
            for _ in range(max(0, n)):
                sym = rng.choice(pool)
                if kind in ("field", "grove", "fountain"):         # big pieces: search the whole yard for a place they fit
                    lim = yard if kind == "field" else fence      # farmland surrounds the compound
                    spots = [(x + 0.5, y + 0.5) for x in range(int(lim[0]), int(lim[2]), 2) for y in range(int(lim[1]), int(lim[3]), 2)]
                    rng.shuffle(spots)
                    if kind == "fountain":
                        spots.sort(key=lambda p: abs(p[0] - (gx0 + gx1) / 2) + abs(p[1] - (gy0 + gy1) / 2))
                    for cx, cy in spots[:600]:
                        rot = rng.choice((0, 90)) if kind == "field" else 0
                        if place(sym, cx + (sym.w / 2 - 0.5), cy + (sym.h / 2 - 0.5), rot, bounds=lim):
                            break
                    continue
                for _try in range(40):
                    cx, cy = rng.uniform(gx0, gx1), rng.uniform(gy0, gy1)
                    if kind == "shrub" and (int(cx), int(cy)) not in near_building:
                        continue                                    # shrubs hug the buildings
                    edge = min(cx - gx0, gx1 - cx, cy - gy0, gy1 - cy)
                    if kind in ("tree", "hedge") and edge > 9 and not (style == "park" and rng.random() < 0.3):
                        continue                                    # trees and hedges line the edges, the middle stays clear
                    if kind in ("bench", "planter") and ent and abs(cx - ent.get("x", cx)) + abs(cy - ent.get("y", cy)) > 14:
                        continue
                    if kind == "fountain":
                        cx, cy = (gx0 + gx1) / 2 + rng.uniform(-6, 6), (gy0 + gy1) / 2 + rng.uniform(-6, 6)
                    rot = rng.choice((0, 90)) if kind in ("bench", "planter", "hedge", "field", "stones") else rng.randrange(0, 360, 15)
                    if kind == "rock" and place(sym, cx, cy, rot):         # boulders come in small clusters
                        for _k in range(rng.randint(0, 2)):
                            place(rng.choice(pool), cx + rng.uniform(-1.6, 1.6), cy + rng.uniform(-1.6, 1.6), rng.randrange(0, 360, 15), 0)
                        break
                    if kind != "rock" and place(sym, cx, cy, rot):
                        break
    res.decor = list(getattr(res, "decor", []) or []) + out
    res.meta.setdefault("decor", {})["exterior"] = len(out)
    return res


REACH = {"short": 2, "medium": 3, "far": 5}


def spread_plan(res, rng, origin="random", reach="medium") -> dict | None:
    """Pick the source room and how many doors away every room is: {"origin": zone id, "dist": {zone: steps}, "reach": n}."""
    from . import validate
    adj = validate.zone_graph(res)
    rooms = [z for z in res.zones if z in adj and res.zones[z].base not in ("core",) and "vertical" not in res.zones[z].tags]
    if not rooms:
        return None
    ent = validate.entrance_zone_id(res)
    pool = []
    if origin == "entrance" and ent in adj:
        pool = [ent]
    elif origin not in ("random", "entrance", "", None):
        pool = [z for z in rooms if origin in res.zones[z].tags]
    if not pool:
        pool = [z for z in rooms if z != ent and len(adj[z]) >= 1] or rooms
    src = rng.choice(sorted(pool))
    dist = {src: 0}
    queue = [src]
    for z in queue:
        for n in sorted(adj[z]):
            if n not in dist:
                dist[n] = dist[z] + 1
                queue.append(n)
    return {"origin": src, "dist": dist, "reach": REACH.get(reach, int(reach) if str(reach).isdigit() else 3)}


def spread_kind(base: str, d: int | None, reach: int) -> str:
    """How bad it is d doors from the source: the full incident near it, a struggle further out, nothing beyond."""
    if d is None or d > reach or base == "none":
        return "none"
    if d <= reach // 2:
        return base
    return "struggle"


def _facing_sides(res, g, p, dist) -> set:
    """Sides of tile ``p`` whose doors open toward a room closer to the source."""
    mine = dist.get(p.zone)
    out = set()
    if mine is None:
        return out
    for side in "NESW":
        for i, c in enumerate(p.side_cls(side)):
            if c != 1:
                continue
            cx, cy = {"N": (p.x + i, p.y - 1), "S": (p.x + i, p.y + p.h), "W": (p.x - 1, p.y + i), "E": (p.x + p.w, p.y + i)}[side]
            other = g.occ.get((cx, cy))
            if other is not None and other.zone in dist and dist[other.zone] < mine:
                out.add(side)
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
    plan = spread_plan(res, rng, opts.get("origin", "random"), opts.get("reach", "medium")) \
        if dec.where == "spread" and dec.incident != "none" else None
    res.markers = [m for m in res.markers if m.get("label") != "Source of the outbreak"]
    if plan is not None:                                 # the GM can see where it all began
        src = plan["origin"]
        for g in res.grids:
            for p in g.placed:
                if p.zone == src:
                    res.markers.append({"type": "threat", "level": g.index, "x": p.x + p.w / 2.0, "y": p.y + p.h / 2.0,
                                        "gm_only": True, "label": "Source of the outbreak", "zone": src})
                    break
    # streets and campuses are outdoors: furnish only enclosed rooms there, never open halls or yards behind a door
    outdoors = res.layout is not None and res.layout.topology in ("street", "campus")
    chars = (".",) if outdoors else (".", "r")
    for g in res.grids:
        for p in g.placed:
            rows = floors.get(p.tile.id)
            if not rows or p.tile.type == "wing":
                continue
            grid = transform(decode(rows), p.o.rot, p.o.mirror)
            rects = free_rects(grid, chars=chars)
            hall = False
            if not rects and not outdoors:
                rects = hall_bands(grid)             # big open hall: line its walls, keep the middle clear
                hall = bool(rects)
            kind = dec.incident
            toward, nest, facing = None, False, set()
            if plan is not None:
                d = plan["dist"].get(p.zone)
                kind = spread_kind(dec.incident, d, plan["reach"])
                nest = d == 0
                facing = _facing_sides(res, g, p, plan["dist"])
                toward = sorted(facing)[0] if facing else None
                if not rects and kind != "none":          # a room with no free floor still shows it: mark its passages
                    for rect in free_rects(grid, min_side=3, limit=2, chars=(".", "r", "c")):
                        extra = []
                        dec._incident([], extra, kind, p, rect, g.index, None, toward, nest)
                        g.filler.extend(extra)
            if not rects:
                continue
            if plan is not None:
                pass
            elif kind != "none":
                if dec.where == "overlay" and p.zone not in hot:
                    kind = "none"
                elif dec.where == "random" and rng.random() > 0.3:
                    kind = "none"
            for rect in rects:
                z = res.zones.get(p.zone)
                if z is not None and (z.base == "core" or list(z.tags) == ["vertical"]):
                    continue                          # stair/lift cores are circulation: no furniture
                kits = symbol_kits()
                prefer = ([z.base] if z.base in kits else list(z.tags[:2])) if z is not None else []
                cond = (res.meta.get("zone_conditions") or {}).get(p.zone) or res.meta.get("condition", "Average")
                messy = kind != "none" or cond in ("Cluttered", "Scrap", "Derelict", "Disrepair")
                items, extra = dec.furnish(p.tile, p, rect, g.index, p.zone, kind, prefer, messy, toward, nest)
                for it in items:                      # trace each item to the tile it stands in
                    it["tile"], it["tx"], it["ty"] = p.tile.id, p.x, p.y
                    if hall:
                        it["hall"] = True
                out.extend(items)
                for f in extra:
                    g.filler.append(f)
                if plan is not None:                       # barricades only on the doors that face the source
                    if kind != "none" and not nest and facing:
                        for b in dec.barricade(p, g.index, p.zone, rect, sides=facing, all_doors=True):
                            b["tile"], b["tx"], b["ty"] = p.tile.id, p.x, p.y
                            out.append(b)
                elif kind == "overrun":
                    for b in dec.barricade(p, g.index, p.zone, rect):
                        b["tile"], b["tx"], b["ty"] = p.tile.id, p.x, p.y
                        out.append(b)
    res.decor = out
    res.meta["decor"] = {"items": len(out), "incident": dec.incident, "where": dec.where}
    if plan is not None:
        z = res.zones.get(plan["origin"])
        res.meta["decor"].update(origin=plan["origin"], origin_name=z.name if z is not None else plan["origin"],
                                 reach=plan["reach"], spread=dict(plan["dist"]))
    return res
