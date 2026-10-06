"""Headless rendering & export (PNG, PDF, thumbnails)."""
from __future__ import annotations

import math
import os
import re
from typing import Optional

from PyQt6.QtCore import QPointF, Qt, QRectF, QSizeF
from PyQt6.QtGui import QImage, QPainter, QPixmap, QColor, QPen, QPdfWriter, QPageSize
from PyQt6.QtWidgets import QApplication

from core.project import Level, Project, Piece, decode_embed
from core.render import draw_node_border, draw_piece, draw_zone_borders


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
                 scale: float = 1.0, transparent: bool = False,
                 grid_color: str | None = None,
                 grid_opacity: float | None = None,
                 include_node_borders: bool | None = None,
                 include_zones: bool | None = None) -> QImage:
    w = max(1, int(project.canvas_w * scale))
    h = max(1, int(project.canvas_h * scale))
    image_format = (QImage.Format.Format_ARGB32 if transparent
                    else QImage.Format.Format_RGB32)
    img = QImage(w, h, image_format)
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
        draw_piece(painter, p, pm, project)
        draw_borders = (getattr(project, "export_node_borders", False)
                        if include_node_borders is None else include_node_borders)
        if draw_borders:
            draw_node_border(painter, p, pm, project)
        painter.restore()
    draw_regions = (getattr(project, "export_zones", True)
                    if include_zones is None else include_zones)
    if draw_regions:
        draw_zone_borders(
            painter, level.zones, project,
            lambda x, y: QPointF(x * scale, y * scale), scale)
    if include_grid:
        _draw_grid(painter, project, scale,
                   grid_color or project.export_grid_color,
                   project.export_grid_opacity if grid_opacity is None
                   else grid_opacity)
    painter.end()
    return img


def sample_level_color(project: Project, level: Level, world_x: float,
                       world_y: float, cache: dict | None = None) -> QColor:
    """Sample the rendered map pixel without editor-only guides or grid lines."""
    x = int(math.floor(world_x))
    y = int(math.floor(world_y))
    if not (0 <= x < project.canvas_w and 0 <= y < project.canvas_h):
        return QColor()

    image = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(level.background))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(-x, -y)
    cache = cache if cache is not None else {}
    for piece in level.paint_order():
        layer = level.layer_by_id(piece.layer)
        layer_opacity = layer.opacity if layer else 1.0
        pixmap = piece_pixmap(piece, project, cache)
        painter.save()
        painter.translate(piece.center[0], piece.center[1])
        painter.rotate(piece.rotation)
        painter.scale(piece.scale * (-1 if piece.flip_h else 1),
                      piece.scale * (-1 if piece.flip_v else 1))
        painter.setOpacity(layer_opacity * piece.opacity)
        draw_piece(painter, piece, pixmap, project)
        painter.restore()
    painter.end()
    return image.pixelColor(0, 0)


def export_level_to_file(project, level, path, include_grid=True, scale=1.0,
                         transparent=False, grid_color=None, grid_opacity=None,
                         include_node_borders=None, include_zones=None):
    img = render_level(project, level, include_grid, scale, transparent,
                       grid_color, grid_opacity, include_node_borders,
                       include_zones)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path, "PNG")
    return path


def export_all_levels(project, out_dir, include_grid=True, scale=1.0,
                      name_prefix="map", transparent=False, grid_color=None,
                      grid_opacity=None, include_node_borders=None,
                      include_zones=None):
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for i, lvl in enumerate(project.levels, 1):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in lvl.name)
        path = os.path.join(out_dir, f"{name_prefix}_{i:02d}_{safe}.png")
        export_level_to_file(project, lvl, path, include_grid, scale, transparent,
                             grid_color, grid_opacity, include_node_borders,
                             include_zones)
        out.append(path)
    return out


# ---- presets ----
PRESETS = {
    "Original (1×)": 1.0,
    "Print 2×": 2.0,
    "Print 4×": 4.0,
    "Foundry (140px/sq)": None,   # computed
    "Roll20 (70px/sq)": None,
    "Tabletop Sim (1024px)": None,
    "Tabletop Sim (2048px)": None,
    "Tabletop Sim (3072px high-res)": None,
}


def preset_scale(project: Project, name: str) -> float:
    if name in PRESETS and PRESETS[name] is not None:
        return PRESETS[name]
    if name.startswith("Foundry"):
        return 140.0 / max(1, project.cell_size)
    if name.startswith("Roll20"):
        return 70.0 / max(1, project.cell_size)
    if name.startswith("Tabletop"):
        match = re.search(r"(1024|2048|3072)", name)
        target = int(match.group(1)) if match else 1024
        return target / max(1, project.canvas_w, project.canvas_h)
    return 1.0


def export_pdf(project, out_path, include_grid=True, scale=1.0, levels=None,
               transparent=False, grid_color=None, grid_opacity=None,
               include_node_borders=None, include_zones=None):
    """Export chosen level(s) to PDF with the same grid/render options as PNG.

    Each selected level becomes one page. ``levels`` defaults to every level
    to preserve the original API/behavior.
    """
    ensure_app()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    writer = QPdfWriter(out_path)
    writer.setResolution(96)
    selected_levels = list(project.levels if levels is None else levels)
    if not selected_levels:
        return out_path
    first = render_level(project, selected_levels[0], include_grid, scale,
                         transparent, grid_color, grid_opacity,
                         include_node_borders, include_zones)
    writer.setPageSize(QPageSize(
        QSizeF(first.width() / 96.0, first.height() / 96.0),
        QPageSize.Unit.Inch))
    painter = QPainter(writer)
    painter.drawImage(0, 0, first)
    for lvl in selected_levels[1:]:
        writer.newPage()
        img = render_level(project, lvl, include_grid, scale, transparent,
                           grid_color, grid_opacity, include_node_borders,
                           include_zones)
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
