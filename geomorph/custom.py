"""Add your own tiles to the registry (kept in ``data/custom_tiles.json``).

A custom tile is any PNG laid out like the pack's own: ``w x h`` squares with a
2-square clear border, at ``pps`` pixels per square. Edge data is detected from
the image (you can correct it with the edge overrides file), tags are whatever
you give it.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from . import DATA_DIR
from .edges import REVIEW_CONF, analyse_image
from .placement import hull_sides
from .registry import BORDER_SQUARES, Registry, Tile, derive_tags

CUSTOM_FILE = DATA_DIR / "custom_tiles.json"


def make_custom_tile(image_path, tile_id, title, tile_type="standard", tags=None, w=None, h=None,
                     pps=None, rooms=None, table=None) -> Tile:
    image_path = Path(image_path)
    with Image.open(image_path) as im:
        px = im.size
    if pps is None:
        # infer from a standard-looking size: (squares + 4) * pps
        pps = max(1, round(px[0] / ((w or 20) + 2 * BORDER_SQUARES)))
    w = w or round(px[0] / pps) - 2 * BORDER_SQUARES
    h = h or round(px[1] / pps) - 2 * BORDER_SQUARES
    edges = analyse_image(str(image_path), w, h, pps)
    if tags is None:
        tags = derive_tags(title, rooms or [], table or __import__("geomorph.registry", fromlist=["tag_table"]).tag_table())
    tile = Tile(id=tile_id, number=tile_id, type=tile_type, w=w, h=h, title=title, rooms=list(rooms or []),
                tags=dict(tags), image=str(image_path), px=tuple(px), edges=edges)
    hs = hull_sides(tile)
    tile.review = min([e["conf"] for s, e in edges.items() if s not in hs] or [1.0]) < REVIEW_CONF
    return tile


def save_custom(tile: Tile, path=None):
    """Append (or replace) a custom tile in the user's custom tile file."""
    path = Path(path or CUSTOM_FILE)
    data = {"tiles": []}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    data["tiles"] = [t for t in data["tiles"] if t["id"] != tile.id] + [tile.to_json()]
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return path


def add_custom_tile(reg: Registry, *args, save=True, **kw) -> Tile:
    tile = make_custom_tile(*args, **kw)
    reg.add(tile)
    if save:
        save_custom(tile)
    return tile
