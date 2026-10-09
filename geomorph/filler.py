"""Procedural filler layer: simple grid pieces for what the tile set lacks.

Ground, roads, walls, fences, tunnels, rock, voids, stairs, lifts, shafts,
doors, airlocks, domes, walkways, landing pads, rubble and markers — all on the
same 5 ft grid and drawn in the tiles' own palette and line weight (cyan
linework over dark teal fill), so a map mixing tiles and filler reads as one.

A filler piece is a plain dict ``{"kind", "level", "x", "y", "w", "h"}`` in grid
squares (plus optional ``rot``/``label``). ``draw_filler`` paints one into any
Pillow ``ImageDraw``; ``filler_png`` writes a cached PNG for canvas export.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

LINE = (127, 232, 238, 255)       # tile linework cyan
LINE_DIM = (79, 150, 158, 255)
FILL = (30, 58, 64, 255)          # tile dark teal
FILL_LIGHT = (44, 84, 92, 255)
ROCK = (22, 30, 34, 255)
GROUND = (16, 26, 30, 255)
VOID_C = (6, 8, 10, 255)
WATER = (22, 60, 84, 255)
ALERT = (240, 170, 60, 255)

KINDS = ("scorch", "resin", "drag", "building", "ground", "road", "wall", "fence", "tunnel", "rock", "void", "stairs", "lift",
         "shaft", "door", "airlock", "dome", "walkway", "pad", "rubble", "pit",
         "water", "patch", "crate")


def _lw(pps):
    return max(1, int(pps * 0.09))


def draw_filler(d: ImageDraw.ImageDraw, kind: str, box, pps: float, rot: int = 0, label: str = ""):
    """Draw one filler piece into ``box`` (x0, y0, x1, y1 in pixels)."""
    x0, y0, x1, y1 = [int(v) for v in box]
    lw = _lw(pps)
    sq = int(pps)
    w, h = x1 - x0, y1 - y0
    if kind == "ground":
        d.rectangle(box, fill=GROUND)
        for gx in range(x0, x1, sq * 2):
            for gy in range(y0, y1, sq * 2):
                d.point((gx + sq // 2, gy + sq // 2), fill=LINE_DIM)
    elif kind == "road":
        d.rectangle(box, fill=FILL)
        horiz = w >= h
        if horiz:
            for gx in range(x0 + sq // 2, x1, sq * 2):
                d.line((gx, (y0 + y1) // 2, min(gx + sq, x1), (y0 + y1) // 2), fill=LINE_DIM, width=lw)
        else:
            for gy in range(y0 + sq // 2, y1, sq * 2):
                d.line(((x0 + x1) // 2, gy, (x0 + x1) // 2, min(gy + sq, y1)), fill=LINE_DIM, width=lw)
        d.rectangle(box, outline=LINE_DIM, width=lw)
    elif kind == "wall":
        d.rectangle(box, fill=LINE)
    elif kind == "fence":
        horiz = w >= h
        if horiz:
            yy = (y0 + y1) // 2
            d.line((x0, yy, x1, yy), fill=LINE_DIM, width=lw)
            for gx in range(x0, x1 + 1, sq):
                d.line((gx, yy - sq // 4, gx, yy + sq // 4), fill=LINE, width=lw)
        else:
            xx = (x0 + x1) // 2
            d.line((xx, y0, xx, y1), fill=LINE_DIM, width=lw)
            for gy in range(y0, y1 + 1, sq):
                d.line((xx - sq // 4, gy, xx + sq // 4, gy), fill=LINE, width=lw)
    elif kind == "tunnel":
        d.rectangle(box, fill=FILL_LIGHT)
        d.rectangle(box, outline=LINE, width=lw)
        horiz = w >= h
        for k in range(sq // 2, (w if horiz else h), sq * 2):
            if horiz:
                d.line((x0 + k, y0, x0 + k, y1), fill=LINE_DIM, width=1)
            else:
                d.line((x0, y0 + k, x1, y0 + k), fill=LINE_DIM, width=1)
    elif kind == "rock":
        d.rectangle(box, fill=ROCK)
        for gx in range(x0, x1, sq):
            for gy in range(y0, y1, sq):
                if (gx // sq * 7 + gy // sq * 13) % 5 == 0:
                    d.line((gx + 3, gy + 3, gx + sq // 2, gy + sq // 3), fill=LINE_DIM, width=1)
    elif kind == "void":
        d.rectangle(box, fill=VOID_C)
        d.rectangle(box, outline=ALERT, width=lw)
        d.line((x0, y0, x1, y1), fill=ALERT, width=1)
        d.line((x0, y1, x1, y0), fill=ALERT, width=1)
    elif kind == "stairs":
        d.rectangle(box, fill=FILL, outline=LINE, width=lw)
        steps = max(3, (h if rot in (0, 180) else w) // max(2, sq // 2))
        for k in range(1, steps):
            if rot in (0, 180):
                yy = y0 + k * h // steps
                d.line((x0, yy, x1, yy), fill=LINE, width=1)
            else:
                xx = x0 + k * w // steps
                d.line((xx, y0, xx, y1), fill=LINE, width=1)
    elif kind == "lift":
        d.rectangle(box, fill=FILL, outline=LINE, width=lw)
        d.line((x0, y0, x1, y1), fill=LINE, width=1)
        d.line((x0, y1, x1, y0), fill=LINE, width=1)
    elif kind == "shaft":
        d.rectangle(box, fill=VOID_C, outline=LINE, width=lw)
        d.ellipse((x0 + w // 4, y0 + h // 4, x1 - w // 4, y1 - h // 4), outline=LINE_DIM, width=lw)
    elif kind == "door":
        d.rectangle(box, fill=FILL)
        horiz = w >= h
        if horiz:
            d.rectangle((x0 + 2, y0 + h // 3, x1 - 2, y1 - h // 3), fill=LINE, outline=LINE)
        else:
            d.rectangle((x0 + w // 3, y0 + 2, x1 - w // 3, y1 - 2), fill=LINE, outline=LINE)
    elif kind == "airlock":
        d.rectangle(box, fill=FILL, outline=ALERT, width=lw)
        d.line((x0, y0, x1, y1), fill=ALERT, width=1)
        d.line((x0, y1, x1, y0), fill=ALERT, width=1)
    elif kind == "dome":
        d.ellipse(box, outline=LINE, width=lw)
    elif kind == "walkway":
        d.rectangle(box, fill=FILL, outline=LINE, width=lw)
        horiz = w >= h
        for k in range(sq, (w if horiz else h), sq):
            if horiz:
                d.line((x0 + k, y0, x0 + k, y1), fill=LINE_DIM, width=1)
            else:
                d.line((x0, y0 + k, x1, y0 + k), fill=LINE_DIM, width=1)
    elif kind == "pad":
        d.rectangle(box, fill=FILL, outline=LINE, width=lw)
        cx, cy, r = (x0 + x1) // 2, (y0 + y1) // 2, min(w, h) // 3
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=LINE, width=lw)
        d.line((cx - r // 2, cy, cx + r // 2, cy), fill=LINE, width=lw)
        d.line((cx, cy - r // 2, cx, cy + r // 2), fill=LINE, width=lw)
    elif kind == "rubble":
        d.rectangle(box, fill=ROCK)
        for gx in range(x0, x1, max(4, sq // 2)):
            for gy in range(y0, y1, max(4, sq // 2)):
                if (gx * 31 + gy * 17) % 7 < 3:
                    d.polygon([(gx, gy), (gx + sq // 3, gy + 2), (gx + 3, gy + sq // 3)], fill=LINE_DIM)
    elif kind == "pit":
        d.rectangle(box, fill=ROCK, outline=LINE, width=lw)
        for k in range(1, 4):
            m = k * min(w, h) // 8
            d.rectangle((x0 + m, y0 + m, x1 - m, y1 - m), outline=LINE_DIM, width=1)
    elif kind == "water":
        d.rectangle(box, fill=WATER)
    elif kind == "patch":
        d.rectangle(box, fill=LINE)
    elif kind == "scorch":                # acid burn / scorch mark: dark blotch with a hot rim
        d.ellipse(box, fill=(12, 10, 8, 235))
        m = max(2, min(w, h) // 5)
        d.ellipse((x0 + m, y0 + m, x1 - m, y1 - m), outline=(200, 110, 40, 255), width=max(1, lw))
    elif kind == "resin":                 # organic residue: dark blobs with a sickly rim
        d.ellipse(box, fill=(30, 36, 18, 235), outline=(150, 170, 60, 255), width=max(1, lw))
        d.ellipse((x0 + w // 4, y0 + h // 4, x1 - w // 4, y1 - h // 4), fill=(50, 62, 24, 255))
    elif kind == "drag":                  # drag marks: a smeared trail
        horiz = w >= h
        for k in (-1, 0, 1):
            if horiz:
                yy = (y0 + y1) // 2 + k * max(2, h // 4)
                d.line((x0, yy, x1, yy), fill=(120, 40, 36, 255), width=max(1, lw))
            else:
                xx = (x0 + x1) // 2 + k * max(2, w // 4)
                d.line((xx, y0, xx, y1), fill=(120, 40, 36, 255), width=max(1, lw))
    elif kind == "building":
        d.rectangle(box, fill=FILL_LIGHT, outline=LINE, width=lw * 2)
    elif kind == "crate":
        d.rectangle(box, fill=FILL_LIGHT, outline=LINE_DIM, width=lw)
        d.line((x0, y0, x1, y1), fill=LINE_DIM, width=1)
    else:                                    # unknown kind: visible placeholder
        d.rectangle(box, outline=ALERT, width=lw)
    if label:
        d.text((x0 + 3, y0 + 2), label, fill=LINE)


def _num(v):
    v = float(v)
    return int(v) if v == int(v) else round(v, 2)


def piece(kind, level, x, y, w, h, rot=0, label="", **extra):
    """Make a filler piece dict (all values in grid squares)."""
    assert kind in KINDS, kind
    d = {"kind": kind, "level": level, "x": _num(x), "y": _num(y), "w": max(0.25, _num(w)),
         "h": max(0.25, _num(h)), "rot": rot, "label": label}
    d.update(extra)
    return d


def filler_png(p: dict, out_dir, pps: int = 60) -> str:
    """Write (or reuse) a PNG for one filler piece and return its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"filler_{p['kind']}_{p['w']}x{p['h']}_r{p.get('rot', 0)}.png"
    path = out_dir / name
    if not path.exists():
        wpx, hpx = max(1, round(p["w"] * pps)), max(1, round(p["h"] * pps))
        im = Image.new("RGBA", (wpx, hpx), (0, 0, 0, 0))
        draw_filler(ImageDraw.Draw(im), p["kind"], (0, 0, wpx, hpx), pps, p.get("rot", 0))
        im.save(path)
    return str(path)


def filler_piece_dict(p: dict, cell: float, asset_path: str, pps: int = 60, layer="Generated"):
    """Canvas piece dict for a filler piece whose PNG is ``asset_path``."""
    w_px, h_px = max(1, round(p["w"] * pps)), max(1, round(p["h"] * pps))
    scale = cell * p["w"] / w_px
    return {"asset_path": asset_path, "name": f"{p['kind']} {p.get('label', '')}".strip(),
            "x": p["x"] * cell, "y": p["y"] * cell, "w": w_px, "h": h_px, "scale": scale,
            "rotation": 0, "flip_h": False, "flip_v": False, "layer_name": layer,
            "snap": True, "opacity": 1.0, "kind": p["kind"], "level": p["level"]}
