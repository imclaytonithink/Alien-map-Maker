"""Render levels to PNG (and assemble PDFs) from a generation result.

Tiles are loaded from the pack folder and cached as small thumbnails so a map
renders in seconds after the first run. The drawing is shared by the preview
and the exporter. ``gm=True`` includes the GM-only layer (secrets, threat
markers); the player version hides it.
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import CREDITS
from . import filler as F
from .registry import BORDER_SQUARES, PX_PER_SQUARE

Image.MAX_IMAGE_PIXELS = None
BG = (9, 13, 15, 255)
TOP_KINDS = {"door", "airlock", "patch"}
THUMB_PPS = 15          # cached thumbnail: pixels per grid square (300 / 20)


def default_cache_dir():
    return Path(os.environ.get("GEOMORPH_CACHE", Path.home() / ".cache" / "geomorph_thumbs"))


class TileImages:
    """Loads tile images (cached thumbnails); injectable for tests."""

    def __init__(self, tiles_dir, cache_dir=None, loader=None):
        self.tiles_dir = Path(tiles_dir) if tiles_dir else None
        self.cache = Path(cache_dir) if cache_dir else default_cache_dir()
        self.loader = loader          # loader(tile) -> RGBA image at THUMB_PPS (tests)
        self._mem = {}

    symbols_dir = None                  # folder of the Symbols pack (decor sprites)

    def thumb_rel(self, rel: str, symbol=False):
        """Thumbnail of any pack image by relative path (overlays; symbols when ``symbol``)."""
        class _T:                       # minimal tile-like holder
            id = ("sym:" if symbol else "ov:") + rel
            image = rel
        if symbol:
            saved = self.tiles_dir
            self.tiles_dir = Path(self.symbols_dir) if self.symbols_dir else None
            try:
                return self.thumb(_T)
            finally:
                self.tiles_dir = saved
        return self.thumb(_T)

    def thumb(self, tile) -> Image.Image | None:
        if tile.id in self._mem:
            return self._mem[tile.id]
        im = None
        if self.loader is not None:
            im = self.loader(tile)
        else:
            safe = tile.id.replace("/", "_").replace(":", "_")
            cp = self.cache / f"{safe}.png"
            if cp.exists():
                im = Image.open(cp).convert("RGBA")
            elif self.tiles_dir is not None and (self.tiles_dir / tile.image).exists():
                src = Image.open(self.tiles_dir / tile.image)
                src.load()
                k = PX_PER_SQUARE // THUMB_PPS
                rgba = src.convert("RGBA")
                im = rgba.reduce(k)
                try:
                    self.cache.mkdir(parents=True, exist_ok=True)
                    im.save(cp)
                except OSError:
                    pass
        self._mem[tile.id] = im
        return im


def _font(size, bold=True):
    names = ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf") if bold else ("DejaVuSans.ttf", "Arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                      # old Pillow: fixed-size bitmap font
        return ImageFont.load_default()


def level_bounds(res, level_index, margin=3):
    g = res.grids[level_index]
    xs0, ys0, xs1, ys1 = [], [], [], []
    for p in g.placed:
        xs0.append(p.x); ys0.append(p.y); xs1.append(p.x + p.w); ys1.append(p.y + p.h)
    for f in g.filler:
        xs0.append(f["x"]); ys0.append(f["y"]); xs1.append(f["x"] + f["w"]); ys1.append(f["y"] + f["h"])
    if not xs0:
        return (0, 0, 20, 20)
    return (max(0, int(min(xs0)) - margin), max(0, int(min(ys0)) - margin),
            int(max(xs1)) + margin + 1, int(max(ys1)) + margin + 1)


def shared_bounds(res, margin=3):
    crop = res.meta.get("crop")
    if crop:
        return (max(0, int(crop[0])), max(0, int(crop[1])), int(crop[2]), int(crop[3]))
    bs = [level_bounds(res, i, margin) for i in range(len(res.grids))]
    return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))


def render_level(res, level_index, images: TileImages, pps=16, gm=True, numbers=True,
                 bounds=None, title=True, shared=True) -> Image.Image:
    g = res.grids[level_index]
    x0, y0, x1, y1 = bounds or (shared_bounds(res) if shared else level_bounds(res, level_index))
    W, H = int((x1 - x0) * pps), int((y1 - y0) * pps)
    head = int(pps * 3) if title else 0
    foot = int(pps * 2.2)
    im = Image.new("RGBA", (W, H + head + foot), BG)
    layer = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(layer)

    def px(v, o):
        return int(round((v - o) * pps))
    below = [f for f in g.filler if f["kind"] not in TOP_KINDS and f["kind"] != "void" and not f.get("decor")]
    below.sort(key=lambda f: f["kind"] not in ("ground", "rock", "water"))      # terrain first, then what stands on it
    for f in below:
        draw_box = (px(f["x"], x0), px(f["y"], y0), px(f["x"] + f["w"], x0), px(f["y"] + f["h"], y0))
        F.draw_filler(d, f["kind"], draw_box, pps, f.get("rot", 0), f.get("label", "") if f["kind"] in ("building", "pad", "pit") else "")
    for p in g.placed:
        th = images.thumb(p.tile)
        bx0, by0 = px(p.x, x0), px(p.y, y0)
        if th is None:
            d.rectangle((bx0, by0, px(p.x + p.w, x0), px(p.y + p.h, y0)), fill=F.FILL, outline=F.LINE)
            d.text((bx0 + 3, by0 + 3), p.tile.id, fill=F.LINE)
            continue
        t = th
        scale = pps / THUMB_PPS
        if abs(scale - 1) > 1e-6:
            t = t.resize((max(1, int(t.width * scale)), max(1, int(t.height * scale))), Image.LANCZOS)
        if p.o.mirror:
            t = t.transpose(Image.FLIP_LEFT_RIGHT)
        if p.o.rot:
            t = t.transpose({90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}[p.o.rot])
        border = int(BORDER_SQUARES * pps)
        if p.tile.bbox:
            ox, oy, _w, _h = p.tile.image_geometry(p.o.rot, p.o.mirror)
            _paste_clipped(layer, t, bx0 - int(ox * pps), by0 - int(oy * pps))
            continue
        layer.alpha_composite(t, (bx0 - border, by0 - border)) if bx0 - border >= 0 and by0 - border >= 0 \
            else _paste_clipped(layer, t, bx0 - border, by0 - border)
        _draw_craft(res, p, layer, images, x0, y0, pps)
    d = ImageDraw.Draw(layer)
    for f in g.filler:                      # incident marks (debris, burns, resin, drag trails) sit on top of the tiles
        if f.get("decor"):
            box = (px(f["x"], x0), px(f["y"], y0), px(f["x"] + f["w"], x0), px(f["y"] + f["h"], y0))
            F.draw_filler(d, f["kind"], box, pps, f.get("rot", 0), "")
    _draw_decor(res, g, layer, images, x0, y0, pps)
    d = ImageDraw.Draw(layer)
    for f in g.filler:
        if f["kind"] in TOP_KINDS or f["kind"] == "void":
            box = (px(f["x"], x0), px(f["y"], y0), px(f["x"] + f["w"], x0), px(f["y"] + f["h"], y0))
            F.draw_filler(d, f["kind"], box, pps, f.get("rot", 0), f.get("label", "") if f["kind"] in ("void", "airlock") else "")
    _overlays(res, g, layer, x0, y0, pps, gm)
    _markers(res, g, layer, x0, y0, pps, gm, numbers)
    tilt = res.meta.get("tilt")
    if tilt:                               # a crashed wreck sits at an angle
        layer = layer.rotate(tilt, expand=True, resample=Image.BICUBIC, fillcolor=BG)
        im = Image.new("RGBA", (layer.width, layer.height + head + foot), BG)
        H = layer.height
    im.paste(layer, (0, head))
    dr = ImageDraw.Draw(im)
    if title:
        name = res.meta.get("name", "")
        dr.text((int(pps), int(pps * 0.5)), f"{name} — {g.name}".strip(" —"), fill=F.LINE, font=_font(int(pps * 1.3)))
        sub = res.meta.get("archetype") or res.meta.get("ship_type") or ""
        dr.text((int(pps), int(pps * 1.9)), f"seed {res.meta.get('seed', '')}   {sub}", fill=F.LINE_DIM,
                font=_font(max(8, int(pps * 0.8))))
    sq = int(pps)
    ly = H + head + int(pps * 0.5)
    dr.rectangle((int(pps), ly, int(pps) + sq, ly + sq), outline=F.LINE)
    dr.rectangle((int(pps) + sq, ly, int(pps) + 2 * sq, ly + sq), outline=F.LINE)
    dr.text((int(pps) + 2 * sq + 6, ly + 2), "= 1 TON  (5 ft squares)", fill=F.LINE, font=_font(max(8, int(pps * 0.8))))
    return im


def _draw_craft(res, p, layer, images, x0, y0, pps):
    """Optional craft/vehicle overlays on a tile (``options['craft']`` lists the categories)."""
    from .overlays import selected_overlays
    craft = (res.options or {}).get("craft")
    for rel in selected_overlays(p.tile, craft):
        th = images.thumb_rel(rel)
        if th is None:
            continue
        t = th
        scale = pps / THUMB_PPS
        if abs(scale - 1) > 1e-6:
            t = t.resize((max(1, int(t.width * scale)), max(1, int(t.height * scale))), Image.LANCZOS)
        if p.o.mirror:
            t = t.transpose(Image.FLIP_LEFT_RIGHT)
        if p.o.rot:
            t = t.transpose({90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}[p.o.rot])
        border = int(BORDER_SQUARES * pps)
        _paste_clipped(layer, t, int((p.x - x0) * pps) - border, int((p.y - y0) * pps) - border)


def _draw_decor(res, g, layer, images, x0, y0, pps):
    """Symbols (furniture, machinery, cargo...) at real size, rotated about their own centre."""
    syms = getattr(res, "symbols", None) or {}
    for it in getattr(res, "decor", []) or []:
        if it["level"] != g.index or it["sym"] not in syms:
            continue
        s = syms[it["sym"]]
        th = images.thumb_rel(s.rel, symbol=True)
        if th is None:
            continue
        k = SYM_STEP = 20                       # pack px per square (300) / thumbnail px per square (15)
        l, t, rr, b = [v // 20 for v in s.bbox[:2]] + [-(-v // 20) for v in s.bbox[2:]]
        t_ = th.crop((l, t, rr, b))
        if it.get("flip"):
            t_ = t_.transpose(Image.FLIP_LEFT_RIGHT)
        scale = pps / THUMB_PPS
        if abs(scale - 1) > 1e-6:
            t_ = t_.resize((max(1, int(t_.width * scale)), max(1, int(t_.height * scale))), Image.LANCZOS)
        if it["rot"] % 360:
            t_ = t_.rotate(-it["rot"], expand=True, resample=Image.BICUBIC)
        _paste_clipped(layer, t_, int((it["cx"] - x0) * pps - t_.width / 2), int((it["cy"] - y0) * pps - t_.height / 2))


def _paste_clipped(dst, src, x, y):
    """alpha_composite ``src`` at (x, y), clipped to ``dst`` (nothing happens when it is entirely outside)."""
    l, t = max(0, x), max(0, y)
    rr, bb = min(dst.width, x + src.width), min(dst.height, y + src.height)
    if rr <= l or bb <= t:
        return
    dst.alpha_composite(src.crop((l - x, t - y, rr - x, bb - y)), (l, t))


def _overlays(res, g, layer, x0, y0, pps, gm):
    ov = res.overlays or {}
    over = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    zone_boxes = {}
    for p in g.placed:
        zone_boxes.setdefault(p.zone, []).append(p)
    for zid in ov.get("power_failure", []):
        for p in zone_boxes.get(zid, []):
            d.rectangle(((p.x - x0) * pps, (p.y - y0) * pps, (p.x + p.w - x0) * pps, (p.y + p.h - y0) * pps),
                        fill=(0, 0, 0, 150))
    for zid in ov.get("quarantine", []):
        for p in zone_boxes.get(zid, []):
            d.rectangle(((p.x - x0) * pps, (p.y - y0) * pps, (p.x + p.w - x0) * pps, (p.y + p.h - y0) * pps),
                        outline=(240, 200, 60, 255), width=max(2, int(pps * 0.2)))
    for zid in ov.get("lockdown", []):
        for p in zone_boxes.get(zid, []):
            d.rectangle(((p.x - x0) * pps, (p.y - y0) * pps, (p.x + p.w - x0) * pps, (p.y + p.h - y0) * pps),
                        outline=(230, 70, 60, 255), width=max(2, int(pps * 0.15)))
    layer.alpha_composite(over)


def _markers(res, g, layer, x0, y0, pps, gm, numbers):
    d = ImageDraw.Draw(layer)
    fnt = _font(max(8, int(pps * 0.95)))
    for m in res.markers:
        if m.get("level") not in (None, g.index) and g.index not in m.get("levels", [m.get("level")]):
            continue
        if m.get("gm_only") and not gm:
            continue
        cx, cy = (m["x"] - x0) * pps, (m["y"] - y0) * pps
        t = m["type"]
        r = pps * 0.9
        if t == "threat":
            d.polygon([(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)], fill=(200, 40, 40, 235), outline=(255, 200, 200, 255))
            d.text((cx - r * 0.25, cy - r * 0.2), "!", fill=(255, 255, 255, 255), font=fnt)
        elif t == "secret":
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(120, 40, 160, 235), outline=(230, 200, 255, 255))
            d.text((cx - r * 0.35, cy - r * 0.55), "S", fill=(255, 255, 255, 255), font=fnt)
        elif t == "shaft":
            d.ellipse((cx - r * 0.8, cy - r * 0.8, cx + r * 0.8, cy + r * 0.8), outline=F.LINE, width=2, fill=(5, 8, 10, 255))
            d.text((cx - r * 0.35, cy - r * 0.55), "U", fill=F.LINE, font=fnt)
        elif t == "entrance":
            d.polygon([(cx, cy), (cx - r, cy - r * 1.4), (cx + r, cy - r * 1.4)], fill=F.ALERT)
        elif t in ("breach", "damage", "salvage"):
            col = {"breach": (255, 140, 40, 255), "damage": (230, 90, 60, 255), "salvage": (140, 140, 160, 255)}[t]
            d.ellipse((cx - r * 0.7, cy - r * 0.7, cx + r * 0.7, cy + r * 0.7), outline=col, width=3)
            d.line((cx - r * 0.5, cy - r * 0.5, cx + r * 0.5, cy + r * 0.5), fill=col, width=2)
    if numbers:
        for entry in res.key:
            if entry.get("level") != g.index or entry.get("n") is None:
                continue
            cx, cy = (entry["x"] - x0) * pps, (entry["y"] - y0) * pps
            r = pps * 1.0
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(12, 22, 26, 235), outline=F.LINE, width=2)
            txt = str(entry["n"])
            tw = d.textlength(txt, font=fnt)
            d.text((cx - tw / 2, cy - r * 0.6), txt, fill=(235, 250, 250, 255), font=fnt)


def render_section(res, pps=18) -> Image.Image:
    """Side-view building section: stacked levels, vertical cores, shafts, tall rooms."""
    sec = res.section or {}
    levels = sec.get("levels", [])
    if not levels:
        return Image.new("RGBA", (10, 10), BG)
    width = max(l["w"] for l in levels)
    lh = 4
    W, H = int(width * pps * 0.4 + 9 * pps), int(len(levels) * lh * pps * 0.6 + 4 * pps)
    im = Image.new("RGBA", (W, H), BG)
    d = ImageDraw.Draw(im)
    fnt = _font(max(8, int(pps * 0.8)))
    k = pps * 0.4
    x_off = 6 * pps
    y = 2 * pps
    for l in levels:
        x = x_off + l["x"] * k
        w = l["w"] * k
        h = lh * pps * 0.55
        d.rectangle((x, y, x + w, y + h), fill=F.FILL, outline=F.LINE, width=2)
        d.text((4, y + h / 2 - 5), l["name"][:14], fill=F.LINE, font=fnt)
        for z in l.get("zones", []):
            zx = x_off + z["x"] * k
            col = F.FILL_LIGHT if not z.get("void") else F.VOID_C
            d.rectangle((zx + 1, y + 1, zx + z["w"] * k - 1, y + h - 1), fill=col, outline=F.LINE_DIM)
            if z.get("void"):
                d.text((zx + 3, y + 3), "void", fill=F.ALERT, font=fnt)
        y += h + pps * 0.5
    for v in sec.get("verticals", []):
        vx = x_off + v["x"] * k
        d.rectangle((vx, 2 * pps, vx + v["w"] * k, y - pps * 0.5), outline=F.ALERT, width=2)
        d.text((vx + 2, y - pps * 0.4), v.get("label", ""), fill=F.ALERT, font=fnt)
    d.text((pps, 0.3 * pps), "BUILDING SECTION", fill=F.LINE, font=_font(int(pps * 1.0)))
    return im
