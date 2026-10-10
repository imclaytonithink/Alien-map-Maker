"""Atmosphere for rooms in a bad state: dim light, red emergency lamps, sealed shutters, quarantine hatching.

One overlay image per affected room, built here and used both by the generator preview/exports (PIL) and
when the map is placed on the canvas (as an embedded image on its own layer), so the two always match.

* power failure: the room goes dark, with a red emergency lamp glowing over each doorway
* lockdown: a faint red cast; **GM only**: a red shutter across every doorway (players do not see it)
* quarantine: a sickly yellow-green cast and a hazard-striped border
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter

MAX_LAMPS = 14
WALL_RUN = 2                # a lamp needs a straight wall this many cells (half squares) either side of it
TARGET_LIT = 0.8            # add lamps until about this share of the open floor is lit
LIT_CELLS = 7               # cells (half squares) a lamp lights well
LIGHT_RADIUS = 3.8          # squares a lamp reaches (walls and furniture block it)
LIT_AT = 48                 # light level (of 255) from which a spot counts as lit rather than in shadow
DOOR = 1                    # edge class value for a door square (geomorph.edges.DOOR)
_GLOW = {}


def affected(res, g) -> list:
    """[{"tile", "states", "colors", "lights"}] for the tiles on level ``g`` whose zone is dark, locked down or
    quarantined, or that hold a light the user placed."""
    ov = res.overlays or {}
    states = {}
    for key in ("power_failure", "lockdown", "quarantine"):
        for zid in ov.get(key, []):
            states.setdefault(zid, []).append(key)
    lights = [L for L in (getattr(res, "lights", None) or []) if L.get("level") == g.index]
    col = colors(res)
    out = []
    for p in g.placed:
        if p.tile.type == "wing":
            continue
        mine = [L for L in lights if p.x <= L["x"] < p.x + p.w and p.y <= L["y"] < p.y + p.h]
        if p.zone in states or mine:
            out.append({"tile": p, "states": states.get(p.zone, []), "colors": col, "lights": mine})
    return out


def door_points(p) -> list:
    """Door squares of a placed tile as (x, y, side) in squares relative to the tile's top-left, on its edge."""
    pts = []
    for side in "NESW":
        cls = p.side_cls(side)
        for i, c in enumerate(cls):
            if c != DOOR:
                continue
            if side == "N":
                pts.append((i + 0.5, 0.0, side))
            elif side == "S":
                pts.append((i + 0.5, float(p.h), side))
            elif side == "W":
                pts.append((0.0, i + 0.5, side))
            else:
                pts.append((float(p.w), i + 0.5, side))
    return pts


def _glow(diameter: int, color, peak: int) -> Image.Image:
    key = (diameter, color, peak)
    if key not in _GLOW:
        g = Image.radial_gradient("L").resize((diameter, diameter), Image.BILINEAR)
        alpha = g.point(lambda v: int(max(0, 255 - v) ** 1.6 / 255 ** 0.6 * peak / 255))
        sprite = Image.new("RGBA", (diameter, diameter), color + (0,))
        sprite.putalpha(alpha)
        _GLOW[key] = sprite
    return _GLOW[key]


_LIGHT = {}


def _light_sprite(diameter: int) -> Image.Image:
    """Radial light, 255 at the lamp falling smoothly to 0 at the radius (kept bright for most of the way)."""
    if diameter not in _LIGHT:
        g = Image.radial_gradient("L").resize((diameter, diameter), Image.BILINEAR)
        _LIGHT[diameter] = g.point(lambda v: int(255 * max(0.0, 1 - v / 255.0) ** 1.1))
    return _LIGHT[diameter]


