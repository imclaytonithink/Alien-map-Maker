"""Headless rendering & export (PNG, PDF, thumbnails)."""
from __future__ import annotations

import math
import os
import re
from collections import OrderedDict

from PyQt6.QtCore import (QByteArray, QBuffer, QIODevice, QPointF, Qt, QRectF,
                          QSize, QSizeF)
from PyQt6.QtGui import (QBrush, QImage, QImageReader, QPainter, QPainterPath,
                         QPixmap, QColor, QPen, QPdfWriter, QPageSize, QFont,
                         QFontMetricsF, QTransform)
from PyQt6.QtWidgets import QApplication

from core.imaging import decode_guard
from core.guides import column_label, grid_counts, label_step, row_label
from core.project import DEFAULT_GUIDE_COLOR, Level, Project, Piece, decode_embed
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


def _scaled_image_from_reader(reader: QImageReader, target_size) -> QImage:
    source = reader.size()
    if not source.isValid() or source.width() <= 0 or source.height() <= 0:
        return QImage()
    if target_size is None:
        max_w = max_h = 2048
    else:
        max_w = max(1, int(target_size[0]))
        max_h = max(1, int(target_size[1]))
    factor = min(1.0, max_w / source.width(), max_h / source.height())
    if factor < 0.999:
        reader.setScaledSize(QSize(max(1, round(source.width() * factor)),
                                   max(1, round(source.height() * factor))))
    with decode_guard(source.width(), source.height()):
        return reader.read()


def _scaled_pixmap_from_reader(reader: QImageReader, target_size) -> QPixmap:
    image = _scaled_image_from_reader(reader, target_size)
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


# ---- decoded-size disk cache: big tile PNGs decode once, then load in ms ----
DISK_CACHE_MAX_SIDE = 2048


