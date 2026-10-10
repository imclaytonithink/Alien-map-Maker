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
TARGET_LIT = 0.6            # add lamps until about this share of the open floor is lit
LIT_CELLS = 5               # cells (half squares) a lamp lights well
LIGHT_RADIUS = 3.8          # squares a lamp reaches (walls and furniture block it)
LIT_AT = 48                 # light level (of 255) from which a spot counts as lit rather than in shadow
DOOR = 1                    # edge class value for a door square (geomorph.edges.DOOR)
_GLOW = {}


def affected(res, g) -> list:
    """[{"tile": Placed, "states": [...]}] for the tiles on level ``g`` whose zone is dark, locked down or quarantined."""
    ov = res.overlays or {}
    states = {}
    for key in ("power_failure", "lockdown", "quarantine"):
        for zid in ov.get(key, []):
            states.setdefault(zid, []).append(key)
    out = []
    for p in g.placed:
        if p.zone in states and p.tile.type != "wing":
            out.append({"tile": p, "states": states[p.zone]})
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
    return True


def _lamps(p, rows):
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
        if all(max(abs(best[0] - l[0]), abs(best[1] - l[1])) >= 6 for l in lamps):
            add(best)
    while rows is not None and cand and len(lamps) < MAX_LAMPS and floor and not covered():   # then spread more
        far = max(cand, key=lambda c: min([max(abs(c[0] - l[0]), abs(c[1] - l[1])) for l in lamps] or [99]))
        if min([max(abs(far[0] - l[0]), abs(far[1] - l[1])) for l in lamps] or [99]) < 6:
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
INK = 16                    # alpha at which a thumbnail pixel counts as drawn wall


