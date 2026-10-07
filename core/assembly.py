"""Role-driven map building (pure logic, no Qt).

Three strategies share one set of asset *roles* (core/asset_roles.py):

* ``assembly``  - pack deck plans, rooms and empty rooms into a map (uniform
  grid or a mixed-size packing), optionally with hull parts and overlays.
* ``tiles``     - the classic floor/wall/door tile-by-tile builder.
* ``furnish``   - drop interior parts into existing rooms.

``generate_map`` dispatches on ``opts["strategy"]``.
"""
from __future__ import annotations

import math
import random
import re
import statistics
from collections import Counter
from functools import reduce

from core import generator as gen
from core.seeds import coerce_seed

STRATEGIES = ("assembly", "tiles", "furnish")
MODULE_ROLES = ("deck_plan", "room", "empty_room")
DEFAULT_BORDER = 2          # transparent cells around Mobius-style modules
MAX_BORDER = 8


# ---------------------------------------------------------------------------
# Pools
# ---------------------------------------------------------------------------
def _item(asset, role, feet_per_square):
    size = gen._asset_size(asset) or (0, 0)
    fw, fh = size
    return {"path": asset.path, "name": asset.name,
            "w": int(asset.width or 0), "h": int(asset.height or 0),
            "feet_w": fw, "feet_h": fh, "role": role,
            "id": gen._geomorph_id(asset.name),
            "cells_w": max(1, round(fw / feet_per_square)) if fw else 0,
            "cells_h": max(1, round(fh / feet_per_square)) if fh else 0}


def build_pools(assets, roles, feet_per_square: int = 5) -> dict:
    """Group assets by role and work out the pixel density shared by modules.

    ``roles`` maps asset path -> RoleInfo (core.asset_roles.classify_roles).
    """
    fps = max(1, int(feet_per_square or 5))
    modules = {role: [] for role in MODULE_ROLES}
    ship, overlays, symbols, parts = [], [], [], []
    for asset in assets:
        info = roles.get(asset.path)
        if info is None or not asset.width or not asset.height:
            continue
        role = info.role
        item = _item(asset, role, fps)
        if role in modules and item["feet_w"] and max(asset.width, asset.height) >= 700:
            modules[role].append(item)
        elif role == "ship_part" and item["feet_w"] and max(asset.width, asset.height) >= 700:
            ship.append(item)
        elif role == "overlay" and item["feet_w"]:
            overlays.append(item)
        elif role == "symbol":
            symbols.append(item)
        elif role == "interior_part":
            parts.append(item)

    # Pixels per grid square. 100x100 ft modules are the reference (20x20
    # squares plus a 2-square transparent border on every side); other sizes
    # fall back to the same border assumption.
    def density_of(item):
        return item["w"] / max(1, item["cells_w"] + 2 * DEFAULT_BORDER)

    all_modules = [it for pool in modules.values() for it in pool] + ship
    reference = [it for it in all_modules if (it["feet_w"], it["feet_h"]) == (100, 100)]
    sample = reference or all_modules
    density = 0.0
    if sample:
        top = max(density_of(it) for it in sample)
        density = statistics.median(
            [density_of(it) for it in sample if density_of(it) >= top * 0.75])
    # drop modules from a clearly lower-resolution pack so everything stays in register
    if density:
        for role in list(modules):
            modules[role] = [it for it in modules[role]
                             if density_of(it) >= density * 0.6]
        ship = [it for it in ship if density_of(it) >= density * 0.6]
    for pool in list(modules.values()) + [ship]:
        for it in pool:
            _fit_borders(it, density)
    overlay_ids = {}
    base_by_id = {it["id"]: it for pool in modules.values() for it in pool if it["id"]}
    for it in overlays:
        base = base_by_id.get(it["id"])
        if base and abs(it["w"] - base["w"]) <= 0.03 * base["w"]:
            overlay_ids.setdefault(it["id"], []).append(it)
    for it in symbols:
        it["pixels_per_square"] = density
        text = f"{it['path']} {it['name']}".casefold()
        if density and "highres" not in text and "high res" not in text:
            it["pixels_per_square"] = density / 5.0
    for it in parts:
        text = f"{it['path']} {it['name']}".casefold()
        it["pixels_per_square"] = density if (
            density and ("highres" in text or "high res" in text)) else (
            density / 5.0 if density else 0.0)
    return {"modules": modules, "ship": ship, "overlays": overlay_ids,
            "symbols": symbols, "parts": parts,
            "pixels_per_square": density,
            "counts": {**{role: len(pool) for role, pool in modules.items()},
                       "ship_part": len(ship), "symbol": len(symbols),
                       "interior_part": len(parts),
                       "overlay": sum(len(v) for v in overlay_ids.values())}}