def decode_cache_dir() -> str:
    return os.environ.get("SCENEBOARD_PX_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "sceneboard_px")


def file_image(path: str, target_size) -> QImage:
    """Scaled decode of an image file, thread-safe, using the on-disk cache.

    Safe to call from a worker thread (returns a QImage, never a QPixmap)."""
    import hashlib
    try:
        st = os.stat(path)
    except OSError:
        return QImage()
    size = (max(1, int(target_size[0])), max(1, int(target_size[1])))
    cacheable = max(size) <= DISK_CACHE_MAX_SIDE and st.st_size > 256 * 1024
    cp = ""
    if cacheable:
        digest = hashlib.sha1(
            f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|{size[0]}x{size[1]}".encode()
        ).hexdigest()
        cp = os.path.join(decode_cache_dir(), digest[:2], digest + ".png")
        if os.path.isfile(cp):
            cached = QImage(cp)
            if not cached.isNull():
                return cached
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    image = _scaled_image_from_reader(reader, size)
    if cp and not image.isNull():
        try:
            os.makedirs(os.path.dirname(cp), exist_ok=True)
            image.save(cp + ".tmp.png", "PNG", 1)
            os.replace(cp + ".tmp.png", cp)
        except OSError:
            pass
    return image


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
        image = file_image(source, requested_size)
        pm = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
    _cache_remember(cache, key, pm)
    return pm


MAX_DECODE_PIXELS = 36_000_000     # cap for one decoded source image


def source_target_size(piece, factor: float) -> tuple[int, int]:
    """How large to decode a node's whole source image so that the part it
    shows (its crop) appears at ``factor`` (zoom or export scale) without
    being blurry. A node cropped to a fifth of its picture needs the picture
    decoded five times larger than the node itself."""
    crop = getattr(piece, "crop_rect", None) or [0.0, 0.0, 1.0, 1.0]
    crop_w = max(0.01, float(crop[2]) - float(crop[0]))
    crop_h = max(0.01, float(crop[3]) - float(crop[1]))
    width = max(1.0, float(piece.w) * float(piece.scale) * factor / crop_w)
    height = max(1.0, float(piece.h) * float(piece.scale) * factor / crop_h)
    if width * height > MAX_DECODE_PIXELS:
        shrink = math.sqrt(MAX_DECODE_PIXELS / (width * height))
        width, height = width * shrink, height * shrink
    return max(1, round(width)), max(1, round(height))


# ---- level backdrops (solid color, tiled floor texture, or transparent) ----
def backdrop_is_transparent(level) -> bool:
    return getattr(level, "backdrop", "color") == "none"


_IMAGE_SIZES: "OrderedDict[tuple, tuple[int, int] | None]" = OrderedDict()


def _image_size(path: str) -> tuple[int, int] | None:
    """Pixel size of an image file (header only), cached per file version so the
    canvas doesn't reread it on every repaint."""
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    key = (path, stamp)
    if key in _IMAGE_SIZES:
        return _IMAGE_SIZES[key]
    size = QImageReader(path).size()
    found = ((size.width(), size.height())
             if size.isValid() and size.width() > 0 and size.height() > 0 else None)
    _IMAGE_SIZES[key] = found
    while len(_IMAGE_SIZES) > 64:
        _IMAGE_SIZES.popitem(last=False)
    return found


def _bucket(n: float) -> int:
    """Round a pixel size up to a sqrt(2) step so zooming reuses decodes."""
    n = max(8.0, float(n))
    steps = math.ceil(math.log(n / 8.0, math.sqrt(2.0)) - 1e-9)
    return int(round(8 * math.sqrt(2.0) ** steps))


def texture_world_size(project, level) -> tuple[float, float] | None:
    """World size of one backdrop texture tile, or None without a texture."""
    path = level.backdrop_texture_in_use() if hasattr(level, "backdrop_texture_in_use") else ""
    if not path:
        return None
    size = _image_size(project.resolve_asset(path))
    if size is None:
        return None
    squares = float(getattr(level, "backdrop_tile", 0.0) or 0.0)
    width = squares * max(1, project.cell_size) if squares > 0 else float(size[0])
    return width, width * size[1] / float(size[0])


def backdrop_texture_brush(project, level, factor: float, cache,
                           origin=(0.0, 0.0)) -> QBrush | None:
    """A brush that tiles the level's backdrop texture at ``factor`` device px
    per world px, anchored so tiles start at the map's top-left (``origin``,
    in device px)."""
    tile = texture_world_size(project, level)
    if tile is None:
        return None
    tile_w, tile_h = max(1.0, tile[0] * factor), max(1.0, tile[1] * factor)
    probe = Piece(asset_path=level.backdrop_texture, w=tile[0], h=tile[1])
    pm = piece_pixmap(probe, project, cache, (_bucket(tile_w), _bucket(tile_h)))
    if pm.isNull():
        return None
    brush = QBrush(pm)
    transform = QTransform()
    transform.translate(origin[0], origin[1])
    transform.scale(tile_w / pm.width(), tile_h / pm.height())
    brush.setTransform(transform)
    return brush


def draw_backdrop(painter, project, level, rect: QRectF, factor: float, cache,
                  origin=(0.0, 0.0)) -> None:
    """Fill ``rect`` (device px) with the level's backdrop: its color, plus
    the tiled texture in texture mode. Transparent backdrops draw nothing."""
    mode = getattr(level, "backdrop", "color")
    if mode == "none":
        return
    color = QColor(level.background)
    painter.fillRect(rect, color if color.isValid() else QColor("#10141c"))
    if mode != "texture":
        return
    brush = backdrop_texture_brush(project, level, factor, cache, origin)
    if brush is None:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setOpacity(painter.opacity() * max(0.0, min(1.0, float(
        getattr(level, "backdrop_opacity", 1.0)))))
    painter.fillRect(rect, brush)
    painter.restore()


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
                 include_zones: bool | None = None,
                 include_centerlines: bool = False,
                 include_guides: bool = False,
                 include_coordinates: bool = False,
                 opaque: bool = False) -> QImage:
    """Render one level. Editor aids (canvas centerlines, placed guides and a
    grid-coordinate border) are only drawn when explicitly requested.

    ``transparent`` leaves the level's backdrop out. A level whose backdrop is
    "none" renders transparent anyway, unless ``opaque`` asks for a solid
    image (Tabletop Simulator boards), which then uses the level's color."""
    w = max(1, int(project.canvas_w * scale))
    h = max(1, int(project.canvas_h * scale))
    see_through = (transparent or backdrop_is_transparent(level)) and not opaque
    image_format = (QImage.Format.Format_ARGB32 if see_through
                    else QImage.Format.Format_RGB32)
    img = QImage(w, h, image_format)
    cache: dict = PixmapCache()
    if see_through:
        img.fill(QColor(0, 0, 0, 0))
    else:
        background = QColor(level.background)
        img.fill(background if background.isValid() else QColor("#10141c"))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    if not see_through and not transparent:
        draw_backdrop(painter, project, level, QRectF(0, 0, w, h), scale, cache)
    for p in level.paint_order():
        lyr = level.layer_by_id(p.layer)
        if lyr is not None and not getattr(lyr, "export", True):
            continue        # "Don't export" layers stay on the canvas only
        lop = lyr.opacity if lyr else 1.0
        pm = piece_pixmap(p, project, cache, source_target_size(p, scale))
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
    if include_centerlines:
        _draw_centerlines(painter, project, scale)
    if include_guides:
        _draw_guides(painter, project, level, scale)
    try:
        from core import legend
        legend.draw_export(painter, img.width(), img.height())
    except Exception:                         # a missing/broken legend must never break an export
        pass
    painter.end()
    if include_coordinates:
        img = _with_coordinate_border(img, project, level, scale, see_through)
    return img


