"""Cut-out areas of image nodes (no Qt imports).

Shapes are polygons in the node's *source* coordinates: ``(u, v)`` fractions
of the original picture (0..1 across its width and height), measured before
any crop, flip, rotation or scaling. A hole therefore stays on the same part
of the picture when the node is moved, rotated, flipped, cropped, mirrored or
swapped for a same-sized variant.

* ``cutouts`` — holes: everything inside any of these polygons is hidden.
* ``clip_shapes`` — the node only shows what is inside *every* one of these
  polygons. Pasted sections and "keep only this area" use them.

Nothing here touches the image file; it is all non-destructive node data.
"""
from __future__ import annotations

import copy
import math
import uuid

MAX_POINTS = 600          # per polygon (lassos are simplified before this)
MAX_SHAPES = 200          # per node and per kind
_LIMIT = (-2.0, 3.0)      # shapes may hang over the picture's edge a little
_DECIMALS = 6
_IMAGE_FLAGS = ("is_text", "is_patch", "is_scale_bar", "is_connector")


# -- small helpers --------------------------------------------------------
def _get(piece, name, default=None):
    if isinstance(piece, dict):
        return piece.get(name, default)
    return getattr(piece, name, default)


def is_image(piece) -> bool:
    """Image nodes (library or embedded pictures) can have cut-outs."""
    if any(bool(_get(piece, flag, False)) for flag in _IMAGE_FLAGS):
        return False
    return bool(_get(piece, "asset_path", "") or _get(piece, "embedded", ""))


def has_shapes(piece) -> bool:
    return bool(_get(piece, "cutouts", None) or _get(piece, "clip_shapes", None))


def crop_box(piece) -> tuple[float, float, float, float]:
    crop = list(_get(piece, "crop_rect", None) or [0.0, 0.0, 1.0, 1.0])
    if len(crop) != 4:
        return 0.0, 0.0, 1.0, 1.0
    return float(crop[0]), float(crop[1]), float(crop[2]), float(crop[3])


# -- polygon validation & geometry -----------------------------------------
def clean_crop(raw) -> list[float] | None:
    """A valid [left, top, right, bottom] crop in 0..1, or None."""
    try:
        crop = [max(0.0, min(1.0, float(value))) for value in list(raw)[:4]]
    except (TypeError, ValueError):
        return None
    if len(crop) != 4 or crop[2] - crop[0] < 1e-4 or crop[3] - crop[1] < 1e-4:
        return None
    return crop


