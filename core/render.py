"""Shared piece drawing used by both the live canvas and the exporter.

Draws inside an already-transformed painter (translated to the piece center,
rotated, and scaled by zoom*scale). Local coordinates are world pixels.
"""
from __future__ import annotations

from collections import OrderedDict

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBitmap, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen,
    QPixmap, QPolygonF, QRegion,
)

_ALPHA_PATH_CACHE: dict[tuple[int, int, int], QPainterPath] = {}


def draw_piece(painter: QPainter, piece, pm: QPixmap | None,
               project=None):
    w, h = piece.w, piece.h
    if piece.is_text:
        font = QFont(piece.font_family)
        font.setPixelSize(max(4, int(piece.font_size)))
        font.setBold(piece.font_bold)
        font.setItalic(getattr(piece, "font_italic", False))
        font.setUnderline(getattr(piece, "font_underline", False))
        painter.setFont(font)

        text_rect = QRectF(-w / 2, -h / 2, w, h)
        background = getattr(piece, "text_background_color", "")
        background_opacity = max(0.0, min(
            1.0, float(getattr(piece, "text_background_opacity", 0.85))))
        if background and background_opacity > 0:
            painter.save()
            painter.setOpacity(painter.opacity() * background_opacity)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(background))
            painter.drawRect(text_rect)
            painter.restore()

        padding = min(
            max(0, min(100, int(getattr(piece, "text_padding", 4)))),
            max(0, int((min(w, h) - 1) / 2)))
        inner_rect = text_rect.adjusted(padding, padding, -padding, -padding)
        horizontal = {
            "left": Qt.AlignmentFlag.AlignLeft,
            "center": Qt.AlignmentFlag.AlignHCenter,
            "right": Qt.AlignmentFlag.AlignRight,
        }.get(getattr(piece, "text_halign", "center"),
              Qt.AlignmentFlag.AlignHCenter)
        vertical = {
            "top": Qt.AlignmentFlag.AlignTop,
            "center": Qt.AlignmentFlag.AlignVCenter,
            "bottom": Qt.AlignmentFlag.AlignBottom,
        }.get(getattr(piece, "text_valign", "center"),
              Qt.AlignmentFlag.AlignVCenter)
        painter.setPen(QColor(piece.text_color))
        painter.drawText(inner_rect,
                         horizontal | vertical | Qt.TextFlag.TextWordWrap,
                         piece.text)
        return
    if getattr(piece, "is_patch", False):
        fill = QColor(getattr(piece, "patch_color", "#10141c"))
        opacity = max(0.0, min(1.0, float(getattr(piece, "patch_opacity", 1.0))))
        if not fill.isValid() or opacity <= 0:
            return
        painter.save()
        painter.setOpacity(painter.opacity() * opacity)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRect(QRectF(-w / 2, -h / 2, w, h))
        painter.restore()
        return
    if getattr(piece, "is_scale_bar", False):
        color = QColor(getattr(piece, "scale_color", "#ffffff"))
        pen = QPen(color)
        pen.setWidthF(max(0.5, float(getattr(piece, "scale_line_width", 3.0))))
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        painter.setPen(pen)
        half = max(1.0, w / 2.0)
        tick = max(3.0, min(10.0, h * 0.22))
        painter.drawLine(QPointF(-half, -tick / 2), QPointF(half, -tick / 2))
        painter.drawLine(QPointF(-half, -tick), QPointF(-half, tick / 2))
        painter.drawLine(QPointF(half, -tick), QPointF(half, tick / 2))
        units = str(getattr(piece, "scale_units", "ft"))
        distance = float(getattr(piece, "scale_distance", 5.0))
        number = f"{distance:g}"
        caption = str(getattr(piece, "scale_caption", "")).strip()
        label = caption or f"{number} {units}"
        font = QFont("Arial")
        font.setPixelSize(max(8, min(24, int(max(8.0, h * 0.34)))))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(color)
        label_rect = QRectF(-half, tick * 0.35, w, max(8.0, h - tick * 0.35))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignHCenter |
                         Qt.AlignmentFlag.AlignTop, label)
        return
    if getattr(piece, "is_connector", False):
        color = QColor(getattr(piece, "connector_color", "#ffcc66"))
        pen = QPen(color)
        pen.setWidthF(max(0.5, float(getattr(piece, "connector_width", 3.0))))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(color)
        start = QPointF(-w / 2, -h / 2)
        end = QPointF(w / 2, h / 2)
        painter.drawLine(start, end)
        if getattr(piece, "connector_arrow", True):
            dx, dy = w, h
            length = math.hypot(dx, dy) or 1.0
            ux, uy = dx / length, dy / length
            arrow_len = max(8.0, min(20.0, length * 0.16))
            normal_x, normal_y = -uy, ux
            base_x = end.x() - ux * arrow_len
            base_y = end.y() - uy * arrow_len
            arrow = QPolygonF([
                end,
                QPointF(base_x + normal_x * arrow_len * 0.46,
                        base_y + normal_y * arrow_len * 0.46),
                QPointF(base_x - normal_x * arrow_len * 0.46,
                        base_y - normal_y * arrow_len * 0.46),
            ])
            painter.drawPolygon(arrow)
        label = str(getattr(piece, "connector_label", "")).strip()
        if label:
            font = QFont("Arial")
            font.setPixelSize(max(9, min(22, int(max(9.0, min(w, h or w) * 0.28)))))
            font.setBold(True)
            painter.setFont(font)
            label_rect = QRectF(-w * 0.35, -h * 0.35 - 22, w * 0.7, 24)
            painter.setPen(color)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)
        return
    tint_color = piece.tint_color
    tint_strength = piece.tint_strength
    if project is not None:
        tint_mode = getattr(piece, "tint_mode", "inherit")
        if tint_mode == "original":
            tint_color, tint_strength = "", 0.0
        elif tint_mode == "override":
            tint_color = piece.tint_color
            tint_strength = piece.tint_strength
        else:
            tint_color = getattr(project, "tint_color", "")
            tint_strength = getattr(project, "tint_strength", 0.0)
    if pm is not None and not pm.isNull():
        if tint_color and tint_strength > 0:
            pm = tinted_pixmap(pm, tint_color, tint_strength)
        crop = getattr(piece, "crop_rect", [0.0, 0.0, 1.0, 1.0])
        source = QRectF(crop[0] * pm.width(), crop[1] * pm.height(),
                        (crop[2] - crop[0]) * pm.width(),
                        (crop[3] - crop[1]) * pm.height())
        painter.drawPixmap(QRectF(-w / 2, -h / 2, w, h), pm, source)
    else:
        painter.setBrush(QColor("#555"))
        painter.setPen(QPen(QColor("#999")))
        painter.drawRect(QRectF(-w / 2, -h / 2, w, h))


