"""Small vector icons painted at runtime so they follow the theme colors.

Used by the layer rows (visibility eye, export picture, padlock). Each icon is
drawn on a 24-unit grid and rendered at several pixel sizes, so it stays crisp
on normal and high-DPI screens.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

ICON_SIZES = (16, 18, 20, 24, 32, 36, 48)


def _render(draw, color: str, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 24.0, size / 24.0)
    draw(painter, QColor(color))
    painter.end()
    return pixmap


def _icon(draw, color: str) -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(_render(draw, color, size))
    return icon


def _pen(color: QColor, width: float = 1.9) -> QPen:
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _slash(painter: QPainter, color: QColor):
    painter.setPen(_pen(color, 2.1))
    painter.drawLine(QPointF(4.0, 20.5), QPointF(20.0, 3.5))


# -- eye: layer shown / hidden -------------------------------------------------
def _eye_outline() -> QPainterPath:
    path = QPainterPath()
    path.moveTo(2.0, 12.0)
    path.quadTo(12.0, 2.5, 22.0, 12.0)
    path.quadTo(12.0, 21.5, 2.0, 12.0)
    path.closeSubpath()
    return path


def eye_icon(color: str, open_: bool = True) -> QIcon:
    def draw(painter: QPainter, c: QColor):
        painter.setPen(_pen(c))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_eye_outline())
        if open_:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c)
            painter.drawEllipse(QPointF(12.0, 12.0), 3.6, 3.6)
        else:
            painter.drawEllipse(QPointF(12.0, 12.0), 2.6, 2.6)
            _slash(painter, c)
    return _icon(draw, color)


# -- picture: layer included in / left out of PNG and PDF exports --------------
def export_icon(color: str, included: bool = True) -> QIcon:
    def draw(painter: QPainter, c: QColor):
        painter.setPen(_pen(c, 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(3.0, 5.0, 18.0, 14.0), 2.2, 2.2)
        mountains = QPainterPath()
        mountains.moveTo(5.0, 17.0)
        mountains.lineTo(10.0, 11.0)
        mountains.lineTo(13.0, 14.0)
        mountains.lineTo(15.5, 11.5)
        mountains.lineTo(19.0, 17.0)
        mountains.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c)
        painter.drawPath(mountains)
        painter.drawEllipse(QPointF(16.2, 8.6), 1.6, 1.6)
        if not included:
            _slash(painter, c)
    return _icon(draw, color)


# -- padlock: layer locked / unlocked -----------------------------------------
def lock_icon(color: str, locked: bool = True) -> QIcon:
    def draw(painter: QPainter, c: QColor):
        painter.setPen(_pen(c, 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        shackle = QPainterPath()
        if locked:
            shackle.moveTo(8.0, 11.0)
            shackle.lineTo(8.0, 8.0)
            shackle.arcTo(QRectF(8.0, 4.0, 8.0, 8.0), 180.0, -180.0)
            shackle.lineTo(16.0, 11.0)
        else:
            shackle.moveTo(8.0, 11.0)
            shackle.lineTo(8.0, 6.5)
            shackle.arcTo(QRectF(8.0, 2.5, 8.0, 8.0), 180.0, -180.0)
            shackle.lineTo(16.0, 8.0)
        painter.drawPath(shackle)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c)
        painter.drawRoundedRect(QRectF(5.0, 11.0, 14.0, 10.0), 2.0, 2.0)
    return _icon(draw, color)
