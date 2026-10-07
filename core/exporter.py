"""Headless rendering & export (PNG, PDF, thumbnails)."""
from __future__ import annotations

import math
import os
import re
from collections import OrderedDict

from PyQt6.QtCore import (QByteArray, QBuffer, QIODevice, QPointF, Qt, QRectF,
                          QSize, QSizeF)
from PyQt6.QtGui import (QImage, QImageReader, QPainter, QPixmap, QColor, QPen,
                         QPdfWriter, QPageSize)
from PyQt6.QtWidgets import QApplication

from core.project import Level, Project, Piece, decode_embed
from core.render import draw_node_border, draw_piece, draw_zone_borders


def ensure_app():
    if QApplication.instance() is None:
        QApplication([])


def embedded_size(encoded: str) -> tuple[int, int] | None:
    """Read embedded-image dimensions without decoding its full pixel array."""
    try:
        buffer = QBuffer()
        buffer.setData(QByteArray(decode_embed(encoded)))
        if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            return None
        reader = QImageReader(buffer)
        size = reader.size()
        buffer.close()
        return (size.width(), size.height()) if size.isValid() else None
    except (TypeError, ValueError):
        return None


class PixmapCache(OrderedDict):
    """Small LRU for decoded/scaled map images, bounded by bytes and entries."""

    def __init__(self, max_bytes=256 * 1024 * 1024, max_entries=64):
        super().__init__()
        self.max_bytes = int(max_bytes)
        self.max_entries = int(max_entries)

    @staticmethod
    def _bytes(pixmap):
        return max(0, pixmap.width()) * max(0, pixmap.height()) * 4

    def remember(self, key, pixmap):
        size = self._bytes(pixmap)
        if size <= 0 or size > self.max_bytes:
            return
        if key in self:
            del self[key]
        self[key] = pixmap
        while self and (len(self) > self.max_entries or
                        sum(self._bytes(item) for item in self.values()) > self.max_bytes):
            self.popitem(last=False)

    def get(self, key, default=None):
        value = super().get(key, default)
        if value is not default and key in self:
            self.move_to_end(key)
        return value


def _scaled_pixmap_from_reader(reader: QImageReader, target_size) -> QPixmap:
    source = reader.size()
    if not source.isValid() or source.width() <= 0 or source.height() <= 0:
        return QPixmap()
    if target_size is None:
        max_w = max_h = 2048
    else:
        max_w = max(1, int(target_size[0]))
        max_h = max(1, int(target_size[1]))
    factor = min(1.0, max_w / source.width(), max_h / source.height())
    if factor < 0.999:
        reader.setScaledSize(QSize(max(1, round(source.width() * factor)),
                                   max(1, round(source.height() * factor))))
    image = reader.read()
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


def _cache_remember(cache: dict, key, pixmap: QPixmap):
    if isinstance(cache, PixmapCache):
        cache.remember(key, pixmap)
        return
    size = max(0, pixmap.width()) * max(0, pixmap.height()) * 4
    if size <= 0 or size > 256 * 1024 * 1024:
        return
    cache[key] = pixmap
    while cache and (len(cache) > 64 or sum(
            max(0, value.width()) * max(0, value.height()) * 4
            for value in cache.values() if isinstance(value, QPixmap)) > 256 * 1024 * 1024):
        oldest = next(iter(cache))
        if oldest == key and len(cache) == 1:
            break
        cache.pop(oldest, None)


def piece_pixmap(piece: Piece, project: Project, cache: dict,
                 target_size=None) -> QPixmap:
    """Read a source image only as large as its current display/export target."""
    requested_size = ((2048, 2048) if target_size is None else
                      (max(1, int(target_size[0])), max(1, int(target_size[1]))))
    if piece.embedded:
        source_key = "emb:" + piece.id
        embedded = True
        source = None
    else:
        source_key = piece.asset_path
        embedded = False
        source = project.resolve_asset(piece.asset_path) if piece.asset_path else ""
    key = (source_key, requested_size)
    pm = cache.get(key)
    if pm is not None:
        # Preserve LRU behavior for ordinary dictionaries supplied by callers.
        if not isinstance(cache, PixmapCache) and key in cache:
            cache[key] = cache.pop(key)
        return pm

    if embedded:
        source = decode_embed(piece.embedded)
    if not source:
        pm = QPixmap()
    elif embedded:
        buffer = QBuffer()
        buffer.setData(QByteArray(source))
        if buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            reader = QImageReader(buffer)
            reader.setAutoTransform(True)
            pm = _scaled_pixmap_from_reader(reader, requested_size)
            buffer.close()
        else:
            pm = QPixmap()
    else:
        reader = QImageReader(source)
        reader.setAutoTransform(True)
        pm = _scaled_pixmap_from_reader(reader, requested_size)
    _cache_remember(cache, key, pm)
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
    if getattr(project, "show_centerlines", False):
        mid = QPen(QColor(color))
        mid.setWidthF(max(1.0, scale))
        mid.setStyle(Qt.PenStyle.DashLine)
        painter.setOpacity(opacity * 0.45)
        painter.setPen(mid)
        half = cell / 2.0
        gx = half
        while gx < w:
            painter.drawLine(int(gx * scale), 0, int(gx * scale), int(h * scale))
            gx += cell
        gy = half
        while gy < h:
            painter.drawLine(0, int(gy * scale), int(w * scale), int(gy * scale))
            gy += cell
        painter.setPen(pen)
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
    cache: dict = PixmapCache()
    for p in level.paint_order():
        lyr = level.layer_by_id(p.layer)
        lop = lyr.opacity if lyr else 1.0
        target_size = (max(1, round(p.w * p.scale * scale)),
                       max(1, round(p.h * p.scale * scale)))
        pm = piece_pixmap(p, project, cache, target_size)
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
    cache = cache if cache is not None else PixmapCache()
    for piece in level.paint_order():
        layer = level.layer_by_id(piece.layer)
        layer_opacity = layer.opacity if layer else 1.0
        target_size = (max(1, round(piece.w * piece.scale)),
                       max(1, round(piece.h * piece.scale)))
        pixmap = piece_pixmap(piece, project, cache, target_size)
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
