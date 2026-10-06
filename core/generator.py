"""Asset-driven map generation for small tiles and prebuilt geomorphs.

The tile-by-tile mode classifies small assets into semantic categories, lays
out connected rooms and corridors, and places fixtures on matching surfaces.
The geomorph mode aligns full-size modules, paired overlays, and symbols.
Pure logic (no Qt) so both generation paths can be unit-tested headlessly.

Tile-mode settings (Starship / Colony base / Research lab / Random) drive room
shapes, layout style, and fixture density; room connectivity is BFS-verified.
"""
from __future__ import annotations

import math
import os
import random
import re
import statistics
from typing import Optional

from core.project import parse_size_from_name

CATEGORIES = ["floor", "wall", "corridor", "door",
              "wall_fixture", "floor_fixture", "hazard"]

KEYWORDS = {
    "floor": ["floor", "deck", "deckplate", "tile", "grating", "carpet",
              "ground", "plate", "mat", "room", "pod", "laboratory"],
    "wall": ["wall", "bulkhead", "hull", "partition", "barrier"],
    "corridor": ["corridor", "hall", "hallway", "passage", "tunnel"],
    "door": ["door", "hatch", "airlock", "portal", "gate"],
    "wall_fixture": ["terminal", "computer", "console", "screen", "monitor",
                     "display", "panel", "switch", "controls", "keypad",
                     "intercom", "reactor", "valve", "gauge", "keyboard"],
    "floor_fixture": ["crate", "locker", "bed", "cryo", "cryopod", "table",
                      "chair", "barrel", "box", "pallet", "corpse", "body",
                      "canister", "shelf", "rack", "stool", "cabinet", "tube"],
    "hazard": ["spill", "hazard", "blood", "acid", "warning", "fire",
               "smoke", "biohazard", "slime"],
}

SETTINGS = ["Starship", "Colony base", "Research lab", "Random"]

# Per-setting generation personality.
_SETTING_PARAMS = {
    "Starship": dict(layout="corridors", rw=(3, 6), rh=(2, 5),
                     rooms_mult=1.0, wfix=0.18, ffix=0.10, hazard=0.45),
    "Colony base": dict(layout="organic", rw=(4, 8), rh=(4, 7),
                        rooms_mult=0.8, wfix=0.08, ffix=0.28, hazard=0.30),
    "Research lab": dict(layout="grid", rw=(3, 5), rh=(3, 5),
                         rooms_mult=1.2, wfix=0.22, ffix=0.12, hazard=0.55),
}


def _asset_text(asset) -> str:
    return "/".join((getattr(asset, "folder", ""), asset.name,
                     getattr(asset, "path", ""))).replace("\\", "/").lower()


def _asset_size(asset):
    size = getattr(asset, "size", None)
    if size and len(size) == 2 and size[0] > 0 and size[1] > 0:
        return tuple(size)
    return (parse_size_from_name(asset.name)
            or parse_size_from_name(_asset_text(asset)))


def _is_large_core_geomorph(asset) -> bool:
    """Keep full-size geomorph modules out of the one-cell tile classifier."""
    text = _asset_text(asset)
    size = _asset_size(asset)
    return ("core" in text and not _is_symbol_pack_asset(asset)
            and size in {(50, 50), (100, 100)}
            and max(getattr(asset, "width", 0), getattr(asset, "height", 0)) >= 500)


def _is_symbol_pack_asset(asset) -> bool:
    """Recognize Symbols folders/archive roots without matching mixed ZIP names."""
    for source in (getattr(asset, "folder", ""), getattr(asset, "path", "")):
        for part in source.replace("\\", "/").split("/"):
            label = os.path.splitext(part)[0].strip().casefold()
            if label == "symbol" or label.startswith("symbols"):
                return True
    return False


