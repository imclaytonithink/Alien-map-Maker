"""Headless rendering & export (PNG, PDF, thumbnails)."""
from __future__ import annotations

import math
import os
from typing import Optional

from PyQt6.QtCore import Qt, QRectF, QSizeF
from PyQt6.QtGui import QImage, QPainter, QPixmap, QColor, QPen, QPdfWriter, QPageSize
from PyQt6.QtWidgets import QApplication

from core.project import Level, Project, Piece, decode_embed
from core.render import draw_piece


def ensure_app():
    if QApplication.instance() is None:
        QApplication([])


def piece_pixmap(piece: Piece, project: Project, cache: dict) -> QPixmap:
    if piece.embedded:
        key = "emb:" + piece.id
        pm = cache.get(key)
        if pm is None:
            pm = QPixmap()
            pm.loadFromData(decode_embed(piece.embedded))
            cache[key] = pm
        return pm
    key = piece.asset_path
    pm = cache.get(key)
    if pm is None:
        pm = QPixmap()
        if piece.asset_path:
            pm.load(project.resolve_asset(piece.asset_path))
        cache[key] = pm
    return pm


def _draw_grid(painter, project, scale, color, opacity):
    if not project.grid_major:
        project.grid_major = 5
    pen = QPen(QColor(color))
    pen.setWidthF(max(1.0, scale))
    if project.grid_style == "dashed":
        pen.setStyle(Qt.PenStyle.DashLine)
    elif project.grid_style == "dotted":
        pen.setStyle(Qt.PenStyle.DotLine)
    cell = project.cell_size
    w = project.canvas_w
    h = project.canvas_h
    painter.setOpacity(opacity * 0.6)
    painter.setPen(pen)
    for gx in range(0, w + 1, cell):
        painter.drawLine(int(gx * scale), 0, int(gx * scale), int(h * scale))
    for gy in range(0, h + 1, cell):
        painter.drawLine(0, int(gy * scale), int(w * scale), int(gy * scale))
    # major lines
    pen.setWidthF(max(2.0, scale * 1.6))
    painter.setOpacity(opacity)
    painter.setPen(pen)
    step = cell * project.grid_major
    for gx in range(0, w + 1, step):
        painter.drawLine(int(gx * scale), 0, int(gx * scale), int(h * scale))
    for gy in range(0, h + 1, step):
        painter.drawLine(0, int(gy * scale), int(w * scale), int(gy * scale))


def render_level(project: Project, level: Level, include_grid: bool = True,
                 scale: float = 1.0, transparent: bool = False) -> QImage:
    w = max(1, int(project.canvas_w * scale))
    h = max(1, int(project.canvas_h * scale))
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    if transparent:
        img.fill(QColor(0, 0, 0, 0))
    else:
        img.fill(QColor(level.background))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    cache: dict = {}
    for p in level.paint_order():
        lyr = level.layer_by_id(p.layer)
        lop = lyr.opacity if lyr else 1.0
        pm = piece_pixmap(p, project, cache)
        # Piece.x/y is the visual top-left; center accounts for p.scale.
        cx = (p.x + p.w * p.scale / 2.0) * scale
        cy = (p.y + p.h * p.scale / 2.0) * scale
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(p.rotation)
        sx = scale * p.scale * (-1 if p.flip_h else 1)
        sy = scale * p.scale * (-1 if p.flip_v else 1)
        painter.scale(sx, sy)
        painter.setOpacity(lop * p.opacity)
        draw_piece(painter, p, pm)
        painter.restore()
    if include_grid:
        _draw_grid(painter, project, scale, project.export_grid_color,
                   project.export_grid_opacity)
    painter.end()
    return img


def export_level_to_file(project, level, path, include_grid=True, scale=1.0,
                         transparent=False):
    img = render_level(project, level, include_grid, scale, transparent)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path, "PNG")
    return path


def export_all_levels(project, out_dir, include_grid=True, scale=1.0,
                      name_prefix="map", transparent=False):
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for i, lvl in enumerate(project.levels, 1):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in lvl.name)
        path = os.path.join(out_dir, f"{name_prefix}_{i:02d}_{safe}.png")
        export_level_to_file(project, lvl, path, include_grid, scale, transparent)
        out.append(path)
    return out


# ---- presets ----
PRESETS = {
    "Original (1×)": 1.0,
    "Print 2×": 2.0,
    "Print 4×": 4.0,
    "Foundry (140px/sq)": None,   # computed
    "Roll20 (70px/sq)": None,
    "Tabletop Sim (1024px board)": None,
}


def preset_scale(project: Project, name: str) -> float:
    if name in PRESETS and PRESETS[name] is not None:
        return PRESETS[name]
    if name.startswith("Foundry"):
        return 140.0 / max(1, project.cell_size)
    if name.startswith("Roll20"):
        return 70.0 / max(1, project.cell_size)
    if name.startswith("Tabletop"):
        return 1024.0 / max(1, project.canvas_w)
    return 1.0


def export_pdf(project, out_path, include_grid=True, scale=1.0):
    ensure_app()
    writer = QPdfWriter(out_path)
    writer.setResolution(96)
    cache: dict = {}
    painter = QPainter(writer)
    for i, lvl in enumerate(project.levels):
        img = render_level(project, lvl, include_grid, scale)
        if i == 0:
            writer.setPageSize(QPageSize(
                QSizeF(img.width() / 96.0, img.height() / 96.0),
                QPageSize.Unit.Inch))
        else:
            writer.newPage()
        painter.drawImage(0, 0, img)
    painter.end()
    return out_path


def save_thumbnail(project, bmap_path, size=256):
    ensure_app()
    img = render_level(project, project.levels[0], False, 1.0)
    img = img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    thumb = os.path.splitext(bmap_path)[0] + ".png"
    img.save(thumb, "PNG")
    return thumb