def _wall_face_point(art, cx, cy, wall, p):
    """Where the wall really is for the lamp on floor cell (cx, cy): the tile art is scanned from the cell centre toward
    the wall, and the first inked pixel (median over a few parallel lines) is the wall face. Returns (x, y) in squares
    relative to the tile, or None when there is no art to read."""
    if art is None:
        return None
    ux, uy = {"N": (0, -1), "S": (0, 1), "W": (-1, 0), "E": (1, 0)}[wall]
    ax, ay = (1, 0) if wall in "NS" else (0, 1)
    sx = ((cx + 0.5) / 2.0 + ART_BORDER) * ART_PPS
    sy = ((cy + 0.5) / 2.0 + ART_BORDER) * ART_PPS
    alpha = art.getchannel("A") if art.mode == "RGBA" else art.convert("RGBA").getchannel("A")
    px = alpha.load()
    W, H = alpha.size
    hits = []
    for off in (-3.0, -1.5, 0.0, 1.5, 3.0):
        for i in range(0, int(ART_PPS * 1.2 / 0.5)):
            t = i * 0.5
            x, y = int(sx + ux * t + ax * off), int(sy + uy * t + ay * off)
            if not (0 <= x < W and 0 <= y < H):
                break
            if px[x, y] >= INK:
                if t >= 1.0:                      # ink on the floor cell itself contradicts the floor map: no reading
                    hits.append(t)
                break
    if len(hits) < 3:
        return None
    t = sorted(hits)[len(hits) // 2]
    return ((sx + ux * t) / ART_PPS - ART_BORDER, (sy + uy * t) / ART_PPS - ART_BORDER)


def _light_map(p, rows, W, H, pps, radius_sq, art=None):
    """Where the room's lamps reach, 0-255: bright at a lamp, fading out, blocked by walls and furniture.
    Everything a lamp cannot see stays dark, which leaves real shadows."""
    lamps = _lamps(p, rows)
    light = Image.new("L", (W, H), 0)
    spots = []
    r = int(pps * radius_sq)
    sprite = _light_sprite(2 * r)
    dome = max(3, int(pps * 0.3))
    for cx, cy, wall in lamps:
        x0, y0 = cx * pps / 2.0, cy * pps / 2.0                     # the cell's top-left, in pixels
        half = pps / 4.0
        gx, gy = {"N": (x0 + half, y0), "S": (x0 + half, y0 + pps / 2.0),
                  "W": (x0, y0 + half), "E": (x0 + pps / 2.0, y0 + half)}[wall]   # the cell edge beside the wall
        face = _wall_face_point(art, cx, cy, wall, p)                 # ... or the wall itself, read from the art
        if face is not None:
            if wall in "NS":
                gy = face[1] * pps
            else:
                gx = face[0] * pps
        local = Image.new("L", (W, H), 0)
        sx, sy = int(gx) - r, int(gy) - r
        sx0, sy0 = max(0, -sx), max(0, -sy)
        sx1, sy1 = min(2 * r, W - sx), min(2 * r, H - sy)
        if sx1 > sx0 and sy1 > sy0:
            local.paste(sprite.crop((sx0, sy0, sx1, sy1)), (sx + sx0, sy + sy0))
        if rows is not None:
            vis = _light_mask(rows, cx, cy, int(radius_sq * 2) + 1, W, H)
            local = ImageChops.multiply(local, vis)
        light = ImageChops.lighter(light, local)
        spots.append((gx, gy, wall, dome))
    return light, spots


def room_overlay(room: dict, pps: int, part: str = "all", art=None) -> Image.Image | None:
    """The overlay for one room. ``part``: "public" (what anyone sees), "gm" (shutters only) or "all".

    Everything public is confined to the building inside the tile (the floor map says where the walls and
    the outside are). With the power out the room is dark except where an emergency lamp reaches: lit pools
    are easy to read, and whatever a lamp cannot see (behind walls and furniture, far corners) stays in shadow."""
    p, states = room["tile"], room["states"]
    W, H = int(p.w * pps), int(p.h * pps)
    rows = _floor(p)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if part in ("all", "public"):
        mask = _room_mask(rows, W, H)
        dark = "power_failure" in states
        lit = dark or "lockdown" in states
        light, spots = _light_map(p, rows, W, H, pps, LIGHT_RADIUS if dark else 2.0, art) if lit else (None, [])
        body = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if dark:
            shade = Image.new("L", (W, H), 168)                       # full darkness ...
            shade = ImageChops.subtract(shade, light.point(lambda v: int(v * 168 * 0.88 / 255)))   # ... lifted by lamp light
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
            edge = ImageChops.subtract(mask, inner)
            stripes.putalpha(ImageChops.multiply(stripes.getchannel("A"), edge))
            body.alpha_composite(stripes)
        if light is not None:                                          # the red wash of the lamps themselves
            wash = Image.new("RGBA", (W, H), (255, 40, 30, 0))
            wash.putalpha(light.point(lambda v: int(v * (62 if dark else 40) / 255)))
            body.alpha_composite(wash)
        body.putalpha(ImageChops.multiply(body.getchannel("A"), mask))
        img.alpha_composite(body)
        d = ImageDraw.Draw(img)
        for gx, gy, wall, rr in spots:                                 # round lamps: half-discs, flat side on the wall
            box = (gx - rr, gy - rr, gx + rr, gy + rr)
            start, end = {"N": (0, 180), "S": (180, 360), "W": (-90, 90), "E": (90, 270)}[wall]
            d.pieslice(box, start, end, fill=(255, 150, 130, 255), outline=(120, 20, 16, 255))
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
    return img


def shadow_fraction(room: dict, pps: int = 10) -> float | None:
    """Share of a dark tile's corridor and hall floor that no lamp lights (None without a floor map or when not dark)."""
    p = room["tile"]
    rows = _floor(p)
    if rows is None or "power_failure" not in room["states"]:
        return None
    W, H = int(p.w * pps), int(p.h * pps)
    light, _spots = _light_map(p, rows, W, H, pps, LIGHT_RADIUS)
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


def paint(res, g, layer: Image.Image, x0, y0, pps, gm=True, images=None):
    """Draw every affected room's overlay onto ``layer`` (the level render)."""
    from . import render
    for room in affected(res, g):
        p = room["tile"]
        art = render.oriented_thumb(images, p) if images is not None else None
        im = room_overlay(room, pps, "all" if gm else "public", art)
        if im is not None:
            _put(layer, im, int(round((p.x - x0) * pps)), int(round((p.y - y0) * pps)))
