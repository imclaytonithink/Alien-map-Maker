"""The 'Symbols & Abbreviations' legend that ships with the Geomorphs pack.

The image is never copied into the repository: it is found in the asset library (or the geomorph tile folder)
by its file name. It can be shown over the canvas and added to exported maps.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap

LEGEND_NAME = "symbols & abbreviations.png"
STATE = {"path": "", "show": False, "export": False}
_cache: dict = {}


def find_in_library(library, *extra_dirs) -> str:
    """Absolute path of the legend image, or '' when the pack has not been imported."""
    for a in getattr(library, "assets", []) or []:
        if str(a.path).replace("\\", "/").rsplit("/", 1)[-1].lower() == LEGEND_NAME:
            p = library.abs_path(a.path)
            if os.path.exists(p):
                return p
    for d in extra_dirs:
        if d:
            p = os.path.join(d, "Symbols & Abbreviations.png")
            if os.path.exists(p):
                return p
    return ""


def pixmap() -> QPixmap | None:
    p = STATE["path"]
    if not p or not os.path.exists(p):
        return None
    if _cache.get("path") != p:
        _cache.update(path=p, pm=QPixmap(p))
    pm = _cache["pm"]
    return None if pm.isNull() else pm


def _draw(painter: QPainter, area: QRectF, max_h: float, opacity: float):
    pm = pixmap()
    if pm is None:
        return
    h = min(max_h, area.height() - 20)
    if h < 40:
        return
    w = h * pm.width() / pm.height()
    x, y = area.right() - w - 10, area.top() + 10
    painter.save()
    painter.setOpacity(opacity)
    painter.fillRect(QRectF(x - 4, y - 4, w + 8, h + 8), QColor(9, 13, 15))
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawPixmap(QRectF(x, y, w, h), pm, QRectF(pm.rect()))
    painter.restore()


def draw_overlay(painter: QPainter, widget_rect):
    """Screen-space overlay on the canvas (top right corner)."""
    if STATE["show"]:
        r = QRectF(widget_rect)
        _draw(painter, r, r.height() * 0.8, 0.94)


def draw_export(painter: QPainter, width: float, height: float):
    """Bottom-right corner of an exported map (about a third of its height)."""
    if STATE["export"] and pixmap() is not None:
        _draw(painter, QRectF(0, 0, width, height), height * 0.4, 1.0)
