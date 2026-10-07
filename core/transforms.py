"""Geometry for mirror copies and grid copies of nodes (no Qt imports).

Nodes are drawn as translate(center) · rotate(angle) · scale(flip), so a copy
mirrored across a vertical line has its center reflected, its angle negated
and its horizontal flip toggled (a horizontal line toggles the vertical flip).
"""
from __future__ import annotations

import copy
import uuid

MIRROR_AXES = ("v", "h")       # "v": the line x = pos, "h": the line y = pos


def keeps_orientation(data: dict) -> bool:
    """Text and scale bars must stay readable: mirror where they sit, never
    flip their lettering."""
    return bool(data.get("is_text") or data.get("is_scale_bar"))


def visual_center(data: dict) -> tuple[float, float]:
    scale = float(data.get("scale", 1.0) or 1.0)
    return (float(data.get("x", 0.0)) + float(data.get("w", 0.0)) * scale / 2.0,
            float(data.get("y", 0.0)) + float(data.get("h", 0.0)) * scale / 2.0)


def on_mirror_line(data: dict, axis: str, pos: float, tolerance: float = 0.5) -> bool:
    """True when a node is centered on the line, so its mirror image would land
    exactly on top of it."""
    cx, cy = visual_center(data)
    return abs((cx if axis == "v" else cy) - pos) <= tolerance


def mirror_piece_data(data: dict, axis: str, pos: float) -> dict:
    """A copy of a node's saved data, mirrored across a vertical (``axis="v"``,
    the line x = pos) or horizontal (``axis="h"``, y = pos) line, with a new id."""
    if axis not in MIRROR_AXES:
        raise ValueError(f"unknown mirror axis: {axis!r}")
    out = copy.deepcopy(data)
    out["id"] = uuid.uuid4().hex
    scale = float(out.get("scale", 1.0) or 1.0)
    width = float(out.get("w", 0.0)) * scale
    height = float(out.get("h", 0.0)) * scale
    cx, cy = visual_center(out)
    if axis == "v":
        cx = 2.0 * pos - cx
    else:
        cy = 2.0 * pos - cy
    out["x"] = cx - width / 2.0
    out["y"] = cy - height / 2.0
    angle = (-float(out.get("rotation", 0.0))) % 360.0
    out["rotation"] = 0.0 if abs(angle) < 1e-9 or abs(angle - 360.0) < 1e-9 else angle
    if not keeps_orientation(out):
        key = "flip_h" if axis == "v" else "flip_v"
        out[key] = not bool(out.get(key, False))
    return out


def grid_offsets(rows: int, cols: int, step_x: float, step_y: float) -> list[tuple[float, float]]:
    """Offsets of every cell in a rows x cols grid except the original (0, 0),
    row by row."""
    rows, cols = max(1, int(rows)), max(1, int(cols))
    return [(col * step_x, row * step_y)
            for row in range(rows) for col in range(cols)
            if (row, col) != (0, 0)]


def remap_groups(datas: list[dict]) -> None:
    """Give every group in a freshly copied set of nodes a new group id (in
    place), so copies form their own groups instead of joining the originals'."""
    mapping: dict[str, str] = {}
    for data in datas:
        group = data.get("group_id") or ""
        if group:
            data["group_id"] = mapping.setdefault(group, uuid.uuid4().hex)
