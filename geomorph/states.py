"""Room-state symbols and the map legend.

Every room in a bad state gets a small symbol on its floor, and the map gets a legend in an empty corner that
explains the symbols and how each state looks:

* Power out - dark, red emergency lamps          (lightning bolt, struck through)
* Lockdown - doors sealed, power on, alarm lamps (padlock, on caution-tape yellow)
* Quarantine - sealed against contamination      (biohazard trefoil, green)
* Hull breach - decompression, on the hull       (hole in the hull, air rushing out)
* Battle damage - scorched, rubble on the floor  (cracked plate)

Drawn with PIL, so the generator preview, its exports and the canvas (embedded pictures) all use the same art.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFont

ORDER = ("power_failure", "lockdown", "quarantine", "breach", "damage")
MARKER_STATES = ("breach", "damage")     # these come from the map's markers, not from whole rooms
LABEL = {"power_failure": "Power out", "lockdown": "Lockdown", "quarantine": "Quarantine", "breach": "Hull breach",
         "damage": "Battle damage"}
ABOUT = {"power_failure": "Dark. Red emergency lamps light the corridors and big rooms.",
         "lockdown": "Doors sealed, power on. Amber alarm lamps; caution tape round the room.",
         "quarantine": "Sealed against contamination. Green dashed line round the room.",
         "breach": "Hole in the hull: decompression, vacuum beyond.",
         "damage": "Scorched and torn up, rubble on the floor. Walls and doors may give."}
K = 4                                    # drawn this many times larger, then shrunk: smooth edges


def _font(size):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "DejaVuSans.ttf", "arial.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def symbol(state: str, size: int) -> Image.Image:
    """The state's symbol as an RGBA image ``size`` pixels square."""
    S = size * K
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = S / 2
    lw = max(2, S // 14)
    if state == "lockdown":
        d.ellipse((lw, lw, S - lw, S - lw), fill=(247, 200, 20, 255), outline=(16, 16, 16, 255), width=lw)
        bw, bh = S * 0.42, S * 0.30                             # padlock: body and shackle
        bx, by = r - bw / 2, r - bh / 2 + S * 0.08
        d.arc((r - bw * 0.32, by - bh * 0.95, r + bw * 0.32, by + bh * 0.35), 180, 360, fill=(16, 16, 16, 255), width=lw)
        d.rectangle((bx, by, bx + bw, by + bh), fill=(16, 16, 16, 255))
        d.ellipse((r - lw * 0.7, by + bh * 0.3, r + lw * 0.7, by + bh * 0.3 + lw * 1.4), fill=(247, 200, 20, 255))
    elif state == "power_failure":
        d.ellipse((lw, lw, S - lw, S - lw), fill=(14, 16, 26, 255), outline=(255, 60, 50, 255), width=lw)
        bolt = [(0.56, 0.16), (0.30, 0.55), (0.48, 0.55), (0.40, 0.86), (0.70, 0.43), (0.52, 0.43), (0.62, 0.16)]
        d.polygon([(x * S, y * S) for x, y in bolt], fill=(255, 214, 60, 255))
        d.line((S * 0.22, S * 0.22, S * 0.78, S * 0.78), fill=(255, 60, 50, 255), width=lw + 1)
    elif state == "quarantine":
        green = (70, 220, 120, 255)
        d.ellipse((lw, lw, S - lw, S - lw), fill=(10, 40, 24, 255), outline=green, width=lw)
        lobe = S * 0.19                                          # biohazard trefoil: three rings round a hub
        for k in range(3):
            a = math.radians(-90 + k * 120)
            cx, cy = r + math.cos(a) * S * 0.17, r + math.sin(a) * S * 0.17
            d.ellipse((cx - lobe, cy - lobe, cx + lobe, cy + lobe), outline=green, width=lw)
        hub = S * 0.07
        d.ellipse((r - hub, r - hub, r + hub, r + hub), fill=green)
    elif state == "breach":
        orange = (255, 140, 40, 255)
        d.ellipse((lw, lw, S - lw, S - lw), fill=(40, 20, 10, 255), outline=orange, width=lw)
        pts = []
        for k in range(12):                                       # a ragged hole in the hull...
            a = math.radians(k * 30 + 8)
            rad = S * (0.17 if k % 2 == 0 else 0.11)
            pts.append((r + math.cos(a) * rad, r + math.sin(a) * rad))
        d.polygon(pts, fill=(0, 0, 0, 255), outline=orange, width=max(1, lw // 2))
        for k in range(4):                                        # ...with the air rushing out of it
            a = math.radians(k * 90)
            ca, sa = math.cos(a), math.sin(a)
            x1, y1, x2, y2 = r + ca * S * 0.21, r + sa * S * 0.21, r + ca * S * 0.29, r + sa * S * 0.29
            d.line((x1, y1, x2, y2), fill=orange, width=lw)
            hx, hy = -sa * S * 0.09, ca * S * 0.09
            d.polygon([(x2 + ca * S * 0.11, y2 + sa * S * 0.11), (x2 + hx, y2 + hy), (x2 - hx, y2 - hy)], fill=orange)
    elif state == "damage":
        red = (255, 96, 60, 255)
        d.ellipse((lw, lw, S - lw, S - lw), fill=(46, 14, 10, 255), outline=red, width=lw)
        d.rounded_rectangle((S * 0.27, S * 0.27, S * 0.73, S * 0.73), radius=S * 0.05, fill=(150, 150, 156, 255))
        crack = [(0.42, 0.24), (0.50, 0.40), (0.42, 0.52), (0.56, 0.62), (0.50, 0.78)]   # a plate split by a crack
        d.line([(x * S, y * S) for x, y in crack], fill=(46, 14, 10, 255), width=lw + 2, joint="curve")
        d.line([(0.50 * S, 0.40 * S), (0.66 * S, 0.44 * S)], fill=(46, 14, 10, 255), width=lw)
        for x, y in ((0.30, 0.30), (0.70, 0.70)):                 # scorch
            d.ellipse((x * S - lw, y * S - lw, x * S + lw, y * S + lw), fill=red)
    return im.resize((size, size), Image.LANCZOS)


def one_state_per_room(res) -> None:
    """Keep at most one state per room: the first in legend order wins (power out, then lockdown, then quarantine;
    those over a breach or battle damage, and one breach or damage marker per room)."""
    ov = res.overlays or {}
    taken = set()
    for k in ("power_failure", "lockdown", "quarantine"):
        kept = [z for z in ov.get(k, []) if z not in taken]
        taken.update(kept)
        if k in ov:
            ov[k] = kept
    out = []
    for m in res.markers:
        if m.get("type") in MARKER_STATES and m.get("zone"):
            if m["zone"] in taken:
                continue
            taken.add(m["zone"])
        out.append(m)
    res.markers[:] = out


def states_in(res) -> list:
    """The legend's states for this map, in legend order."""
    ov = res.overlays or {}
    have = {k for k in ("power_failure", "lockdown", "quarantine") if ov.get(k)}
    have |= {m.get("type") for m in res.markers if m.get("type") in MARKER_STATES}
    return [k for k in ORDER if k in have]


def _sample(state, w, h):
    """A little swatch showing how the room looks (tape, dashed line, dark with a lamp)."""
    from . import atmosphere as A
    im = Image.new("RGBA", (w, h), (24, 40, 46, 255))
    d = ImageDraw.Draw(im)
    if state == "power_failure":
        im.paste((6, 8, 16, 255), (0, 0, w, h))
        glow = A._light_sprite(h * 2)
        tint = Image.new("RGBA", glow.size, (255, 36, 28, 0))
        tint.putalpha(glow.point(lambda v: int(v * 0.6)))
        im.alpha_composite(tint, (w // 2 - h, -h // 2 - 2))
        d.pieslice((w // 2 - 5, -5, w // 2 + 5, 5), 0, 180, fill=(232, 38, 28, 255))
    else:
        mask = Image.new("L", (w, h), 255)
        band = A._outline_band(mask, w, h, max(3, h // 4) if state == "lockdown" else max(2, h // 7),
                               "tape" if state == "lockdown" else "dashed", max(8, h // 2))
        if state == "lockdown":
            im.alpha_composite(band)
            d.pieslice((w // 2 - 5, 0, w // 2 + 5, 10), 0, 180, fill=(255, 190, 46, 255))
        elif state == "quarantine":
            im.alpha_composite(Image.new("RGBA", (w, h), (60, 200, 110, 40)))
            im.alpha_composite(band)
    if state == "breach":                                         # the hull torn open onto black space
        im.paste((24, 40, 46, 255), (0, 0, w, h))
        cut = [(w * 0.55, 0), (w * 0.62, h * 0.3), (w * 0.52, h * 0.55), (w * 0.64, h * 0.8), (w * 0.58, h), (w, h), (w, 0)]
        d.polygon(cut, fill=(0, 0, 0, 255), outline=(255, 140, 40, 255))
    elif state == "damage":                                       # scorch marks and rubble
        im.paste((24, 40, 46, 255), (0, 0, w, h))
        for i, (fx, fy, fr) in enumerate(((0.3, 0.5, 0.45), (0.7, 0.35, 0.3))):
            rr = h * fr
            d.ellipse((w * fx - rr, h * fy - rr, w * fx + rr, h * fy + rr), fill=(12, 12, 12, 200))
        for fx, fy in ((0.2, 0.3), (0.35, 0.7), (0.5, 0.45), (0.62, 0.75), (0.8, 0.5), (0.72, 0.2)):
            rr = max(1.5, h * 0.09)
            d.rectangle((w * fx - rr, h * fy - rr, w * fx + rr, h * fy + rr), fill=(130, 128, 120, 255))
    d.rectangle((0, 0, w - 1, h - 1), outline=(120, 200, 210, 255))
    return im


def legend(states, square_px: int) -> Image.Image | None:
    """A compact legend panel for ``states`` (None when there is nothing to explain)."""
    if not states:
        return None
    s = max(10, int(square_px))
    pad, icon, sw = s // 2, int(s * 1.4), int(s * 2.4)
    title_f, name_f, text_f = _font(max(10, int(s * 0.75))), _font(max(9, int(s * 0.6))), _font(max(8, int(s * 0.45)))
    row_h = int(s * 1.9)
    tx = pad + icon + s // 3 + sw + s // 3                       # where the words start
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text_w = max(max(probe.textlength(LABEL[st], font=name_f), probe.textlength(ABOUT[st], font=text_f)) for st in states)
    width = int(max(tx + text_w + pad, probe.textlength("Legend", font=title_f) + 2 * pad))
    height = pad * 2 + int(s * 1.2) + row_h * len(states)
    im = Image.new("RGBA", (width, height), (8, 14, 18, 235))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, width - 1, height - 1), outline=(120, 200, 210, 255), width=max(1, s // 12))
    d.text((pad, pad - 2), "Legend", fill=(230, 250, 250, 255), font=title_f)
    y = pad + int(s * 1.2)
    for st in states:
        im.alpha_composite(symbol(st, icon), (pad, y + (row_h - icon) // 2))
        x = pad + icon + s // 3
        im.alpha_composite(_sample(st, sw, int(icon * 0.8)), (x, y + (row_h - int(icon * 0.8)) // 2))
        d.text((tx, y + int(s * 0.1)), LABEL[st], fill=(235, 250, 250, 255), font=name_f)
        d.text((tx, y + int(s * 0.85)), ABOUT[st], fill=(170, 200, 205, 255), font=text_f)
        y += row_h
    return im


def legend_size_sq(states) -> tuple:
    """The legend's footprint in grid squares (for finding a free corner)."""
    im = legend(states, 20)
    return (math.ceil(im.width / 20), math.ceil(im.height / 20)) if im else (0, 0)


def symbol_spot(p):
    """Where a room's state symbol goes: the middle of its floor (squares, map coordinates)."""
    from .dressing import _key_point
    return _key_point(p)


def free_corner(occupied, bounds, size_sq, margin=1):
    """A top-left square (x, y) for a ``size_sq`` box inside ``bounds`` (x0, y0, x1, y1) that touches no occupied box
    (each (x0, y0, x1, y1)); corners first, then any free spot; None when nothing fits."""
    x0, y0, x1, y1 = bounds
    w, h = size_sq

    def free(x, y):
        if x < x0 or y < y0 or x + w > x1 or y + h > y1:
            return False
        return all(x + w + margin <= a or x >= c + margin or y + h + margin <= b or y >= e + margin
                   for a, b, c, e in occupied)
    corners = [(x0 + margin, y0 + margin), (x1 - w - margin, y0 + margin),
               (x0 + margin, y1 - h - margin), (x1 - w - margin, y1 - h - margin)]
    for x, y in corners:
        if free(x, y):
            return (x, y)
    for y in range(int(y0), int(y1 - h) + 1):
        for x in range(int(x0), int(x1 - w) + 1):
            if free(x, y):
                return (x, y)
    return None


SYMBOL_SQ = 1.4                          # a symbol's size in grid squares
_OPEN_KINDS = ("ground", "rock", "road", "water", "void", "patch")


def placements(res, g) -> list:
    """[(state, x, y)] symbol centres (squares) on level ``g``: one per room in a bad state, one per breach or
    battle-damage marker.
    Several states in one room sit side by side."""
    ov = res.overlays or {}
    out, done = [], set()
    for p in g.placed:
        here = [k for k in ("power_failure", "lockdown", "quarantine") if p.zone and p.zone in ov.get(k, [])]
        if not here or p.zone in done:
            continue
        done.add(p.zone)                                   # one set of symbols per room, on its first tile
        x, y = symbol_spot(p)
        y += SYMBOL_SQ + 0.4                               # just below the room's key number, not over it
        for i, st in enumerate(here):
            out.append((st, x + (i - (len(here) - 1) / 2) * (SYMBOL_SQ + 0.2), y))
    for m in res.markers:
        if m.get("type") in MARKER_STATES and m.get("level", g.index) == g.index:
            out.append((m["type"], m["x"], m["y"]))
    return out


def level_states(res, g) -> list:
    """The legend's entries for level ``g``: only the states that appear on it."""
    have = {st for st, _x, _y in placements(res, g)}
    return [k for k in ORDER if k in have]


def legend_spot(res, g, bounds=None):
    """Top-left (x, y) in squares for level ``g``'s legend, in an empty part of the map: a free corner inside the
    map's bounds if there is one, else just right of the map."""
    sts = level_states(res, g)
    if not sts:
        return None
    w, h = legend_size_sq(sts)
    boxes = [(p.x, p.y, p.x + p.w, p.y + p.h) for p in g.placed]
    boxes += [(f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"]) for f in g.filler if f["kind"] not in _OPEN_KINDS]
    if bounds is None:
        xs0 = [b[0] for b in boxes] or [0]
        ys0 = [b[1] for b in boxes] or [0]
        bounds = (min(xs0) - 3, min(ys0) - 3, max(b[2] for b in boxes) + 3, max(b[3] for b in boxes) + 3) if boxes else (0, 0, w, h)
    spot = free_corner(boxes, bounds, (w, h))
    return spot if spot is not None else (bounds[2] + 1, bounds[1] + 1)
