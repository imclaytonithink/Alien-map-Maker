"""Pure helpers for placed guides and grid coordinates (no Qt imports).

Guides themselves live on each level (``core.project.Guide``); this module
holds the shared arithmetic used by the canvas, the exporter and dialogs.
"""
from __future__ import annotations

import math

# Candidate label strides for rulers: show every label, every 2nd, 5th, …
LABEL_STEPS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)


def column_label(index: int) -> str:
    """Spreadsheet-style column name: 0 -> A, 25 -> Z, 26 -> AA, 27 -> AB."""
    index = int(index)
    if index < 0:
        return ""
    label = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def row_label(index: int) -> str:
    """Row name: 0 -> 1, 1 -> 2, …"""
    return str(int(index) + 1) if index >= 0 else ""


def grid_counts(canvas_w: float, canvas_h: float, cell: float) -> tuple[int, int]:
    """Columns and rows covering the canvas (a partial last square counts)."""
    cell = max(1.0, float(cell))
    return (max(1, math.ceil(canvas_w / cell - 1e-9)),
            max(1, math.ceil(canvas_h / cell - 1e-9)))


def label_step(spacing_px: float, label_px: float, gap: float = 4.0) -> int:
    """Smallest stride in LABEL_STEPS so neighbouring labels never overlap."""
    if spacing_px <= 0:
        return LABEL_STEPS[-1]
    for step in LABEL_STEPS:
        if step * spacing_px >= label_px + gap:
            return step
    return LABEL_STEPS[-1]


def layout_positions(extent: float, cell: float, every: int = 0,
                     margin: float = 0.0, center: bool = False) -> list[float]:
    """Guide positions along one axis of a map ``extent`` world px long.

    ``every``  a guide every N squares (interior grid lines only),
    ``margin`` a guide N squares in from both edges,
    ``center`` a guide through the middle.
    """
    cell = max(1.0, float(cell))
    found: set[float] = set()
    if every and every > 0:
        step = every * cell
        k = 1
        while k * step < extent - 1e-6:
            found.add(round(k * step, 6))
            k += 1
    if margin and margin > 0:
        inset = margin * cell
        if 0 < inset < extent / 2.0 + 1e-6:
            found.add(round(inset, 6))
            found.add(round(extent - inset, 6))
    if center:
        found.add(round(extent / 2.0, 6))
    return sorted(found)


def format_amount(value: float) -> str:
    """Compact number for readouts: 14, 6.5, 2.33."""
    if abs(value - round(value)) < 0.005:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def describe_position(axis: str, pos: float, cell: float,
                      feet_per_square: float) -> str:
    """Readout such as ``x 14 sq · 70 ft`` for a guide at world ``pos``."""
    squares = pos / max(1.0, float(cell))
    feet = squares * (feet_per_square or 5)
    name = "x" if axis == "v" else "y"
    return f"{name} {format_amount(squares)} sq · {format_amount(feet)} ft"


def nearest(value: float, candidates, threshold: float):
    """Closest candidate within ``threshold`` (ties: the later one), else None."""
    best, best_distance = None, threshold + 1e-9
    for candidate in candidates:
        distance = abs(candidate - value)
        if distance <= best_distance + 1e-9:
            best, best_distance = candidate, distance
    return best
