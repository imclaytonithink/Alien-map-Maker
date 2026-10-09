"""Build a map out of the assets you picked in the library.

Pure logic (no Qt) so the whole builder can be unit-tested headlessly.

Nothing is guessed about what an asset *is*. There is no keyword classifier,
no automatic sorting and no "role" for an asset to be filed under: the map is
made from **exactly the assets you selected**, and only from those. The
library's own folder structure (the one the ZIP arrived with) is used for
browsing, never for deciding what belongs where on the map.

Three layouts share one packer:

``grid``     tidy rows, left to right, wrapping like text.
``scatter``  random grid positions, no overlaps.
``fill``     the same row packing, but the selection repeats until the whole
             area is covered - floors, decks and other tiling art.

Every placement lands on whole grid squares, so a generated map is as aligned
as one you built by hand.

How big an asset is on the map
-------------------------------
An asset's footprint comes from its own numbers, with the same rule the canvas
uses when you drag that asset over by hand (see ``ui.canvas.asset_world_size``):

* a name that codes a size in feet (``[100x100]``) on a high-resolution image
  is sized with the project's feet-per-square, so a 100x100 ft tile covers
  20x20 squares;
* anything else keeps its pixel size (one image pixel per map pixel);
* anything still too big for the area is shrunk to fit, and reported.
"""
from __future__ import annotations

import math
import random

from core.seeds import coerce_seed

LAYOUTS = ("grid", "scatter", "fill")
LAYOUT_LABELS = {
    "grid": "Tidy rows — left to right, wrapping",
    "scatter": "Random scatter — spread out, no overlaps",
    "fill": "Fill the area — repeat the selection until it is covered",
}
LAYOUT_ABOUT = {
    "grid": "Places your assets one after another in neat rows. Predictable, "
            "easy to read, and easy to tidy up afterwards.",
    "scatter": "Drops each copy somewhere random on the grid. Good for "
               "scattering props, debris and symbols.",
    "fill": "Keeps laying copies down until the area is full. Best for floor, "
            "deck and terrain tiles.",
}
ROTATIONS = (0, 90, 180, 270)

MAX_PIECES = 4000          # hard stop so a tiny tile cannot loop forever
SCATTER_TRIES = 200        # random positions tried per scattered copy
MIN_SCALE, MAX_SCALE = 0.02, 20.0


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------
def world_size(px_w: int, px_h: int, named, cell_size: float,
               feet_per_square: int) -> tuple[float, float]:
    """World-pixel size of an image, using the canvas's own placement rule."""
    px_w, px_h = float(px_w or 0), float(px_h or 0)
    if px_w <= 0 or px_h <= 0:
        return 0.0, 0.0
    w, h = px_w, px_h
    if named and named[0] > 0 and named[1] > 0 and (
            px_w >= named[0] * 4 or px_h >= named[1] * 4):
        # One uniform factor, so the image keeps its proportions even when the
        # nominal name and the pixels disagree slightly.
        fx = named[0] / feet_per_square * cell_size / px_w
        fy = named[1] / feet_per_square * cell_size / px_h
        factor = math.sqrt(fx * fy)
        w, h = px_w * factor, px_h * factor
    return max(1.0, w), max(1.0, h)


def _cell_span(world_px: float, cell_size: float) -> int:
    """Grid squares an image covers.

    A size worked out from feet is exact in principle (100 ft at 5 ft per
    square is 20 squares) but arrives with floating-point dust, so a span
    within a hundredth of a square of a whole number is taken as that number.
    Anything genuinely part-way across a square rounds up, so art is never
    clipped by the space reserved for it.
    """
    spans = world_px / max(1e-9, cell_size)
    nearest = round(spans)
    if nearest >= 1 and abs(spans - nearest) <= 0.01:
        return int(nearest)
    return max(1, int(math.ceil(spans - 1e-9)))