def _draw_centerlines(painter, project, scale):
    """Dashed amber lines through the middle of the canvas."""
    painter.save()
    mid = QPen(QColor("#ffb000"))
    mid.setWidthF(max(1.5, scale * 1.5))
    mid.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(mid)
    painter.setOpacity(0.85)
    painter.drawLine(int(project.canvas_w * scale / 2), 0,
                     int(project.canvas_w * scale / 2),
                     int(project.canvas_h * scale))
    painter.drawLine(0, int(project.canvas_h * scale / 2),
                     int(project.canvas_w * scale),
                     int(project.canvas_h * scale / 2))
    painter.restore()


def guide_qcolor(project) -> QColor:
    color = QColor(getattr(project, "guide_color", DEFAULT_GUIDE_COLOR))
    return color if color.isValid() else QColor(DEFAULT_GUIDE_COLOR)


def _draw_guides(painter, project, level, scale):
    """Placed guides as crisp lines with a faint dark edge (any background)."""
    guides = list(getattr(level, "guides", []) or [])
    if not guides:
        return
    width = max(1, int(round(scale * 1.5)))
    w, h = int(project.canvas_w * scale), int(project.canvas_h * scale)
    color = guide_qcolor(project)
    opacity = max(0.1, min(1.0, float(getattr(project, "guide_opacity", 0.9))))
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    halo = QPen(QColor(0, 0, 0, 80))
    halo.setWidth(width + 2)
    line = QPen(color)
    line.setWidth(width)
    for pen, alpha in ((halo, 1.0), (line, opacity)):
        painter.setPen(pen)
        painter.setOpacity(alpha)
        for guide in guides:
            at = int(round(guide.pos * scale))
            if guide.axis == "v" and 0 <= at <= w:
                painter.drawLine(at, 0, at, h)
            elif guide.axis == "h" and 0 <= at <= h:
                painter.drawLine(0, at, w, at)
    painter.restore()


def _with_coordinate_border(image, project, level, scale, transparent):
    """Return ``image`` inside a border one square wide, labeled with column
    letters (top and bottom) and row numbers (left and right). The border is
    a whole square so a VTT grid still lines up, just offset by one square."""
    cell_px = max(1.0, project.cell_size * scale)
    border = max(1, int(round(cell_px)))
    w, h = image.width(), image.height()
    out = QImage(w + 2 * border, h + 2 * border, image.format())
    background = QColor(level.background)
    if not background.isValid():
        background = QColor("#10141c")
    out.fill(QColor(0, 0, 0, 0) if transparent else background)
    luminance = (0.2126 * background.red() + 0.7152 * background.green()
                 + 0.0722 * background.blue())
    if transparent or luminance < 140:
        text, halo = QColor("#eef2f6"), QColor(0, 0, 0, 190)
    else:
        text, halo = QColor("#1b2129"), QColor(255, 255, 255, 190)

    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.drawImage(border, border, image)
    frame = QPen(text)
    frame.setWidthF(max(1.0, scale))
    painter.setPen(frame)
    painter.setOpacity(0.45)
    painter.drawRect(QRectF(border - 0.5, border - 0.5, w + 1, h + 1))
    painter.setOpacity(1.0)

    font = QFont()
    font.setBold(True)
    font.setPixelSize(max(6, min(64, int(border * 0.42))))
    metrics = QFontMetricsF(font)
    cols, rows = grid_counts(project.canvas_w, project.canvas_h, project.cell_size)
    col_step = label_step(cell_px, metrics.horizontalAdvance(column_label(cols - 1)), 3)
    row_step = label_step(cell_px, metrics.height(), 1)
    halo_pen = QPen(halo, max(1.5, font.pixelSize() / 5.0))
    halo_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

    def label(value: str, cx: float, cy: float):
        path = QPainterPath()
        path.addText(cx - metrics.horizontalAdvance(value) / 2.0,
                     cy + (metrics.ascent() - metrics.descent()) / 2.0, font, value)
        painter.strokePath(path, halo_pen)
        painter.fillPath(path, text)

    for i in range(0, cols, col_step):
        cx = border + min((i + 0.5) * cell_px, w - 0.5 * min(cell_px, w))
        label(column_label(i), cx, border / 2.0)
        label(column_label(i), cx, border + h + border / 2.0)
    for j in range(0, rows, row_step):
        cy = border + min((j + 0.5) * cell_px, h - 0.5 * min(cell_px, h))
        label(row_label(j), border / 2.0, cy)
        label(row_label(j), border + w + border / 2.0, cy)
    painter.end()
    return out


