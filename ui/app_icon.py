"""SceneBoard's application icon, drawn in code.

A folded paper map with a square grid and a location pin on a dark tile:
"this makes maps", readable from 16 px taskbar size up to 256 px. The same
drawing produces the window icon at runtime and, through make_icon.py, the
multi-size ``ui/icons/SceneBoard.ico`` that build.bat gives the EXE.
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QColor, QIcon, QImage, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient)

ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
APP_USER_MODEL_ID = "ArenaMaps.SceneBoard"

# Panels of the folded map on a 256-unit grid: (top-left, top-right,
# bottom-right, bottom-left). Alternate slants read as a folded sheet.
_PANELS = (
    ((40.0, 80.0), (96.0, 64.0), (96.0, 188.0), (40.0, 204.0)),
    ((96.0, 64.0), (160.0, 80.0), (160.0, 204.0), (96.0, 188.0)),
    ((160.0, 80.0), (216.0, 64.0), (216.0, 188.0), (160.0, 204.0)),
)
_PANEL_FILL = ("#f1f5fa", "#c9d6e5", "#e6edf5")


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def paint_app_icon(painter: QPainter, size: int):
    """Draw the icon into a ``size`` x ``size`` area at the painter's origin."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    k = size / 256.0
    painter.scale(k, k)
    small = size <= 24
    medium = size <= 48

    # dark rounded tile
    tile = QRectF(8.0, 8.0, 240.0, 240.0)
    gradient = QLinearGradient(0.0, 8.0, 0.0, 248.0)
    gradient.setColorAt(0.0, QColor("#2a3d59"))
    gradient.setColorAt(1.0, QColor("#111a28"))
    painter.setPen(QPen(QColor("#4f6f96"), 6.0 if small else 4.0))
    painter.setBrush(gradient)
    painter.drawRoundedRect(tile, 52.0, 52.0)

    # soft shadow under the map
    shadow = QPolygonF([QPointF(44.0, 212.0), QPointF(220.0, 196.0),
                        QPointF(222.0, 204.0), QPointF(46.0, 220.0)])
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 70))
    painter.drawPolygon(shadow)

    # folded map panels
    for corners, fill in zip(_PANELS, _PANEL_FILL):
        painter.setBrush(QColor(fill))
        painter.setPen(QPen(QColor("#9fb1c7"), 2.0) if not small else Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([QPointF(*point) for point in corners]))

    # square grid printed on the map
    if not small:
        pen = QPen(QColor(46, 111, 223, 165 if medium else 140))
        pen.setWidthF(5.0 if medium else 3.0)
        painter.setPen(pen)
        rows = (0.5,) if medium else (0.25, 0.5, 0.75)
        cols = (0.5,) if medium else (0.5,)
        for top_left, top_right, bottom_right, bottom_left in _PANELS:
            for t in rows:
                a = _lerp(top_left, bottom_left, t)
                b = _lerp(top_right, bottom_right, t)
                painter.drawLine(QPointF(*a), QPointF(*b))
            for t in cols:
                a = _lerp(top_left, top_right, t)
                b = _lerp(bottom_left, bottom_right, t)
                painter.drawLine(QPointF(*a), QPointF(*b))

    # a route on the map, ending at the pin
    if not small:
        route = QPainterPath()
        route.moveTo(62.0, 176.0)
        route.cubicTo(92.0, 140.0, 118.0, 176.0, 140.0, 150.0)
        route.cubicTo(150.0, 140.0, 158.0, 146.0, 170.0, 146.0)
        pen = QPen(QColor("#e8572f"), 7.0 if medium else 5.0)
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([2.2, 1.6])
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(route)

    # location pin
    grow = 1.25 if small else 1.0
    cx, cy, radius = 172.0, 90.0, 36.0 * grow
    tip = QPointF(cx, cy + radius * 1.95)
    pin = QPainterPath()
    pin.moveTo(tip)
    pin.cubicTo(QPointF(cx - radius * 0.55, cy + radius * 1.25),
                QPointF(cx - radius, cy + radius * 0.75),
                QPointF(cx - radius, cy))
    pin.arcTo(QRectF(cx - radius, cy - radius, radius * 2.0, radius * 2.0), 180.0, -180.0)
    pin.cubicTo(QPointF(cx + radius, cy + radius * 0.75),
                QPointF(cx + radius * 0.55, cy + radius * 1.25), tip)
    pin.closeSubpath()
    fill = QRadialGradient(QPointF(cx - radius * 0.35, cy - radius * 0.4), radius * 1.6)
    fill.setColorAt(0.0, QColor("#ff8a5c"))
    fill.setColorAt(1.0, QColor("#d93a22"))
    painter.setPen(QPen(QColor("#7a1d10"), 3.0 if not small else 5.0))
    painter.setBrush(fill)
    painter.drawPath(pin)
    if not small:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPointF(cx, cy), radius * 0.38, radius * 0.38)
    painter.restore()


def icon_image(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_app_icon(painter, size)
    painter.end()
    return image


def app_icon() -> QIcon:
    """The window/taskbar icon with hand-tuned small sizes."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(icon_image(size)))
    return icon


def use_own_taskbar_icon():
    """Running from source on Windows, the taskbar would show python.exe's
    icon; an explicit AppUserModelID makes it use SceneBoard's window icon.
    The packaged EXE already carries the icon, so it is left alone."""
    if os.name != "nt" or getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def ico_bytes(sizes=ICO_SIZES) -> bytes:
    """A Windows .ico holding one PNG-compressed image per size."""
    import struct
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice

    images = []
    for size in sizes:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        icon_image(size).save(buffer, "PNG")
        buffer.close()
        images.append((size, bytes(data)))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, payload = b"", b""
    for size, png in images:
        dimension = 0 if size >= 256 else size      # 0 means 256 in ICO headers
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32,
                               len(png), offset + len(payload))
        payload += png
    return header + entries + payload
