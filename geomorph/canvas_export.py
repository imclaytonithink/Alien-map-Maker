"""Turn a generation result into SceneBoard canvas pieces (no Qt needed).

Tiles become image nodes that point at the library's copy of the tile PNG (or
an embedded thumbnail when the pack is not in the library); procedural filler
becomes small embedded PNGs; the numbered key and markers become text nodes on
their own layers, so the GM can hide or delete them.
"""
from __future__ import annotations

import base64
from pathlib import Path

from . import filler as F
from .placement import tile_piece
from .registry import BORDER_SQUARES, PX_PER_SQUARE

LAYER_TILES, LAYER_FILLER, LAYER_KEY, LAYER_GM = "Geomorph tiles", "Geomorph filler", "Geomorph key", "Geomorph GM only"


def library_resolver(assets):
    """tile -> (store-relative path, px_w, px_h) using file names in the library.

    ``assets`` is an iterable of objects with ``path``, ``width`` and ``height``.
    A tile's PNG is matched by its file name, which is unique per tile in the pack.
    """
    by_name = {}
    for a in assets:
        by_name.setdefault(str(a.path).replace("\\", "/").rsplit("/", 1)[-1].lower(), a)

    def resolve(tile):
        name = tile.image.replace("\\", "/").rsplit("/", 1)[-1].lower()
        a = by_name.get(name)
        if a is None:
            return None
        return a.path, int(a.width or 0) or None, int(a.height or 0) or None
    return resolve


def _embed(path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _text_piece(text, cx, cy, cell, layer, size=0.9, color="#e8fafa", bg="#0c161a", level=None):
    w = h = cell * 2.0
    return {"is_text": True, "text": str(text), "name": f"Key {text}", "x": cx * cell - w / 2, "y": cy * cell - h / 2,
            "w": w, "h": h, "scale": 1.0, "rotation": 0, "flip_h": False, "flip_v": False, "layer_name": layer,
            "snap": False, "opacity": 1.0, "font_size": max(6, int(cell * size)), "font_bold": True,
            "text_color": color, "text_background_color": bg, "text_background_opacity": 0.9,
            "text_auto_size": False, "text_halign": "center", "text_valign": "center", "level": level}


MARKER_GLYPH = {"threat": ("!", "#ff6b5e"), "secret": ("S", "#d9a6ff"), "breach": ("B", "#ffa040"),
                "damage": ("X", "#ff7a5a"), "salvage": ("-", "#a0a0b0"), "entrance": ("E", "#f0aa3c"),
                "shaft": ("U", "#7fe8ee")}


def to_canvas(res, cell: float, resolver=None, tile_images=None, filler_dir=None, gm=True, add_key=True):
    """Return ``{"levels": [{"name", "pieces"}], "warnings": [...], "grid": (cols, rows)}``.

    ``resolver(tile)`` -> ``(asset_path, px_w, px_h)`` or ``None`` (not in the library).
    ``tile_images`` (a ``render.TileImages``) supplies an embedded fallback thumbnail.
    """
    warnings, missing = [], set()
    out_levels = []
    n_tiles = 0
    cols = rows = 0
    for g in res.grids:
        pieces = []
        for p in g.placed:
            t = p.tile
            asset = resolver(t) if resolver else None
            emb = ""
            if asset is None:
                thumb = tile_images.thumb(t) if tile_images is not None else None
                if thumb is not None:
                    import io
                    buf = io.BytesIO()
                    thumb.save(buf, "PNG")
                    emb = base64.b64encode(buf.getvalue()).decode("ascii")
                    px_w, px_h = thumb.size
                    path = ""
                else:
                    missing.add(t.id)
                    continue
            else:
                path, px_w, px_h = asset
                px_w = px_w or (t.w + 2 * BORDER_SQUARES) * PX_PER_SQUARE
                px_h = px_h or (t.h + 2 * BORDER_SQUARES) * PX_PER_SQUARE
            d = tile_piece(p, cell, LAYER_TILES, asset_path=path)
            if t.bbox:
                if emb:
                    d["embedded"], d["asset_path"] = emb, ""
                pieces.append(d)
                n_tiles += 1
                continue
            d["w"], d["h"] = px_w, px_h
            d["scale"] = cell * (t.w + 2 * BORDER_SQUARES) / px_w
            # re-centre with the true pixel size (library copy may be lower resolution)
            cw, ch = p.w * cell, p.h * cell
            ww, hh = px_w * d["scale"], px_h * d["scale"]
            d["x"] = p.x * cell + cw / 2 - ww / 2
            d["y"] = p.y * cell + ch / 2 - hh / 2
            if emb:
                d["embedded"] = emb
                d["asset_path"] = ""
            pieces.append(d)
            n_tiles += 1
        if filler_dir is not None:
            for f in g.filler:
                big = max(f["w"], f["h"])
                pps = max(6, min(60, int(1400 / max(1, big))))
                png = F.filler_png(f, filler_dir, pps)
                d = F.filler_piece_dict(f, cell, "", pps, LAYER_FILLER)
                d["embedded"] = _embed(png)
                d["asset_path"] = ""
                pieces.insert(0 if f["kind"] in ("ground", "rock", "road", "water") else len(pieces), d)
        if add_key:
            for e in res.key:
                if e.get("level") == g.index and e.get("n") is not None:
                    pieces.append(_text_piece(e["n"], e["x"], e["y"], cell, LAYER_KEY))
            for m in res.markers:
                if m.get("level") not in (None, g.index) or (m.get("gm_only") and not gm):
                    continue
                glyph = MARKER_GLYPH.get(m["type"])
                if glyph:
                    pieces.append(_text_piece(glyph[0], m["x"], m["y"], cell, LAYER_GM if m.get("gm_only") else LAYER_KEY,
                                              size=0.8, color=glyph[1]))
        out_levels.append({"name": g.name, "pieces": pieces})
        cols = max(cols, g.cols)
        rows = max(rows, g.rows)
    if missing:
        warnings.append(f"{len(missing)} tile image(s) were not found in the library or the tile folder and were skipped: "
                        + ", ".join(sorted(missing)[:8]))
    xs = [d["x"] for lv in out_levels for d in lv["pieces"]]
    ys = [d["y"] for lv in out_levels for d in lv["pieces"]]
    xe = [d["x"] + d["w"] * d["scale"] for lv in out_levels for d in lv["pieces"]]
    ye = [d["y"] + d["h"] * d["scale"] for lv in out_levels for d in lv["pieces"]]
    need = (int(max(xe) // cell) + 2, int(max(ye) // cell) + 2) if xe else (cols, rows)
    return {"levels": out_levels, "warnings": warnings, "grid": need, "tiles": n_tiles,
            "wanted": sum(len(g.placed) for g in res.grids),
            "title": res.text.get("title", res.meta.get("name", ""))}
