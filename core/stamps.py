"""Quick-stamp hotbar: nine slots bound to the keys 1-9 (no Qt imports).

A slot either holds a library asset (placed the way a library drop would
place it) or a node template that keeps the pinned node's size, rotation,
flips, tint, crop and text styling. Slots are app-wide, so the same hotbar
follows you from map to map.

A slot in *door mode* (``"edge": True``) does not fill a square: each copy
sits centered on the nearest grid line and turns to run along it — for doors,
hatches, airlocks and vents.
"""
from __future__ import annotations

import json
import os

SLOT_COUNT = 9
SLOT_KINDS = ("asset", "node")
# Placement-specific fields that every stamped copy sets for itself.
_TEMPLATE_DROP = ("id", "x", "y", "z", "layer", "group_id", "locked")


class StampError(ValueError):
    """The thing the user tried to pin cannot live on the hotbar."""


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")


def _base_name(path: str) -> str:
    return os.path.splitext(os.path.basename(str(path).replace("\\", "/")))[0]


def _display_name(name: str) -> str:
    name = os.path.basename(str(name).replace("\\", "/"))
    return os.path.splitext(name)[0] if name.lower().endswith(_IMAGE_EXTS) else name


def asset_slot(asset_path: str, name: str = "", edge: bool = False) -> dict:
    if not asset_path:
        raise StampError("Pick an asset from the library first.")
    return {"kind": "asset", "asset_path": str(asset_path),
            "name": _display_name(name or asset_path), "edge": bool(edge)}


def node_label(data: dict) -> str:
    if data.get("is_text"):
        first = (str(data.get("text", "")).strip().splitlines() or [""])[0]
        return f"Text: {first[:24]}" if first else "Text label"
    if data.get("is_patch"):
        return "Patch"
    if data.get("is_scale_bar"):
        return "Scale bar"
    if data.get("is_connector"):
        return str(data.get("connector_label") or "Connection marker")
    return _display_name(data.get("name") or data.get("asset_path", "") or "Node")


def node_slot(piece_data: dict, edge: bool = False) -> dict:
    """Pin a node (its saved data) with all of its styling."""
    if not isinstance(piece_data, dict):
        raise StampError("Select one node first.")
    if piece_data.get("embedded"):
        raise StampError(
            "This image is embedded in the map rather than stored in your asset "
            "library, so it can't follow you to other maps. Import it into the "
            "library first, then pin it.")
    template = {key: value for key, value in piece_data.items()
                if key not in _TEMPLATE_DROP}
    return {"kind": "node", "asset_path": str(template.get("asset_path", "") or ""),
            "name": node_label(piece_data), "template": template, "edge": bool(edge)}


def with_edge(slot: dict, on: bool) -> dict:
    """The same slot with door mode switched on or off."""
    out = dict(slot)
    out["edge"] = bool(on)
    return out


def is_edge_slot(slot) -> bool:
    return bool(slot) and bool(slot.get("edge", False))


DOOR_WORDS = ("door", "hatch", "airlock", "portal", "gate", "iris", "vent")


def looks_like_door(name: str) -> bool:
    """Doors and hatches start out in door mode when pinned."""
    lowered = str(name or "").casefold()
    return any(word in lowered for word in DOOR_WORDS)


def normalize_slot(raw) -> dict | None:
    if not isinstance(raw, dict) or raw.get("kind") not in SLOT_KINDS:
        return None
    edge = bool(raw.get("edge", False))
    if raw["kind"] == "asset":
        try:
            return asset_slot(raw.get("asset_path", ""), raw.get("name", ""), edge)
        except StampError:
            return None
    template = raw.get("template")
    if not isinstance(template, dict) or template.get("embedded"):
        return None
    slot = node_slot(template, edge)
    if raw.get("name"):
        slot["name"] = str(raw["name"])
    return slot


def normalize_slots(raw) -> list:
    slots = [normalize_slot(item) for item in (raw if isinstance(raw, list) else [])]
    slots = slots[:SLOT_COUNT]
    return slots + [None] * (SLOT_COUNT - len(slots))


def slots_to_json(slots) -> str:
    return json.dumps(normalize_slots(list(slots)))


def slots_from_json(text) -> list:
    try:
        return normalize_slots(json.loads(text or "[]"))
    except (TypeError, ValueError):
        return normalize_slots([])


def slot_label(slot) -> str:
    return "" if not slot else str(slot.get("name") or _base_name(slot.get("asset_path", "")))


def edge_placement(wx: float, wy: float, cell: float, width: float, height: float,
                   rotation: float) -> tuple[float, float, float]:
    """Where a door-mode stamp lands for a pointer at (wx, wy).

    ``width``/``height`` are the copy's visual size and ``rotation`` its
    pinned angle. Returns the copy's center and new angle: centered on the
    nearest grid line, its long side along the line, and its ends on grid
    points (an odd number of squares long sits between two crossings, an
    even number is centered on one). The copy's "bottom" faces the side of
    the line the pointer is on, so a swing arc follows the pointer.
    """
    import math
    cell = max(1e-6, float(cell))
    angle = math.radians(rotation)
    extent_x = abs(width * math.cos(angle)) + abs(height * math.sin(angle))
    extent_y = abs(width * math.sin(angle)) + abs(height * math.cos(angle))
    lies_flat = extent_x >= extent_y          # long side currently horizontal
    length = max(extent_x, extent_y)
    squares = max(1, int(round(length / cell)))
    line_x = round(wx / cell) * cell          # nearest vertical grid line
    line_y = round(wy / cell) * cell          # nearest horizontal grid line

    def along(value: float) -> float:
        if squares % 2 == 0:
            return round(value / cell) * cell
        return (math.floor(value / cell) + 0.5) * cell

    # The pinned look counts as "on a horizontal line, pointer below" when its
    # long side is horizontal, else as "on a vertical line, pointer left".
    if abs(wy - line_y) <= abs(wx - line_x):
        turn = 0.0 if lies_flat else 270.0
        if wy < line_y:
            turn += 180.0
        cx, cy = along(wx), line_y
    else:
        turn = 90.0 if lies_flat else 0.0
        if wx > line_x:
            turn += 180.0
        cx, cy = line_x, along(wy)
    return cx, cy, (rotation + turn) % 360.0