_TINT_CACHE: "OrderedDict[tuple, QPixmap]" = OrderedDict()
_TINT_CACHE_MAX = 256


def tinted_pixmap(pm: QPixmap, color: str, strength: float) -> QPixmap:
    """Blend ``color`` over the image's own pixels only.

    The tint is composited inside the pixmap with SourceAtop, so fully
    transparent areas stay transparent. (Doing it on the destination painter
    instead tints the canvas behind the image, i.e. the invisible margin.)
    """
    strength = max(0.0, min(1.0, float(strength)))
    key = (pm.cacheKey(), str(color).lower(), round(strength, 3))
    cached = _TINT_CACHE.get(key)
    if cached is not None:
        _TINT_CACHE.move_to_end(key)
        return cached
    out = QPixmap(pm.size())
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.drawPixmap(0, 0, pm)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
    painter.setOpacity(strength)
    painter.fillRect(out.rect(), QColor(color))
    painter.end()
    _TINT_CACHE[key] = out
    while len(_TINT_CACHE) > _TINT_CACHE_MAX:
        _TINT_CACHE.popitem(last=False)
    return out


def draw_node_border(painter: QPainter, piece, pm: QPixmap | None,
                     project) -> bool:
    """Draw an optional bounds or alpha-silhouette border in piece-local space."""
    if (piece.is_text or getattr(piece, "is_patch", False)
            or getattr(piece, "is_scale_bar", False)
            or getattr(piece, "is_connector", False)
            or piece.border_mode == "off"):
        return False
    if piece.border_mode == "override":
        color_name = piece.border_color
        opacity = piece.border_opacity
    else:
        color_name = getattr(project, "border_color", "#69b7f5")
        opacity = getattr(project, "border_opacity", 0.85)
    color = QColor(color_name)
    if not color.isValid() or opacity <= 0:
        return False
    shape = piece.border_shape
    if shape == "inherit":
        shape = getattr(project, "node_border_shape", "bounds")
    if shape not in {"bounds", "alpha"}:
        shape = "bounds"

    painter.save()
    painter.setOpacity(painter.opacity() * max(0.0, min(1.0, float(opacity))))
    pen = QPen(color)
    pen.setWidthF(max(0.5, float(getattr(project, "border_width", 2.0))))
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if shape == "alpha" and pm is not None and not pm.isNull():
        path = _alpha_outline_path(pm)
        crop = getattr(piece, "crop_rect", [0.0, 0.0, 1.0, 1.0])
        src_x = crop[0] * pm.width()
        src_y = crop[1] * pm.height()
        src_w = max(1.0, (crop[2] - crop[0]) * pm.width())
        src_h = max(1.0, (crop[3] - crop[1]) * pm.height())
        clip = QPainterPath()
        clip.addRect(QRectF(src_x, src_y, src_w, src_h))
        path = path.intersected(clip)
        if not path.isEmpty():
            painter.translate(-piece.w / 2.0, -piece.h / 2.0)
            painter.scale(piece.w / src_w, piece.h / src_h)
            painter.translate(-src_x, -src_y)
            painter.drawPath(path)
            painter.restore()
            return True

    x0, x1 = -piece.w / 2.0, piece.w / 2.0
    y0, y1 = -piece.h / 2.0, piece.h / 2.0
    edges = list(getattr(piece, "border_edges", [True] * 4))
    edges.extend([True] * max(0, 4 - len(edges)))
    for enabled, p0, p1 in (
        (edges[0], (x0, y0), (x1, y0)),  # top
        (edges[1], (x1, y0), (x1, y1)),  # right
        (edges[2], (x1, y1), (x0, y1)),  # bottom
        (edges[3], (x0, y1), (x0, y0)),  # left
    ):
        if enabled:
            painter.drawLine(QPointF(*p0), QPointF(*p1))
    painter.restore()
    return True


