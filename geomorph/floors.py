"""Floor maps: which parts of a tile are open room floor (where loose symbols may go).

Tile floors are transparent and walls, furniture and machines are opaque. At half-square resolution each cell is
one of ``#`` (wall/furniture), ``c`` (open floor connected to the tile edge: corridors and the outside) or ``.``
(enclosed room floor, reachable only through a door). Decor goes only on ``.`` so corridors stay clear.
"""
from __future__ import annotations

import re
from collections import deque

from PIL import Image

from .registry import BORDER_SQUARES, PX_PER_SQUARE

Image.MAX_IMAGE_PIXELS = None
SUB = 2                    # cells per grid square (half-square resolution)
WING_SOLID_AT = 0.3         # wings: their corridors are narrow, so only well-filled cells are solid
SOLID_AT = 0.002           # fraction of opaque pixels that makes a cell solid (catches thin dashed outlines too)


def analyse_floor(path, w, h, edges=None, pps=PX_PER_SQUARE, border=BORDER_SQUARES, joins="", solid_at=SOLID_AT):
    """Rows of the floor map (strings of '#', 'c', '.'), ``h*SUB`` rows of ``w*SUB`` chars.

    ``edges`` (the tile's side data) tells where the doors are: open floor connected to a door or to the tile
    boundary is circulation ('c'); enclosed room floor stays '.'.
    """
    im = Image.open(path) if not isinstance(path, Image.Image) else path
    a = im.convert("RGBA").getchannel("A").point(lambda v: 255 if v > 16 else 0)   # any visible line counts, even a faint dashed one
    box = (border * pps, border * pps, (border + w) * pps, (border + h) * pps)
    a = a.crop(box).resize((w * SUB, h * SUB), Image.BOX)
    px = a.load()
    cols, rows = w * SUB, h * SUB
    grid = [["#" if px[x, y] / 255.0 >= solid_at else "." for x in range(cols)] for y in range(rows)]
    dq = deque()

    def flood(mark):
        while dq:
            x, y = dq.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < cols and 0 <= ny < rows and grid[ny][nx] == ".":
                    grid[ny][nx] = mark
                    dq.append((nx, ny))

    def seed(x, y, mark):
        if 0 <= x < cols and 0 <= y < rows and grid[y][x] == ".":
            grid[y][x] = mark
            dq.append((x, y))
    # 'o' = outside: open floor touching the tile boundary (never furnished)
    # ``joins``: sides where the tile meets the rest of the ship (a wing's hull side); floor there is not outside
    for x in range(cols):
        if "N" not in joins:
            seed(x, 0, "o")
        if "S" not in joins:
            seed(x, rows - 1, "o")
    for y in range(rows):
        if "W" not in joins:
            seed(0, y, "o")
        if "E" not in joins:
            seed(cols - 1, y, "o")
    flood("o")
    for side in joins:                          # what comes in through the hull side is the wing's corridor
        for j in range(cols if side in "NS" else rows):
            if side == "N": seed(j, 0, "c")
            elif side == "S": seed(j, rows - 1, "c")
            elif side == "W": seed(0, j, "c")
            else: seed(cols - 1, j, "c")
    flood("c")
    # 'c' = circulation: floor reached from a door
    for side, e in (edges or {}).items():
        for i, c in enumerate(e["cls"]):
            if c == 0:
                continue
            for k in range(SUB):
                j = i * SUB + k
                for depth in (1, 2):
                    if side == "N": seed(j, depth, "c")
                    elif side == "S": seed(j, rows - 1 - depth, "c")
                    elif side == "W": seed(depth, j, "c")
                    else: seed(cols - 1 - depth, j, "c")
    flood("c")
    # a corridor is at most ~2 squares wide: any bigger open area that the flood reached is a room or bay
    for x0, y0, rw, rh in free_rects(["".join("." if c == "c" else c for c in r) for r in grid], 6, 40):
        for yy in range(y0, y0 + rh):
            for xx in range(x0, x0 + rw):
                if grid[yy][xx] == "c":
                    grid[yy][xx] = "r"          # big open area behind a door: a hall or bay, furnished only indoors
    return ["".join(r) for r in grid]


