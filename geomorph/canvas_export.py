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
from .overlays import selected_overlays
from .placement import tile_piece
from .registry import BORDER_SQUARES, PX_PER_SQUARE

LAYER_TILES, LAYER_FILLER, LAYER_KEY, LAYER_GM = "Geomorph tiles", "Geomorph filler", "Geomorph key", "Geomorph GM only"
LAYER_CRAFT = "Geomorph craft"
LAYER_DECOR = "Geomorph decor"
LAYER_ATMO = "Geomorph atmosphere"
LAYER_STATES = "Room states"           # symbols on rooms that are dark, locked down, quarantined, breached
LAYER_LEGEND = "Map legend"            # explains those symbols; its export can be switched off


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


def room_record(res, placed, cell: float, rotation: float) -> dict:
    """The room a placed tile belongs to, kept on its canvas node for the MU/TH/UR terminal export."""
    zone = (res.zones or {}).get(placed.zone)
    states = [k for k in ("lockdown", "quarantine", "power_failure")
              if placed.zone and placed.zone in (res.overlays or {}).get(k, [])]
    t = placed.tile
    if res.kind == "ship":
        arch, base, name, access = "Ship", placed.zone, t.title, "staff"
        tags = [placed.zone] + [g for g in t.tags if g != placed.zone]
    else:
        arch = res.meta.get("archetype", "")
        base = zone.base if zone is not None else str(placed.zone).split("#")[0]
        name = zone.name if zone is not None else t.title
        tags = list(zone.tags) if zone is not None else list(t.tags)
        access = zone.access if zone is not None else "staff"
    return {"arch": arch, "zone": placed.zone, "base": base, "name": name, "tags": list(tags),
            "access": access, "states": states, "fw": placed.w * cell, "fh": placed.h * cell,
            "rot": float(rotation)}


MARKER_GLYPH = {"threat": ("!", "#ff6b5e"), "secret": ("S", "#d9a6ff"), "breach": ("B", "#ffa040"),
                "damage": ("X", "#ff7a5a"), "salvage": ("-", "#a0a0b0"), "entrance": ("E", "#f0aa3c"),
                "shaft": ("U", "#7fe8ee")}


def _image_piece(im, x, y, cell_px, layer, name, level):
    """An embedded picture placed at (x, y) world px, drawn at ``cell_px`` pixels per square of its own art."""
    import io
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return {"asset_path": "", "embedded": base64.b64encode(buf.getvalue()).decode("ascii"), "name": name,
            "x": x, "y": y, "w": im.width, "h": im.height, "scale": 1.0, "rotation": 0, "flip_h": False,
            "flip_v": False, "layer_name": layer, "snap": False, "opacity": 1.0, "level": level}


def state_pieces(res, g, cell: float) -> list:
    """Room-state symbols and the legend for level ``g`` as canvas pieces (on their own layers)."""
    from . import states as S
    out = []
    art_px = 40                                                  # symbols and legend are drawn at 40 px a square
    scale = cell / art_px
    for st, sx, sy in S.placements(res, g):
        icon = S.symbol(st, int(art_px * S.SYMBOL_SQ))
        d = _image_piece(icon, sx * cell - icon.width * scale / 2, sy * cell - icon.height * scale / 2, art_px,
                         LAYER_STATES, f"{S.LABEL[st]} symbol", g.index)
        d["scale"] = scale
        out.append(d)
    spot = S.legend_spot(res, g)
    if spot is not None:
        panel = S.legend(S.level_states(res, g), art_px)
        d = _image_piece(panel, spot[0] * cell, spot[1] * cell, art_px, LAYER_LEGEND, "Map legend", g.index)
        d["scale"] = scale
        out.append(d)
    return out