def _entry_fields(entry) -> tuple:
    """Read (path, name, px_w, px_h, named_size) from an Asset or a dict."""
    if isinstance(entry, dict):
        return (entry.get("path", ""), entry.get("name", ""),
                int(entry.get("w") or 0), int(entry.get("h") or 0),
                entry.get("size"))
    return (getattr(entry, "path", ""), getattr(entry, "name", ""),
            int(getattr(entry, "width", 0) or 0),
            int(getattr(entry, "height", 0) or 0),
            getattr(entry, "size", None))


def make_item(entry, cell_size: float, feet_per_square: int,
              scale: float = 1.0) -> dict | None:
    """One placeable asset: source pixels, world scale and grid footprint."""
    path, name, px_w, px_h, named = _entry_fields(entry)
    if not path or px_w <= 0 or px_h <= 0:
        return None
    cell = max(1.0, float(cell_size))
    fps = max(1, int(feet_per_square or 5))
    w, h = world_size(px_w, px_h, named, cell, fps)
    factor = min(MAX_SCALE, max(MIN_SCALE, float(scale or 1.0)))
    w, h = w * factor, h * factor
    return {"path": path, "name": name or path.rsplit("/", 1)[-1],
            "w": px_w, "h": px_h, "scale": w / px_w,
            "cells_w": _cell_span(w, cell), "cells_h": _cell_span(h, cell)}


def describe_selection(entries, cell_size: float, feet_per_square: int,
                       scale: float = 1.0) -> list[dict]:
    """Per-asset summary for the dialog: name plus its footprint in squares."""
    out = []
    for entry in entries:
        item = make_item(entry, cell_size, feet_per_square, scale)
        if item is None:
            continue
        out.append({"path": item["path"], "name": item["name"],
                    "cells_w": item["cells_w"], "cells_h": item["cells_h"],
                    "px_w": item["w"], "px_h": item["h"]})
    return out


def _footprint(item: dict, rotation: int) -> tuple[int, int]:
    return ((item["cells_h"], item["cells_w"]) if rotation in (90, 270)
            else (item["cells_w"], item["cells_h"]))