def _is_geomorph_overlay(asset) -> bool:
    name = asset.name.casefold()
    if re.search(r"\[\s*overlay\s*\]|\boverlays?\b", name):
        return True
    for source in (getattr(asset, "folder", ""), getattr(asset, "path", "")):
        parts = source.replace("\\", "/").split("/")
        for index, part in enumerate(parts):
            label = os.path.splitext(part)[0].strip().casefold()
            if ((label in {"overlay", "overlays"} and
                 (index > 0 or len(parts) == 1)) or
                    label.startswith(("overlay ", "overlays "))):
                return True
    return False


def classify_assets(assets) -> dict:
    """Return the small-tile taxonomy; full geomorphs use a separate mode."""
    cats = {c: [] for c in CATEGORIES}
    for a in assets:
        # Whole 100x100 / 50x50 deck plans must not be squeezed down to a
        # single cell by the legacy floor/wall generator. Symbols likewise
        # have their own size-aware scatter path in geomorph mode.
        if _is_large_core_geomorph(a) or _is_symbol_pack_asset(a):
            continue
        cat = _category(a.name)
        if cat and (a.width or a.height):
            cats[cat].append({"path": a.path, "name": a.name,
                              "w": a.width, "h": a.height})
    return cats


def _geomorph_id(name: str) -> str:
    """Canonicalize a tile filename so ``[Overlay]`` pairs with its base."""
    stem = os.path.splitext(name)[0].casefold()
    stem = re.sub(r"\[\s*overlay[^\]]*\]", " ", stem)
    stem = re.sub(r"\b\d+\s*x\s*\d+\b", " ", stem)
    stem = re.sub(r"\b(core|overlay)\b", " ", stem)
    return re.sub(r"[^a-z0-9]+", " ", stem).strip()


