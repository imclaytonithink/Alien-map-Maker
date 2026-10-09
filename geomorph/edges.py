"""Edge/door analysis of the tile images (run once, stored in the manifest).

Every tile image has a 2-square transparent border; the plan itself is
``w x h`` squares at 300 px per square. Walls are opaque lines on the tile
boundary; corridors and doorways leave gaps in them. For each of the four
sides we sample a thin band centred on the boundary and record, per square,
how see-through it is (1 - mean alpha, normalised to the wall level of that side).

The result per side is ``{"cls": [WALL|DOOR|VOID per square], "raw": [0..1], "conf": f}``.
``conf`` is the fraction of squares that are clearly wall or clearly open;
tiles below ``REVIEW_CONF`` are flagged ``review`` so they can be corrected in
the hand-editing file ``edge_overrides.json`` (hybrid approach: auto-detect,
then fix by hand).
"""
from __future__ import annotations

from PIL import Image

from .registry import BORDER_SQUARES, PX_PER_SQUARE, SIDES

Image.MAX_IMAGE_PIXELS = None
BAND = 36                 # px, centred on the boundary line
REVIEW_CONF = 0.9

# Per-square classes along a tile side.
WALL, DOOR, VOID = 0, 1, 2   # wall line | door glyph in the wall | no wall at all
# VOID means "no boundary wall": on a hull side that is the outside; on an
# interior side of a standard tile it is an open span that connects like a door.


def passable(cls) -> bool:
    return cls != WALL


def classify(raw):
    """Class of one boundary square from its normalised see-through value.

    Walls on the tile boundary are solid lines (~0). A door glyph dips the
    average to about a third. No line at all (open floor, chamfered hull) is
    transparent (~1).
    """
    if raw >= 0.7:
        return VOID
    if raw > 0.15:
        return DOOR
    return WALL


def _clear(raw):
    return raw <= 0.1 or raw >= 0.8 or 0.25 <= raw <= 0.5


def side_data(raw):
    cls = [classify(v) for v in raw]
    conf = round(sum(1 for v in raw if _clear(v)) / len(raw), 2)
    return {"cls": cls, "raw": list(raw), "conf": conf}


def _alpha(im):
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    return im.getchannel("A")


def analyse_image(path, w, h):
    """Return per-side edge data for a w x h (squares) tile image."""
    im = Image.open(path)
    alpha = _alpha(im)
    S, B = PX_PER_SQUARE, BORDER_SQUARES * PX_PER_SQUARE
    x0, y0, x1, y1 = B, B, B + w * S, B + h * S
    half = BAND // 2
    boxes = {"N": (x0, y0 - half, x1, y0 + half, True),
             "S": (x0, y1 - half, x1, y1 + half, True),
             "W": (x0 - half, y0, x0 + half, y1, False),
             "E": (x1 - half, y0, x1 + half, y1, False)}
    out = {}
    for side in SIDES:
        l, t, r, b, horiz = boxes[side]
        band = alpha.crop((l, t, r, b))
        n = w if horiz else h
        band = band.resize((n, 1) if horiz else (1, n), Image.BOX)
        vals = [band.getpixel((i, 0) if horiz else (0, i)) / 255.0 for i in range(n)]
        wall = max(vals) or 1.0
        raw = [round(1.0 - v / wall, 2) for v in vals]
        out[side] = side_data(raw)
    return out
