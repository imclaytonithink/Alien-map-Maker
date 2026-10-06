"""Shared piece drawing used by both the live canvas and the exporter.

Draws inside an already-transformed painter (translated to the piece center,
rotated, and scaled by zoom*scale). Local coordinates are world pixels.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QPainter, QPixmap, QColor, QFont, QPen, QFontMetrics


def draw_piece(painter: QPainter, piece, pm: QPixmap | None):
    w, h = piece.w, piece.h
    if piece.is_text:
        font = QFont(piece.font_family)
        font.setPixelSize(max(4, int(piece.font_size)))
        font.setBold(piece.font_bold)
        painter.setFont(font)
        painter.setPen(QColor(piece.text_color))
        painter.drawText(QRectF(-w / 2, -h / 2, w, h),
                         Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                         piece.text)
        return
    if pm is not None and not pm.isNull():
        painter.drawPixmap(QRectF(-w / 2, -h / 2, w, h), pm,
                           QRectF(0, 0, pm.width(), pm.height()))
    else:
        painter.setBrush(QColor("#555"))
        painter.setPen(QPen(QColor("#999")))
        painter.drawRect(QRectF(-w / 2, -h / 2, w, h))
    if piece.tint_color and piece.tint_strength > 0:
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        painter.setOpacity(piece.tint_strength)
        painter.fillRect(QRectF(-w / 2, -h / 2, w, h), QColor(piece.tint_color))
        painter.restore()


def compute_text_size(text: str, font_family: str, font_size: int,
                      bold: bool) -> tuple[int, int]:
    font = QFont(font_family)
    font.setPixelSize(max(4, int(font_size)))
    font.setBold(bold)
    fm = QFontMetrics(font)
    lines = text.split("\n")
    w = max((fm.horizontalAdvance(ln) for ln in lines), default=10)
    h = fm.height() * len(lines)
    return max(10, w + 8), max(10, h + 6)