def classify_geomorph_assets(assets, feet_per_square: int = 5,
                             tile_feet: int = 100) -> dict:
    """Find full-size Core geomorphs, their paired overlays, and symbols.

    RPG Mobius 100x100 Core assets represent 20x20 five-foot squares with a
    two-square transparent border on every side. The source PNG's pixel density
    is measured from its filename dimensions and image header, so the same
    layout works at other project grid scales too.
    """
    feet_per_square = max(1, int(feet_per_square or 5))
    requested_size = (int(tile_feet), int(tile_feet))
    border_cells = max(0, round(10 / feet_per_square))
    core, overlays, symbols = [], {}, []
    for asset in assets:
        text = _asset_text(asset)
        size = _asset_size(asset)
        if not size or not asset.width or not asset.height:
            continue
        is_overlay = _is_geomorph_overlay(asset)
        in_core_group = "core" in text
        in_symbol_pack = _is_symbol_pack_asset(asset)
        is_large = max(asset.width, asset.height) >= 1000

        if (in_core_group and not in_symbol_pack and is_large
                and size == requested_size):
            cells_w = max(1, round(size[0] / feet_per_square))
            cells_h = max(1, round(size[1] / feet_per_square))
            item = {"path": asset.path, "name": asset.name,
                    "w": asset.width, "h": asset.height,
                    "feet_w": size[0], "feet_h": size[1],
                    "core_w": cells_w, "core_h": cells_h,
                    "border_cells": border_cells,
                    "id": _geomorph_id(asset.name)}
            if is_overlay:
                if item["id"]:
                    overlays.setdefault(item["id"], []).append(item)
            else:
                core.append(item)
            continue

        if in_symbol_pack and not is_overlay:
            name = asset.name.lower()
            if any(term in name for term in ("checkerboard", "grid template", "calibration")):
                continue
            symbols.append({"path": asset.path, "name": asset.name,
                            "w": asset.width, "h": asset.height,
                            "size": size})

    def item_density(item):
        total_w = item["core_w"] + 2 * item["border_cells"]
        total_h = item["core_h"] + 2 * item["border_cells"]
        return statistics.median((item["w"] / max(1, total_w),
                                  item["h"] / max(1, total_h)))

    # If both standard and high-resolution packs are present, favor the
    # highest-resolution Core set so modules and paired art stay in register.
    for item in core:
        item["pixels_per_square"] = item_density(item)
    preferred_density = max((item["pixels_per_square"] for item in core),
                            default=0.0)
    if preferred_density:
        core = [item for item in core
                if item["pixels_per_square"] >= preferred_density * 0.9]
    core.sort(key=lambda item: item["name"].casefold())
    core_ids = {item["id"] for item in core if item["id"]}
    unpaired_overlay_count = sum(
        len(pool) for key, pool in overlays.items() if key not in core_ids)
    selected_overlays = {}
    for key, pool in overlays.items():
        if key not in core_ids:
            continue
        compatible = [item for item in pool
                      if item_density(item) >= preferred_density * 0.9]
        unpaired_overlay_count += len(pool) - len(compatible)
        if compatible:
            selected_overlays[key] = sorted(
                compatible, key=lambda item: item["name"].casefold())
    overlays = selected_overlays

    # Prefer the largest duplicate of a symbol (archives may include both
    # standard and high-resolution copies), then scale each copy using its
    # own source density rather than assuming all imports match the Core DPI.
    largest_symbols = {}
    for item in symbols:
        key = item["name"].casefold()
        old = largest_symbols.get(key)
        if old is None or item["w"] * item["h"] > old["w"] * old["h"]:
            largest_symbols[key] = item
    symbols = list(largest_symbols.values())
    for item in symbols:
        item["pixels_per_square"] = preferred_density
        size = item.get("size")
        path_text = f"{item['path']} {item['name']}".casefold()
        if size and preferred_density:
            nominal_cells = max(1.0, size[0] / feet_per_square)
            observed = item["w"] / nominal_cells
            # The published high-res and standard packs differ by exactly 5x.
            if observed < preferred_density * 0.6:
                item["pixels_per_square"] = preferred_density / 5.0
        elif "highres" not in path_text and "high res" not in path_text:
            item["pixels_per_square"] = preferred_density
    symbols.sort(key=lambda item: item["name"].casefold())
    pixels_per_square = statistics.median(
        [item["pixels_per_square"] for item in core]) if core else 0.0
    return {"core": core, "overlays": overlays, "symbols": symbols,
            "pixels_per_square": pixels_per_square,
            "core_cells": (core[0]["core_w"], core[0]["core_h"]) if core else (0, 0),
            "border_cells": border_cells,
            "unpaired_overlay_count": unpaired_overlay_count}


def summarize(cats: dict) -> str:
    return ", ".join(f"{c}:{len(v)}" for c, v in cats.items() if v)


def _category(name: str) -> Optional[str]:
    n = name.lower()
    for cat in CATEGORIES:
        for kw in KEYWORDS[cat]:
            if kw in n:
                return cat
    return None


def resolve_setting(opts: dict, rng: random.Random) -> str:
    s = opts.get("setting") or "Random"
    if s not in SETTINGS or s == "Random":
        s = rng.choice(["Starship", "Colony base", "Research lab"])
    return s