def free_rects(rows, min_side=4, limit=6, chars=(".",)):
    """Greedy largest empty rectangles made of ``chars`` cells (default: enclosed room floor), as (x, y, w, h) in cells."""
    g = [list(r) for r in rows]
    H, W = len(g), len(g[0]) if g else 0
    out = []
    for _ in range(limit):
        best = None
        heights = [0] * W
        for y in range(H):
            for x in range(W):
                heights[x] = heights[x] + 1 if g[y][x] in chars else 0
            stack = []
            for x in range(W + 1):
                cur = heights[x] if x < W else 0
                start = x
                while stack and stack[-1][1] >= cur:
                    sx, sh = stack.pop()
                    w = x - sx
                    if sh >= min_side and w >= min_side and (best is None or sh * w > best[2] * best[3]):
                        best = (sx, y - sh + 1, w, sh)
                    start = sx
                stack.append((start, cur))
        if best is None or best[2] * best[3] < min_side * min_side:
            break
        out.append(best)
        x, y, w, h = best
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                g[yy][xx] = "x"
    return out


def encode(rows) -> str:
    return "|".join("".join(f"{m.group(1)}{len(m.group(0))}" if len(m.group(0)) > 1 else m.group(1)
                            for m in re.finditer(r"(.)\1*", r)) for r in rows)


def decode(s: str):
    rows = []
    for part in s.split("|"):
        out = []
        for ch, n in re.findall(r"(.)(\d*)", part):
            out.append(ch * (int(n) if n else 1))
        rows.append("".join(out))
    return rows