def sample_level_color(project: Project, level: Level, world_x: float,
                       world_y: float, cache: dict | None = None) -> QColor:
    """Sample the rendered map pixel without editor-only guides or grid lines."""
    x = int(math.floor(world_x))
    y = int(math.floor(world_y))
    if not (0 <= x < project.canvas_w and 0 <= y < project.canvas_h):
        return QColor()

    image = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
    background = QColor(level.background)
    image.fill(background if background.isValid() else QColor("#10141c"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    cache = cache if cache is not None else PixmapCache()
    draw_backdrop(painter, project, level, QRectF(0, 0, 1, 1), 1.0, cache,
                  origin=(-x, -y))
    painter.translate(-x, -y)
    for piece in level.paint_order():
        layer = level.layer_by_id(piece.layer)
        layer_opacity = layer.opacity if layer else 1.0
        pixmap = piece_pixmap(piece, project, cache, source_target_size(piece, 1.0))
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
                         include_node_borders=None, include_zones=None,
                         include_centerlines=False, include_guides=False,
                         include_coordinates=False, opaque=False):
    img = render_level(project, level, include_grid, scale, transparent,
                       grid_color, grid_opacity, include_node_borders,
                       include_zones, include_centerlines, include_guides,
                       include_coordinates, opaque=opaque)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path, "PNG")
    return path


def export_all_levels(project, out_dir, include_grid=True, scale=1.0,
                      name_prefix="map", transparent=False, grid_color=None,
                      grid_opacity=None, include_node_borders=None,
                      include_zones=None, include_centerlines=False,
                      include_guides=False, include_coordinates=False,
                      opaque=False):
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for i, lvl in enumerate(project.levels, 1):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in lvl.name)
        path = os.path.join(out_dir, f"{name_prefix}_{i:02d}_{safe}.png")
        export_level_to_file(project, lvl, path, include_grid, scale, transparent,
                             grid_color, grid_opacity, include_node_borders,
                             include_zones, include_centerlines, include_guides,
                             include_coordinates, opaque=opaque)
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
               include_node_borders=None, include_zones=None,
               include_centerlines=False, include_guides=False,
               include_coordinates=False, opaque=False):
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
    extras = (include_centerlines, include_guides, include_coordinates)
    first = render_level(project, selected_levels[0], include_grid, scale,
                         transparent, grid_color, grid_opacity,
                         include_node_borders, include_zones, *extras,
                         opaque=opaque)
    writer.setPageSize(QPageSize(
        QSizeF(first.width() / 96.0, first.height() / 96.0),
        QPageSize.Unit.Inch))
    painter = QPainter(writer)
    painter.drawImage(0, 0, first)
    for lvl in selected_levels[1:]:
        writer.newPage()
        img = render_level(project, lvl, include_grid, scale, transparent,
                           grid_color, grid_opacity, include_node_borders,
                           include_zones, *extras, opaque=opaque)
        painter.drawImage(0, 0, img)
    painter.end()
    return out_path


def save_thumbnail(project, bmap_path, size=256, thumb_path=None):
    """Write a small preview of the first level for the recent-maps list.

    Pass ``thumb_path`` (the app keeps thumbnails in its own data folder).
    The old default, ``<map>.png`` beside the map, could overwrite an export
    that had the same name as the map, so it is only used when no path is
    given."""
    ensure_app()
    scale = min(1.0, (size * 2.0) / max(1, project.canvas_w, project.canvas_h))
    img = render_level(project, project.levels[0], False, scale)
    img = img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    thumb = thumb_path or (os.path.splitext(bmap_path)[0] + ".png")
    os.makedirs(os.path.dirname(os.path.abspath(thumb)), exist_ok=True)
    img.save(thumb, "PNG")
    return thumb