def _fit_borders(item, density):
    """Transparent border (in squares) on each side, from the image size."""
    item["density"] = density
    if not density:
        item["border_x"] = item["border_y"] = float(DEFAULT_BORDER)
        return
    for axis, px, cells in (("border_x", item["w"], item["cells_w"]),
                            ("border_y", item["h"], item["cells_h"])):
        extra = (px / density - cells) / 2.0
        item[axis] = round(min(MAX_BORDER, max(0.0, extra)) * 4) / 4.0


def categories_from_roles(assets, roles) -> dict:
    """Small-tile pools for the tile-by-tile builder, from roles."""
    cats = {c: [] for c in gen.CATEGORIES}
    for asset in assets:
        info = roles.get(asset.path)
        if info is None or not (asset.width or asset.height):
            continue
        role = info.role
        target = None
        if role == "floor_tile":
            target = "floor"
        elif role == "wall_tile":
            target = "wall"
        elif role == "corridor":
            target = "corridor"
        elif role == "door":
            target = "door"
        elif role == "interior_part":
            cat = gen._category(asset.name)
            target = "wall_fixture" if cat == "wall_fixture" else "floor_fixture"
        elif role == "overlay" and gen._category(asset.name) == "hazard":
            target = "hazard"
        if target:
            cats[target].append({"path": asset.path, "name": asset.name,
                                 "w": asset.width, "h": asset.height})
    return cats


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def _footprint(item, rotation):
    cw, ch = item["cells_w"], item["cells_h"]
    return (ch, cw) if rotation in (90, 270) else (cw, ch)


def _module_piece(item, left, top, rotation, cs, density, layer):
    """Piece for a module whose *core* rectangle's top-left is (left, top) in
    squares. The art is centered on the core, so rotating about its center
    keeps it in register."""
    fw, fh = _footprint(item, rotation)
    scale = cs / max(1.0, density)
    img_w, img_h = item["w"] * scale, item["h"] * scale
    cx, cy = (left + fw / 2.0) * cs, (top + fh / 2.0) * cs
    return gen._mk(item, cx - img_w / 2.0, cy - img_h / 2.0, scale, rotation, layer)


def _gcd_unit(items) -> int:
    values = [v for it in items for v in (it["cells_w"], it["cells_h"]) if v]
    return max(1, reduce(math.gcd, values)) if values else 1