def polygon_area(poly) -> float:
    total = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def polygon_bounds(poly) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def clean_polygon(raw) -> list[list[float]] | None:
    """A safe copy of a saved polygon, or None when it is unusable."""
    if not isinstance(raw, (list, tuple)):
        return None
    points: list[list[float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return None
        try:
            x, y = float(item[0]), float(item[1])
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        x = round(max(_LIMIT[0], min(_LIMIT[1], x)), _DECIMALS)
        y = round(max(_LIMIT[0], min(_LIMIT[1], y)), _DECIMALS)
        if points and points[-1] == [x, y]:
            continue
        points.append([x, y])
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    if len(points) > MAX_POINTS:
        step = len(points) / float(MAX_POINTS)
        points = [points[int(i * step)] for i in range(MAX_POINTS)]
    if len(points) < 3 or polygon_area(points) < 1e-10:
        return None
    return points


def clean_polygons(raw) -> list[list[list[float]]]:
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for item in raw:
        poly = clean_polygon(item)
        if poly is not None:
            out.append(poly)
        if len(out) >= MAX_SHAPES:
            break
    return out


def point_in_polygon(x: float, y: float, poly) -> bool:
    """Non-zero winding rule (matches how the shapes are painted)."""
    winding = 0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if y0 <= y:
            if y1 > y and (x1 - x0) * (y - y0) - (x - x0) * (y1 - y0) > 0:
                winding += 1
        elif y1 <= y and (x1 - x0) * (y - y0) - (x - x0) * (y1 - y0) < 0:
            winding -= 1
    return winding != 0


def clip_to_box(poly, box) -> list[tuple[float, float]]:
    """The part of a polygon inside an axis-aligned box (Sutherland–Hodgman).
    Concave polygons may come back with zero-width bridges, which add no area."""
    x0, y0, x1, y1 = box
    out = [(float(x), float(y)) for x, y in poly]
    for inside, cross in (
            (lambda p: p[0] >= x0, lambda a, b: (x0, a[1] + (b[1] - a[1]) * (x0 - a[0]) / (b[0] - a[0]))),
            (lambda p: p[0] <= x1, lambda a, b: (x1, a[1] + (b[1] - a[1]) * (x1 - a[0]) / (b[0] - a[0]))),
            (lambda p: p[1] >= y0, lambda a, b: (a[0] + (b[0] - a[0]) * (y0 - a[1]) / (b[1] - a[1]), y0)),
            (lambda p: p[1] <= y1, lambda a, b: (a[0] + (b[0] - a[0]) * (y1 - a[1]) / (b[1] - a[1]), y1))):
        points, out = out, []
        if not points:
            break
        previous = points[-1]
        for current in points:
            if inside(current):
                if not inside(previous):
                    out.append(cross(previous, current))
                out.append(current)
            elif inside(previous):
                out.append(cross(previous, current))
            previous = current
    return out


def overlap_area(poly, box) -> float:
    clipped = clip_to_box(poly, box)
    return polygon_area(clipped) if len(clipped) >= 3 else 0.0


def polygon_overlaps_box(poly, box) -> bool:
    """Does a polygon share any area with an axis-aligned box?"""
    x0, y0, x1, y1 = box
    bx0, by0, bx1, by1 = polygon_bounds(poly)
    if bx1 <= x0 or bx0 >= x1 or by1 <= y0 or by0 >= y1:
        return False
    box_area = max(0.0, (x1 - x0) * (y1 - y0))
    return overlap_area(poly, box) > 1e-12 + 1e-9 * box_area


def polygon_covers_box(poly, box) -> bool:
    """True when the whole box lies inside the polygon."""
    x0, y0, x1, y1 = box
    ex, ey = (x1 - x0) * 1e-6, (y1 - y0) * 1e-6     # an area drawn exactly on the edge counts
    corners = [(x0 + ex, y0 + ey), (x1 - ex, y0 + ey), (x1 - ex, y1 - ey), (x0 + ex, y1 - ey)]
    if not all(point_in_polygon(cx, cy, poly) for cx, cy in corners):
        return False
    box_area = max(0.0, (x1 - x0) * (y1 - y0))
    return overlap_area(poly, box) >= box_area * (1.0 - 1e-6)


# -- building area polygons (world space) ----------------------------------
def rect_polygon(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    return [(left, top), (right, top), (right, bottom), (left, bottom)]


def ellipse_polygon(x0: float, y0: float, x1: float, y1: float,
                    segments: int = 96) -> list[tuple[float, float]]:
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    cx, cy = (left + right) / 2.0, (top + bottom) / 2.0
    rx, ry = (right - left) / 2.0, (bottom - top) / 2.0
    segments = max(12, int(segments))
    return [(cx + rx * math.cos(2.0 * math.pi * i / segments),
             cy + ry * math.sin(2.0 * math.pi * i / segments))
            for i in range(segments)]


def simplify_path(points, tolerance: float) -> list[tuple[float, float]]:
    """Ramer–Douglas–Peucker simplification of a hand-drawn lasso."""
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 4 or tolerance <= 0:
        return pts

    def perpendicular(p, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        if dx == 0 and dy == 0:
            return math.hypot(p[0] - a[0], p[1] - a[1])
        return abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0]) / math.hypot(dx, dy)

    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        first, last = stack.pop()
        best, index = 0.0, None
        for i in range(first + 1, last):
            d = perpendicular(pts[i], pts[first], pts[last])
            if d > best:
                best, index = d, i
        if index is not None and best > tolerance:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, k in zip(pts, keep) if k]


# -- world <-> source coordinates of a node ---------------------------------
def _frame(piece):
    scale = float(_get(piece, "scale", 1.0) or 1.0)
    w = float(_get(piece, "w", 0.0) or 0.0)
    h = float(_get(piece, "h", 0.0) or 0.0)
    cx = float(_get(piece, "x", 0.0) or 0.0) + w * scale / 2.0
    cy = float(_get(piece, "y", 0.0) or 0.0) + h * scale / 2.0
    angle = math.radians(float(_get(piece, "rotation", 0.0) or 0.0))
    return scale, w, h, cx, cy, angle


def world_to_source(piece, wx: float, wy: float) -> tuple[float, float]:
    """Map a world point onto the node's original picture (u, v)."""
    scale, w, h, cx, cy, angle = _frame(piece)
    dx, dy = wx - cx, wy - cy
    cos_a, sin_a = math.cos(-angle), math.sin(-angle)
    lx = (dx * cos_a - dy * sin_a) / max(scale, 1e-9)
    ly = (dx * sin_a + dy * cos_a) / max(scale, 1e-9)
    if _get(piece, "flip_h", False):
        lx = -lx
    if _get(piece, "flip_v", False):
        ly = -ly
    fx = lx / max(w, 1e-9) + 0.5
    fy = ly / max(h, 1e-9) + 0.5
    u0, v0, u1, v1 = crop_box(piece)
    return u0 + fx * (u1 - u0), v0 + fy * (v1 - v0)


def source_to_world(piece, u: float, v: float) -> tuple[float, float]:
    scale, w, h, cx, cy, angle = _frame(piece)
    u0, v0, u1, v1 = crop_box(piece)
    fx = (u - u0) / max(u1 - u0, 1e-9)
    fy = (v - v0) / max(v1 - v0, 1e-9)
    lx, ly = (fx - 0.5) * w, (fy - 0.5) * h
    if _get(piece, "flip_h", False):
        lx = -lx
    if _get(piece, "flip_v", False):
        ly = -ly
    lx, ly = lx * scale, ly * scale
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    return cx + lx * cos_a - ly * sin_a, cy + lx * sin_a + ly * cos_a


def world_polygon_to_source(piece, points) -> list[list[float]] | None:
    return clean_polygon([list(world_to_source(piece, x, y)) for x, y in points])


def source_polygon_to_world(piece, poly) -> list[tuple[float, float]]:
    return [source_to_world(piece, u, v) for u, v in poly]


def visible_at_source(piece, u: float, v: float) -> bool:
    """False inside a hole or outside a clip shape (clicks fall through)."""
    for clip in _get(piece, "clip_shapes", None) or []:
        if not point_in_polygon(u, v, clip):
            return False
    for hole in _get(piece, "cutouts", None) or []:
        if point_in_polygon(u, v, hole):
            return False
    return True


# -- operations on saved node data ------------------------------------------
def overlaps(piece, poly) -> bool:
    """Does a source-space polygon touch the node's visible (cropped) area?"""
    return bool(poly) and polygon_overlaps_box(poly, crop_box(piece))


def covers(piece, poly) -> bool:
    """Would a hole of this shape hide the whole visible node?"""
    return bool(poly) and polygon_covers_box(poly, crop_box(piece))


def add_cutout(data: dict, poly) -> bool:
    poly = clean_polygon(poly)
    if poly is None or not overlaps(data, poly):
        return False
    holes = clean_polygons(data.get("cutouts") or [])
    if len(holes) >= MAX_SHAPES:
        return False
    holes.append(poly)
    data["cutouts"] = holes
    return True


def _intersect(a, b):
    box = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return box if box[2] - box[0] > 1e-6 and box[3] - box[1] > 1e-6 else None


def reframe(data: dict, box) -> dict:
    """Copy of ``data`` cropped to the source box, with the picture left
    exactly where it was on the map."""
    out = copy.deepcopy(data)
    u0, v0, u1, v1 = crop_box(data)
    cw, ch = max(u1 - u0, 1e-9), max(v1 - v0, 1e-9)
    scale = float(data.get("scale", 1.0) or 1.0)
    out["w"] = float(data.get("w", 0.0)) * (box[2] - box[0]) / cw
    out["h"] = float(data.get("h", 0.0)) * (box[3] - box[1]) / ch
    cx, cy = source_to_world(data, (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
    out["x"] = cx - out["w"] * scale / 2.0
    out["y"] = cy - out["h"] * scale / 2.0
    out["crop_rect"] = [round(float(value), 9) for value in box]
    return out


def section_data(data: dict, poly, min_size: float = 1.0) -> dict | None:
    """Saved data for a new node showing just the part of ``data`` inside the
    source-space polygon (for cut / copy / paste). None when nothing of the
    node lies inside it. ``min_size`` is the smallest useful side in world px."""
    poly = clean_polygon(poly)
    if poly is None or not overlaps(data, poly):
        return None
    box = _intersect(crop_box(data), polygon_bounds(poly))
    if box is None:
        return None
    out = reframe(data, box)
    scale = float(data.get("scale", 1.0) or 1.0)
    if out["w"] * scale < min_size or out["h"] * scale < min_size:
        return None
    out["id"] = uuid.uuid4().hex
    out["clip_shapes"] = clean_polygons(data.get("clip_shapes") or []) + [poly]
    out["cutouts"] = [hole for hole in clean_polygons(data.get("cutouts") or [])
                      if polygon_overlaps_box(hole, box)]
    out["locked"] = False
    out["group_id"] = ""
    base = str(data.get("name") or "Image")
    out["name"] = base if base.endswith(" (part)") else base + " (part)"
    return out


def keep_only(data: dict, poly, min_size: float = 1.0) -> dict | None:
    """``data`` trimmed to the part inside the polygon (same node id)."""
    out = section_data(data, poly, min_size)
    if out is None:
        return None
    out["id"] = data.get("id", out["id"])
    out["name"] = data.get("name", out["name"])
    out["locked"] = bool(data.get("locked", False))
    out["group_id"] = data.get("group_id", "")
    return out


def clone_window(data: dict, du: float, dv: float) -> dict:
    """Move the picture inside a node by (du, dv) source units while the node
    stays put — what a clone patch does when you pick where to copy from."""
    out = copy.deepcopy(data)
    u0, v0, u1, v1 = crop_box(data)
    du = max(-u0, min(1.0 - u1, du))
    dv = max(-v0, min(1.0 - v1, dv))
    out["crop_rect"] = [u0 + du, v0 + dv, u1 + du, v1 + dv]
    return out


def world_offset_to_source(piece, dx: float, dy: float) -> tuple[float, float]:
    """A world-space shift expressed in the node's source units."""
    u0, v0 = world_to_source(piece, 0.0, 0.0)
    u1, v1 = world_to_source(piece, dx, dy)
    return u1 - u0, v1 - v0