def to_canvas(res, cell: float, resolver=None, tile_images=None, filler_dir=None, gm=True, add_key=False,
              legend=True):
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
            d["room"] = room_record(res, p, cell, d.get("rotation", 0))
            if t.bbox:
                if emb:
                    d["embedded"], d["asset_path"] = emb, ""
                # the plan box is off-centre in a wing image: remember where it sits
                d["room"]["ox"] = (p.x + p.w / 2.0) * cell - (d["x"] + d["w"] * d["scale"] / 2.0)
                d["room"]["oy"] = (p.y + p.h / 2.0) * cell - (d["y"] + d["h"] * d["scale"] / 2.0)
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
            for rel in selected_overlays(t, (res.options or {}).get("craft")):
                class _T:
                    image = rel
                ov = resolver(_T) if resolver else None
                if ov is None:
                    continue
                od = dict(d, asset_path=ov[0], name=f"{t.number} craft {rel.rsplit('/', 1)[-1][:40]}",
                          layer_name=LAYER_CRAFT)
                od.pop("embedded", None)
                pieces.append(od)
        if filler_dir is not None:
            for f in g.filler:
                big = max(f["w"], f["h"])
                pps = max(6, min(60, int(1400 / max(1, big))))
                png = F.filler_png(f, filler_dir, pps)
                d = F.filler_piece_dict(f, cell, "", pps, LAYER_FILLER)
                d["embedded"] = _embed(png)
                d["asset_path"] = ""
                pieces.insert(0 if f["kind"] in ("ground", "rock", "road", "water") else len(pieces), d)
        if filler_dir is not None:                    # dim rooms, emergency lamps, shutters (GM layer), hazard borders
            from . import atmosphere as A
            for room in A.affected(res, g):
                p = room["tile"]
                for part, layer_name in (("public", LAYER_ATMO), ("gm", LAYER_GM)):
                    if part == "gm" and not gm:
                        continue
                    from . import render as _render
                    art = _render.oriented_thumb(tile_images, p) if tile_images is not None else None
                    im = A.room_overlay(room, 30, part, art)
                    if im is None or im.getbbox() is None:
                        continue
                    png = Path(filler_dir) / f"atmosphere_{g.index}_{p.x}_{p.y}_{part}.png"
                    png.parent.mkdir(parents=True, exist_ok=True)
                    im.save(png)
                    pieces.append({"asset_path": "", "embedded": _embed(png), "name": f"{layer_name} {p.zone}",
                                   "x": p.x * cell, "y": p.y * cell, "w": im.width, "h": im.height,
                                   "scale": cell * p.w / im.width, "rotation": 0, "flip_h": False, "flip_v": False,
                                   "layer_name": layer_name, "snap": False, "opacity": 1.0, "level": g.index})
        n_decor, no_art = 0, set()
        for it in getattr(res, "decor", []) or []:
            sym = (getattr(res, "symbols", None) or {}).get(it["sym"])
            if it["level"] != g.index or sym is None:
                continue
            class _S:
                image = sym.rel
            ov = resolver(_S) if resolver else None
            if ov is None:
                no_art.add(sym.cat)
                continue
            import math
            sc = cell / 300.0
            pw, ph = sym.px
            dx = (pw / 2 - (sym.bbox[0] + sym.bbox[2]) / 2) / 300.0
            dy = (ph / 2 - (sym.bbox[1] + sym.bbox[3]) / 2) / 300.0
            if it.get("flip"):
                dx = -dx
            a = math.radians(it["rot"])
            rx, ry = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
            pieces.append({"asset_path": ov[0], "name": sym.name[:40], "x": (it["cx"] + rx) * cell - pw * sc / 2,
                           "y": (it["cy"] + ry) * cell - ph * sc / 2, "w": pw, "h": ph, "scale": sc,
                           "rotation": it["rot"], "flip_h": bool(it.get("flip")), "flip_v": False,
                           "layer_name": LAYER_DECOR, "snap": False, "opacity": 1.0, "level": g.index})
            n_decor += 1
        if no_art:
            warnings.append("Symbol decor skipped for folders not in the library (import the Symbols ZIP): "
                            + ", ".join(sorted(no_art)[:6]))
        if legend:
            pieces.extend(state_pieces(res, g, cell))
        if add_key:                        # numbered key and letter markers: off by default (they need the key text)
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