def _alpha_outline_path(pm: QPixmap) -> QPainterPath:
    key = (int(pm.cacheKey()), pm.width(), pm.height())
    cached = _ALPHA_PATH_CACHE.get(key)
    if cached is not None:
        return cached
    path = QPainterPath()
    image = pm.toImage()
    if not image.isNull():
        mask = QBitmap.fromImage(image.createAlphaMask())
        path.addRegion(QRegion(mask))
    if len(_ALPHA_PATH_CACHE) >= 128:
        _ALPHA_PATH_CACHE.pop(next(iter(_ALPHA_PATH_CACHE)))
    _ALPHA_PATH_CACHE[key] = path
    return path


def draw_zone_borders(painter: QPainter, zones, project,
                      point_transform, width_scale: float = 1.0) -> None:
    """Draw gameplay-zone outlines and optional centered labels."""
    for zone in zones:
        if not zone.points:
            continue
        if zone.border_mode == "override":
            color_name, opacity = zone.border_color, zone.border_opacity
        else:
            color_name = getattr(project, "border_color", "#69b7f5")
            opacity = getattr(project, "border_opacity", 0.85)
        color = QColor(color_name)
        if not color.isValid():
            color = QColor("#69b7f5")
        painter.save()
        painter.setOpacity(painter.opacity() * max(0.0, min(1.0, float(opacity))))
        if zone.border_mode != "off" and len(zone.points) >= 2 and opacity > 0:
            pen = QPen(color)
            pen.setWidthF(max(0.5, float(getattr(project, "border_width", 2.0))
                              * max(0.01, width_scale)))
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for index, start in enumerate(zone.points):
                end = zone.points[(index + 1) % len(zone.points)]
                if index < len(zone.edge_visible) and zone.edge_visible[index]:
                    painter.drawLine(point_transform(*start), point_transform(*end))

        label_parts = []
        if getattr(zone, "show_label", True):
            label = str(getattr(zone, "label", "")).strip() or zone.name
            if label:
                label_parts.append(label)
        if getattr(zone, "show_id", False):
            label_parts.append(f"#{zone.id[:6]}")
        if label_parts:
            label = "  ".join(label_parts)
            cx = sum(point[0] for point in zone.points) / len(zone.points)
            cy = sum(point[1] for point in zone.points) / len(zone.points)
            center = point_transform(cx, cy)
            font = QFont("Arial")
            font.setPixelSize(max(9, min(26, int(14 * max(0.5, width_scale)))))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(color)
            width = max(80.0, min(360.0, len(label) * 9.0 * max(0.5, width_scale)))
            height = max(18.0, 24.0 * max(0.5, width_scale))
            painter.drawText(QRectF(center.x() - width / 2,
                                    center.y() - height / 2, width, height),
                             Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()


def compute_text_size(text: str, font_family: str, font_size: int,
                      bold: bool, italic: bool = False,
                      padding: int = 4) -> tuple[int, int]:
    """Return a snug editable text-node box for explicit newline-separated text."""
    font = QFont(font_family)
    font.setPixelSize(max(4, int(font_size)))
    font.setBold(bold)
    font.setItalic(italic)
    fm = QFontMetrics(font)
    lines = text.split("\n") or [""]
    width = max((fm.horizontalAdvance(line) for line in lines), default=0)
    height = fm.height() * max(1, len(lines))
    pad = max(0, min(100, int(padding)))
    return max(10, width + pad * 2), max(10, height + pad * 2)