def suggest_region(opts: dict, cap: int = 800) -> tuple[int, int, int, int]:
    """A region big enough for the selection, so rooms and other large assets
    always fit: the total footprint of every copy, with headroom for the
    layout, and at least as wide/tall as the biggest single piece.

    Used for the dialog's "automatic" size so the user never has to guess a
    map size that fits their selection."""
    cell = max(1.0, float(opts.get("cell_size", 70)))
    fps = max(1, int(opts.get("feet_per_square", 5) or 5))
    scale = min(MAX_SCALE, max(MIN_SCALE, float(opts.get("scale", 1.0) or 1.0)))
    layout = opts.get("layout", "grid")
    if layout not in LAYOUTS:
        layout = "grid"
    rotate = bool(opts.get("rotate", False))
    rotations = ROTATIONS if rotate else (0,)
    copies = max(1, int(opts.get("copies", 1) or 1)) if layout != "fill" else 1
    gap = max(0, int(opts.get("gap", 0) or 0))
    border = max(0, int(opts.get("margin", 0) or 0))

    items = [item for item in
             (make_item(entry, cell, fps, scale)
              for entry in (opts.get("selection") or []))
             if item is not None]
    if not items:
        return (0, 0, 29, 29)

    total = 0
    min_w = min_h = 1
    for item in items:
        spins = [_footprint(item, rotation) for rotation in rotations]
        min_w = max(min_w, min(w for w, _h in spins))
        min_h = max(min_h, min(h for _w, h in spins))
        w, h = min(spins, key=lambda pair: pair[0] * pair[1])
        total += w * h * copies
        total += gap * max(w, h) * copies          # the gaps between pieces

    if layout == "grid":
        # Try the shelf packing once against a tall canvas and report exactly
        # the rectangle it used - the automatic size then hugs the result.
        cols = min(cap, max(min_w, 8, round((total * 1.6) ** 0.5)))
        placements, _skipped, _full = pack_rows(
            list(items) * copies, 0, 0, cols, cap, gap, rotations)
        if placements:
            used_w = max(cx + _footprint(item, rotation)[0]
                         for item, cx, _cy, rotation in placements)
            used_h = max(cy + _footprint(item, rotation)[1]
                         for item, _cx, cy, rotation in placements)
            return (0, 0, min(cap, used_w + 2 * border) - 1,
                    min(cap, used_h + 2 * border) - 1)
        layout = "scatter"        # fall through to the area estimate

    headroom = {"scatter": 1.7, "fill": 1.05}.get(layout, 1.7)
    area = int(total * headroom) + 16
    cols = max(min_w, round((area * 1.6) ** 0.5))
    rows = max(min_h, -(-area // max(1, cols)))
    cols = min(cap, max(8, cols) + 2 * border)
    rows = min(cap, max(8, rows) + 2 * border)
    return (0, 0, cols - 1, rows - 1)


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------
def _piece(item: dict, cx: int, cy: int, rotation: int, cell_size: float,
           layer: str) -> dict:
    """Piece dict for ``item`` whose *visible* box starts at cell (cx, cy).

    ``Piece`` treats x/y as the top-left of the unrotated, scaled box and
    rotates about its centre. The placement is expressed as the box the user
    actually sees, and that box starts exactly on a grid line - so a generated
    node is aligned exactly like one dragged there by hand, in any rotation.
    """
    w_px, h_px = item["w"] * item["scale"], item["h"] * item["scale"]
    box_w, box_h = ((h_px, w_px) if rotation in (90, 270) else (w_px, h_px))
    centre_x = cx * cell_size + box_w / 2.0
    centre_y = cy * cell_size + box_h / 2.0
    return {"asset_path": item["path"], "name": item["name"],
            "x": centre_x - w_px / 2.0, "y": centre_y - h_px / 2.0,
            "w": item["w"], "h": item["h"], "scale": item["scale"],
            "rotation": rotation, "layer_name": layer, "snap": True,
            "opacity": 1.0, "flip_h": False, "flip_v": False,
            "_box": (cx * cell_size, cy * cell_size, box_w, box_h)}


def pack_rows(order, x0: int, y0: int, cols: int, rows: int, gap: int,
              rotations=ROTATIONS):
    """Row-major shelf packing.

    Returns ``(placements, skipped, area_full)`` where placements are
    ``(item, cell_x, cell_y, rotation)`` tuples. ``skipped`` counts copies that
    could not be placed in any rotation (too large for the space left), and
    ``area_full`` says the remaining rows are shorter than every asset.

    An asset that is too tall for the space left under the cursor is passed
    over, so a smaller one later in the order can still fill that row.
    """
    placements, skipped = [], 0
    cx, cy, row_h = x0, y0, 0
    limit_x, limit_y = x0 + cols, y0 + rows
    heights = [_footprint(item, rotation)[1]
               for item in order for rotation in rotations]
    min_h = min(heights) if heights else 1
    if rows < min_h:
        return placements, len(list(order)), True
    full = False
    order = list(order)
    for position, item in enumerate(order):
        placed = False
        for rotation in rotations:
            fw, fh = _footprint(item, rotation)
            if fw > cols or fh > rows:
                continue
            if cx + fw > limit_x:                 # wrap to the next row
                cx, cy, row_h = x0, cy + row_h + gap, 0
                if cy + min_h > limit_y:
                    full = True
                    break
            if cy + fh > limit_y:                 # try another rotation
                continue
            placements.append((item, cx, cy, rotation))
            cx += fw + gap
            row_h = max(row_h, fh)
            placed = True
            break
        if full:
            # Nothing at all fits under the cursor any more: report the rest of
            # the order as unfitted rather than quietly dropping it.
            skipped += len(order) - position
            break
        if not placed:
            skipped += 1
    return placements, skipped, full


class _Occupancy:
    """Grid of taken cells, used by the scatter layout."""

    def __init__(self, cols: int, rows: int):
        self.cols, self.rows = cols, rows
        self._rows = [bytearray(cols) for _ in range(rows)]

    def free(self, x: int, y: int, w: int, h: int) -> bool:
        if x < 0 or y < 0 or x + w > self.cols or y + h > self.rows:
            return False
        return all(not any(self._rows[yy][x:x + w]) for yy in range(y, y + h))

    def take(self, x: int, y: int, w: int, h: int):
        for yy in range(max(0, y), min(self.rows, y + h)):
            row = self._rows[yy]
            for xx in range(max(0, x), min(self.cols, x + w)):
                row[xx] = 1


def pack_scatter(rng, order, x0: int, y0: int, cols: int, rows: int, gap: int,
                 rotations=ROTATIONS):
    """Random, non-overlapping placements. Returns ``(placements, skipped)``."""
    taken = _Occupancy(cols, rows)
    placements, skipped = [], 0
    for item in order:
        spins = list(rotations)
        rng.shuffle(spins)
        placed = False
        for rotation in spins:
            fw, fh = _footprint(item, rotation)
            if fw > cols or fh > rows:
                continue
            pad = max(0, gap)
            span_w, span_h = fw + 2 * pad, fh + 2 * pad
            if span_w > cols or span_h > rows:
                span_w, span_h = fw, fh
                pad = 0
            for _attempt in range(SCATTER_TRIES):
                cx = rng.randint(0, cols - span_w)
                cy = rng.randint(0, rows - span_h)
                if not taken.free(cx, cy, span_w, span_h):
                    continue
                taken.take(cx, cy, span_w, span_h)
                placements.append((item, x0 + cx + pad, y0 + cy + pad, rotation))
                placed = True
                break
            if placed:
                break
        if not placed:
            skipped += 1
    return placements, skipped


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------
def _empty(seed, message: str, layout: str = "grid", skipped: int = 0) -> dict:
    return {"pieces": [], "seed": seed, "setting": "Selection",
            "layout": layout, "mode": "selection", "connected": None,
            "counts": {"pieces": 0, "assets": 0, "skipped": int(skipped)},
            "warnings": [message]}


def build_map(opts: dict) -> dict:
    """Lay the selected assets out on the grid.

    ``opts``
        ``selection``       list of Assets (or dicts) - the assets you picked
        ``cell_size``       map pixels per grid square
        ``feet_per_square`` for assets whose name codes a size in feet
        ``region``          ``(x0, y0, x1, y1)`` in grid squares
        ``layout``          ``grid`` | ``scatter`` | ``fill``
        ``copies``          copies of each selected asset (grid / scatter)
        ``gap``             empty squares left between placements
        ``scale``           extra size factor applied to every asset
        ``rotate``          allow 90° steps as well as upright
        ``shuffle``         randomise the placement order
        ``flips``           also mirror some copies (adds variety)
        ``margin``          empty border squares kept around the layout
        ``layer_name``      layer the pieces are reported under
        ``seed``            seed for every random choice
    """
    seed = opts.get("seed", 1)
    rng = random.Random(coerce_seed(seed))
    cell = max(1.0, float(opts.get("cell_size", 70)))
    fps = max(1, int(opts.get("feet_per_square", 5) or 5))
    x0, y0, x1, y1 = opts.get("region", (0, 0, 59, 59))
    x0, y0 = int(x0), int(y0)
    cols = max(1, int(x1) - x0 + 1)
    rows = max(1, int(y1) - y0 + 1)
    layout = opts.get("layout", "grid")
    if layout not in LAYOUTS:
        layout = "grid"
    gap = max(0, int(opts.get("gap", 0) or 0))
    copies = max(1, int(opts.get("copies", 1) or 1))
    scale = min(MAX_SCALE, max(MIN_SCALE, float(opts.get("scale", 1.0) or 1.0)))
    rotate = bool(opts.get("rotate", False))
    shuffle = bool(opts.get("shuffle", layout == "scatter"))
    flips = bool(opts.get("flips", False))
    margin = max(0, int(opts.get("margin", 0) or 0))
    layer = opts.get("layer_name") or "Generated"
    rotations = ROTATIONS if rotate else (0,)
    # The layout happens inside the border; a border bigger than the area is
    # simply ignored.
    ix, iy = x0 + margin, y0 + margin
    icols, irows = cols - 2 * margin, rows - 2 * margin
    if icols < 1 or irows < 1:
        ix, iy, icols, irows = x0, y0, cols, rows

    items = [item for item in
             (make_item(entry, cell, fps, scale)
              for entry in (opts.get("selection") or []))
             if item is not None]
    if not items:
        return _empty(seed, "Select the assets you want on the map first.", layout)

    warnings: list[str] = []

    def fits_area(item) -> bool:
        return any(_footprint(item, rotation)[0] <= icols
                   and _footprint(item, rotation)[1] <= irows
                   for rotation in rotations)

    unusable = [item for item in items if not fits_area(item)]
    usable = [item for item in items if fits_area(item)]
    if unusable:
        biggest = max(unusable, key=lambda it: it["cells_w"] * it["cells_h"])
        names = ", ".join(sorted({item["name"] for item in unusable})[:3])
        warnings.append(
            f"{len(unusable)} asset(s) are bigger than that area and were "
            f"left out ({names}" + (" …" if len(unusable) > 3 else "") + "). "
            f"The largest covers {biggest['cells_w']} x {biggest['cells_h']} "
            f"squares; the area is {cols} x {rows}. Make the map bigger, lower "
            "the asset size, or allow rotation.")
    if not usable:
        return _empty(seed, "None of the selected assets fit that area. Make "
                            "the map larger, lower the asset size, or allow "
                            "rotation." + (" " + warnings[-1] if warnings else ""),
                      layout, skipped=len(unusable))

    if layout == "fill":
        order = []
        while len(order) < MAX_PIECES:
            batch = list(usable)
            if shuffle:
                rng.shuffle(batch)
            order.extend(batch)
        placements, _skipped, _full = pack_rows(order, ix, iy, icols, irows,
                                                gap, rotations)
        skipped = 0
    else:
        order = list(usable) * copies
        if shuffle:
            rng.shuffle(order)
        if layout == "scatter":
            placements, skipped = pack_scatter(rng, order, ix, iy, icols, irows,
                                               gap, rotations)
        else:
            placements, skipped, _full = pack_rows(
                order, ix, iy, icols, irows, gap, rotations)

    if skipped:
        warnings.append(
            f"{skipped} placement(s) did not fit the area — make the map "
            "bigger, use a smaller scale, or allow rotation.")
    if not placements:
        return _empty(seed, "Nothing fit in that area. Make the map larger or "
                            "pick smaller assets.", layout)

    pieces = []
    for item, cx, cy, rotation in placements:
        piece = _piece(item, cx, cy, rotation, cell, layer)
        if flips:
            # Mirroring keeps the footprint, so alignment is unaffected.
            piece["flip_h"] = rng.random() < 0.5
            piece["flip_v"] = rng.random() < 0.5
        pieces.append(piece)
    used_w = max(cx - x0 + _footprint(item, rotation)[0]
                 for item, cx, _cy, rotation in placements)
    used_h = max(cy - y0 + _footprint(item, rotation)[1]
                 for item, _cx, cy, rotation in placements)
    if (layout != "fill" and not opts.get("quiet_unused")
            and (used_w < cols or used_h < rows)):
        warnings.append(
            f"The assets cover {used_w} x {used_h} of the {cols} x {rows} "
            "square area; the rest was left empty.")
    return {"pieces": pieces, "seed": seed, "setting": "Selection",
            "layout": layout, "mode": "selection", "connected": None,
            "counts": {"pieces": len(pieces),
                       "assets": len({p["asset_path"] for p in pieces}),
                       "skipped": skipped + len(unusable)},
            "warnings": warnings,
            "canvas_cells": (x0 + used_w, y0 + used_h),
            "used_cells": (used_w, used_h),
            "region_cells": (cols, rows)}