def _put(dst: Image.Image, src: Image.Image, x: int, y: int):
    """alpha_composite that tolerates a source hanging over the edge."""
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(src.width, dst.width - x), min(src.height, dst.height - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    dst.alpha_composite(src.crop((sx0, sy0, sx1, sy1)), (x + sx0, y + sy0))


def _floor(p):
    """The tile's floor map turned to its placed orientation: rows of cells (2 per square), or None."""
    from . import decor, floors
    code = decor.tile_floors().get(p.tile.id)
    if not code:
        return None
    rows = floors.transform(floors.decode(code), p.o.rot, p.o.mirror)
    return rows if len(rows) == p.h * floors.SUB and len(rows[0]) == p.w * floors.SUB else None


def walk_chars(rows) -> str:
    """Floor that emergency lights serve: corridors and big open halls. Enclosed rooms stay dark (hiding places);
    only a tile with no corridor or hall at all falls back to its room floor."""
    return "cr" if any(ch in "cr" for r in rows for ch in r) else "c.r"


def _room_mask(rows, W, H) -> Image.Image:
    """255 inside the building (walls and floor), 0 outside it: light and tint never leave the room."""
    if rows is None:
        return Image.new("L", (W, H), 255)
    m = Image.new("L", (len(rows[0]), len(rows)), 0)
    m.putdata([0 if ch == "o" else 255 for r in rows for ch in r])
    return m.resize((W, H), Image.NEAREST)


def _wall_face(rows, x, y, d, walk, run=None) -> bool:
    """True when the floor cell (x, y) sits against a long, straight wall on side ``d`` (not a console or a machine):
    the wall cell is solid and so are its neighbours two cells either way, while the floor in front of it is open."""
    H, W = len(rows), len(rows[0])
    dx, dy = {"N": (0, -1), "S": (0, 1), "W": (-1, 0), "E": (1, 0)}[d]
    ax, ay = (1, 0) if d in "NS" else (0, 1)                      # along the wall
    run = WALL_RUN if run is None else run
    for k in range(-run, run + 1):
        fx, fy = x + ax * k, y + ay * k
        wx, wy = fx + dx, fy + dy
        if not (0 <= fx < W and 0 <= fy < H and 0 <= wx < W and 0 <= wy < H):
            return False
        if rows[wy][wx] != "#" or rows[fy][fx] not in walk:
            return False
    for step in range(2, 5):                                   # a wall is thin; a deep solid block is a machine or console
        wx, wy = x + dx * step, y + dy * step
        if not (0 <= wx < W and 0 <= wy < H) or rows[wy][wx] != "#":
            return True
    return False


DOOR_CLEAR = 1.2            # squares a fixture keeps from a doorway
WALL_SPAN_PX = 10           # solid wall needed this far either side of a fixture (a door is a gap in the wall)


def _wall_clear(art, face, wall) -> bool:
    """True when the wall around ``face`` (squares in the tile) is solid for a stretch either side, i.e. no door or
    opening is there. Without art there is nothing to read, so it passes."""
    if art is None or face is None:
        return True
    ax, ay = (1, 0) if wall in "NS" else (0, 1)
    ux, uy = {"N": (0, -1), "S": (0, 1), "W": (-1, 0), "E": (1, 0)}[wall]
    wp = _wall_mask(art).load()
    W, H = art.size
    cx, cy = (face[0] + ART_BORDER) * ART_PPS, (face[1] + ART_BORDER) * ART_PPS
    total = good = 0
    for k in range(-WALL_SPAN_PX, WALL_SPAN_PX + 1):
        for depth in (1.5, 3.0):                              # a little way into the wall, away from the room
            x, y = int(cx + ux * depth + ax * k), int(cy + uy * depth + ay * k)
            if 0 <= x < W and 0 <= y < H:
                total += 1
                good += 1 if wp[x, y] else 0
    return total > 0 and good / total >= 0.95


def _face_agrees(c, face, tol=0.3) -> bool:
    """The wall found in the art must sit where the floor map says it does, else the fixture would float."""
    if face is None:
        return True
    cx, cy, d = c
    want = {"N": ("y", cy / 2.0), "S": ("y", (cy + 1) / 2.0), "W": ("x", cx / 2.0), "E": ("x", (cx + 1) / 2.0)}[d]
    return abs(face[0 if want[0] == "x" else 1] - want[1]) <= tol


def near_door(p, x, y) -> bool:
    """Is (x, y) (squares in the tile) within DOOR_CLEAR of one of its edge doors?"""
    return any((x - dx) ** 2 + (y - dy) ** 2 < DOOR_CLEAR ** 2 for dx, dy, _s in door_points(p))


def _lamps(p, rows, art=None):
    """Emergency lamp spots as (cell_x, cell_y, wall_dir): floor cells next to a wall, near each doorway
    (or near the corners when the room has none). The lamp sits flush against that wall."""
    SUB = 2
    cand = []
    walk = walk_chars(rows) if rows is not None else "c.r"
    if rows is not None:
        H, W = len(rows), len(rows[0])
        for run in (WALL_RUN, 1, 0):                      # prefer long straight walls; shorter ones only if the tile has none
            for y in range(H):
                for x in range(W):
                    if rows[y][x] in walk:
                        for d in "NSWE":
                            if _wall_face(rows, x, y, d, walk, run):
                                cand.append((x, y, d))
                                break
            if cand:
                break
    doors = door_points(p)
    SUBW, SUBH = p.w * SUB, p.h * SUB
    corners = [(3, 3), (SUBW - 4, 3), (3, SUBH - 4), (SUBW - 4, SUBH - 4)]
    anchors = [(int(dx * SUB), int(dy * SUB)) for dx, dy, _s in doors] + corners
    lamps = []
    floor = {(x, y) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch in walk} if rows is not None else set()
    lit = set()
    _ok = {}

    def ok(c):
        if c not in _ok:
            face = _wall_face_point(art, c[0], c[1], c[2], p)
            x0, y0 = c[0] / 2.0 + 0.25, c[1] / 2.0 + 0.25
            _ok[c] = (not near_door(p, *(face or (x0, y0))) and _wall_clear(art, face, c[2])
                      and _face_agrees(c, face))
        return _ok[c]
    cand = [c for c in cand if ok(c)]

    def covered():
        return bool(floor) and len(lit & floor) / len(floor) >= TARGET_LIT

    def add(cell):
        lamps.append(cell)
        if rows is not None:
            lit.update(_visible_cells(rows, cell[0], cell[1], LIT_CELLS, walk))
    for ax, ay in anchors:                              # one by each doorway and near the corners, until lit enough
        if covered():
            break
        near = [c for c in cand if max(abs(c[0] - ax), abs(c[1] - ay)) <= 10]
        if not near:
            continue
        best = min(near, key=lambda c: (c[0] - ax) ** 2 + (c[1] - ay) ** 2)
        if all(max(abs(best[0] - l[0]), abs(best[1] - l[1])) >= 8 for l in lamps):
            add(best)
    while rows is not None and cand and len(lamps) < MAX_LAMPS and floor and not covered():   # then spread more
        far = max(cand, key=lambda c: min([max(abs(c[0] - l[0]), abs(c[1] - l[1])) for l in lamps] or [99]))
        if min([max(abs(far[0] - l[0]), abs(far[1] - l[1])) for l in lamps] or [99]) < 8:
            break
        add(far)
    return lamps[:MAX_LAMPS]


def _visible_cells(rows, cx, cy, radius_cells, walk="c.r") -> set:
    """Cells of the served floor (``walk``) within ``radius_cells`` of (cx, cy) that a straight line reaches
    without crossing a wall or furniture."""
    CH, CW = len(rows), len(rows[0])
    out = set()
    for ty in range(max(0, cy - radius_cells), min(CH, cy + radius_cells + 1)):
        for tx in range(max(0, cx - radius_cells), min(CW, cx + radius_cells + 1)):
            if rows[ty][tx] not in walk:
                continue
            n = max(abs(tx - cx), abs(ty - cy))
            if all(rows[round(cy + (ty - cy) * k / n)][round(cx + (tx - cx) * k / n)] not in "#o" for k in range(1, n)):
                out.add((tx, ty))
    return out


def _light_mask(rows, cx, cy, radius_cells, W, H, walk=None) -> Image.Image:
    """The lamp's line-of-sight cells as an image (blurred a touch by the resize)."""
    m = Image.new("L", (len(rows[0]), len(rows)), 0)
    px = m.load()
    for tx, ty in _visible_cells(rows, cx, cy, radius_cells, walk or walk_chars(rows)):
        px[tx, ty] = 255
    return m.resize((W, H), Image.BILINEAR)


ART_PPS = 15                # the tile thumbnails are 15 px per square, with a 2 square border all round
ART_BORDER = 2
INK = 16                    # alpha at which a thumbnail pixel counts as drawn art
WALL_ALPHA, WALL_LUM = 200, 100        # walls are solid and dark in the art; floors are pale and see-through
RAY_STEP_DEG = 0.5
SOFTEN_PX = 4.0             # extra blur (art pixels) so no pool has a hard edge
DEFAULT_LIGHT = "#ff241c"
DEFAULT_FIXTURE = "#e8261c"
WASH_PEAK = 95             # how strongly a lamp tints the floor next to it (0-255)
_CACHE = {}


def colors(res) -> tuple:
    """(emergency light colour, fixture colour) chosen for this map (hex strings)."""
    a = (res.options or {}).get("atmosphere") or {}
    return a.get("light") or DEFAULT_LIGHT, a.get("fixture") or DEFAULT_FIXTURE


def _rgb(c, default=DEFAULT_LIGHT) -> tuple:
    try:
        c = str(c).lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return _rgb(default) if c != default else (255, 36, 28)


def _darker(rgb, f=0.45) -> tuple:
    return tuple(int(v * f) for v in rgb)


def _wall_face_point(art, cx, cy, wall, p):
    """Where the wall really is for the lamp on floor cell (cx, cy): the tile art is scanned from the cell centre toward
    the wall, and the first solid wall pixel (median over a few parallel lines) is the wall face. Returns (x, y) in
    squares relative to the tile, or None when there is no art to read."""
    if art is None:
        return None
    ux, uy = {"N": (0, -1), "S": (0, 1), "W": (-1, 0), "E": (1, 0)}[wall]
    ax, ay = (1, 0) if wall in "NS" else (0, 1)
    sx = ((cx + 0.5) / 2.0 + ART_BORDER) * ART_PPS
    sy = ((cy + 0.5) / 2.0 + ART_BORDER) * ART_PPS
    wp = _wall_mask(art).load()
    W, H = art.size
    hits = []
    for off in (-3.0, -1.5, 0.0, 1.5, 3.0):
        for i in range(0, int(ART_PPS * 1.2 / 0.5)):
            t = i * 0.5
            x, y = int(sx + ux * t + ax * off), int(sy + uy * t + ay * off)
            if not (0 <= x < W and 0 <= y < H):
                break
            if wp[x, y]:
                if t >= 1.0:                      # a wall pixel on the floor cell itself contradicts the floor map
                    hits.append(t)
                break
    if len(hits) < 3:
        return None
    t = sorted(hits)[len(hits) // 2]
    return ((sx + ux * t) / ART_PPS - ART_BORDER, (sy + uy * t) / ART_PPS - ART_BORDER)


def _wall_mask(art) -> Image.Image:
    """255 where the tile art is a wall: solid (opaque) and dark. Cached on the image object."""
    cached = getattr(art, "_wall_mask", None)
    if cached is None:
        rgba = art if art.mode == "RGBA" else art.convert("RGBA")
        solid = rgba.getchannel("A").point(lambda v: 255 if v >= WALL_ALPHA else 0)
        dark = rgba.convert("L").point(lambda v: 255 if v < WALL_LUM else 0)
        cached = ImageChops.multiply(solid, dark)
        try:
            art._wall_mask = cached
        except AttributeError:
            pass
    return cached


_ART_CELLS = {}


def _cells_to_art(rows, chars, w, h, grow_sq=0.0) -> Image.Image:
    """An art-sized mask (tile area placed inside the border) that is 255 on the floor cells in ``chars``,
    optionally grown by ``grow_sq`` squares so it reaches right up to the walls. Cached per tile layout."""
    key = ("".join(rows), len(rows[0]), chars, w, h, grow_sq)
    full = _ART_CELLS.get(key)
    if full is None:
        m = Image.new("L", (len(rows[0]), len(rows)), 0)
        m.putdata([255 if ch in chars else 0 for r in rows for ch in r])
        m = m.resize((w * ART_PPS, h * ART_PPS), Image.NEAREST)
        for _ in range(int(grow_sq * ART_PPS)):              # grow one pixel at a time: cheap, unlike one big filter
            m = m.filter(ImageFilter.MaxFilter(3))
        full = Image.new("L", ((w + 2 * ART_BORDER) * ART_PPS, (h + 2 * ART_BORDER) * ART_PPS), 0)
        full.paste(m, (ART_BORDER * ART_PPS, ART_BORDER * ART_PPS))
        if len(_ART_CELLS) > 200:
            _ART_CELLS.clear()
        _ART_CELLS[key] = full
    return full


def _cast(wall, x, y, inward, radius_px, full=False):
    """Light as straight rays from (x, y) (art pixels): each ray runs until it hits a wall (the wall face is lit)
    or reaches the radius. ``inward`` is the way the fixture faces; a ceiling light (``full``) shines all round."""
    import math
    W, H = wall.size
    lit = Image.new("L", (W, H), 0)
    lp, wp = lit.load(), wall.load()
    base = math.atan2(inward[1], inward[0]) if inward else 0.0
    span = math.pi if (full or not inward) else math.pi / 2
    n = int(2 * span / math.radians(RAY_STEP_DEG))
    for i in range(n + 1):
        ang = base - span + 2 * span * i / n
        dx, dy = math.cos(ang), math.sin(ang)
        for s in range(1, radius_px + 1):
            px, py = int(x + dx * s), int(y + dy * s)
            if not (0 <= px < W and 0 <= py < H):
                break
            lp[px, py] = 255
            if wp[px, py]:
                break
    return lit


def _paste_l(dst, src, x, y):
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(src.width, dst.width - x), min(src.height, dst.height - y)
    if sx1 > sx0 and sy1 > sy0:
        dst.paste(src.crop((sx0, sy0, sx1, sy1)), (x + sx0, y + sy0))


def _lamp_light(spec, p, rows, art, W, H, pps):
    """One lamp's light over the tile (0-255 at W x H): bright at the fixture, feathering out to its radius, stopped by walls
    (read from the art, so a hallway fills right up to both walls), and kept to the floor it serves."""
    radius_px = max(2, int(spec["radius"] * ART_PPS))
    if art is not None and not p.tile.bbox:
        wall = _wall_mask(art)
        x, y = (spec["fx"] + ART_BORDER) * ART_PPS, (spec["fy"] + ART_BORDER) * ART_PPS
        inward = {"N": (0, 1), "S": (0, -1), "W": (1, 0), "E": (-1, 0)}.get(spec["wall"])
        if inward:
            x, y = x + inward[0] * 2.0, y + inward[1] * 2.0
        lit = _cast(wall, x, y, inward, radius_px, full=spec["wall"] is None)
        lit = lit.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(SOFTEN_PX))
        fall = Image.new("L", wall.size, 0)
        _paste_l(fall, _light_sprite(2 * radius_px), int(x) - radius_px, int(y) - radius_px)
        lit = ImageChops.multiply(lit, fall)
        if rows is not None:                     # automatic lamps serve corridors and halls only; user lights go anywhere inside
            chars = "c.r" if spec["user"] else walk_chars(rows)
            lit = ImageChops.multiply(lit, _cells_to_art(rows, chars, p.w, p.h, grow_sq=0.8 if not spec["user"] else 0.3))
        box = (ART_BORDER * ART_PPS, ART_BORDER * ART_PPS, (ART_BORDER + p.w) * ART_PPS, (ART_BORDER + p.h) * ART_PPS)
        lit = lit.crop(box).resize((W, H), Image.BILINEAR)
    elif rows is not None:                       # no art: line of sight over the floor-map cells instead
        cx, cy = int(spec["fx"] * 2), int(spec["fy"] * 2)
        cx, cy = min(max(cx, 0), len(rows[0]) - 1), min(max(cy, 0), len(rows) - 1)
        r = int(pps * spec["radius"])
        lit = Image.new("L", (W, H), 0)
        _paste_l(lit, _light_sprite(2 * r), int(spec["fx"] * pps) - r, int(spec["fy"] * pps) - r)
        walk = "c.r" if spec["user"] else walk_chars(rows)
        lit = ImageChops.multiply(lit, _light_mask(rows, cx, cy, int(spec["radius"] * 2) + 1, W, H, walk))
    else:
        lit = Image.new("L", (W, H), 0)
    s = spec["strength"]
    return lit if s >= 0.999 else lit.point(lambda v: int(v * s))


def lamp_specs(room, rows, art, pps) -> list:
    """Every light shining in this room: the automatic emergency lamps (corridors and halls, when the power is
    out or the room is locked down) and any lights the user placed. Positions are in squares inside the tile."""
    p, states = room["tile"], room["states"]
    light_c, fixture_c = room.get("colors") or (DEFAULT_LIGHT, DEFAULT_FIXTURE)
    dark = "power_failure" in states
    specs = []
    if dark or "lockdown" in states:
        for cx, cy, wall in _lamps(p, rows, art):
            face = _wall_face_point(art, cx, cy, wall, p)
            if face is None:
                x0, y0 = cx / 2.0, cy / 2.0
                face = {"N": (x0 + 0.25, y0), "S": (x0 + 0.25, y0 + 0.5), "W": (x0, y0 + 0.25), "E": (x0 + 0.5, y0 + 0.25)}[wall]
            specs.append({"fx": face[0], "fy": face[1], "wall": wall, "radius": LIGHT_RADIUS if dark else 2.0,
                          "strength": 1.0 if dark else 0.55, "color": light_c, "fixture": fixture_c, "user": False})
    for L in room.get("lights") or ():
        specs.append({"fx": L["x"] - p.x, "fy": L["y"] - p.y, "wall": (L.get("wall") or None) if L.get("kind", "wall") == "wall" else None,
                      "radius": float(L.get("radius", 4.0)), "strength": float(L.get("strength", 1.0)),
                      "color": L.get("color") or light_c, "fixture": L.get("fixture") or fixture_c, "user": True})
    return specs


def _light_map(p, rows, W, H, pps, radius_sq, art=None, room=None):
    """Where the room's automatic lamps reach, 0-255, plus their fixtures: (light, specs). Used for the shadow
    measure and by the tests; :func:`room_overlay` builds the picture from the same specs."""
    room = room or {"tile": p, "states": ["power_failure"]}
    specs = [dict(sp, radius=radius_sq) if not sp["user"] else sp for sp in lamp_specs(room, rows, art, pps)]
    light = Image.new("L", (W, H), 0)
    for sp in specs:
        light = ImageChops.lighter(light, _lamp_light(sp, p, rows, art, W, H, pps))
    return light, specs


def room_overlay(room: dict, pps: int, part: str = "all", art=None) -> Image.Image | None:
    """The overlay for one room. ``part``: "public" (what anyone sees), "gm" (shutters only) or "all".

    Everything is confined to the building inside the tile. With the power out the room is dark except where a lamp
    reaches: each pool is brightest at its fixture and feathers out, filling a hallway wall to wall, and whatever no
    lamp can see (rooms, far ends) stays in shadow. Lights placed by the user shine the same way, in any colour."""
    p, states = room["tile"], room["states"]
    W, H = int(p.w * pps), int(p.h * pps)
    rows = _floor(p)
    key = (p.tile.id, p.o.rot, p.o.mirror, p.w, p.h, pps, part, tuple(states), tuple(room.get("colors") or ()),
           tuple(sorted((tuple(sorted(L.items())) for L in room.get("lights") or ()), key=str)), art is not None)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached.copy()
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if part in ("all", "public"):
        mask = _room_mask(rows, W, H)
        dark = "power_failure" in states
        specs = lamp_specs(room, rows, art, pps)
        masks = [_lamp_light(sp, p, rows, art, W, H, pps) for sp in specs]
        body = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if dark:
            light = Image.new("L", (W, H), 0)
            for m in masks:
                light = ImageChops.lighter(light, m)
            shade = ImageChops.subtract(Image.new("L", (W, H), 168), light.point(lambda v: int(v * 168 * 0.9 / 255)))
            layer = Image.new("RGBA", (W, H), (4, 6, 14, 0))
            layer.putalpha(shade)
            body.alpha_composite(layer)
        if "lockdown" in states:
            body.alpha_composite(Image.new("RGBA", (W, H), (200, 20, 20, 26 if dark else 34)))
        if "quarantine" in states:
            body.alpha_composite(Image.new("RGBA", (W, H), (200, 210, 40, 24)))
            band = max(3, int(pps * 0.32))
            stripes = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(stripes)
            for k in range(-H, W + H, band * 2):
                sd.polygon([(k, 0), (k + band, 0), (k + band - H, H), (k - H, H)], fill=(235, 200, 40, 215))
            pad = Image.new("L", (W + 2 * band, H + 2 * band), 0)      # border follows the building outline
            pad.paste(mask, (band, band))
            inner = pad.filter(ImageFilter.MinFilter(2 * band + 1)).crop((band, band, band + W, band + H))
            stripes.putalpha(ImageChops.multiply(stripes.getchannel("A"), ImageChops.subtract(mask, inner)))
            body.alpha_composite(stripes)
        for sp, m in zip(specs, masks):                                # each lamp's coloured wash, strongest at the fixture
            wash = Image.new("RGBA", (W, H), _rgb(sp["color"]) + (0,))
            wash.putalpha(m.point(lambda v: int(v * WASH_PEAK / 255)))
            body.alpha_composite(wash)
        body.putalpha(ImageChops.multiply(body.getchannel("A"), mask))
        img.alpha_composite(body)
        d = ImageDraw.Draw(img)
        rr = max(3, int(pps * 0.3))
        for sp in specs:                                               # the fixtures: half-discs on the wall, discs overhead
            gx, gy = sp["fx"] * pps, sp["fy"] * pps
            fill, edge = _rgb(sp["fixture"], DEFAULT_FIXTURE) + (255,), _darker(_rgb(sp["fixture"], DEFAULT_FIXTURE)) + (255,)
            box = (gx - rr, gy - rr, gx + rr, gy + rr)
            if sp["wall"] is None:
                d.ellipse(box, fill=fill, outline=edge)
            else:
                start, end = {"N": (0, 180), "S": (180, 360), "W": (-90, 90), "E": (90, 270)}[sp["wall"]]
                d.pieslice(box, start, end, fill=fill, outline=edge)
    if part in ("all", "gm") and "lockdown" in states:
        d = ImageDraw.Draw(img)
        t = max(3, int(pps * 0.3))
        for dx, dy, side in door_points(p):
            cx, cy = int(dx * pps), int(dy * pps)
            half = int(pps * 0.5)
            if side in "NS":
                y0 = 0 if side == "N" else H - t
                d.rectangle((cx - half, y0, cx + half, y0 + t), fill=(255, 60, 50, 240))
            else:
                x0 = 0 if side == "W" else W - t
                d.rectangle((x0, cy - half, x0 + t, cy + half), fill=(255, 60, 50, 240))
    if len(_CACHE) > 400:
        _CACHE.clear()
    _CACHE[key] = img
    return img.copy()


def shadow_fraction(room: dict, pps: int = 10) -> float | None:
    """Share of a dark tile's corridor and hall floor that no lamp lights (None without a floor map or when not dark)."""
    p = room["tile"]
    rows = _floor(p)
    if rows is None or "power_failure" not in room["states"]:
        return None
    W, H = int(p.w * pps), int(p.h * pps)
    light, _specs = _light_map(p, rows, W, H, pps, LIGHT_RADIUS, None, dict(room, lights=()))
    px = light.load()
    inside = unlit = 0
    walk = walk_chars(rows)
    for cy, row in enumerate(rows):
        for cx, ch in enumerate(row):
            if ch in walk:
                inside += 1
                if px[int((cx + 0.5) * pps / 2), int((cy + 0.5) * pps / 2)] < LIT_AT:
                    unlit += 1
    return unlit / inside if inside else None


def snap_light(res, g, x, y, images=None, kind="wall", radius=4.0, strength=1.0, color=None, fixture=None):
    """A user light for the click (x, y) (squares on level ``g``), or None when nothing sensible is there.

    A wall light snaps to the nearest wall face within about two squares (read from the tile art when there is some);
    a ceiling light goes exactly where it was clicked, as long as that is inside a building."""
    from . import render
    tile = next((p for p in g.placed if p.x <= x < p.x + p.w and p.y <= y < p.y + p.h and p.tile.type != "wing"), None)
    if tile is None:
        return None
    rows = _floor(tile)
    light_c, fixture_c = colors(res)
    base = {"level": g.index, "kind": kind, "radius": float(radius), "strength": float(strength),
            "color": color or light_c, "fixture": fixture or fixture_c}
    if rows is None:
        return None
    rx, ry = x - tile.x, y - tile.y
    cx, cy = min(int(rx * 2), len(rows[0]) - 1), min(int(ry * 2), len(rows) - 1)
    if kind != "wall":
        if rows[cy][cx] == "o":
            return None
        return dict(base, x=round(x, 3), y=round(y, 3), wall="")
    art = render.oriented_thumb(images, tile) if images is not None else None
    best = None
    for yy in range(max(0, cy - 4), min(len(rows), cy + 5)):
        for xx in range(max(0, cx - 4), min(len(rows[0]), cx + 5)):
            if rows[yy][xx] == "#" or rows[yy][xx] == "o":
                continue
            for d in "NSWE":
                if _wall_face(rows, xx, yy, d, "c.r", 1):
                    fp = _wall_face_point(art, xx, yy, d, tile)
                    if (near_door(tile, *(fp or (xx / 2.0 + 0.25, yy / 2.0 + 0.25))) or not _wall_clear(art, fp, d)
                            or not _face_agrees((xx, yy, d), fp)):
                        continue
                    dist = (xx + 0.5 - rx * 2) ** 2 + (yy + 0.5 - ry * 2) ** 2
                    if best is None or dist < best[0]:
                        best = (dist, xx, yy, d)
    if best is None:
        return None
    _dist, xx, yy, d = best
    face = _wall_face_point(art, xx, yy, d, tile)
    if face is None:
        x0, y0 = xx / 2.0, yy / 2.0
        face = {"N": (x0 + 0.25, y0), "S": (x0 + 0.25, y0 + 0.5), "W": (x0, y0 + 0.25), "E": (x0 + 0.5, y0 + 0.25)}[d]
    return dict(base, x=round(tile.x + face[0], 3), y=round(tile.y + face[1], 3), wall=d)


def paint(res, g, layer: Image.Image, x0, y0, pps, gm=True, images=None):
    """Draw every affected room's overlay onto ``layer`` (the level render)."""
    from . import render
    for room in affected(res, g):
        p = room["tile"]
        art = render.oriented_thumb(images, p) if images is not None else None
        im = room_overlay(room, pps, "all" if gm else "public", art)
        if im is not None:
            _put(layer, im, int(round((p.x - x0) * pps)), int(round((p.y - y0) * pps)))