def resolve_layout(opts: dict, params: dict, rng: random.Random) -> str:
    """Dialog passes display names ('Corridors', 'Grid', …); normalize to
    lowercase keys the layout engine understands. 'Random' defers to the
    setting's preferred style."""
    lay = (opts.get("layout") or "Random").strip().lower()
    if lay in ("random", ""):
        return params["layout"]
    if lay in ("corridors", "grid", "organic"):
        return "organic" if lay == "organic" else lay
    return params["layout"]


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
def _place_rooms(rng: random.Random, W, H, opts: dict, params: dict):
    n = max(1, int(opts.get("rooms", 6) * params.get("rooms_mult", 1.0)))
    layout = opts.get("layout", "random")
    lo_w, hi_w = params["rw"]
    lo_h, hi_h = params["rh"]
    # keep rooms inside the region (fixes IndexError on tiny areas)
    margin = 1 if (W >= 4 and H >= 4) else 0
    hi_w = max(1, min(hi_w, W - 2 * margin))
    lo_w = max(1, min(lo_w, hi_w))
    hi_h = max(1, min(hi_h, H - 2 * margin))
    lo_h = max(1, min(lo_h, hi_h))

    rooms = []
    attempts = 0
    while len(rooms) < n and attempts < 500:
        attempts += 1
        w = rng.randint(lo_w, hi_w)
        h = rng.randint(lo_h, hi_h)
        w = max(1, min(w, W - 2 * margin))
        h = max(1, min(h, H - 2 * margin))
        if layout == "corridors":
            if rooms and rng.random() < 0.75:
                prev = rooms[-1]
                x = prev[0] + prev[2] + rng.randint(3, 7)
                if x + w > W - margin:
                    x = rng.randint(margin, max(margin, W - w - margin))
                y = rng.randint(margin, max(margin, H - h - margin))
            else:
                x = rng.randint(margin, max(margin, W - w - margin))
                y = rng.randint(margin, max(margin, H - h - margin))
        elif layout == "grid":
            cols = max(1, int(math.sqrt(n)))
            i = len(rooms)
            gw = (W - 2 * margin) // cols
            rows = max(1, (n + cols - 1) // cols)
            gh = (H - 2 * margin) // rows
            if gw < lo_w or gh < lo_h:
                x = rng.randint(margin, max(margin, W - w - margin))
                y = rng.randint(margin, max(margin, H - h - margin))
            else:
                cx = (i % cols) * gw + margin
                cy = (i // cols) * gh + margin
                x = min(cx, W - w - margin)
                y = min(cy, H - h - margin)
                x = max(margin, x)
                y = max(margin, y)
        else:  # random / organic
            x = rng.randint(margin, max(margin, W - w - margin))
            y = rng.randint(margin, max(margin, H - h - margin))
        if x + w > W or y + h > H or x < 0 or y < 0:
            continue
        if all(_gap(r, (x, y, w, h)) for r in rooms):
            rooms.append((x, y, w, h))
    if not rooms and W >= 1 and H >= 1:
        # degenerate region: one room filling what we can
        rooms.append((margin, margin,
                      max(1, min(W - 2 * margin, W)),
                      max(1, min(H - 2 * margin, H))))
        rooms[0] = (max(0, rooms[0][0]), max(0, rooms[0][1]),
                    max(1, min(rooms[0][2], W - rooms[0][0])),
                    max(1, min(rooms[0][3], H - rooms[0][1])))
    return rooms


def _gap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax - 1 < bx + bw and ax + aw + 1 > bx and
                ay - 1 < by + bh and ay + ah + 1 > by)


def _carve(rng, grid, rooms, W, H):
    # fill rooms
    for (x, y, w, h) in rooms:
        for yy in range(y, min(y + h, H)):
            for xx in range(x, min(x + w, W)):
                grid[yy][xx] = 1
    # connect consecutive room centers with L corridors (spanning chain)
    for i in range(1, len(rooms)):
        a, b = rooms[i - 1], rooms[i]
        ax = min(max(a[0] + a[2] // 2, 0), W - 1)
        ay = min(max(a[1] + a[3] // 2, 0), H - 1)
        bx = min(max(b[0] + b[2] // 2, 0), W - 1)
        by = min(max(b[1] + b[3] // 2, 0), H - 1)
        x = ax
        step = 1 if bx > ax else -1
        while x != bx:
            grid[ay][x] = 1
            x += step
        y = ay
        step = 1 if by > ay else -1
        while y != by:
            grid[y][bx] = 1
            y += step
        grid[by][bx] = 1


def _walls(grid, W, H):
    for y in range(H):
        for x in range(W):
            if grid[y][x] == 0:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H and grid[ny][nx] == 1:
                        grid[y][x] = 2
                        break


def _doors(grid, W, H):
    for y in range(H):
        for x in range(W):
            if grid[y][x] == 2:
                floors = 0
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H and grid[ny][nx] == 1:
                        floors += 1
                if floors >= 2:
                    grid[y][x] = 3


def _repair_connectivity(grid, W, H):
    """BFS-verify; if any floor area is sealed off, carve a corridor from the
    largest component to each stray component (guarantees no sealed areas)."""
    def components():
        seen = [[False] * W for _ in range(H)]
        comps = []
        for y in range(H):
            for x in range(W):
                if grid[y][x] in (1, 3) and not seen[y][x]:
                    comp = []
                    stack = [(x, y)]
                    seen[y][x] = True
                    while stack:
                        cx, cy = stack.pop()
                        comp.append((cx, cy))
                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            nx, ny = cx + dx, cy + dy
                            if (0 <= nx < W and 0 <= ny < H
                                    and not seen[ny][nx]
                                    and grid[ny][nx] in (1, 3)):
                                seen[ny][nx] = True
                                stack.append((nx, ny))
                    comps.append(comp)
        comps.sort(key=len, reverse=True)
        return comps

    comps = components()
    repaired = 0
    while len(comps) > 1:
        main = comps[0]
        stray = comps[1]
        # nearest pair (centroid-to-centroid is enough for gameplay maps)
        mx = sum(c[0] for c in main) / len(main)
        my = sum(c[1] for c in main) / len(main)
        sx, sy = min(stray, key=lambda c: (c[0] - mx) ** 2 + (c[1] - my) ** 2)
        bx = int(round(mx))
        by = int(round(my))
        bx = min(max(bx, 0), W - 1)
        by = min(max(by, 0), H - 1)
        x = sx
        step = 1 if bx > sx else -1
        while x != bx:
            if grid[sy][x] == 0:
                grid[sy][x] = 1
            x += step
        y = sy
        step = 1 if by > sy else -1
        while y != by:
            if grid[y][bx] == 0:
                grid[y][bx] = 1
            y += step
        if grid[by][bx] == 0:
            grid[by][bx] = 1
        repaired += 1
        comps = components()
        if repaired > 60:
            break
    return repaired


def _connected(grid, W, H) -> bool:
    start = None
    total = 0
    for y in range(H):
        for x in range(W):
            if grid[y][x] in (1, 3):
                total += 1
                if start is None:
                    start = (x, y)
    if not start:
        return True
    seen = set([start])
    stack = [start]
    while stack:
        x, y = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and grid[ny][nx] in (1, 3) and (nx, ny) not in seen:
                seen.add((nx, ny))
                stack.append((nx, ny))
    return len(seen) == total


# --------------------------------------------------------------------------
# Piece helpers
# --------------------------------------------------------------------------
def _mk(a, px, py, scale, rot, layer_name):
    """Build a piece dict. x/y is the VISUAL top-left (Piece treats x/y as
    top-left of the scaled box), so the caller centers the scaled size."""
    w = a["w"] * scale
    h = a["h"] * scale
    return {"asset_path": a["path"], "name": a["name"], "x": px, "y": py,
            "w": a["w"], "h": a["h"], "scale": scale, "rotation": rot,
            "layer_name": layer_name, "snap": True, "opacity": 1.0,
            "_vw": w, "_vh": h}


def generate(opts: dict) -> dict:
    rng = random.Random(opts.get("seed", 1))
    cs = opts["cell_size"]
    x0, y0, x1, y1 = opts["region"]
    W = max(1, x1 - x0 + 1)
    H = max(1, y1 - y0 + 1)
    cats = opts["categories"]
    floor_pool = cats["floor"] + cats["corridor"]
    wall_pool = cats["wall"]
    door_pool = cats["door"] or floor_pool
    wfix = cats["wall_fixture"]
    ffix = cats["floor_fixture"]
    hz = cats["hazard"]
    clutter = max(0.0, min(1.0, opts.get("clutter", 0.3)))

    setting = resolve_setting(opts, rng)
    params = dict(_SETTING_PARAMS[setting])
    layout = resolve_layout(opts, params, rng)

    grid = [[0] * W for _ in range(H)]
    rooms = _place_rooms(rng, W, H, dict(opts, layout=layout), params)
    _carve(rng, grid, rooms, W, H)
    _walls(grid, W, H)
    _doors(grid, W, H)
    repairs = _repair_connectivity(grid, W, H)
    connected = _connected(grid, W, H)

    pieces = []
    door_cells = 0
    for y in range(H):
        for x in range(W):
            t = grid[y][x]
            if t == 1:
                pool = floor_pool
            elif t == 2:
                pool = wall_pool
            elif t == 3:
                pool = door_pool
                door_cells += 1
            else:
                continue
            if not pool:
                continue
            a = rng.choice(pool)
            scale = cs / max(a["w"], a["h"], 1)
            w = a["w"] * scale
            h = a["h"] * scale
            px = (x0 + x) * cs + (cs - w) / 2
            py = (y0 + y) * cs + (cs - h) / 2
            rot = rng.choice([0, 90, 180, 270]) if t != 2 else 0
            pieces.append(_mk(a, px, py, scale, rot, "Base"))

    # floor fixtures: inside room interiors, density from clutter + setting
    prob = clutter * (0.35 + params["ffix"])
    occupied: set[tuple[int, int]] = set()
    for (rx, ry, rw, rh) in rooms:
        for yy in range(ry + 1, ry + rh - 1):
            for xx in range(rx + 1, rx + rw - 1):
                if grid[yy][xx] != 1 or (xx, yy) in occupied:
                    continue
                if ffix and rng.random() < prob:
                    a = rng.choice(ffix)
                    scale = min(1.0, cs / max(a["w"], a["h"], 1))
                    w = a["w"] * scale
                    h = a["h"] * scale
                    px = (x0 + xx) * cs + (cs - w) / 2
                    py = (y0 + yy) * cs + (cs - h) / 2
                    pieces.append(_mk(a, px, py, scale, 0, "Props"))
                    occupied.add((xx, yy))

    # wall fixtures: on wall cells adjacent to floor (never on the floor)
    for y in range(H):
        for x in range(W):
            if grid[y][x] != 2:
                continue
            adj_floor = False
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < H and grid[ny][nx] == 1:
                    adj_floor = True
                    break
            if adj_floor and wfix and rng.random() < params["wfix"]:
                a = rng.choice(wfix)
                scale = min(1.0, cs / max(a["w"], a["h"], 1))
                w = a["w"] * scale
                h = a["h"] * scale
                px = (x0 + x) * cs + (cs - w) / 2
                py = (y0 + y) * cs + (cs - h) / 2
                pieces.append(_mk(a, px, py, scale, 0, "Props"))

    # occasional hazard on floors
    if hz:
        for (rx, ry, rw, rh) in rooms:
            if rng.random() < params["hazard"]:
                yy = rng.randint(ry, min(ry + rh - 1, H - 1))
                xx = rng.randint(rx, min(rx + rw - 1, W - 1))
                if 0 <= yy < H and 0 <= xx < W and grid[yy][xx] == 1:
                    a = rng.choice(hz)
                    scale = min(1.0, cs / max(a["w"], a["h"], 1))
                    w = a["w"] * scale
                    h = a["h"] * scale
                    px = (x0 + xx) * cs + (cs - w) / 2
                    py = (y0 + yy) * cs + (cs - h) / 2
                    pieces.append(_mk(a, px, py, scale, 0, "Overlay"))

    warnings = []
    if not floor_pool:
        warnings.append("No floor/room tiles found — map may be empty.")
    if not wall_pool:
        warnings.append("No wall tiles found.")
    if not connected:
        warnings.append("Some areas are unreachable (connectivity check failed).")
    if repairs:
        warnings.append(f"Auto-connected {repairs} isolated area(s) with "
                        f"spanning corridors (BFS-verified).")
    for d in pieces:
        d.pop("_vw", None)
        d.pop("_vh", None)
    return {"pieces": pieces, "seed": opts.get("seed", 1),
            "setting": setting, "layout": layout,
            "counts": {"rooms": len(rooms), "pieces": len(pieces),
                       "doors": door_cells, "repairs": repairs},
            "warnings": warnings, "connected": connected}


def _geomorph_scale(asset: dict, cell_size: float) -> float:
    """Scale a source geomorph so one high-res source square equals one map cell."""
    border = int(asset.get("border_cells", 2))
    px_per_cell_x = asset["w"] / max(1, asset["core_w"] + 2 * border)
    px_per_cell_y = asset["h"] / max(1, asset["core_h"] + 2 * border)
    pixels_per_cell = max(1.0, (px_per_cell_x + px_per_cell_y) / 2.0)
    return float(cell_size) / pixels_per_cell


def _place_geomorph_symbols(rng, symbols, pieces, *, x0, y0, cols, rows,
                            core_w, core_h, cell_size, pixels_per_square,
                            density, module_count):
    """Sprinkle small high-resolution Symbols assets without symbol-symbol overlap."""
    if not symbols or pixels_per_square <= 0 or density <= 0:
        return 0
    map_w = cols * core_w
    map_h = rows * core_h
    pool = []
    for asset in symbols:
        source_density = float(asset.get("pixels_per_square", pixels_per_square))
        if source_density <= 0:
            continue
        asset_w = asset["w"] / source_density
        asset_h = asset["h"] / source_density
        if asset_w <= max(map_w, map_h) and asset_h <= max(map_w, map_h):
            pool.append(asset)
    if not pool:
        return 0
    target = min(60, int(round(module_count * 3.0 * density)))
    if target <= 0:
        return 0

    occupied = []
    placed = 0
    attempts = 0
    max_attempts = max(30, target * 20)
    while placed < target and attempts < max_attempts:
        attempts += 1
        asset = rng.choice(pool)
        source_density = max(1.0, float(
            asset.get("pixels_per_square", pixels_per_square)))
        scale = float(cell_size) / source_density
        rotation = rng.choice((0, 90, 180, 270))
        raw_w = asset["w"] * scale
        raw_h = asset["h"] * scale
        box_w, box_h = ((raw_h, raw_w) if rotation in (90, 270)
                        else (raw_w, raw_h))
        width_cells = box_w / cell_size
        height_cells = box_h / cell_size
        if width_cells > map_w or height_cells > map_h:
            continue
        left = rng.uniform(0, max(0.0, map_w - width_cells))
        top = rng.uniform(0, max(0.0, map_h - height_cells))
        right, bottom = left + width_cells, top + height_cells
        # Leave a small gap so repeated labels and icons do not pile into one
        # unreadable cluster. The map artwork beneath them remains editable.
        if any(not (right + 0.15 <= ox or left >= ox2 + 0.15 or
                    bottom + 0.15 <= oy or top >= oy2 + 0.15)
               for ox, oy, ox2, oy2 in occupied):
            continue
        center_x = (x0 + left + width_cells / 2.0) * cell_size
        center_y = (y0 + top + height_cells / 2.0) * cell_size
        px = center_x - raw_w / 2.0
        py = center_y - raw_h / 2.0
        pieces.append(_mk(asset, px, py, scale, rotation, "Symbols"))
        occupied.append((left, top, right, bottom))
        placed += 1
    return placed


def generate_geomorphs(opts: dict) -> dict:
    """Assemble compatible 100x100 Core modules, paired overlays, and symbols."""
    rng = random.Random(opts.get("seed", 1))
    cs = max(1, float(opts.get("cell_size", 70)))
    x0, y0, x1, y1 = opts.get("region", (0, 0, 59, 59))
    region_w = max(0, int(x1 - x0 + 1))
    region_h = max(0, int(y1 - y0 + 1))
    categories = opts.get("geomorph_categories", {})
    core_pool = list(categories.get("core", []))
    overlay_pools = categories.get("overlays", {})
    symbol_pool = list(categories.get("symbols", []))
    warnings = []
    if not core_pool:
        return {"pieces": [], "seed": opts.get("seed", 1),
                "setting": "Geomorphs", "layout": "Grid", "mode": "geomorph",
                "counts": {"pieces": 0, "geomorphs": 0, "overlays": 0,
                           "symbols": 0, "rooms": 0},
                "warnings": ["No 100x100 Core geomorphs found. Import the "
                             "Geomorphs or Custom Tiles ZIP first."],
                "connected": False}

    first = core_pool[0]
    core_w = max(1, int(first.get("core_w", 20)))
    core_h = max(1, int(first.get("core_h", 20)))
    border = max(0, int(categories.get("border_cells", 2)))
    requested = opts.get("geomorph_grid", 3)
    if isinstance(requested, (tuple, list)) and len(requested) >= 2:
        requested_cols, requested_rows = int(requested[0]), int(requested[1])
    else:
        requested_cols = requested_rows = int(requested or 3)
    requested_cols = max(1, min(8, requested_cols))
    requested_rows = max(1, min(8, requested_rows))

    cols = min(requested_cols, region_w // core_w)
    rows = min(requested_rows, region_h // core_h)
    if opts.get("mode") != "area":
        cols, rows = requested_cols, requested_rows
    if cols < requested_cols or rows < requested_rows:
        if cols < 1 or rows < 1:
            warnings.append(
                f"The selected area must fit at least one {core_w}x{core_h}-square geomorph.")
            return {"pieces": [], "seed": opts.get("seed", 1),
                    "setting": "Geomorphs", "layout": "Grid", "mode": "geomorph",
                    "counts": {"pieces": 0, "geomorphs": 0, "overlays": 0,
                               "symbols": 0, "rooms": 0},
                    "warnings": warnings, "connected": False}
        warnings.append(f"Placed a {cols}x{rows} module grid to fit the available area.")

    tile_count = cols * rows
    if len(core_pool) >= tile_count:
        chosen = rng.sample(core_pool, tile_count)
    else:
        chosen = list(core_pool)
        chosen.extend(rng.choice(core_pool) for _ in range(tile_count - len(chosen)))
        rng.shuffle(chosen)

    density = max(0.0, min(1.0, float(opts.get("clutter", 0.35))))
    pieces = []
    overlay_count = 0
    module_index = 0
    for row in range(rows):
        for col in range(cols):
            asset = chosen[module_index]
            module_index += 1
            rotation = rng.choice((0, 90, 180, 270))
            px = (x0 + col * core_w - border) * cs
            py = (y0 + row * core_h - border) * cs
            scale = _geomorph_scale(asset, cs)
            pieces.append(_mk(asset, px, py, scale, rotation, "Geomorphs"))

            matches = overlay_pools.get(asset.get("id", ""), [])
            if matches and rng.random() < density:
                overlay = rng.choice(matches)
                # The paired layer is registered to its base: identical origin,
                # source scale, and rotation preserve the two-square gutter.
                pieces.append(_mk(overlay, px, py, scale, rotation,
                                  "Overlays"))
                overlay_count += 1

    pixels_per_square = float(categories.get("pixels_per_square", 0.0))
    symbol_count = _place_geomorph_symbols(
        rng, symbol_pool, pieces, x0=x0, y0=y0, cols=cols, rows=rows,
        core_w=core_w, core_h=core_h, cell_size=cs,
        pixels_per_square=pixels_per_square, density=density,
        module_count=tile_count)

    for piece in pieces:
        piece.pop("_vw", None)
        piece.pop("_vh", None)
    return {"pieces": pieces, "seed": opts.get("seed", 1),
            "setting": "Geomorphs", "layout": f"{cols}x{rows} grid",
            "mode": "geomorph",
            "counts": {"pieces": len(pieces), "geomorphs": tile_count,
                       "overlays": overlay_count, "symbols": symbol_count,
                       "rooms": 0},
            "warnings": warnings, "connected": None}