def transform(rows, rot=0, mirror=False):
    """Floor map after mirror (left-right) then clockwise rotation, like the tile image."""
    g = [list(r) for r in rows]
    if mirror:
        g = [r[::-1] for r in g]
    for _ in range((rot % 360) // 90):
        g = [list(col) for col in zip(*g[::-1])]
    return ["".join(r) for r in g]


def solid_depth(rows, side, lo, hi, limit=12, none_if_empty=False):
    """Open distance (in squares) between a tile's plan edge and its first solid cell, along a strip.

    ``rows`` is the (already rotated/mirrored) floor map; ``lo``..``hi`` are the strip's squares along the side.
    A corridor that stops at the plan edge would end in empty ground when the tile's visible walls sit further in.
    """
    H, W = len(rows), len(rows[0])
    a, b = int(lo * SUB), int(hi * SUB)
    best = None
    for k in range(max(0, a), min((W if side in "NS" else H), b)):
        d = 0
        n = (H if side in "NS" else W)
        while d < n:
            if side == "N": c = rows[d][k]
            elif side == "S": c = rows[H - 1 - d][k]
            elif side == "W": c = rows[k][d]
            else: c = rows[k][W - 1 - d]
            if c == "#":
                break
            d += 1
        else:
            d = None                                # nothing solid at all on this strip
        if d is not None:
            best = d if best is None else min(best, d)
    if best is None:
        return None if none_if_empty else 0.0
    return min(limit, best / SUB)


def hall_bands(rows, depth=4, door_clear=6, min_open=200):
    """Wall-hugging strips in a big open hall that has no free room floor.

    Promenades, command centres and the like are mostly open floor, so the normal
    free-rectangle search finds nothing. This marks the floor within ``depth``
    cells (2 squares) of a wall as usable, except near the tile's edge doors, so
    furniture lines the walls and the middle of the hall stays clear. Returns
    rectangles in cells, like :func:`free_rects`."""
    H = len(rows)
    W = len(rows[0]) if H else 0
    open_cells = sum(1 for r in rows for ch in r if ch in "cr")
    if open_cells < min_open:
        return []
    INF = 10 ** 6
    dist = [[INF] * W for _ in range(H)]
    from collections import deque
    dq = deque()
    for y in range(H):
        for x in range(W):
            if rows[y][x] == "#":
                dist[y][x] = 0
                dq.append((x, y))
    while dq:
        x, y = dq.popleft()
        if dist[y][x] >= depth:
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and dist[ny][nx] > dist[y][x] + 1:
                dist[ny][nx] = dist[y][x] + 1
                dq.append((nx, ny))
    doors = [(x, y) for y in range(H) for x in range(W)
             if rows[y][x] in "cr" and (x in (0, W - 1) or y in (0, H - 1)
                                        or any(0 <= x + dx < W and 0 <= y + dy < H and rows[y + dy][x + dx] == "o"
                                               for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))))]
    mask = [["x"] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if rows[y][x] in "cr" and 0 < dist[y][x] <= depth:
                if all(max(abs(x - dx), abs(y - dy)) > door_clear for dx, dy in doors):
                    mask[y][x] = "."
    return free_rects(["".join(r) for r in mask], min_side=4, limit=8, chars=(".",))


def build_missing(registry, dirs, floors_path=None) -> dict:
    """Floor maps for tiles that have none yet (wings and nose transitions), read from the pack images in ``dirs``.

    Wing images carry their plan box (``tile.bbox``, squares) instead of the usual 2-square border, so the image is
    cropped to it first. Returns {tile id: encoded map} for the tiles it could read; ``floors_path`` gets them added."""
    import json
    from pathlib import Path
    from .registry import PX_PER_SQUARE as S
    out = {}
    for t in registry.tiles.values():
        if t.id in _existing(floors_path):
            continue
        src = next((Path(d) / t.image for d in dirs if (Path(d) / t.image).exists()), None)
        if src is None:
            continue
        im = Image.open(src)
        if t.bbox:
            l, top, r, b = t.bbox
            im = im.crop((int(l * S), int(top * S), int(r * S), int(b * S)))
            # wing corridors are one square wide: a half-square cell is wall only when an outline fills much of it
            rows = _wing_floor(im, t)
        else:
            rows = analyse_floor(im, t.w, t.h, t.edges)
        out[t.id] = encode(rows)
    if floors_path is not None and out:
        p = Path(floors_path)
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        data.update(out)
        p.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return out


def _wing_floor(im, t) -> list:
    """Floor map of a wing: its corridors are clear art like any tile, but they run out through the hull side into
    the image's empty margin, so the edge flood can't tell them from outside. A wing is a simple shape, so per row
    everything between the first and the last bit of wall is inside it (corridor), and the rest is outside."""
    a = im.convert("RGBA").getchannel("A").point(lambda v: 255 if v > 16 else 0)
    cols, rows = t.w * SUB, t.h * SUB
    px = a.resize((cols, rows), Image.BOX).load()
    solid = [[px[x, y] / 255.0 >= WING_SOLID_AT for x in range(cols)] for y in range(rows)]

    def span(cells):
        hits = [i for i, v in enumerate(cells) if v]
        return (hits[0], hits[-1]) if hits else (0, -1)
    across = [span(solid[y]) for y in range(rows)]
    down = [span([solid[y][x] for y in range(rows)]) for x in range(cols)]
    out = []
    for y in range(rows):
        line = []
        for x in range(cols):
            if solid[y][x]:
                line.append("#")
            elif across[y][0] < x < across[y][1] or down[x][0] < y < down[x][1]:
                line.append("c")
            else:
                line.append("o")
        out.append("".join(line))
    return out


def _existing(path):
    import json
    from pathlib import Path
    if path is None or not Path(path).exists():
        return {}
    cache = getattr(_existing, "_c", None)
    if cache is None or cache[0] != str(path):
        _existing._c = cache = (str(path), json.loads(Path(path).read_text(encoding="utf-8")))
    return cache[1]
