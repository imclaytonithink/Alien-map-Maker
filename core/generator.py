"""Themed map generator (ALIENS RPG).

Classifies imported assets by filename into semantic categories, then lays out
connected rooms + corridors and places fixtures on the correct surfaces.
Pure logic (no Qt) so it can be unit-tested headlessly; the UI passes the
taxonomy in and gets back a list of piece dicts ready for Project.

Settings (Starship / Colony base / Research lab / Random) drive room shapes,
layout style and fixture densities. Every room is connected by a spanning
corridor chain and the result is BFS-verified — no sealed areas.
"""
from __future__ import annotations

import math
import random
from typing import Optional

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


def classify_assets(assets) -> dict:
    """Return {category: [{"path","name","w","h"}, ...]}."""
    cats = {c: [] for c in CATEGORIES}
    for a in assets:
        cat = _category(a.name)
        if cat and (a.width or a.height):
            cats[cat].append({"path": a.path, "name": a.name,
                              "w": a.width, "h": a.height})
    return cats


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