def _pack(rng, pool_by_role, enabled, weights, width, height, *,
          packing, rotate):
    """Place modules on a unit grid. Returns [(item, unit_x, unit_y, rotation)]
    plus the unit size in squares."""
    candidates = [it for role in enabled for it in pool_by_role[role]]
    if packing == "uniform":
        families = Counter(tuple(sorted((it["cells_w"], it["cells_h"])))
                           for it in candidates)
        if families:
            base = max(families, key=lambda k: (families[k], k[0] * k[1]))
            for role in enabled:
                pool_by_role = dict(pool_by_role)
                pool_by_role[role] = [
                    it for it in pool_by_role[role]
                    if tuple(sorted((it["cells_w"], it["cells_h"]))) == base]
            candidates = [it for role in enabled for it in pool_by_role[role]]
    unit = _gcd_unit(candidates)
    ucols, urows = width // unit, height // unit
    if ucols < 1 or urows < 1 or not candidates:
        return [], unit, 0, 0
    taken = [[False] * ucols for _ in range(urows)]
    placements = []

    def fits(ux, uy, uw, uh):
        if ux + uw > ucols or uy + uh > urows:
            return False
        return all(not taken[y][x] for y in range(uy, uy + uh)
                   for x in range(ux, ux + uw))

    for uy in range(urows):
        for ux in range(ucols):
            if taken[uy][ux]:
                continue
            options = {}
            for role in enabled:
                for item in pool_by_role[role]:
                    square = item["cells_w"] == item["cells_h"]
                    if packing == "uniform":
                        spins = (0, 90, 180, 270) if (rotate and square) else (
                            (0, 180) if rotate else (0,))
                    else:
                        spins = (0, 90, 180, 270) if rotate else (0, 180)
                    for spin in spins:
                        fw, fh = _footprint(item, spin)
                        uw, uh = -(-fw // unit), -(-fh // unit)
                        if fits(ux, uy, uw, uh):
                            options.setdefault(role, []).append((item, spin, uw, uh))
            if not options:
                taken[uy][ux] = True        # too small a gap for any module
                continue
            roles_here = list(options)
            w = [max(0.0, weights.get(r, 1.0)) for r in roles_here]
            if sum(w) <= 0:
                w = [1.0] * len(roles_here)
            role = rng.choices(roles_here, weights=w)[0]
            item, spin, uw, uh = rng.choice(options[role])
            for y in range(uy, uy + uh):
                for x in range(ux, ux + uw):
                    taken[y][x] = True
            placements.append((item, ux, uy, spin))
    return placements, unit, ucols, urows


def _ship_side(item):
    words = set(re.findall(r"[a-z0-9]+", item["name"].casefold()))
    if "nose" in words:
        return "nose"
    if "starboard" in words:
        return "starboard"
    if "port" in words:
        return "port"
    return None


def _ship_family(item):
    match = re.search(r"\(([^)]*)\)", item["name"])
    return match.group(1).strip().casefold() if match else ""


def _choose_hull(rng, ship_items):
    """Pick a nose plus a matching port/starboard pair (same ship family)."""
    sides = {"nose": [], "port": [], "starboard": []}
    for item in ship_items:
        side = _ship_side(item)
        if side:
            sides[side].append(item)
    chosen = {}
    families = sorted({_ship_family(i) for i in sides["port"]} &
                      {_ship_family(i) for i in sides["starboard"]})
    family = rng.choice(families) if families else None
    for side in ("port", "starboard"):
        pool = [i for i in sides[side]
                if family is None or _ship_family(i) == family] or sides[side]
        if pool:
            chosen[side] = rng.choice(pool)
    if sides["nose"]:
        chosen["nose"] = rng.choice(sides["nose"])
    return chosen


def generate_assembly(opts: dict) -> dict:
    """Pack modules into a map. See module docstring and the opts used below."""
    seed = opts.get("seed", 1)
    rng = random.Random(coerce_seed(seed))
    cs = max(1.0, float(opts.get("cell_size", 70)))
    pools = opts.get("pools") or {}
    modules = pools.get("modules", {})
    density = float(pools.get("pixels_per_square", 0.0))
    x0, y0, x1, y1 = opts.get("region", (0, 0, 59, 59))
    width, height = max(0, x1 - x0 + 1), max(0, y1 - y0 + 1)
    warnings = []

    def empty(message):
        return {"pieces": [], "seed": seed, "setting": "Assembly",
                "layout": "", "mode": "assembly", "connected": None,
                "counts": {"pieces": 0, "modules": 0, "overlays": 0,
                           "symbols": 0, "hull": 0, "rooms": 0},
                "warnings": [message]}

    enabled = [r for r in opts.get("roles", MODULE_ROLES) if modules.get(r)]
    if not enabled or not density:
        return empty("No modules to assemble. Import deck plans or rooms, or "
                     "tick more ingredient types.")
    empty_share = max(0.0, min(1.0, float(opts.get("empty_share", 0.15))))
    weights = {"deck_plan": 1.0, "room": 1.0, "empty_room": empty_share * 2}
    if "empty_room" in enabled and len(enabled) > 1:
        weights["deck_plan"] = weights["room"] = max(0.05, 1.0 - empty_share)
    packing = opts.get("packing", "mixed")
    rotate = bool(opts.get("rotate", True))

    # hull parts surround the interior; the canvas grows to hold them
    hull = _choose_hull(rng, pools.get("ship", [])) if opts.get("hull") else {}
    margin_l = max(0, math.ceil(hull["port"]["cells_w"])) if "port" in hull else 0
    margin_r = max(0, math.ceil(hull["starboard"]["cells_w"])) if "starboard" in hull else 0
    margin_t = max(0, math.ceil(hull["nose"]["cells_h"])) if "nose" in hull else 0
    if opts.get("hull") and not hull:
        warnings.append("No nose / port / starboard hull parts were found "
                        "(check the Ship parts role).")
    ix0, iy0 = x0 + margin_l, y0 + margin_t

    placements, unit, ucols, urows = _pack(
        rng, modules, enabled, weights, width, height,
        packing=packing, rotate=rotate)
    if not placements:
        return empty(f"The area ({width}x{height} squares) is smaller than every "
                     "available module. Make the map larger.")

    pieces, module_count, overlay_count = [], 0, 0
    overlay_chance = max(0.0, min(1.0, float(opts.get("overlay_density", 0.35))))
    overlays = pools.get("overlays", {})
    used_w = max(ux * unit + _footprint(it, sp)[0] for it, ux, uy, sp in placements)
    used_h = max(uy * unit + _footprint(it, sp)[1] for it, ux, uy, sp in placements)
    for item, ux, uy, spin in placements:
        left, top = ix0 + ux * unit, iy0 + uy * unit
        piece = _module_piece(item, left, top, spin, cs, density, "Geomorphs")
        pieces.append(piece)
        module_count += 1
        matches = overlays.get(item.get("id", ""), [])
        if matches and rng.random() < overlay_chance:
            ov = rng.choice(matches)
            pieces.append(gen._mk(ov, piece["x"], piece["y"], piece["scale"],
                                  spin, "Overlays"))
            overlay_count += 1

    hull_count = 0
    if hull:
        if "nose" in hull:
            it = hull["nose"]
            left = ix0 + (used_w - it["cells_w"]) / 2.0
            pieces.append(_module_piece(it, left, y0, 0, cs, density, "Hull"))
            hull_count += 1
        for side, left in (("port", x0), ("starboard", ix0 + used_w)):
            if side in hull:
                it = hull[side]
                top = iy0 + (used_h - it["cells_h"]) / 2.0
                pieces.append(_module_piece(it, left, top, 0, cs, density, "Hull"))
                hull_count += 1

    symbol_count = 0
    sym_density = max(0.0, min(1.0, float(opts.get("symbol_density", 0.0))))
    if sym_density and pools.get("symbols"):
        symbol_count = gen._place_geomorph_symbols(
            rng, pools["symbols"], pieces, x0=ix0, y0=iy0, cols=1, rows=1,
            core_w=used_w, core_h=used_h, cell_size=cs,
            pixels_per_square=density, density=sym_density,
            module_count=module_count)
    for piece in pieces:
        piece.pop("_vw", None)
        piece.pop("_vh", None)

    total_w, total_h = margin_l + width + margin_r, margin_t + height
    if hull and any(s in hull for s in ("nose", "port", "starboard")):
        total_h = max(total_h, margin_t + used_h)
    covered = sum(_footprint(it, sp)[0] * _footprint(it, sp)[1]
                  for it, _x, _y, sp in placements)
    if covered < 0.85 * width * height:
        warnings.append(
            f"Modules fill {round(100 * covered / (width * height))}% of the area; "
            "the rest is too small for any module. Resize the map to a multiple "
            f"of {unit} squares for a tight fit.")
    return {"pieces": pieces, "seed": seed, "setting": "Assembly",
            "layout": f"{packing} packing", "mode": "assembly",
            "counts": {"pieces": len(pieces), "modules": module_count,
                       "overlays": overlay_count, "symbols": symbol_count,
                       "hull": hull_count, "rooms": module_count},
            "warnings": warnings, "connected": None,
            "canvas_cells": (total_w, total_h),
            "interior_origin": (margin_l, margin_t)}


# ---------------------------------------------------------------------------
# Furnishing
# ---------------------------------------------------------------------------
def furnish_rooms(opts: dict) -> dict:
    """Scatter interior parts inside room rectangles.

    ``opts["targets"]`` is a list of {"x","y","w","h"} rectangles in world
    pixels (the visible bounds of the rooms to furnish).
    """
    seed = opts.get("seed", 1)
    rng = random.Random(coerce_seed(seed))
    cs = max(1.0, float(opts.get("cell_size", 70)))
    pools = opts.get("pools") or {}
    parts = list(pools.get("parts", []))
    density = float(pools.get("pixels_per_square", 0.0))
    targets = list(opts.get("targets", []))
    margin = max(0.0, float(opts.get("wall_margin", 1.0)))
    per_100 = max(0.0, float(opts.get("furnish_density", 0.5))) * 12.0
    gap = max(0.0, float(opts.get("part_gap", 0.2)))
    fail = lambda msg: {"pieces": [], "seed": seed, "setting": "Furnish",
                        "layout": "", "mode": "furnish", "connected": None,
                        "counts": {"pieces": 0, "rooms": 0},
                        "warnings": [msg]}
    if not parts:
        return fail("No interior parts to place. Import furniture and equipment, "
                    "or sort some assets as Interior parts.")
    if not targets:
        return fail("There are no rooms to furnish. Select room nodes, or choose "
                    "'every empty room on this level'.")

    def part_cells(item):
        ppsq = item.get("pixels_per_square") or 0.0
        if ppsq and item["w"] >= 700:
            scale = cs / ppsq
        else:
            scale = min(1.0, cs / max(item["w"], item["h"], 1))
        return scale, item["w"] * scale / cs, item["h"] * scale / cs

    pieces, furnished = [], 0
    for target in targets:
        left = target["x"] / cs + margin
        top = target["y"] / cs + margin
        right = (target["x"] + target["w"]) / cs - margin
        bottom = (target["y"] + target["h"]) / cs - margin
        room_w, room_h = right - left, bottom - top
        if room_w < 1 or room_h < 1:
            continue
        count = max(1, round(room_w * room_h / 100.0 * per_100))
        placed = []
        attempts = 0
        while len(placed) < count and attempts < count * 25:
            attempts += 1
            item = rng.choice(parts)
            scale, pw, ph = part_cells(item)
            spin = rng.choice((0, 90, 180, 270))
            bw, bh = (ph, pw) if spin in (90, 270) else (pw, ph)
            if bw > room_w or bh > room_h:
                continue
            px = rng.uniform(left, right - bw)
            py = rng.uniform(top, bottom - bh)
            box = (px - gap, py - gap, px + bw + gap, py + bh + gap)
            if any(not (box[2] <= o[0] or box[0] >= o[2] or box[3] <= o[1] or box[1] >= o[3])
                   for o in placed):
                continue
            placed.append(box)
            cx, cy = (px + bw / 2.0) * cs, (py + bh / 2.0) * cs
            w_px, h_px = item["w"] * scale, item["h"] * scale
            pieces.append(gen._mk(item, cx - w_px / 2.0, cy - h_px / 2.0, scale,
                                  spin, "Props"))
        if placed:
            furnished += 1
    for piece in pieces:
        piece.pop("_vw", None)
        piece.pop("_vh", None)
    warnings = []
    if not pieces:
        warnings.append("Nothing fit. The rooms may be too small, or the parts too large.")
    return {"pieces": pieces, "seed": seed, "setting": "Furnish", "layout": "",
            "mode": "furnish", "connected": None,
            "counts": {"pieces": len(pieces), "rooms": furnished},
            "warnings": warnings}


def generate_map(opts: dict) -> dict:
    """Run the strategy named by ``opts["strategy"]``."""
    strategy = opts.get("strategy", "assembly")
    if strategy == "tiles":
        return gen.generate(opts)
    if strategy == "furnish":
        return furnish_rooms(opts)
    return generate_assembly(opts)
