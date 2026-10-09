"""Interactive map canvas — snapping grid, multi-select, layers, tint/text,
smart guides, cell highlight, group rotate, and a quick toolbar."""
from __future__ import annotations

import math
import os
from typing import Optional

from PyQt6.QtCore import (Qt, QEvent, QLineF, QObject, QPoint, QPointF, QRectF, QRunnable,
                          QSize, QThreadPool, QTimer, pyqtSignal)
from PyQt6.QtGui import (
    QImage, QImageReader, QPainter, QPixmap, QColor, QPen, QBrush, QCursor, QFont,
    QFontMetricsF, QPolygonF, QTransform,
)
from PyQt6.QtWidgets import QWidget, QFrame, QPushButton, QHBoxLayout, QToolTip

from core.guides import (column_label, describe_position, grid_counts,
                         label_step, nearest, row_label)
from core.project import Guide, Piece, Project, Level, ZoneRegion, snap_value
from core.transforms import (grid_offsets, mirror_piece_data, on_mirror_line,
                             remap_groups)
from core import exporter
from core.render import draw_node_border, draw_piece, draw_zone_borders
from ui.canvas_tools import CloneToolMixin, CutoutToolMixin, SelectionToolsMixin
from ui.theme import theme_colors

HANDLE_DIST = 26
HANDLE_R = 7
RESIZE_HIT = 7
MIN_VISUAL_SIZE = 4.0
# resize handle name -> (x, y) direction in the node's local frame
RESIZE_HANDLES = {
    "nw": (-1, -1), "n": (0, -1), "ne": (1, -1), "e": (1, 0),
    "se": (1, 1), "s": (0, 1), "sw": (-1, 1), "w": (-1, 0),
}
GUIDE_DIST = 12  # screen px threshold for smart guides (and placed guides)
GUIDE_HIT = 5    # screen px: how close the pointer must be to grab a guide
GUIDE_SNAP = 8   # screen px: a dragged guide snaps to grid lines / node edges
RAIL = 8         # guide-rail thickness along the canvas edges (screen px)
RAIL_LABELED = 20  # rail thickness when grid coordinates are shown


class _BoundsSignals(QObject):
    done = pyqtSignal(str, object)       # piece id, {threshold: bounds}


class _BoundsTask(QRunnable):
    """Find the visible-pixel bounds of one image off the GUI thread."""

    def __init__(self, piece_id: str, path: str, embedded: str,
                 signals: _BoundsSignals):
        super().__init__()
        self.piece_id, self.path, self.embedded = piece_id, path, embedded
        self.signals = signals

    def run(self):
        from ui.image_utils import all_bounds_for_image, bounds_table_for_file
        table = None
        try:
            if self.embedded:
                from PyQt6.QtGui import QImage
                from core.project import decode_embed
                table = all_bounds_for_image(
                    QImage.fromData(decode_embed(self.embedded)))
            elif self.path:
                table = bounds_table_for_file(self.path)
        except Exception:
            table = None
        try:
            self.signals.done.emit(self.piece_id, table)
        except RuntimeError:
            pass


class _DecodeSignals(QObject):
    done = pyqtSignal(object, object, QImage)


class _DecodeTask(QRunnable):
    """Decode one map image at one display size off the GUI thread."""

    def __init__(self, key, path: str, size, signals: _DecodeSignals):
        super().__init__()
        self.key, self.path, self.size, self.signals = key, path, size, signals

    def run(self):
        try:
            image = exporter.file_image(self.path, self.size)
        except Exception:
            image = QImage()
        try:
            self.signals.done.emit(self.key, self.size, image)
        except RuntimeError:
            pass


class CanvasView(SelectionToolsMixin, CutoutToolMixin, CloneToolMixin, QWidget):
    selectionChanged = pyqtSignal(object)   # list[Piece]
    zoneSelectionChanged = pyqtSignal(object)  # ZoneRegion or None
    dirty = pyqtSignal()
    viewChanged = pyqtSignal()  # zoom, pan, or viewport size changed
    cursorMoved = pyqtSignal(float, float)
    historyPush = pyqtSignal(str, bool)   # label, coalesce
    colorPickStateChanged = pyqtSignal(bool)
    contextMenuRequested = pyqtSignal(QPoint, object)   # global pos, hit Piece|None
    layerSoloChanged = pyqtSignal(object)
    historyDiscardLast = pyqtSignal()
    statusMessage = pyqtSignal(str)
    freeTransformChanged = pyqtSignal(bool)
    guideMenuRequested = pyqtSignal(QPoint, object)  # global pos, {"guide": id} | {"rail": side}
    guideEditRequested = pyqtSignal(str)             # guide id (double-click)
    guideSettingsChanged = pyqtSignal()              # show/snap/lock flags changed here
    railsChanged = pyqtSignal()                      # rail thickness / visibility changed
    stampToolChanged = pyqtSignal(object)            # armed hotbar slot index, or None
    cutoutChanged = pyqtSignal()                     # cut-out tool / area / targets changed
    cutoutMenuRequested = pyqtSignal(QPoint)         # right-click inside the cut-out area

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)

        self.project: Optional[Project] = None
        self.level_index: int = 0
        self.library = None
        self.theme_mode = "dark"
        self.theme_accent = "#69b7f5"

        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        # Entry cap is high on purpose: a busy map shows hundreds of distinct
        # images, and thrashing a tiny cache re-decodes huge PNGs every paint.
        self._cache: dict = exporter.PixmapCache(
            max_bytes=768 * 1024 * 1024, max_entries=4096)
        self._variants: dict = {}     # source key -> {(w, h): True} sizes decoded
        self._load_queue: dict = {}
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self._process_loads)

        self.selection: set[str] = set()
        self.selected_zone_id: str | None = None
        self.zone_tool: str | None = None  # rectangle | polygon
        self._zone_drag_start: tuple[float, float] | None = None
        self._zone_preview: tuple[float, float, float, float] | None = None
        self._zone_polygon_points: list[tuple[float, float]] = []
        self._zone_edit_drag: dict | None = None
        self.patch_tool = False
        self._patch_drag_start: tuple[float, float] | None = None
        self._patch_preview: tuple[float, float, float, float] | None = None
        self._drag = None
        self._hover_handle = False
        self._marquee: Optional[QRectF] = None
        self._cursor_world = (-1.0, -1.0)
        self._smart_guides: list[tuple[str, float]] = []  # ("v", x) or ("h", y)
        # placed guides: rails along the view edges create them
        self.rails_visible = True
        self._guide_drag: dict | None = None
        self._guide_hover: str | None = None   # id of the guide under the pointer
        self._rail_hover: str | None = None    # "left" | "right" | "top" | "bottom"
        self._guide_flash: set[str] = set()    # guides a moving node is snapped to
        self._remove_cursor: QCursor | None = None
        self.quick_enabled = False   # floating node buttons; right-click covers them
        self.auto_tighten = True     # trim transparent margins when nodes are placed
        self._suppress_history = False
        self._decode_signals = _DecodeSignals()
        self._decode_signals.done.connect(self._decoded)
        self._decode_pool = QThreadPool(self)
        self._decode_pool.setMaxThreadCount(2)
        self._decoding: set = set()
        self._placeholder_pm = None
        self._bounds_signals = _BoundsSignals()
        self._bounds_signals.done.connect(self._bounds_ready)
        self._bounds_pool = QThreadPool(self)
        self._bounds_pool.setMaxThreadCount(1)
        self._bounds_wait: dict[str, tuple] = {}   # piece id -> (options, only_if_untouched)
        self.tighten_options = {"threshold": 16, "padding": 0.0,
                                "sides": (True, True, True, True)}
        self.allow_overlap = False   # align/distribute may overlap nodes only when on
        self.free_transform = False
        self._ft_backup: dict | None = None
        self._ft_pushed = False
        self.solo_layer_id: str | None = None   # view-only isolate; never saved/exported

        self.ref_enabled = False
        self.ref_offset = -1
        self.ref_opacity = 0.28

        self._group_rotate = False
        self._clipboard: list[dict] = []
        self._drop_target = None   # (w, h) of incoming asset during a drag
        self._color_pick_callback = None

        # composition tools that work directly on saved nodes
        self.stamp_tool = False
        self._stamp_template = None
        self._stamp_drag_last = None
        self.stamp_slot = None          # hotbar slot that armed the stamp tool
        self._stamp_tighten = False     # trim stamped library assets like drops
        self._stamp_hover = None        # world point of the stamp preview
        self._stamp_edge = False        # door mode: copies sit on grid lines
        self._stamp_last_spot = None    # door mode: last spot placed while dragging
        self.crop_tool = False
        self._crop_target_id = None
        self._crop_drag = None
        self._crop_preview = None
        self.ruler_tool = False
        self._ruler_drag = None
        self._ruler_result = None
        self.scale_tool = False
        self._scale_drag = None
        self.connector_tool = False
        self._connector_drag = None
        self.lasso_tool = False
        self._lasso_points = []
        self._lasso_add = False
        self.copy_style_mode = False
        self._style_template = None
        self._init_cutout_state()
        self._init_clone_state()

        # quick toolbar (J3)
        self.quick = QFrame(self)
        self.quick.setObjectName("CanvasQuickToolbar")
        self.quick.setToolTip(
            "Quick actions for the selected node(s). Hover over each button for details.")
        ql = QHBoxLayout(self.quick)
        ql.setContentsMargins(2, 2, 2, 2)
        ql.setSpacing(2)
        self._qb = {}
        quick_actions = [
            ("rotL", "⟲", "Rotate selected node(s) 90° left"),
            ("rotR", "⟳", "Rotate selected node(s) 90° right"),
            ("fh", "H", "Flip selected node(s) horizontally"),
            ("fv", "V", "Flip selected node(s) vertically"),
            ("up", "▲", "Bring selected node(s) forward"),
            ("down", "▼", "Send selected node(s) backward"),
            ("lock", "L", "Toggle the lock state of selected node(s)"),
            ("copy", "C", "Copy selected node(s)"),
            ("dup", "+", "Duplicate selected node(s)"),
            ("del", "×", "Delete selected node(s)"),
        ]
        for name, lbl, tooltip in quick_actions:
            b = QPushButton(lbl)
            b.setObjectName("CanvasQuickButton")
            b.setFixedSize(34, 32)
            b.setToolTip(tooltip)
            b.setAccessibleName(tooltip)
            b.clicked.connect(lambda _, n=name: self._quick(n))
            ql.addWidget(b)
            self._qb[name] = b
        self.quick.hide()

    # ------------------------------------------------------------------
    def set_theme(self, mode: str, accent: str):
        self.theme_mode = mode
        colors = theme_colors(mode, accent)
        self.theme_colors = colors
        self.theme_accent = colors["accent"]
        self.quick.setStyleSheet(
            f"background: {colors['panel']}; border:1px solid {colors['border_hot']};")
        self.update()

    def _reset_tool_state(self):
        was_stamping = self.stamp_tool
        was_cutting = self._reset_cutout_state()
        self._reset_clone_state()
        self.stamp_slot = None
        self._stamp_tighten = False
        self._stamp_hover = None
        self._stamp_edge = False
        self._stamp_last_spot = None
        self.zone_tool = None
        self._zone_drag_start = None
        self._zone_preview = None
        self._zone_polygon_points = []
        self.patch_tool = False
        self._patch_drag_start = None
        self._patch_preview = None
        self.stamp_tool = False
        self._stamp_template = None
        self._stamp_drag_last = None
        self.crop_tool = False
        self._crop_target_id = None
        self._crop_drag = None
        self._crop_preview = None
        self.ruler_tool = False
        self._ruler_drag = None
        self._ruler_result = None
        self.scale_tool = False
        self._scale_drag = None
        self.connector_tool = False
        self._connector_drag = None
        self.lasso_tool = False
        self._lasso_points = []
        self.copy_style_mode = False
        self._style_template = None
        if was_stamping:
            self.stampToolChanged.emit(None)
        if was_cutting:
            self.cutoutChanged.emit()

    def set_project(self, project: Project, library=None):
        self.project = project
        self.library = library
        self._clear_pixmaps()
        self.selection.clear()
        self.selected_zone_id = None
        self._reset_tool_state()
        self._reset_guide_state()
        self._zone_edit_drag = None
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self._update_quick()
        self.fit_to_view()
        self.selectionChanged.emit([])
        self.zoneSelectionChanged.emit(None)
        self.update()

    def set_level(self, index: int):
        if self.project and 0 <= index < len(self.project.levels):
            self.level_index = index
            self._reset_tool_state()
            self._reset_guide_state()
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self.selection.clear()
            self.selected_zone_id = None
            self._update_quick()
            self.selectionChanged.emit([])
            self.zoneSelectionChanged.emit(None)
            self.update()

    @property
    def level(self) -> Optional[Level]:
        if self.project and 0 <= self.level_index < len(self.project.levels):
            return self.project.levels[self.level_index]
        return None

    @property
    def selected_zone(self) -> ZoneRegion | None:
        if self.level and self.selected_zone_id:
            return next((zone for zone in self.level.zones
                         if zone.id == self.selected_zone_id), None)
        return None

    def set_zone_tool(self, mode: str | None):
        if mode not in {None, "rectangle", "polygon"}:
            return
        self._reset_tool_state()
        self.zone_tool = mode
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if mode
                               else Qt.CursorShape.ArrowCursor))
        self.update()

    def set_patch_tool(self, active: bool = True):
        self._reset_tool_state()
        self.patch_tool = bool(active)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if active
                               else Qt.CursorShape.ArrowCursor))
        self.update()

    def set_stamp_tool(self, active: bool = True) -> bool:
        selected = self.selected_pieces()
        if active and len(selected) != 1:
            return False
        self._reset_tool_state()
        self.stamp_tool = bool(active)
        self._stamp_template = selected[0].to_dict() if active else None
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if active
                               else Qt.CursorShape.ArrowCursor))
        self.update()
        return True

    def set_crop_tool(self, active: bool = True) -> bool:
        selected = self.selected_pieces()
        if active and (len(selected) != 1 or selected[0].is_text
                       or selected[0].is_patch or selected[0].is_scale_bar
                       or selected[0].is_connector):
            return False
        self._reset_tool_state()
        self.crop_tool = bool(active)
        self._crop_target_id = selected[0].id if active else None
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if active
                               else Qt.CursorShape.ArrowCursor))
        self.update()
        return True

    def set_ruler_tool(self, active: bool = True):
        self._reset_tool_state()
        self.ruler_tool = bool(active)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if active
                               else Qt.CursorShape.ArrowCursor))
        self.update()

    def set_scale_tool(self, active: bool = True):
        self._reset_tool_state()
        self.scale_tool = bool(active)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if active
                               else Qt.CursorShape.ArrowCursor))
        self.update()

    def set_connector_tool(self, active: bool = True):
        self._reset_tool_state()
        self.connector_tool = bool(active)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if active
                               else Qt.CursorShape.ArrowCursor))
        self.update()

    def set_lasso_tool(self, active: bool = True):
        self._reset_tool_state()
        self.lasso_tool = bool(active)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if active
                               else Qt.CursorShape.ArrowCursor))
        self.update()

    def set_copy_style_mode(self, active: bool = True) -> bool:
        selected = self.selected_pieces()
        if active and not selected:
            return False
        self._reset_tool_state()
        self.copy_style_mode = bool(active)
        self._style_template = selected[0].to_dict() if active else None
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor if active
                               else Qt.CursorShape.ArrowCursor))
        self.update()
        return True

    def cancel_extra_tool(self) -> bool:
        active = (self.stamp_tool or self.crop_tool or self.ruler_tool
                  or self.scale_tool or self.connector_tool or self.lasso_tool
                  or self.copy_style_mode or self.cutout_tool or self.clone_tool)
        if active:
            self._reset_tool_state()
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self.update()
        return active

    def cancel_patch_tool(self):
        if not self.patch_tool:
            return False
        self.set_patch_tool(False)
        return True

    def cancel_zone_tool(self):
        if self.zone_tool is None and not self._zone_polygon_points:
            return False
        self.set_zone_tool(None)
        return True

    def finish_zone_polygon(self):
        points = list(self._zone_polygon_points)
        if len(points) > 1 and math.dist(points[0], points[-1]) < 1.0:
            points.pop()
        self._zone_polygon_points = []
        if len(points) >= 3 and self.level:
            self._create_zone(points)
        else:
            self.set_zone_tool(None)
        self.update()

    def select_zone(self, zone_id: str | None):
        zone = next((item for item in self.level.zones
                     if item.id == zone_id), None) if self.level else None
        selected_id = zone.id if zone else None
        changed = selected_id != self.selected_zone_id
        self.selected_zone_id = selected_id
        if selected_id is not None:
            self.selection.clear()
            self._update_quick()
            self.selectionChanged.emit([])
        if changed:
            self.zoneSelectionChanged.emit(zone)
        self.update()

    def delete_selected_zone(self):
        zone = self.selected_zone
        if not zone or not self.level:
            return False
        self.push_history("Delete zone")
        self.level.zones.remove(zone)
        self.select_zone(None)
        self.dirty.emit()
        return True

    def _create_zone(self, points):
        if not self.level or len(points) < 3:
            return None
        self.push_history("Add gameplay zone")
        zone = ZoneRegion(name=f"Zone {len(self.level.zones) + 1}",
                          points=points)
        self.level.zones.append(zone)
        self.zone_tool = None
        self._zone_preview = None
        self._zone_drag_start = None
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.select_zone(zone.id)
        self.dirty.emit()
        return zone

    def _create_connector(self, start, end):
        if not self.level:
            return None
        x0, y0 = start
        x1, y1 = end
        width, height = max(2.0, abs(x1 - x0)), max(2.0, abs(y1 - y0))
        center_x, center_y = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        self.push_history("Add connection marker")
        connector = Piece(
            name="Connection marker", is_connector=True,
            x=center_x - width / 2.0, y=center_y - height / 2.0,
            w=width, h=height, snap=False, z=self.level.next_z(),
            layer=self.level.current_layer,
            flip_h=x1 < x0, flip_v=y1 < y0)
        self.level.add(connector)
        self.select([connector])
        self.dirty.emit()
        self.update()
        return connector

    def _create_scale_bar(self, start, end):
        if not self.level:
            return None
        length = max(30.0, math.dist(start, end))
        height = 44.0
        center_x = (start[0] + end[0]) / 2.0
        center_y = (start[1] + end[1]) / 2.0
        distance = length / max(1, self.project.cell_size) * max(
            1, self.project.feet_per_square)
        self.push_history("Add static scale bar")
        scale_bar = Piece(
            name="Static scale bar", is_scale_bar=True,
            x=center_x - length / 2.0, y=center_y - height / 2.0,
            w=length, h=height, snap=False, z=self.level.next_z(),
            layer=self.level.current_layer,
            scale_distance=distance, scale_units="ft")
        self.level.add(scale_bar)
        self.select([scale_bar])
        self.dirty.emit()
        self.update()
        return scale_bar

    def _zone_vertex_at_screen(self, zone, sx, sy, threshold=10):
        for index, point in enumerate(zone.points):
            px, py = self.world_to_screen(*point)
            if math.hypot(px - sx, py - sy) <= threshold:
                return index
        return None

    @staticmethod
    def _point_segment_distance(px, py, ax, ay, bx, by):
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy)
                         / (dx * dx + dy * dy)))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    def _zone_near_edge(self, sx, sy, threshold=9):
        if not self.level:
            return None
        for zone in reversed(self.level.zones):
            for index, point in enumerate(zone.points):
                other = zone.points[(index + 1) % len(zone.points)]
                a = self.world_to_screen(*point)
                b = self.world_to_screen(*other)
                if self._point_segment_distance(sx, sy, *a, *b) <= threshold:
                    return zone
        return None

    def set_ref(self, enabled, offset, opacity):
        self.ref_enabled = enabled
        self.ref_offset = offset
        self.ref_opacity = opacity
        self.update()

    def push_history(self, label: str, coalesce: bool = False):
        """Snapshot before an edit. ``coalesce`` merges rapid repeats of the
        same label (slider drags, held keys) into one undo step."""
        if self._suppress_history:
            return
        if self.free_transform and label in ("Resize", "Rotate", "Move"):
            # a whole free-transform session is a single undo step
            if self._ft_pushed:
                return
            self._ft_pushed = True
            label, coalesce = "Free transform", False
        self.historyPush.emit(label, coalesce)

    # -- free transform mode (Ctrl+T) -----------------------------------
    _FT_FIELDS = ("x", "y", "w", "h", "scale", "rotation", "text_auto_size")

    def begin_free_transform(self) -> bool:
        sel = self.selected_pieces()
        if len(sel) != 1 or sel[0].locked or self.free_transform:
            return False
        p = sel[0]
        self._ft_backup = {"id": p.id, **{k: getattr(p, k) for k in self._FT_FIELDS}}
        self._ft_pushed = False
        self.free_transform = True
        self.freeTransformChanged.emit(True)
        self.update()
        return True

    def end_free_transform(self, commit: bool = True) -> bool:
        if not self.free_transform:
            return False
        self.free_transform = False
        backup, self._ft_backup = self._ft_backup, None
        pushed, self._ft_pushed = self._ft_pushed, False
        if not commit and backup:
            piece = next((q for q in (self.level.pieces if self.level else [])
                          if q.id == backup["id"]), None)
            if piece is not None:
                for k in self._FT_FIELDS:
                    setattr(piece, k, backup[k])
            if pushed:
                self.historyDiscardLast.emit()
        self.freeTransformChanged.emit(False)
        self.selectionChanged.emit(self.selected_pieces())
        self.dirty.emit()
        self.update()
        return True

    def begin_color_pick(self, callback) -> bool:
        """Arm a one-shot eyedropper. The callback receives QColor or None."""
        if not self.project or not self.level or not callable(callback):
            return False
        self.cancel_color_pick()
        self._color_pick_callback = callback
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.colorPickStateChanged.emit(True)
        return True

    def _finish_color_pick(self, color=None) -> bool:
        callback = self._color_pick_callback
        if callback is None:
            return False
        self._color_pick_callback = None
        self.unsetCursor()
        self.colorPickStateChanged.emit(False)
        callback(color)
        return True

    def cancel_color_pick(self) -> bool:
        """Cancel eyedropper mode, returning whether one was active."""
        return self._finish_color_pick(None)

    def _sample_color_at_screen(self, sx: float, sy: float) -> QColor:
        wx, wy = self.screen_to_world(sx, sy)
        return exporter.sample_level_color(
            self.project, self.level, wx, wy, self._cache)

    def set_stamp_template(self, template: dict, slot=None,
                           tighten: bool = False, edge: bool = False) -> bool:
        """Arm the stamp tool with node data (used by the hotbar keys 1-9).
        ``tighten`` trims each copy to its visible pixels, like a library drop;
        ``edge`` (door mode) puts each copy on the nearest grid line."""
        if not self.project or not self.level or not isinstance(template, dict):
            return False
        self._reset_tool_state()
        self.stamp_tool = True
        self._stamp_template = (self._tightened_template(dict(template)) if tighten
                                else dict(template))
        self.stamp_slot = slot
        self._stamp_tighten = bool(tighten)
        self._stamp_edge = bool(edge)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        if self._cursor_world[0] >= 0:
            self._stamp_hover = self._cursor_world
        self.stampToolChanged.emit(slot)
        self.update()
        return True

    def _tightened_template(self, template: dict) -> dict:
        """Trim an asset stamp to its visible pixels up front when the image's
        bounds are already known, so the preview matches what gets placed.
        (Otherwise each placed copy is trimmed afterwards, like a drop.)"""
        path = template.get("asset_path")
        if (not path or template.get("is_overlay") or not self.auto_tighten
                or list(template.get("crop_rect", [0, 0, 1, 1])) != [0.0, 0.0, 1.0, 1.0]):
            return template
        from ui.image_utils import bounds_table_for_file, has_cached_bounds, nearest_threshold
        resolved = self.project.resolve_asset(path)
        if not has_cached_bounds(resolved):
            return template
        bounds = (bounds_table_for_file(resolved) or {}).get(
            nearest_threshold(self.tighten_options.get("threshold", 16)))
        if bounds is None:
            return template
        scale = float(template.get("scale", 1.0) or 1.0)
        kx = float(template.get("w", 0.0)) * scale
        ky = float(template.get("h", 0.0)) * scale
        if kx <= 0 or ky <= 0:
            return template
        pad = max(0.0, float(self.tighten_options.get("padding", 0.0)))
        sides = tuple(self.tighten_options.get("sides", (True, True, True, True)))
        target = [max(0.0, bounds[0] - pad / kx) if sides[0] else 0.0,
                  max(0.0, bounds[1] - pad / ky) if sides[1] else 0.0,
                  min(1.0, bounds[2] + pad / kx) if sides[2] else 1.0,
                  min(1.0, bounds[3] + pad / ky) if sides[3] else 1.0]
        if target[2] - target[0] < 0.02 or target[3] - target[1] < 0.02:
            return template
        out = dict(template)
        out["crop_rect"] = [float(v) for v in target]
        out["w"] = (target[2] - target[0]) * kx / scale
        out["h"] = (target[3] - target[1]) * ky / scale
        return out

    def asset_template(self, store_rel_path: str) -> Optional[dict]:
        """Node data for a library asset, sized and flagged like a library drop
        (None when the image can't be found)."""
        if not self.project or not store_rel_path:
            return None
        if not os.path.isfile(self.project.resolve_asset(store_rel_path)):
            return None
        asset = self.library.get(store_rel_path) if self.library else None
        w, h = self.asset_world_size(store_rel_path, asset)
        is_overlay = bool(asset and asset.is_overlay)
        name = asset.name if asset else store_rel_path.split("/")[-1]
        data = Piece(asset_path=store_rel_path, name=name, w=w, h=h,
                     is_overlay=is_overlay, snap=not is_overlay).to_dict()
        for key in ("id", "x", "y", "z", "layer", "group_id", "locked"):
            data.pop(key, None)
        return data

    def _stamp_piece_at(self, wx: float, wy: float) -> Optional[Piece]:
        """The node a stamp click at (wx, wy) would place (not yet added)."""
        if not self.level or not self._stamp_template:
            return None
        import copy
        from core.project import uuid
        data = copy.deepcopy(self._stamp_template)
        data["id"] = uuid.uuid4().hex
        data["x"] = wx - float(data.get("w", 0)) * float(data.get("scale", 1)) / 2
        data["y"] = wy - float(data.get("h", 0)) * float(data.get("scale", 1)) / 2
        data["layer"] = self.level.current_layer
        data["z"] = self.level.next_z()
        data["locked"] = False
        data["group_id"] = ""
        if self._stamp_edge:
            cx, cy, angle = self._edge_stamp_spot(wx, wy, data)
            data["rotation"] = angle
            data["x"] = cx - float(data.get("w", 0)) * float(data.get("scale", 1)) / 2
            data["y"] = cy - float(data.get("h", 0)) * float(data.get("scale", 1)) / 2
            return Piece.from_dict(data)
        piece = Piece.from_dict(data)
        if piece.snap:
            piece.x = snap_value(piece.x, self.project.cell_size)
            piece.y = snap_value(piece.y, self.project.cell_size)
        return piece

    def _place_stamp(self, wx: float, wy: float):
        piece = self._stamp_piece_at(wx, wy)
        if piece is None:
            return None
        if self._stamp_edge:
            spot = (round(piece.center[0], 2), round(piece.center[1], 2),
                    round(piece.rotation % 360.0, 1))
            if spot == self._stamp_last_spot or self._stamp_spot_taken(piece):
                return None
            self._stamp_last_spot = spot
        self.push_history("Stamp node")
        self.level.add(piece)
        if self._stamp_tighten and self.auto_tighten and not piece.is_overlay:
            self.tighten_piece_async(piece, only_if_untouched=True)
        self.dirty.emit()
        self.update()
        return piece

    def _draw_stamp_preview(self, painter):
        """Faint copy of the armed stamp where a click would place it."""
        if not (self.stamp_tool and self._stamp_template and self._stamp_hover):
            return
        if self._stamp_drag_last is not None:
            return
        ghost = self._stamp_piece_at(*self._stamp_hover)
        if ghost is None:
            return
        self._draw_piece(painter, ghost, 0.5, mark_missing=False)
        accent = QColor(self.theme_accent)
        pen = QPen(accent)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidthF(1.0)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        corners = self._piece_corners_screen(ghost)
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in corners]))
        painter.restore()

    def _piece_corners_screen(self, p: Piece) -> list[tuple[float, float]]:
        cx, cy = p.center
        half_w, half_h = p.vis_w / 2.0, p.vis_h / 2.0
        angle = math.radians(p.rotation)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        out = []
        for lx, ly in ((-half_w, -half_h), (half_w, -half_h),
                       (half_w, half_h), (-half_w, half_h)):
            wx = cx + lx * cos_a - ly * sin_a
            wy = cy + lx * sin_a + ly * cos_a
            out.append(self.world_to_screen(wx, wy))
        return out

    # -- mirror copies and grid copies -----------------------------------
    def mirror_lines(self) -> list[tuple[str, float, str]]:
        """Lines a selection can be mirrored across: (axis, world pos, label).
        The map's center lines come first, then this level's guides."""
        if not self.project:
            return []
        lines = [("v", self.project.canvas_w / 2.0, "Vertical center line of the map"),
                 ("h", self.project.canvas_h / 2.0, "Horizontal center line of the map")]
        if self.level:
            for guide in sorted(self.level.guides, key=lambda g: (g.axis, g.pos)):
                where = describe_position(guide.axis, guide.pos, self.project.cell_size,
                                          self.project.feet_per_square)
                kind = "Vertical" if guide.axis == "v" else "Horizontal"
                lines.append((guide.axis, guide.pos, f"{kind} guide at {where}"))
        return lines

    def nearest_mirror_line(self, axis: str) -> Optional[tuple[str, float, str]]:
        """The guide on ``axis`` nearest the selection's center, else the map's
        center line on that axis."""
        lines = [line for line in self.mirror_lines() if line[0] == axis]
        if not lines:
            return None
        bounds = self.selection_bounds()
        guides = lines[1:]
        if not guides or bounds is None:
            return lines[0]
        middle = ((bounds[0] + bounds[2]) / 2.0 if axis == "v"
                  else (bounds[1] + bounds[3]) / 2.0)
        return min(guides, key=lambda line: abs(line[1] - middle))

    def mirror_selection(self, axis: str, pos: float) -> list[Piece]:
        """Place mirrored copies of the selection on the other side of a line.
        Nodes centered on the line are left alone (their mirror image would
        land exactly on top of them). The copies become the selection."""
        if not self.level or axis not in ("v", "h"):
            return []
        selected = sorted(self.selected_pieces(), key=lambda piece: piece.z)
        if not selected:
            self.statusMessage.emit("Select the nodes to mirror first.")
            return []
        copies, skipped = [], 0
        for piece in selected:
            data = piece.to_dict()
            if on_mirror_line(data, axis, pos):
                skipped += 1
                continue
            copies.append(mirror_piece_data(data, axis, pos))
        if not copies:
            self.statusMessage.emit(
                "The selection is centered on that line, so a mirror copy would "
                "land on top of it.")
            return []
        remap_groups(copies)
        self.push_history("Mirror copy")
        created = []
        for data in copies:
            piece = Piece.from_dict(data)
            self.level.add(piece)
            created.append(piece)
        self.select(created)
        message = f"Mirrored {len(created)} node(s)."
        if skipped:
            message += f" {skipped} centered on the line stayed as they were."
        self.statusMessage.emit(message)
        self.dirty.emit()
        self.update()
        return created

    def selection_bounds(self) -> Optional[tuple[float, float, float, float]]:
        selected = self.selected_pieces()
        if not selected:
            return None
        boxes = [self._aabb(piece) for piece in selected]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def duplicate_as_grid(self, rows: int, cols: int, gap_x: float = 0.0,
                          gap_y: float = 0.0) -> list[Piece]:
        """Repeat the selection (as one block) in a rows x cols grid, ``gap_x``
        / ``gap_y`` world px apart. Originals and copies end up selected."""
        if not self.level:
            return []
        selected = sorted(self.selected_pieces(), key=lambda piece: piece.z)
        bounds = self.selection_bounds()
        if not selected or bounds is None:
            self.statusMessage.emit("Select the nodes to repeat first.")
            return []
        step_x = max(1.0, bounds[2] - bounds[0] + max(0.0, float(gap_x)))
        step_y = max(1.0, bounds[3] - bounds[1] + max(0.0, float(gap_y)))
        offsets = grid_offsets(rows, cols, step_x, step_y)
        if not offsets:
            return []
        from core.project import uuid
        self.push_history("Duplicate as grid")
        created = []
        for dx, dy in offsets:
            batch = []
            for piece in selected:
                data = piece.to_dict()
                data["id"] = uuid.uuid4().hex
                data["x"] = piece.x + dx
                data["y"] = piece.y + dy
                batch.append(data)
            remap_groups(batch)
            for data in batch:
                copy_piece = Piece.from_dict(data)
                self.level.add(copy_piece)
                created.append(copy_piece)
        self.select(selected + created)
        self.statusMessage.emit(
            f"Made {len(offsets)} cop{'y' if len(offsets) == 1 else 'ies'} "
            f"in a {max(1, int(rows))} x {max(1, int(cols))} grid.")
        self.dirty.emit()
        self.update()
        return created

    def _with_groups(self, pieces) -> list[Piece]:
        """Grouped ("glued") nodes are picked together: add the other unlocked,
        visible members of every group in ``pieces``."""
        pieces = list(pieces)
        if not self.level:
            return pieces
        groups = {piece.group_id for piece in pieces if piece.group_id}
        if not groups:
            return pieces
        blocked = {layer.id for layer in self.level.layers
                   if layer.locked or not layer.visible}
        chosen = {piece.id: piece for piece in pieces}
        for piece in self.level.pieces:
            if (piece.group_id in groups and piece.id not in chosen
                    and not piece.locked and piece.layer not in blocked):
                chosen[piece.id] = piece
        return list(chosen.values())

    def _piece_local_fraction(self, piece: Piece, wx: float, wy: float):
        cx, cy = piece.center
        dx, dy = wx - cx, wy - cy
        angle = -math.radians(piece.rotation)
        local_x = (dx * math.cos(angle) - dy * math.sin(angle)) / max(piece.scale, 1e-6)
        local_y = (dx * math.sin(angle) + dy * math.cos(angle)) / max(piece.scale, 1e-6)
        return (local_x / max(piece.w, 1e-6) + 0.5,
                local_y / max(piece.h, 1e-6) + 0.5)

    @staticmethod
    def _clamp_crop_fraction(value):
        return max(0.0, min(1.0, float(value)))

    def _apply_crop(self, piece: Piece, fractions):
        left, top, right, bottom = fractions
        left, right = sorted((self._clamp_crop_fraction(left),
                              self._clamp_crop_fraction(right)))
        top, bottom = sorted((self._clamp_crop_fraction(top),
                              self._clamp_crop_fraction(bottom)))
        if right - left < 0.02 or bottom - top < 0.02:
            return False
        old_w, old_h, old_scale = piece.w, piece.h, piece.scale
        old_crop = piece.crop_rect
        crop_w, crop_h = old_crop[2] - old_crop[0], old_crop[3] - old_crop[1]
        src_left = (1.0 - right if piece.flip_h else left)
        src_right = (1.0 - left if piece.flip_h else right)
        src_top = (1.0 - bottom if piece.flip_v else top)
        src_bottom = (1.0 - top if piece.flip_v else bottom)
        new_crop = [old_crop[0] + src_left * crop_w,
                    old_crop[1] + src_top * crop_h,
                    old_crop[0] + src_right * crop_w,
                    old_crop[1] + src_bottom * crop_h]
        center_x, center_y = piece.center
        shift_x = ((left + right) / 2.0 - 0.5) * old_w * old_scale
        shift_y = ((top + bottom) / 2.0 - 0.5) * old_h * old_scale
        angle = math.radians(piece.rotation)
        new_center_x = center_x + shift_x * math.cos(angle) - shift_y * math.sin(angle)
        new_center_y = center_y + shift_x * math.sin(angle) + shift_y * math.cos(angle)
        self.push_history("Crop image node")
        piece.w = old_w * (right - left)
        piece.h = old_h * (bottom - top)
        piece.crop_rect = new_crop
        piece.x = new_center_x - piece.w * piece.scale / 2.0
        piece.y = new_center_y - piece.h * piece.scale / 2.0
        self.selectionChanged.emit(self.selected_pieces())
        self.dirty.emit()
        self.update()
        return True

    def reset_selected_crop(self) -> bool:
        selected = self.selected_pieces()
        if len(selected) != 1:
            return False
        piece = selected[0]
        if piece.is_text or piece.is_patch or piece.is_connector or piece.is_scale_bar:
            return False
        if piece.crop_rect == [0.0, 0.0, 1.0, 1.0] and not piece.clip_shapes:
            return False
        crop_w = max(1e-6, piece.crop_rect[2] - piece.crop_rect[0])
        crop_h = max(1e-6, piece.crop_rect[3] - piece.crop_rect[1])
        center_x, center_y = piece.center
        self.push_history("Reset image crop")
        piece.w /= crop_w
        piece.h /= crop_h
        piece.crop_rect = [0.0, 0.0, 1.0, 1.0]
        piece.clip_shapes = []
        piece.clone_home = []
        piece.x = center_x - piece.w * piece.scale / 2.0
        piece.y = center_y - piece.h * piece.scale / 2.0
        self.selectionChanged.emit(self.selected_pieces())
        self.dirty.emit()
        self.update()
        return True

    def select_similar(self):
        selected = self.selected_pieces()
        if not selected or not self.level:
            return []
        source = selected[0]
        if source.is_text:
            matches = [p for p in self.level.pieces if p.is_text]
        elif source.is_patch:
            matches = [p for p in self.level.pieces
                       if p.is_patch and p.patch_color == source.patch_color]
        elif source.is_connector:
            matches = [p for p in self.level.pieces if p.is_connector]
        elif source.is_scale_bar:
            matches = [p for p in self.level.pieces if p.is_scale_bar]
        elif source.embedded:
            matches = [p for p in self.level.pieces
                       if p.embedded and p.embedded == source.embedded]
        elif source.asset_path:
            matches = [p for p in self.level.pieces
                       if not p.is_text and not p.is_patch
                       and not p.is_connector and not p.is_scale_bar
                       and p.asset_path == source.asset_path]
        else:
            matches = [p for p in self.level.pieces
                       if p.name.casefold() == source.name.casefold()]
        self.select(matches)
        return matches

    def _style_values(self, source: Piece) -> dict:
        names = ["opacity", "tint_mode", "tint_color", "tint_strength",
                 "border_mode", "border_shape", "border_color",
                 "border_opacity", "border_edges"]
        if source.is_text:
            names.extend(["font_family", "font_size", "font_bold", "font_italic",
                          "font_underline", "text_halign", "text_valign",
                          "text_padding", "text_auto_size", "text_background_color",
                          "text_background_opacity", "text_color"])
        if source.is_patch:
            names.extend(["patch_color", "patch_opacity"])
        if source.is_connector:
            names.extend(["connector_color", "connector_arrow", "connector_width"])
        if source.is_scale_bar:
            names.extend(["scale_units", "scale_color", "scale_line_width"])
        return {name: getattr(source, name) for name in names}

    def _apply_copied_style(self, target: Piece) -> bool:
        if not self._style_template:
            return False
        source = Piece.from_dict(self._style_template)
        if (source.is_text != target.is_text or source.is_patch != target.is_patch
                or source.is_connector != target.is_connector
                or source.is_scale_bar != target.is_scale_bar):
            return False
        values = self._style_values(source)
        if not values:
            return False
        self.push_history("Copy node style")
        for name, value in values.items():
            setattr(target, name, list(value) if isinstance(value, list) else value)
        self.dirty.emit()
        self.update()
        return True

    def replace_selected_image(self, path: str) -> bool:
        if not self.level or not path:
            return False
        selected = self.selected_pieces()
        if len(selected) != 1:
            return False
        piece = selected[0]
        if piece.is_text or piece.is_patch or piece.is_connector or piece.is_scale_bar:
            return False
        reader = QImageReader(path)
        size = reader.size()
        if not size.isValid():
            return False
        from core.project import embed_png
        center_x, center_y = piece.center
        self.push_history("Replace node image")
        piece.asset_path = ""
        piece.embedded = embed_png(path)
        piece.name = os.path.basename(path)
        piece.w, piece.h = float(size.width()), float(size.height())
        piece.crop_rect = [0.0, 0.0, 1.0, 1.0]
        piece.clip_shapes = []          # the shape of a pasted part doesn't carry over
        piece.clone_home = []
        piece.x = center_x - piece.w * piece.scale / 2.0
        piece.y = center_y - piece.h * piece.scale / 2.0
        self._cache.pop("emb:" + piece.id, None)
        self._clear_pixmaps()
        self.selectionChanged.emit(self.selected_pieces())
        self.dirty.emit()
        self.update()
        return True

    # ------------------------------------------------------------------
    def _clear_pixmaps(self):
        self._cache.clear()
        self._variants.clear()
        self._load_queue.clear()

    @staticmethod
    def _bucket(n: float) -> int:
        """Round a pixel size up to a sqrt(2) step so zooming reuses images."""
        n = max(16.0, float(n))
        steps = math.ceil(math.log(n / 16.0, math.sqrt(2.0)) - 1e-9)
        return int(round(16 * math.sqrt(2.0) ** steps))

    def pixmap(self, piece: Piece, target_size=None) -> QPixmap:
        """Display pixmap for a piece. Sizes are bucketed; while the exact
        bucket is still loading, the closest already-decoded size is shown so
        zooming never blocks on decoding a large PNG."""
        if target_size is None:
            return exporter.piece_pixmap(piece, self.project, self._cache, None)
        size = (self._bucket(target_size[0]), self._bucket(target_size[1]))
        source_key = ("emb:" + piece.id) if piece.embedded else piece.asset_path
        key = (source_key, size)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        variants = self._variants.setdefault(source_key, {})
        fallback = None
        want = size[0] * size[1]
        for other in list(variants):
            candidate = self._cache.get((source_key, other)) if other != size else None
            if candidate is None or candidate.isNull():
                if other != size:
                    variants.pop(other, None)
                continue
            area = other[0] * other[1]
            score = (area < want, abs(area - want))   # prefer >= target, then nearest
            if fallback is None or score < fallback[0]:
                fallback = (score, candidate)
        if fallback is not None:
            self._load_queue[key] = (piece, size)
            if not self._load_timer.isActive():
                self._load_timer.start(15)
            return fallback[1]
        path = ("" if piece.embedded or not piece.asset_path
                else self.project.resolve_asset(piece.asset_path))
        if path and os.path.isfile(path):
            # Decode off the GUI thread; show a flat placeholder meanwhile so a
            # first paint of a big map never freezes the window.
            if key not in self._decoding:
                self._decoding.add(key)
                self._decode_pool.start(_DecodeTask(key, path, size, self._decode_signals))
            return self._placeholder()
        pm = exporter.piece_pixmap(piece, self.project, self._cache, size)
        variants[size] = True
        return pm

    def _placeholder(self) -> QPixmap:
        if self._placeholder_pm is None:
            pm = QPixmap(4, 4)
            pm.fill(QColor(120, 140, 160, 70))
            self._placeholder_pm = pm
        return self._placeholder_pm

    def _decoded(self, key, size, image):
        self._decoding.discard(key)
        if self.project is None:
            return
        pm = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        exporter._cache_remember(self._cache, key, pm)
        self._variants.setdefault(key[0], {})[tuple(size)] = True
        self.update()

    def _process_loads(self):
        import time
        deadline = time.monotonic() + 0.03
        while self._load_queue and time.monotonic() < deadline:
            key, (piece, size) = next(iter(self._load_queue.items()))
            del self._load_queue[key]
            if self.project is None:
                continue
            exporter.piece_pixmap(piece, self.project, self._cache, size)
            self._variants.setdefault(key[0], {})[size] = True
        if self._load_queue:
            self._load_timer.start(15)
        self.update()

    # ------------------------------------------------------------------
    def world_to_screen(self, wx, wy):
        return (wx * self.zoom + self.pan_x, wy * self.zoom + self.pan_y)

    def screen_to_world(self, sx, sy):
        return ((sx - self.pan_x) / self.zoom, (sy - self.pan_y) / self.zoom)

    def fit_to_view(self):
        if not self.project:
            return
        vw, vh = self.width(), self.height()
        if vw <= 0 or vh <= 0:
            return
        rail = self.rail_thickness()
        avail_w, avail_h = max(1, vw - 2 * rail), max(1, vh - 2 * rail)
        self.zoom = min(avail_w / self.project.canvas_w,
                        avail_h / self.project.canvas_h) * 0.95
        self.pan_x = (vw - self.project.canvas_w * self.zoom) / 2.0
        self.pan_y = (vh - self.project.canvas_h * self.zoom) / 2.0
        self.update()
        self.viewChanged.emit()

    def set_zoom(self, z):
        cx, cy = self.width() / 2, self.height() / 2
        self.zoom_at(cx, cy, z / max(self.zoom, 1e-6))

    def zoom_at(self, sx, sy, factor):
        wx, wy = self.screen_to_world(sx, sy)
        self.zoom = max(0.05, min(8.0, self.zoom * factor))
        self.pan_x = sx - wx * self.zoom
        self.pan_y = sy - wy * self.zoom
        self.update()
        self.viewChanged.emit()

    def center_on_world(self, wx, wy):
        """Center the canvas viewport on a world-space point."""
        self.pan_x = self.width() / 2.0 - wx * self.zoom
        self.pan_y = self.height() / 2.0 - wy * self.zoom
        self.update()
        self.viewChanged.emit()

    # -- placed guides & rails ---------------------------------------------
    # Thin rails along the inside of the view edges create guides: drag from
    # the left/right rail for a vertical guide, top/bottom for a horizontal
    # one, and drag a guide back onto any rail to remove it. Guides belong to
    # the current level and are undoable, saved, and optionally exported.
    def _reset_guide_state(self):
        self._guide_drag = None
        self._guide_hover = None
        self._rail_hover = None
        self._guide_flash = set()

    def rail_thickness(self) -> int:
        if not self.rails_visible:
            return 0
        if self.project is not None and getattr(self.project, "show_coordinates", False):
            return RAIL_LABELED
        return RAIL

    def set_rails_visible(self, on: bool):
        on = bool(on)
        if on == self.rails_visible:
            return
        self.rails_visible = on
        self._rail_hover = None
        self._update_quick()
        self.railsChanged.emit()
        self.update()

    def set_show_coordinates(self, on: bool):
        if not self.project:
            return
        self.project.show_coordinates = bool(on)
        self._update_quick()
        self.railsChanged.emit()
        self.guideSettingsChanged.emit()
        self.dirty.emit()
        self.update()

    GUIDE_FLAGS = ("show_guides", "snap_to_guides", "lock_guides")

    def set_guide_flag(self, name: str, on: bool):
        """Toggle show_guides, snap_to_guides or lock_guides."""
        if not self.project or name not in self.GUIDE_FLAGS:
            return
        setattr(self.project, name, bool(on))
        self._guide_hover = None
        self.guideSettingsChanged.emit()
        self.dirty.emit()
        self.update()

    def _rail_at(self, sx: float, sy: float) -> str | None:
        """Which rail the point is on (corners are dead zones), or None."""
        t = self.rail_thickness()
        if not t or not self.project:
            return None
        w, h = self.width(), self.height()
        if not (0 <= sx < w and 0 <= sy < h):
            return None
        near_x = sx < t or sx >= w - t
        near_y = sy < t or sy >= h - t
        if near_x and near_y:
            return None
        if sx < t:
            return "left"
        if sx >= w - t:
            return "right"
        if sy < t:
            return "top"
        if sy >= h - t:
            return "bottom"
        return None

    @staticmethod
    def _rail_axis(side: str) -> str:
        return "v" if side in ("left", "right") else "h"

    @staticmethod
    def _split_cursor(axis: str) -> QCursor:
        return QCursor(Qt.CursorShape.SplitHCursor if axis == "v"
                       else Qt.CursorShape.SplitVCursor)

    def _guide_screen_pos(self, guide) -> float:
        return guide.pos * self.zoom + (self.pan_x if guide.axis == "v" else self.pan_y)

    def _guides_visible(self) -> bool:
        return bool(self.project and self.level
                    and getattr(self.project, "show_guides", True))

    def _guide_at(self, sx: float, sy: float, include_locked: bool = False):
        """The visible guide nearest the pointer, within GUIDE_HIT px."""
        if not self._guides_visible():
            return None
        if self.project.lock_guides and not include_locked:
            return None
        best, best_d = None, GUIDE_HIT + 0.5
        for guide in self.level.guides:
            d = abs((sx if guide.axis == "v" else sy) - self._guide_screen_pos(guide))
            if d <= best_d:
                best, best_d = guide, d
        return best

    def _guide_positions(self, axis: str) -> list[float]:
        """Guide positions that nodes and tools snap to (when enabled)."""
        if not (self._guides_visible() and getattr(self.project, "snap_to_guides", True)):
            return []
        return self.level.guide_positions(axis)

    def _flash_guides(self, axis: str, pos: float):
        for guide in self.level.guides:
            if guide.axis == axis and abs(guide.pos - pos) < 1e-6:
                self._guide_flash.add(guide.id)

    def _snap_point_to_guides(self, wx: float, wy: float):
        thr = GUIDE_DIST / max(self.zoom, 1e-6)
        gx = nearest(wx, self._guide_positions("v"), thr)
        gy = nearest(wy, self._guide_positions("h"), thr)
        return (wx if gx is None else gx), (wy if gy is None else gy)

    def _snap_tool_point(self, wx, wy, mods, grid: bool = False):
        """Point snapping shared by drawing tools: Shift (or a drag started
        with Shift) snaps to the grid, otherwise a nearby guide pulls the point
        onto it. Alt places it freely."""
        if mods & Qt.KeyboardModifier.AltModifier:
            return wx, wy
        if grid or mods & Qt.KeyboardModifier.ShiftModifier:
            cell = self.project.cell_size
            return snap_value(wx, cell), snap_value(wy, cell)
        return self._snap_point_to_guides(wx, wy)

    def _guide_snap_value(self, axis: str, raw: float, mods) -> float:
        """Where a dragged guide lands: a grid line, a node edge or center, or
        the canvas middle / edge within GUIDE_SNAP px. Alt places it freely."""
        if mods & Qt.KeyboardModifier.AltModifier or not self.project:
            return raw
        thr = GUIDE_SNAP / max(self.zoom, 1e-6)
        extent = self.project.canvas_w if axis == "v" else self.project.canvas_h
        targets = [0.0, extent / 2.0, float(extent)]
        for piece in self.level.pieces:
            x0, y0, x1, y1 = self._aabb(piece)
            lo, hi = (x0, x1) if axis == "v" else (y0, y1)
            targets += [lo, (lo + hi) / 2.0, hi]
        best = nearest(raw, targets, thr)
        cell = max(1, self.project.cell_size)
        line = round(raw / cell) * cell
        if abs(line - raw) <= thr and (best is None or abs(line - raw) < abs(best - raw) - 1e-9):
            best = line
        return raw if best is None else best

    def _begin_guide_drag(self, sx, sy, axis, guide=None):
        self._guide_drag = {
            "axis": axis, "id": guide.id if guide else None,
            "new": guide is None, "orig": guide.pos if guide else None,
            "pushed": False, "remove": guide is None, "start": (sx, sy),
            "pos": guide.pos if guide else None, "cursor": None,
        }
        self._rail_hover = None
        self._guide_hover = guide.id if guide else None
        self.setCursor(self._split_cursor(axis))
        self.update()

    def _update_guide_drag(self, sx, sy, mods):
        drag = self._guide_drag
        level = self.level
        axis = drag["axis"]
        remove = (self._rail_at(sx, sy) is not None
                  or not self.rect().contains(QPoint(int(sx), int(sy))))
        wx, wy = self.screen_to_world(sx, sy)
        pos = self._guide_snap_value(axis, wx if axis == "v" else wy, mods)
        if drag["new"] and drag["id"] is None:
            if remove:
                return               # still on the rail: no guide yet
            self.push_history("Add guide")
            drag["pushed"] = True
            if not self.project.show_guides:
                self.project.show_guides = True
                self.guideSettingsChanged.emit()
            guide = Guide(axis=axis, pos=pos)
            level.guides.append(guide)
            drag["id"] = guide.id
        elif not drag["new"] and not drag["pushed"]:
            x0, y0 = drag["start"]
            if abs(sx - x0) + abs(sy - y0) < 3:
                return               # a click, not a drag (yet)
            self.push_history("Move guide")
            drag["pushed"] = True
        guide = level.find_guide(drag["id"])
        if guide is None:
            self._guide_drag = None
            return
        guide.pos = pos
        drag.update(pos=pos, remove=remove, cursor=(sx, sy))
        self.setCursor(self._removal_cursor() if remove else self._split_cursor(axis))
        self.update()

    def _finish_guide_drag(self, sx, sy):
        drag, self._guide_drag = self._guide_drag, None
        level = self.level
        guide = level.find_guide(drag["id"]) if (level and drag["id"]) else None
        if guide is not None and drag["pushed"]:
            if drag["remove"]:
                if drag["new"]:
                    # dragged out and straight back: nothing changed
                    level.guides.remove(guide)
                    self.historyDiscardLast.emit()
                else:
                    self.historyDiscardLast.emit()
                    guide.pos = drag["orig"]
                    self.push_history("Remove guide")
                    level.guides.remove(guide)
                    self.dirty.emit()
                    self.statusMessage.emit("Guide removed.")
            else:
                self.dirty.emit()
        self._guide_hover = None
        self._restore_cursor(sx, sy)
        self.update()

    def cancel_guide_drag(self) -> bool:
        """Esc while dragging a guide: put everything back."""
        drag = self._guide_drag
        if drag is None:
            return False
        self._guide_drag = None
        level = self.level
        guide = level.find_guide(drag["id"]) if (level and drag["id"]) else None
        if guide is not None and drag["pushed"]:
            if drag["new"]:
                level.guides.remove(guide)
            else:
                guide.pos = drag["orig"]
            self.historyDiscardLast.emit()
        self._guide_hover = None
        self.setCursor(self._default_cursor())
        self.update()
        return True

    def _removal_cursor(self) -> QCursor:
        """A small × shown while a released guide would be removed."""
        if self._remove_cursor is None:
            pm = QPixmap(22, 22)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            for color, width in ((QColor(0, 0, 0, 210), 5.0), (QColor("#ffffff"), 2.2)):
                pen = QPen(color, width)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawLine(QPointF(5, 5), QPointF(17, 17))
                painter.drawLine(QPointF(17, 5), QPointF(5, 17))
            painter.end()
            self._remove_cursor = QCursor(pm, 11, 11)
        return self._remove_cursor

    def _default_cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.CrossCursor if self._any_tool_active()
                       else Qt.CursorShape.ArrowCursor)

    def _restore_cursor(self, sx, sy):
        side = self._rail_at(sx, sy)
        self.setCursor(self._split_cursor(self._rail_axis(side)) if side
                       else self._default_cursor())

    def _press_target_beats_guide(self, sx, sy) -> bool:
        """True where a press grabs something that wins over a guide: a zone
        vertex or edge, or the selected node's resize / rotate handle."""
        zone = self.selected_zone
        if zone and self._zone_vertex_at_screen(zone, sx, sy) is not None:
            return True
        if self._zone_near_edge(sx, sy):
            return True
        if self.selection and len(self.selection) == 1:
            if self._resize_handle_at(sx, sy):
                return True
            hp = self._rotate_handle_screen(next(iter(self.selected_pieces())))
            if (QPointF(sx, sy) - hp).manhattanLength() <= HANDLE_R + 5:
                return True
        return False

    def _update_guide_hover(self, sx, sy) -> bool:
        """Hover feedback for rails and guides; True when one is under the pointer."""
        side = self._rail_at(sx, sy)
        guide = None if (side or self._any_tool_active()) else self._guide_at(sx, sy)
        if guide is not None and self._press_target_beats_guide(sx, sy):
            guide = None
        hover = (side, guide.id if guide else None)
        was_hovering = bool(self._rail_hover or self._guide_hover)
        if hover != (self._rail_hover, self._guide_hover):
            self._rail_hover, self._guide_hover = hover
            self.update()
        if side or guide:
            self.setCursor(self._split_cursor(self._rail_axis(side) if side else guide.axis))
            return True
        if was_hovering:
            self.setCursor(self._default_cursor())
        return False

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip and self.project and self.level:
            text = self._guide_tooltip(e.pos().x(), e.pos().y())
            if text:
                QToolTip.showText(e.globalPos(), text, self)
            else:
                QToolTip.hideText()
                e.ignore()
            return True
        return super().event(e)

    def _guide_tooltip(self, sx, sy) -> str:
        side = self._rail_at(sx, sy)
        if side:
            kind = "vertical" if self._rail_axis(side) == "v" else "horizontal"
            return (f"Drag onto the map to add a {kind} guide.\n"
                    "Drag a guide back onto any rail to remove it.\n"
                    "Right-click for guide options.")
        guide = None if self._any_tool_active() else self._guide_at(sx, sy, include_locked=True)
        if guide is None:
            return ""
        where = describe_position(guide.axis, guide.pos, self.project.cell_size,
                                  getattr(self.project, "feet_per_square", 5))
        if self.project.lock_guides:
            return f"Guide at {where} (guides are locked)"
        return (f"Guide at {where}\nDrag to move, double-click for an exact "
                "position, drag onto a rail to remove. Alt: no snapping.")

    # Guide edits used by menus and dialogs — each one is a single undo step.
    def _show_guides_after_edit(self):
        if not self.project.show_guides:
            self.project.show_guides = True
            self.guideSettingsChanged.emit()
        self.dirty.emit()
        self.update()

    def add_guide(self, axis: str, pos: float, label: str = "Add guide"):
        if not self.level or self.level.has_guide(axis, pos):
            return None
        self.push_history(label)
        guide = self.level.add_guide(axis, pos)
        self._show_guides_after_edit()
        return guide

    def remove_guide(self, guide_id: str) -> bool:
        if not self.level or self.level.find_guide(guide_id) is None:
            return False
        self.push_history("Remove guide")
        self.level.remove_guide(guide_id)
        self._guide_hover = None
        self.dirty.emit()
        self.update()
        return True

    def set_guide_position(self, guide_id: str, pos: float) -> bool:
        guide = self.level.find_guide(guide_id) if self.level else None
        if guide is None or not math.isfinite(pos) or abs(guide.pos - pos) < 1e-9:
            return False
        self.push_history("Move guide")
        guide.pos = float(pos)
        self.dirty.emit()
        self.update()
        return True

    def clear_guides(self) -> int:
        """Remove every guide on the current level."""
        if not self.level or not self.level.guides:
            return 0
        count = len(self.level.guides)
        self.push_history("Clear guides")
        self.level.guides.clear()
        self._guide_hover = None
        self.dirty.emit()
        self.update()
        return count

    def copy_guides_to_all_levels(self) -> int:
        """Give every other level exactly the current level's guides."""
        if not self.project or not self.level or len(self.project.levels) < 2:
            return 0
        source = [(g.axis, g.pos) for g in self.level.guides]
        self.push_history("Copy guides to all levels")
        for level in self.project.levels:
            if level is not self.level:
                level.guides = [Guide(axis=a, pos=p) for a, p in source]
        self.dirty.emit()
        self.update()
        return len(self.project.levels) - 1

    def add_guides_around_selection(self, kind: str = "edges") -> int:
        """Guides at the selection's outer edges, its center, or both."""
        sel = self.selected_pieces()
        if not sel or not self.level:
            return 0
        boxes = [self._aabb(p) for p in sel]
        x0, x1 = min(b[0] for b in boxes), max(b[2] for b in boxes)
        y0, y1 = min(b[1] for b in boxes), max(b[3] for b in boxes)
        wanted = []
        if kind in ("edges", "both"):
            wanted += [("v", x0), ("v", x1), ("h", y0), ("h", y1)]
        if kind in ("center", "both"):
            wanted += [("v", (x0 + x1) / 2.0), ("h", (y0 + y1) / 2.0)]
        wanted = [(a, p) for a, p in dict.fromkeys(wanted)
                  if not self.level.has_guide(a, p)]
        if not wanted:
            return 0
        self.push_history("Add guides around selection")
        for axis, pos in wanted:
            self.level.add_guide(axis, pos)
        self._show_guides_after_edit()
        return len(wanted)

    def apply_guide_layout(self, vertical, horizontal, replace: bool = True,
                           all_levels: bool = False) -> int:
        """Place guides at world positions computed by the layout dialog."""
        if not self.project or not self.level:
            return 0
        levels = list(self.project.levels) if all_levels else [self.level]
        wanted = sorted({("v", float(p)) for p in vertical}
                        | {("h", float(p)) for p in horizontal})

        def result(level):
            keep = [] if replace else [(g.axis, g.pos) for g in level.guides]
            return sorted(set(keep) | set(wanted))

        if all(result(lv) == sorted((g.axis, g.pos) for g in lv.guides)
               for lv in levels):
            return 0
        self.push_history("Guide layout")
        added = 0
        for level in levels:
            if replace:
                level.guides.clear()
            for axis, pos in wanted:
                added += level.add_guide(axis, pos) is not None
        self._show_guides_after_edit()
        return added

    # drawing ----------------------------------------------------------------
    def _draw_guides(self, painter):
        level = self.level
        if not level or not level.guides:
            return
        dragging = self._guide_drag.get("id") if self._guide_drag else None
        show = self.project.show_guides
        if not show and dragging is None:
            return
        base = exporter.guide_qcolor(self.project)
        opacity = max(0.1, min(1.0, float(self.project.guide_opacity)))
        locked = bool(self.project.lock_guides)
        rect = self._canvas_rect_screen()
        w, h = self.width(), self.height()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for guide in level.guides:
            if not show and guide.id != dragging:
                continue
            at = int(round(self._guide_screen_pos(guide)))
            if not (-3 <= at <= (w if guide.axis == "v" else h) + 3):
                continue
            active = (guide.id in (dragging, self._guide_hover)
                      or guide.id in self._guide_flash)
            removing = guide.id == dragging and bool(self._guide_drag.get("remove"))
            width = 2 if active else 1
            alpha = opacity * (0.7 if locked and not active else 1.0)
            if guide.axis == "v":
                full = (QPoint(at, 0), QPoint(at, h))
                inside = (QPoint(at, int(max(0.0, rect.top()))),
                          QPoint(at, int(min(float(h), rect.bottom()))))
            else:
                full = (QPoint(0, at), QPoint(w, at))
                inside = (QPoint(int(max(0.0, rect.left())), at),
                          QPoint(int(min(float(w), rect.right())), at))
            halo = QPen(QColor(0, 0, 0, 90))
            halo.setWidth(width + 2)
            painter.setOpacity(1.0)
            painter.setPen(halo)
            painter.drawLine(*full)
            pen = QPen(QColor("#9aa3ad") if removing else QColor(base))
            pen.setWidth(width)
            if removing:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setOpacity(alpha * 0.45)       # dimmer over the pasteboard
            painter.drawLine(*full)
            painter.setOpacity(alpha)
            painter.drawLine(*inside)
        painter.restore()

    def _draw_rails(self, painter):
        t = self.rail_thickness()
        if not t or not self.project:
            return
        colors = getattr(self, "theme_colors", None) or theme_colors("dark")
        w, h = self.width(), self.height()
        panel = QColor(colors["panel"])
        panel.setAlpha(235)
        tick = QColor(colors["muted"])
        tick.setAlpha(170)
        text = QColor(colors["text"])
        text.setAlpha(230)
        guide_color = exporter.guide_qcolor(self.project)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setOpacity(1.0)
        rails = {"top": QRectF(0, 0, w, t), "bottom": QRectF(0, h - t, w, t),
                 "left": QRectF(0, t, t, h - 2 * t), "right": QRectF(w - t, t, t, h - 2 * t)}
        drag_side = None
        if self._guide_drag and self._guide_drag.get("remove"):
            pointer = self._guide_drag.get("cursor") or self._guide_drag["start"]
            drag_side = self._rail_at(*pointer)
        for side, r in rails.items():
            painter.fillRect(r, panel)
            if side in (self._rail_hover, drag_side):
                glow = QColor(guide_color)
                glow.setAlpha(70)
                painter.fillRect(r, glow)
        edge = QColor(colors["muted"])
        edge.setAlpha(110)
        painter.setPen(QPen(edge, 1))
        painter.drawLine(t, t - 1, w - t, t - 1)
        painter.drawLine(t, h - t, w - t, h - t)
        painter.drawLine(t - 1, t, t - 1, h - t)
        painter.drawLine(w - t, t, w - t, h - t)
        self._draw_rail_scale(painter, True, t, tick, text)
        self._draw_rail_scale(painter, False, t, tick, text)
        if self.project.show_guides and self.level:
            marker = QPen(guide_color)
            marker.setWidth(2)
            painter.setPen(marker)
            for guide in self.level.guides:
                at = int(round(self._guide_screen_pos(guide)))
                if guide.axis == "v" and t <= at <= w - t:
                    painter.drawLine(at, 0, at, t - 1)
                    painter.drawLine(at, h - t, at, h)
                elif guide.axis == "h" and t <= at <= h - t:
                    painter.drawLine(0, at, t - 1, at)
                    painter.drawLine(w - t, at, w, at)
        painter.restore()

    def _draw_rail_scale(self, painter, horizontal: bool, t: int, tick: QColor,
                         text: QColor):
        """Grid ticks (and coordinates, when enabled) along one pair of rails."""
        project = self.project
        cell = max(1, project.cell_size)
        extent = project.canvas_w if horizontal else project.canvas_h
        cols, rows = grid_counts(project.canvas_w, project.canvas_h, cell)
        count = cols if horizontal else rows
        spacing = cell * self.zoom
        offset = self.pan_x if horizontal else self.pan_y
        length = self.width() if horizontal else self.height()
        far = self.height() if horizontal else self.width()
        if spacing <= 0:
            return
        first = max(0, int(math.floor((t - offset) / spacing)) - 1)
        last = min(count, int(math.ceil((length - t - offset) / spacing)) + 1)
        major = max(1, int(project.grid_major or 5))
        painter.setPen(QPen(tick, 1))
        for i in range(first, last + 1):
            if i * cell > extent + 1e-6:
                break
            at = int(round(i * spacing + offset))
            if not (t <= at <= length - t):
                continue
            is_major = i % major == 0 or i == count
            if not is_major and spacing < 5:
                continue
            size = max(2, int(t * (0.55 if is_major else 0.3)))
            if horizontal:
                painter.drawLine(at, t - 1 - size, at, t - 1)
                painter.drawLine(at, far - t, at, far - t + size)
            else:
                painter.drawLine(t - 1 - size, at, t - 1, at)
                painter.drawLine(far - t, at, far - t + size, at)
        if not getattr(project, "show_coordinates", False):
            return
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(9)
        metrics = QFontMetricsF(font)
        name = column_label if horizontal else row_label
        widest = metrics.horizontalAdvance(name(count - 1))
        if not horizontal and widest > t - 3:
            font.setPixelSize(8)
            metrics = QFontMetricsF(font)
            widest = metrics.horizontalAdvance(name(count - 1))
        step = label_step(spacing, widest if horizontal else metrics.height(), 4)
        painter.setFont(font)
        painter.setPen(QPen(text))
        for i in range((first // step) * step, min(count, last + 1), step):
            center = min((i + 0.5) * cell, extent - min(cell, extent) / 2.0)
            at = center * self.zoom + offset
            if not (t + 2 <= at <= length - t - 2):
                continue
            label = name(i)
            if horizontal:
                boxes = (QRectF(at - 30, 0, 60, t), QRectF(at - 30, far - t, 60, t))
            else:
                boxes = (QRectF(0, at - 8, t, 16), QRectF(far - t, at - 8, t, 16))
            for box in boxes:
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_guide_readout(self, painter):
        drag = self._guide_drag
        if not drag or drag.get("cursor") is None or drag.get("pos") is None:
            return
        if drag.get("remove"):
            message = "Release to remove"
        else:
            message = describe_position(drag["axis"], drag["pos"], self.project.cell_size,
                                        getattr(self.project, "feet_per_square", 5))
        colors = getattr(self, "theme_colors", None) or theme_colors("dark")
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setBold(True)
        metrics = QFontMetricsF(font)
        bw, bh = metrics.horizontalAdvance(message) + 14, metrics.height() + 8
        sx, sy = drag["cursor"]
        bx = min(max(4.0, sx + 16), self.width() - bw - 4)
        by = min(max(4.0, sy + 16), self.height() - bh - 4)
        box = QRectF(bx, by, bw, bh)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setOpacity(1.0)
        fill = QColor(colors["panel"])
        fill.setAlpha(240)
        painter.setPen(QPen(exporter.guide_qcolor(self.project), 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 4, 4)
        painter.setFont(font)
        painter.setPen(QColor(colors["text"]))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, message)
        painter.restore()

    # ------------------------------------------------------------------
    def selected_pieces(self) -> list[Piece]:
        if not self.level:
            return []
        return [p for p in self.level.pieces if p.id in self.selection]

    def select(self, pieces: list[Piece]):
        if self.free_transform and {p.id for p in pieces} != self.selection:
            self.end_free_transform(True)
        if self.selected_zone_id is not None:
            self.selected_zone_id = None
            self.zoneSelectionChanged.emit(None)
        self.selection = {p.id for p in pieces}
        self._update_quick()
        self.selectionChanged.emit(self.selected_pieces())
        self.update()

    def clear_selection(self):
        self.select([])

    def _update_quick(self):
        if self.selection:
            selected = self.selected_pieces()
            lock_button = self._qb.get("lock")
            if selected and lock_button:
                locked = [piece.locked for piece in selected]
                if all(locked):
                    lock_button.setText("U")
                    lock_button.setToolTip("Unlock all selected nodes")
                elif any(locked):
                    lock_button.setText("±")
                    lock_button.setToolTip(
                        "Toggle the lock state of each selected node")
                else:
                    lock_button.setText("L")
                    lock_button.setToolTip("Lock all selected nodes")
            if self.quick_enabled:
                self.quick.show()
                rail = self.rail_thickness()
                self.quick.move(self.width() - self.quick.width() - 6 - rail, 6 + rail)
            else:
                self.quick.hide()
        else:
            self.quick.hide()

    def _quick(self, name):
        sel = self.selected_pieces()
        if not sel:
            return
        self.push_history(f"Quick {name}")
        if name == "rotL":
            for p in sel:
                p.rotation = (p.rotation - 90) % 360
        elif name == "rotR":
            for p in sel:
                p.rotation = (p.rotation + 90) % 360
        elif name == "fh":
            for p in sel:
                p.flip_h = not p.flip_h
        elif name == "fv":
            for p in sel:
                p.flip_v = not p.flip_v
        elif name == "up":
            self._raise(sel)
        elif name == "down":
            self._lower(sel)
        elif name == "lock":
            for p in sel:
                p.locked = not p.locked
        elif name == "copy":
            self.copy()
        elif name == "dup":
            self.duplicate()
        elif name == "del":
            self.delete_selected()
        self._update_quick()
        self.update()
        self.dirty.emit()

    # ------------------------------------------------------------------
    def asset_world_size(self, store_rel_path: str, asset=None) -> tuple[float, float]:
        """Size an asset should have on this canvas, in world px.

        Mobius-style names such as ``[100x100]`` give the size in feet, while
        the PNG itself may be thousands of pixels wide. Those are scaled using
        the project's feet-per-square and square size so a 100x100 ft tile is
        20x20 squares. Plain pixel-sized art keeps its pixel size, and any
        image still larger than the canvas is fitted to a third of it."""
        px_w = px_h = 0
        named = None
        if asset is not None:
            px_w, px_h = int(asset.width or 0), int(asset.height or 0)
            named = getattr(asset, "size", None)
        if not (px_w and px_h):
            size = QImageReader(self.project.resolve_asset(store_rel_path)).size()
            if size.isValid():
                px_w, px_h = size.width(), size.height()
            else:
                pm = self.pixmap(Piece(asset_path=store_rel_path), (2048, 2048))
                px_w = pm.width() if not pm.isNull() else 64
                px_h = pm.height() if not pm.isNull() else 64
        w, h = float(px_w), float(px_h)
        cell = max(1, self.project.cell_size)
        feet = max(1, int(getattr(self.project, "feet_per_square", 5) or 5))
        if named and named[0] > 0 and named[1] > 0 and (
                px_w >= named[0] * 4 or px_h >= named[1] * 4):
            # One uniform factor, so the image keeps its own proportions even
            # when the nominal name and the pixels disagree slightly.
            fx = named[0] / feet * cell / max(1, px_w)
            fy = named[1] / feet * cell / max(1, px_h)
            factor = math.sqrt(fx * fy)
            w, h = px_w * factor, px_h * factor
        limit = 0.34 * min(self.project.canvas_w, self.project.canvas_h)
        if max(w, h) > limit and not (named and px_w >= named[0] * 4):
            f = limit / max(w, h)
            w, h = w * f, h * f
        return max(1.0, w), max(1.0, h)

    def add_asset(self, store_rel_path: str, world_x, world_y) -> Optional[Piece]:
        if not self.level:
            return None
        asset = self.library.get(store_rel_path) if self.library else None
        w, h = self.asset_world_size(store_rel_path, asset)
        is_overlay = bool(asset and asset.is_overlay)
        name = asset.name if asset else store_rel_path.split("/")[-1]
        p = Piece(asset_path=store_rel_path, name=name, w=w, h=h,
                  is_overlay=is_overlay, snap=not is_overlay)
        p.x, p.y = self._drop_origin(world_x, world_y, w, h, p.snap)
        self.push_history("Add node")
        self.level.add(p)
        self.select([p])
        self.dirty.emit()
        if self.auto_tighten and not is_overlay:
            self.tighten_piece_async(p, only_if_untouched=True)
        return p

    def _drop_origin(self, wx, wy, w, h, snap=True):
        """Top-left for a w x h node dropped at (wx, wy): grid-snapped as
        before, unless a nearby guide pulls an edge or the center onto it."""
        x, y = wx - w / 2.0, wy - h / 2.0
        cell = self.project.cell_size
        out_x = snap_value(x, cell) if snap else x
        out_y = snap_value(y, cell) if snap else y
        thr = GUIDE_DIST / max(self.zoom, 1e-6)
        guide_xs, guide_ys = self._guide_positions("v"), self._guide_positions("h")
        if guide_xs:
            tx, hit = self._nearest_target(x, (0.0, w / 2.0, w), guide_xs, thr, None)
            if hit is not None:
                out_x = tx
        if guide_ys:
            ty, hit = self._nearest_target(y, (0.0, h / 2.0, h), guide_ys, thr, None)
            if hit is not None:
                out_y = ty
        return out_x, out_y

    # -- tighten to visible pixels ---------------------------------------
    def tighten_piece_async(self, piece: Piece, only_if_untouched=False,
                            options: dict | None = None) -> bool:
        """Trim a node to its visible pixels (non-destructive crop).

        ``options``: threshold (alpha 1-255 below which a pixel counts as empty),
        padding (map px of margin to keep) and sides (left, top, right, bottom
        flags for which edges may be trimmed). Cached analysis applies at once;
        otherwise the image is analysed in the background first."""
        if piece.is_text or piece.is_patch or piece.is_connector or piece.is_scale_bar:
            return False
        opts = dict(self.tighten_options)
        if options:
            opts.update(options)
        path = ("" if piece.embedded else
                (self.project.resolve_asset(piece.asset_path) if piece.asset_path else ""))
        if path:
            from ui.image_utils import has_cached_bounds, bounds_table_for_file
            if has_cached_bounds(path):
                return self._apply_tighten(
                    piece, bounds_table_for_file(path), opts, only_if_untouched)
        elif not piece.embedded:
            return False
        self._bounds_wait[piece.id] = (opts, only_if_untouched)
        self._bounds_pool.start(_BoundsTask(
            piece.id, path, piece.embedded, self._bounds_signals))
        return True

    def drain_background_work(self):
        """Finish any in-flight bounds-calc work before this widget is torn down.

        ``_bounds_pool`` is a QThreadPool parented to this widget, so Qt
        destroys it as part of the normal child-teardown cascade when the
        widget (or an ancestor) is deleted. That destructor calls
        ``waitForDone()`` deep inside a chain of nested C++ destructors with
        no opportunity for PyQt to release the GIL first — if the pool's one
        worker thread is mid-run on a Python-level task, it can never
        reacquire the GIL to finish, and the deleting thread waits for it
        forever. Draining explicitly from Python here uses the normal,
        GIL-releasing call path and avoids that deadlock.
        """
        self._bounds_pool.clear()
        self._bounds_pool.waitForDone()
        self._decode_pool.clear()
        self._decode_pool.waitForDone()

    def tighten_selected(self, options: dict | None = None) -> int:
        """Trim every selected image node to its visible pixels."""
        count = 0
        for piece in self.selected_pieces():
            if self.tighten_piece_async(piece, options=options):
                count += 1
        if count:
            self.statusMessage.emit(
                f"Tightening {count} node(s) to their visible pixels…")
        return count

    def restore_selected_images(self) -> int:
        """Undo tightening/cropping: show the whole source image again."""
        count = 0
        for piece in self.selected_pieces():
            if piece.crop_rect != [0.0, 0.0, 1.0, 1.0] and not (
                    piece.is_text or piece.is_patch or piece.is_connector
                    or piece.is_scale_bar):
                if self._set_crop_rect(piece, [0.0, 0.0, 1.0, 1.0],
                                       "Restore full image"):
                    count += 1
        return count

    def _bounds_ready(self, piece_id: str, table):
        opts, only_if_untouched = self._bounds_wait.pop(
            piece_id, (dict(self.tighten_options), False))
        if not table or not self.project:
            return
        for level in self.project.levels:
            for piece in level.pieces:
                if piece.id == piece_id:
                    self._apply_tighten(piece, table, opts, only_if_untouched)
                    return

    def _apply_tighten(self, piece: Piece, table, opts: dict,
                       only_if_untouched=False) -> bool:
        if only_if_untouched and list(piece.crop_rect) != [0.0, 0.0, 1.0, 1.0]:
            return False
        from ui.image_utils import nearest_threshold
        bounds = table.get(nearest_threshold(opts.get("threshold", 16)))
        if bounds is None:
            return False
        crop = piece.crop_rect
        crop_w, crop_h = crop[2] - crop[0], crop[3] - crop[1]
        if crop_w <= 0 or crop_h <= 0:
            return False
        # map px per source-image fraction at the node's current size
        kx = piece.w * piece.scale / crop_w
        ky = piece.h * piece.scale / crop_h
        pad = max(0.0, float(opts.get("padding", 0.0)))
        sides = tuple(opts.get("sides", (True, True, True, True)))
        target = [
            max(0.0, bounds[0] - pad / kx) if sides[0] else 0.0,
            max(0.0, bounds[1] - pad / ky) if sides[1] else 0.0,
            min(1.0, bounds[2] + pad / kx) if sides[2] else 1.0,
            min(1.0, bounds[3] + pad / ky) if sides[3] else 1.0,
        ]
        if piece.clip_shapes:
            # a pasted part only ever shrinks inside the piece it already shows
            target = [max(target[0], crop[0]), max(target[1], crop[1]),
                      min(target[2], crop[2]), min(target[3], crop[3])]
            if target[2] - target[0] <= 1e-4 or target[3] - target[1] <= 1e-4:
                return False
        elif target[2] - target[0] < 0.02 or target[3] - target[1] < 0.02:
            return False
        if all(abs(a - b) < 0.0005 for a, b in zip(target, crop)):
            return False                      # already exactly this tight
        if not self._set_crop_rect(piece, target, "Tighten to visible pixels",
                                   quiet=only_if_untouched):
            return False
        if only_if_untouched and piece.snap and piece.rotation == 0:
            cell = max(1, self.project.cell_size)
            piece.x = snap_value(piece.x, cell)
            piece.y = snap_value(piece.y, cell)
        self.update()
        return True

    def _set_crop_rect(self, piece: Piece, target, label: str, quiet=False) -> bool:
        """Set the node's crop to ``target`` (0..1 source fractions) while the
        visible artwork stays exactly where it is on the map."""
        crop = piece.crop_rect
        crop_w, crop_h = crop[2] - crop[0], crop[3] - crop[1]
        if crop_w <= 1e-6 or crop_h <= 1e-6:
            return False
        kx = piece.w * piece.scale / crop_w
        ky = piece.h * piece.scale / crop_h
        dx = ((target[0] + target[2]) / 2.0 - (crop[0] + crop[2]) / 2.0) * kx
        dy = ((target[1] + target[3]) / 2.0 - (crop[1] + crop[3]) / 2.0) * ky
        if piece.flip_h:
            dx = -dx
        if piece.flip_v:
            dy = -dy
        angle = math.radians(piece.rotation)
        cx, cy = piece.center
        new_cx = cx + dx * math.cos(angle) - dy * math.sin(angle)
        new_cy = cy + dx * math.sin(angle) + dy * math.cos(angle)
        if quiet:
            self._suppress_history = True    # part of the placement's own undo step
        try:
            self.push_history(label)
        finally:
            self._suppress_history = False
        piece.w = (target[2] - target[0]) * kx / piece.scale
        piece.h = (target[3] - target[1]) * ky / piece.scale
        piece.crop_rect = [float(v) for v in target]
        piece.x = new_cx - piece.w * piece.scale / 2.0
        piece.y = new_cy - piece.h * piece.scale / 2.0
        self.selectionChanged.emit(self.selected_pieces())
        self.dirty.emit()
        self.update()
        return True

    def add_embedded(self, b64: str, world_x, world_y, name="Custom") -> Piece:
        size = exporter.embedded_size(b64)
        w, h = size if size else (64, 64)
        p = Piece(embedded=b64, name=name, w=w, h=h, snap=False)
        p.x = world_x - w / 2.0
        p.y = world_y - h / 2.0
        self.push_history("Add custom image")
        self.level.add(p)
        self.select([p])
        self.dirty.emit()
        return p

    def add_text(self, world_x, world_y) -> Piece:
        from core.render import compute_text_size
        p = Piece(is_text=True, text="TEXT", name="Text", w=80, h=30,
                  text_color=self.theme_accent)
        p.x = world_x - 40
        p.y = world_y - 15
        p.w, p.h = compute_text_size(p.text, p.font_family, p.font_size,
                                     p.font_bold, p.font_italic, p.text_padding)
        self.push_history("Add text")
        self.level.add(p)
        self.select([p])
        self.dirty.emit()
        return p

    def add_patch(self, world_x, world_y, width, height) -> Piece | None:
        if not self.level or width < 2 or height < 2:
            return None
        index = sum(1 for piece in self.level.pieces if piece.is_patch) + 1
        center = (world_x + width / 2.0, world_y + height / 2.0)
        target_image = next((piece for piece in reversed(self.level.paint_order())
                             if not piece.is_text and not piece.is_patch
                             and piece.hit_test(*center)), None)
        target_layer = target_image.layer if target_image else self.level.current_layer
        patch = Piece(is_patch=True, name=f"Label patch {index}",
                      x=world_x, y=world_y, w=width, h=height,
                      patch_color="#10141c", snap=False, layer=target_layer)
        self.push_history("Add raster-label patch")
        self.level.add(patch)
        self.set_patch_tool(False)
        self.select([patch])
        self.dirty.emit()
        return patch

    # ------------------------------------------------------------------
    def _rotate_handle_screen(self, p: Piece):
        cx, cy = p.center
        scx, scy = self.world_to_screen(cx, cy)
        ang = math.radians(p.rotation)
        # the node's local "up" (0, -1) after the same clockwise rotation the
        # painter applies, so the handle always sits on the box's top edge
        ux = math.sin(ang)
        uy = -math.cos(ang)
        half_h = (p.vis_h / 2.0) * self.zoom
        return QPointF(scx + ux * (half_h + HANDLE_DIST), scy + uy * (half_h + HANDLE_DIST))

    def _visible_world_bounds(self):
        x0, y0 = self.screen_to_world(0, 0)
        x1, y1 = self.screen_to_world(self.width(), self.height())
        return (x0, y0, x1, y1)

    # -- resize handles -------------------------------------------------
    def _resize_handle_points(self, p: Piece) -> dict[str, QPointF]:
        cx, cy = p.center
        scx, scy = self.world_to_screen(cx, cy)
        ang = math.radians(p.rotation)
        ca, sa = math.cos(ang), math.sin(ang)
        hw = p.vis_w / 2.0 * self.zoom
        hh = p.vis_h / 2.0 * self.zoom
        pts = {}
        for name, (hx, hy) in RESIZE_HANDLES.items():
            lx, ly = hx * hw, hy * hh
            pts[name] = QPointF(scx + lx * ca - ly * sa, scy + lx * sa + ly * ca)
        return pts

    def _resize_handle_at(self, sx, sy) -> str | None:
        if len(self.selection) != 1:
            return None
        p = next(iter(self.selected_pieces()), None)
        if p is None or p.locked:
            return None
        click = QPointF(sx, sy)
        best, best_d = None, RESIZE_HIT + 1.0
        for name, pt in self._resize_handle_points(p).items():
            d = max(abs(pt.x() - click.x()), abs(pt.y() - click.y()))
            if d <= RESIZE_HIT and d < best_d:
                best, best_d = name, d
        return best

    def _draw_free_transform_frame(self, painter, p: Piece):
        pts = self._resize_handle_points(p)
        painter.save()
        pen = QPen(QColor(self.theme_accent), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawPolygon(QPolygonF([pts["nw"], pts["ne"], pts["se"], pts["sw"]]))
        painter.setPen(QColor(self.theme_accent))
        painter.drawText(8, self.height() - 10,
                         "Free transform — drag handles to stretch, Shift locks corners, "
                         "Alt from center · Enter apply · Esc cancel")
        painter.restore()

    def _draw_resize_handles(self, painter, p: Piece):
        if p.locked:
            return
        painter.save()
        painter.setPen(QPen(QColor(self.theme_accent), 1.5))
        painter.setBrush(QBrush(QColor("#ffffff")))
        for pt in self._resize_handle_points(p).values():
            painter.drawRect(QRectF(pt.x() - 4, pt.y() - 4, 8, 8))
        painter.restore()

    @staticmethod
    def _resize_cursor(name: str, rotation: float) -> Qt.CursorShape:
        hx, hy = RESIZE_HANDLES[name]
        ang = (math.degrees(math.atan2(hy, hx)) + rotation) % 180
        # 0: horizontal, 45: down-right diagonal, 90: vertical, 135: down-left
        step = int(round(ang / 45.0)) % 4
        return (Qt.CursorShape.SizeHorCursor, Qt.CursorShape.SizeFDiagCursor,
                Qt.CursorShape.SizeVerCursor, Qt.CursorShape.SizeBDiagCursor)[step]

    def _begin_resize(self, name: str):
        p = next(iter(self.selected_pieces()))
        self.push_history("Resize")
        self._drag = {"mode": "resize", "handle": name, "piece": p,
                      "x": p.x, "y": p.y, "w": p.w, "h": p.h, "scale": p.scale,
                      "center": p.center, "rot": p.rotation}

    def _resize_primary(self, sx, sy, e):
        """Drag a handle. Edges stretch one axis; corners scale uniformly
        (Shift = free; reversed in free-transform mode). Alt resizes from the center."""
        d = self._drag
        p: Piece = d["piece"]
        hx, hy = RESIZE_HANDLES[d["handle"]]
        mods = e.modifiers()
        from_center = bool(mods & Qt.KeyboardModifier.AltModifier)
        # Corners keep proportions by default; Shift releases them. In free
        # transform mode that flips: corners are free and Shift locks them.
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        keep_ratio = hx != 0 and hy != 0 and (shift == self.free_transform)
        wx, wy = self.screen_to_world(sx, sy)
        wx, wy = self._snap_resize_point(p, d, wx, wy)
        c0x, c0y = d["center"]
        rad = math.radians(d["rot"])
        ca, sa = math.cos(rad), math.sin(rad)
        dx, dy = wx - c0x, wy - c0y
        lx, ly = dx * ca + dy * sa, -dx * sa + dy * ca   # into the node's frame
        w0, h0 = d["w"] * d["scale"], d["h"] * d["scale"]
        min_w, min_h = MIN_VISUAL_SIZE, MIN_VISUAL_SIZE

        def extent(local, direction, size0, minimum):
            if direction == 0:
                return size0, 0.0
            anchor = 0.0 if from_center else -direction * size0 / 2.0
            size = max(minimum, (local - anchor) * direction * (2.0 if from_center else 1.0))
            return size, anchor

        new_w, ax = extent(lx, hx, w0, min_w)
        new_h, ay = extent(ly, hy, h0, min_h)
        new_scale, w_unit, h_unit = d["scale"], None, None
        if keep_ratio:
            f = max(new_w / w0, new_h / h0, MIN_VISUAL_SIZE / max(min(w0, h0), 1e-6))
            new_w, new_h = w0 * f, h0 * f
            new_scale = d["scale"] * f
            w_unit, h_unit = d["w"], d["h"]
        else:
            w_unit, h_unit = new_w / new_scale, new_h / new_scale
        # new center in the node's frame, then back to world
        ncx = 0.0 if (from_center or hx == 0) else ax + hx * new_w / 2.0
        ncy = 0.0 if (from_center or hy == 0) else ay + hy * new_h / 2.0
        cx = c0x + ncx * ca - ncy * sa
        cy = c0y + ncx * sa + ncy * ca
        p.w, p.h, p.scale = w_unit, h_unit, new_scale
        p.x, p.y = cx - new_w / 2.0, cy - new_h / 2.0
        if p.is_text and not keep_ratio:
            p.text_auto_size = False

    def _snap_resize_point(self, p: Piece, drag: dict, wx: float, wy: float):
        """Snap the dragged edge/corner to grid lines and neighbor edges.
        Only for upright (multiples of 90°) nodes, where handle axes line up
        with the world axes."""
        self._smart_guides = []
        quarter = round(drag["rot"] / 90.0)
        if abs(drag["rot"] - quarter * 90.0) > 0.01:
            return wx, wy
        hx, hy = RESIZE_HANDLES[drag["handle"]]
        if quarter % 2:                      # 90°/270°: handle axes swap
            hx, hy = hy, hx
        thr = GUIDE_DIST / max(self.zoom, 1e-6)
        cell = max(1, self.project.cell_size) if p.snap else None
        xs, ys = [], []
        for o in self.level.pieces:
            if o.id == p.id:
                continue
            bx0, by0, bx1, by1 = self._aabb(o)
            xs += [bx0, (bx0 + bx1) / 2.0, bx1]
            ys += [by0, (by0 + by1) / 2.0, by1]
        mid_x, mid_y = self._canvas_middle_lines()
        xs += mid_x
        ys += mid_y
        guide_xs, guide_ys = self._guide_positions("v"), self._guide_positions("h")
        xs += guide_xs
        ys += guide_ys
        self._guide_flash = set()

        def pick(value, edges):
            best, guide = value, None
            best_d = thr + 1e-9
            if cell:
                line = round(value / cell) * cell
                if abs(line - value) <= thr:
                    best, best_d = line, abs(line - value)
            for e in edges:
                dist = abs(e - value)
                if dist <= thr and dist <= best_d + 1e-9:
                    best, best_d, guide = e, dist, e
            return best, guide

        if hx:
            wx, gx = pick(wx, xs)
            if gx is not None:
                if gx in guide_xs:
                    self._flash_guides("v", gx)
                else:
                    self._smart_guides.append(("v", gx))
        if hy:
            wy, gy = pick(wy, ys)
            if gy is not None:
                if gy in guide_ys:
                    self._flash_guides("h", gy)
                else:
                    self._smart_guides.append(("h", gy))
        return wx, wy

    def _piece_rect_screen(self, p: Piece) -> QRectF:
        w = p.w * self.zoom * p.scale
        h = p.h * self.zoom * p.scale
        return QRectF(-w / 2, -h / 2, w, h)

    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        theme_bg = QColor(getattr(self, "theme_colors", theme_colors("dark"))["bg"])
        painter.fillRect(self.rect(), theme_bg)
        if not self.project or not self.level:
            return

        self._draw_pasteboard(painter, theme_bg)
        self._draw_bg(painter)
        grid_on_top = getattr(self.project, "grid_on_top", True)
        if self.project.show_grid and not grid_on_top:
            self._draw_grid(painter, self.project.grid_color, self.project.grid_opacity)
        self._draw_cell_highlight(painter)
        if self.ref_enabled:
            self._draw_reference(painter)

        for p in self.level.paint_order(self._visible_world_bounds()):
            if self.solo_layer_id and p.layer != self.solo_layer_id:
                continue
            self._draw_piece(painter, p, 1.0)
        if self.project.show_grid and grid_on_top:
            self._draw_grid(painter, self.project.grid_color, self.project.grid_opacity)
        self._draw_canvas_veil_and_frame(painter, theme_bg)
        self._draw_canvas_centerlines(painter)

        if self.project.show_zones:
            draw_zone_borders(
                painter, self.level.zones, self.project,
                lambda x, y: QPointF(*self.world_to_screen(x, y)), self.zoom)
        self._draw_zone_tool_preview(painter)
        self._draw_patch_tool_preview(painter)
        self._draw_compose_tool_previews(painter)
        self._draw_stamp_preview(painter)
        self._draw_cutout_overlay(painter)
        self._draw_clone_overlay(painter)
        if self.selected_zone:
            self._draw_zone_edit_overlay(painter, self.selected_zone)
        self._draw_guides(painter)

        # selection outlines
        for p in self.selected_pieces():
            self._draw_selection(painter, p)
        visible_selection = self.selected_pieces()
        if len(visible_selection) == 1:
            only = visible_selection[0]
            self._draw_resize_handles(painter, only)
            if self.free_transform:
                self._draw_free_transform_frame(painter, only)
            self._draw_rotate_handle(painter, only)

        if self._marquee:
            m = self._marquee
            s0 = self.world_to_screen(m.x(), m.y())
            s1 = self.world_to_screen(m.x() + m.width(), m.y() + m.height())
            r = QRectF(s0[0], s0[1], s1[0] - s0[0], s1[1] - s0[1]).normalized()
            pen = QPen(QColor(self.theme_accent))
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.fillRect(r, QColor(0, 255, 180, 25))
            painter.drawRect(r)

        for g in self._smart_guides:
            pen = QPen(QColor("#ff5a5a"))
            pen.setWidthF(1)
            painter.setPen(pen)
            if g[0] == "v":
                sx = g[1] * self.zoom + self.pan_x
                painter.drawLine(int(sx), 0, int(sx), self.height())
            else:
                sy = g[1] * self.zoom + self.pan_y
                painter.drawLine(0, int(sy), self.width(), int(sy))

        if self._group_rotate:
            self._draw_group_arrows(painter)

        self._draw_rails(painter)
        self._draw_guide_readout(painter)
        from core import legend
        legend.draw_overlay(painter, self.rect())
        painter.end()

    def _canvas_middle_lines(self):
        """World x / y of the canvas middle when centerlines are on (snap targets)."""
        if not self.project or not getattr(self.project, "show_centerlines", False):
            return [], []
        return [self.project.canvas_w / 2.0], [self.project.canvas_h / 2.0]

    def _centerline_at(self, sx: float, sy: float) -> Optional[str]:
        """Return "v" or "h" when the pointer is on a visible canvas centerline."""
        if not self.project or not getattr(self.project, "show_centerlines", False):
            return None
        rect = self._canvas_rect_screen()
        if not rect.adjusted(-GUIDE_HIT, -GUIDE_HIT, GUIDE_HIT, GUIDE_HIT).contains(
                QPointF(sx, sy)):
            return None
        if abs(sx - rect.center().x()) <= GUIDE_HIT:
            return "v"
        if abs(sy - rect.center().y()) <= GUIDE_HIT:
            return "h"
        return None

    def _draw_canvas_centerlines(self, painter):
        """Vertical and horizontal lines through the middle of the canvas, for
        aligning nodes to the map's center."""
        if not getattr(self.project, "show_centerlines", False):
            return
        rect = self._canvas_rect_screen()
        cx, cy = rect.center().x(), rect.center().y()
        painter.save()
        painter.setClipRect(rect)
        pen = QPen(QColor("#ffb000"))
        pen.setWidthF(1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setOpacity(0.85)
        painter.drawLine(QPointF(round(cx) + 0.5, rect.top()),
                         QPointF(round(cx) + 0.5, rect.bottom()))
        painter.drawLine(QPointF(rect.left(), round(cy) + 0.5),
                         QPointF(rect.right(), round(cy) + 0.5))
        painter.restore()

    def center_selection_on_canvas(self, axis: str = "both"):
        """Move the selection so its bounds are centered on the canvas middle."""
        sel = self.selected_pieces()
        if not sel or not self.project:
            return
        boxes = [self._aabb(p) for p in sel]
        left = min(b[0] for b in boxes); right = max(b[2] for b in boxes)
        top = min(b[1] for b in boxes); bottom = max(b[3] for b in boxes)
        dx = self.project.canvas_w / 2.0 - (left + right) / 2.0 if axis in ("both", "h") else 0.0
        dy = self.project.canvas_h / 2.0 - (top + bottom) / 2.0 if axis in ("both", "v") else 0.0
        self.push_history("Center on canvas")
        for p in sel:
            p.x += dx
            p.y += dy
        self.dirty.emit()
        self.update()

    def _canvas_rect_screen(self) -> QRectF:
        x0, y0 = self.world_to_screen(0, 0)
        return QRectF(x0, y0, self.project.canvas_w * self.zoom,
                      self.project.canvas_h * self.zoom)

    def _draw_pasteboard(self, painter, theme_bg: QColor):
        """Hatched area around the canvas so its edge is unmistakable."""
        painter.save()
        rect = self._canvas_rect_screen()
        shadow = QColor(0, 0, 0, 110)
        for grow, alpha in ((10, 25), (6, 40), (3, 60)):
            shadow.setAlpha(alpha)
            painter.fillRect(rect.adjusted(-grow + 4, -grow + 4, grow + 4, grow + 4), shadow)
        painter.restore()

    def _draw_canvas_veil_and_frame(self, painter, theme_bg: QColor):
        """Dim anything hanging off the canvas (it is clipped in exports) and
        outline the real canvas edge."""
        rect = self._canvas_rect_screen()
        view = QRectF(self.rect())
        veil = QColor(theme_bg)
        veil.setAlpha(170)
        hatch = QColor(self.theme_accent)
        hatch.setAlpha(60)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for part in (QRectF(view.left(), view.top(), view.width(), max(0.0, rect.top() - view.top())),
                     QRectF(view.left(), rect.bottom(), view.width(), max(0.0, view.bottom() - rect.bottom())),
                     QRectF(view.left(), rect.top(), max(0.0, rect.left() - view.left()), rect.height()),
                     QRectF(rect.right(), rect.top(), max(0.0, view.right() - rect.right()), rect.height())):
            if part.width() > 0 and part.height() > 0:
                painter.fillRect(part, veil)
                painter.fillRect(part, QBrush(hatch, Qt.BrushStyle.BDiagPattern))
        pen = QPen(QColor(self.theme_accent), 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        painter.restore()

    def _draw_bg(self, painter):
        """The level's backdrop: a color, a tiled floor texture, or (when it is
        transparent) an editor-only checkerboard."""
        x0, y0 = self.world_to_screen(0, 0)
        w = self.project.canvas_w * self.zoom
        h = self.project.canvas_h * self.zoom
        rect = QRectF(x0, y0, w, h)
        if exporter.backdrop_is_transparent(self.level):
            painter.fillRect(rect, self._checker_brush(x0, y0))
            return
        exporter.draw_backdrop(painter, self.project, self.level, rect, self.zoom,
                               self._cache, origin=(x0, y0))

    def _checker_brush(self, x0: float, y0: float) -> QBrush:
        """Grey checkerboard that marks a transparent backdrop on screen."""
        dark = self.theme_mode != "light"
        key = ("checker", dark)
        tile = getattr(self, "_checker_tiles", {}).get(key)
        if tile is None:
            tile = QPixmap(16, 16)
            tile.fill(QColor("#2b2f36" if dark else "#e4e6ea"))
            tile_painter = QPainter(tile)
            other = QColor("#363b44" if dark else "#cfd3d9")
            tile_painter.fillRect(0, 0, 8, 8, other)
            tile_painter.fillRect(8, 8, 8, 8, other)
            tile_painter.end()
            self._checker_tiles = {**getattr(self, "_checker_tiles", {}), key: tile}
        brush = QBrush(tile)
        brush.setTransform(QTransform.fromTranslate(x0, y0))
        return brush

    def _draw_grid(self, painter, color, opacity):
        """Grid lines are anchored to the map origin (world x/y = 0), so they
        stay put under the nodes while you pan and zoom. Major lines fall on
        every Nth line counted from the origin, not from the screen edge."""
        cell = self.project.cell_size
        if cell <= 0:
            return
        vw, vh = self.width(), self.height()
        canvas_w, canvas_h = self.project.canvas_w, self.project.canvas_h
        wx0, wy0 = self.screen_to_world(0, 0)
        wx1, wy1 = self.screen_to_world(vw, vh)
        # only the part of the map that is on screen
        wx0, wx1 = max(0.0, wx0), min(float(canvas_w), wx1)
        wy0, wy1 = max(0.0, wy0), min(float(canvas_h), wy1)
        if wx1 < wx0 or wy1 < wy0:
            return
        top = self.world_to_screen(0, max(0.0, wy0))[1]
        bottom = self.world_to_screen(0, min(float(canvas_h), wy1))[1]
        left = self.world_to_screen(max(0.0, wx0), 0)[0]
        right = self.world_to_screen(min(float(canvas_w), wx1), 0)[0]

        def crisp(value: float) -> float:
            return math.floor(value) + 0.5       # whole-pixel aligned, no shimmer

        def lines(step: int, pen: QPen, alpha: float):
            painter.setOpacity(alpha)
            painter.setPen(pen)
            first_x = math.ceil(wx0 / step) * step
            first_y = math.ceil(wy0 / step) * step
            batch = []
            gx = first_x
            while gx <= wx1 + 1e-6:
                sx = crisp(self.world_to_screen(gx, 0)[0])
                batch.append(QLineF(sx, top, sx, bottom))
                gx += step
            gy = first_y
            while gy <= wy1 + 1e-6:
                sy = crisp(self.world_to_screen(0, gy)[1])
                batch.append(QLineF(left, sy, right, sy))
                gy += step
            if batch:
                painter.drawLines(batch)

        minor = QPen(QColor(color))
        minor.setCosmetic(True)
        minor.setWidthF(1)
        if self.project.grid_style == "dashed":
            minor.setStyle(Qt.PenStyle.DashLine)
        elif self.project.grid_style == "dotted":
            minor.setStyle(Qt.PenStyle.DotLine)
        # very dense grids (tiny cells at low zoom) would be a solid wash
        if cell * self.zoom >= 3:
            lines(cell, minor, opacity * 0.6)
        major = QPen(QColor(color))
        major.setCosmetic(True)
        major.setWidthF(max(2.0, min(4.0, self.zoom * 1.4)))
        major_step = cell * max(1, self.project.grid_major)
        if major_step * self.zoom >= 6:
            lines(major_step, major, opacity)

    def _draw_cell_highlight(self, painter):
        """Calibrated landing indicator.

        - While a piece is selected/hovered we light up the cell where the
          piece's top-left will actually land after grid snap (not the raw
          cursor cell — that was the old offset bug).
        - Otherwise light the cell the cursor is *inside* (floor, not round).
        """
        if not self.project or self._cursor_world[0] < 0:
            return
        cell = max(1, self.project.cell_size)
        wx, wy = self._cursor_world
        target = None
        # landing box of a piece being dragged in from the library / OS
        if self._drop_target is not None:
            dw, dh = self._drop_target
            target = (*self._drop_origin(wx, wy, dw, dh, True), dw, dh)
        elif self._drag and self._drag.get("mode") == "move":
            prim = self._drag.get("primary")
            if prim is not None and prim.id in self.selection:
                # during a move prim.x/y already hold the final (snapped or
                # guide-aligned) position — show exactly that
                target = (prim.x, prim.y, prim.vis_w, prim.vis_h)
        if target is not None:
            # filled cell(s) where the piece will land + footprint outline
            tx, ty, tw, th = target
            x0 = math.floor(tx / cell) * cell
            y0 = math.floor(ty / cell) * cell
            x1 = math.floor((tx + tw - 0.001) / cell) * cell
            y1 = math.floor((ty + th - 0.001) / cell) * cell
            s0 = self.world_to_screen(x0, y0)
            r = QRectF(s0[0], s0[1],
                       (x1 - x0 + cell) * self.zoom,
                       (y1 - y0 + cell) * self.zoom)
            fill = QColor(self.theme_accent)
            fill.setAlpha(46)
            painter.setOpacity(1.0)
            painter.fillRect(r, fill)
            pen = QPen(QColor(self.theme_accent))
            pen.setWidthF(2)
            painter.setPen(pen)
            painter.drawRect(r)
            # footprint of the piece itself
            f0 = self.world_to_screen(tx, ty)
            fr = QRectF(f0[0], f0[1], tw * self.zoom, th * self.zoom)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(fr)
            return
        # plain cursor cell: floor() = cell actually under the cursor
        cx = math.floor(wx / cell) * cell
        cy = math.floor(wy / cell) * cell
        s0 = self.world_to_screen(cx, cy)
        r = QRectF(s0[0], s0[1], cell * self.zoom, cell * self.zoom)
        pen = QPen(QColor(self.theme_accent))
        pen.setWidthF(2)
        painter.setOpacity(0.8)
        painter.setPen(pen)
        painter.drawRect(r)
        painter.setOpacity(1.0)

    def _draw_reference(self, painter):
        idx = self.level_index + self.ref_offset
        if not (0 <= idx < len(self.project.levels)):
            return
        lvl = self.project.levels[idx]
        for p in lvl.paint_order():
            # the ghosted floor is drawn at the reference opacity
            self._draw_piece(painter, p, self.ref_opacity, mark_missing=False)

    def _draw_piece(self, painter, p: Piece, opacity: float, mark_missing: bool = True):
        pm = self.pixmap(p, exporter.source_target_size(p, self.zoom))
        cx, cy = p.center
        scx, scy = self.world_to_screen(cx, cy)
        lyr = self.level.layer_by_id(p.layer)
        if lyr is None and self.project:
            lyr = next((layer for level in self.project.levels
                        for layer in level.layers if layer.id == p.layer), None)
        lop = lyr.opacity if lyr else 1.0
        painter.save()
        painter.translate(scx, scy)
        painter.rotate(p.rotation)
        sx = self.zoom * p.scale * (-1 if p.flip_h else 1)
        sy = self.zoom * p.scale * (-1 if p.flip_v else 1)
        painter.scale(sx, sy)
        painter.setOpacity(max(0.0, min(1.0, opacity)) * lop * p.opacity)
        draw_piece(painter, p, pm, self.project)
        if self.project.show_node_borders:
            draw_node_border(painter, p, pm, self.project)
        painter.restore()
        is_image = not (p.is_text or p.is_patch or p.is_scale_bar or p.is_connector)
        if (mark_missing and is_image and (p.asset_path or p.embedded)
                and (pm is None or pm.isNull())):
            self._draw_missing_marker(painter, p)

    def _draw_missing_marker(self, painter, p: Piece):
        """Editor-only hint over an image that can't be found (exports just show
        the grey box): a red dashed outline and the file name."""
        corners = self._piece_corners_screen(p)
        painter.save()
        pen = QPen(QColor("#ff4d4d"))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in corners]))
        xs = [x for x, _y in corners]
        ys = [y for _x, y in corners]
        box = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        if box.width() >= 40 and box.height() >= 18:
            name = os.path.basename(p.asset_path.replace("\\", "/")) or p.name
            font = QFont(painter.font())
            font.setPixelSize(11)
            painter.setFont(font)
            metrics = QFontMetricsF(font)
            text = metrics.elidedText(f"Missing: {name}", Qt.TextElideMode.ElideMiddle,
                                      max(30.0, box.width() - 8))
            label = QRectF(box.center().x() - (metrics.horizontalAdvance(text) + 10) / 2,
                           box.center().y() - 9, metrics.horizontalAdvance(text) + 10, 18)
            painter.fillRect(label, QColor(20, 6, 6, 200))
            painter.setPen(QColor("#ffb3b3"))
            painter.drawText(label, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _draw_patch_tool_preview(self, painter):
        if not self._patch_preview:
            return
        x0, y0, x1, y1 = self._patch_preview
        s0 = self.world_to_screen(x0, y0)
        s1 = self.world_to_screen(x1, y1)
        rect = QRectF(s0[0], s0[1], s1[0] - s0[0], s1[1] - s0[1]).normalized()
        color = QColor(self.theme_accent)
        fill = QColor(color); fill.setAlpha(35)
        pen = QPen(color); pen.setStyle(Qt.PenStyle.DashLine); pen.setWidthF(2)
        painter.setPen(pen)
        painter.setBrush(QBrush(fill))
        painter.drawRect(rect)

    def _fraction_to_screen(self, piece: Piece, fx: float, fy: float):
        cx, cy = piece.center
        lx = (fx - 0.5) * piece.w * piece.scale
        ly = (fy - 0.5) * piece.h * piece.scale
        angle = math.radians(piece.rotation)
        wx = cx + lx * math.cos(angle) - ly * math.sin(angle)
        wy = cy + lx * math.sin(angle) + ly * math.cos(angle)
        return self.world_to_screen(wx, wy)

    def _draw_compose_tool_previews(self, painter):
        accent = QColor(self.theme_accent)
        if self._crop_preview and self._crop_target_id:
            piece = next((item for item in self.level.pieces
                          if item.id == self._crop_target_id), None)
            if piece:
                left, top, right, bottom = self._crop_preview
                points = [self._fraction_to_screen(piece, left, top),
                          self._fraction_to_screen(piece, right, top),
                          self._fraction_to_screen(piece, right, bottom),
                          self._fraction_to_screen(piece, left, bottom)]
                polygon = QPolygonF([QPointF(x, y) for x, y in points])
                fill = QColor(accent); fill.setAlpha(35)
                pen = QPen(accent); pen.setStyle(Qt.PenStyle.DashLine); pen.setWidthF(2)
                painter.setPen(pen); painter.setBrush(QBrush(fill))
                painter.drawPolygon(polygon)

        if self._connector_drag:
            start = self.world_to_screen(*self._connector_drag["start"])
            end = self.world_to_screen(*self._connector_drag["current"])
            pen = QPen(accent); pen.setStyle(Qt.PenStyle.DashLine); pen.setWidthF(2)
            painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(QPointF(*start), QPointF(*end))

        if self._scale_drag:
            start = self.world_to_screen(*self._scale_drag["start"])
            end = self.world_to_screen(*self._scale_drag["current"])
            pen = QPen(accent); pen.setStyle(Qt.PenStyle.DashLine); pen.setWidthF(2)
            painter.setPen(pen); painter.drawLine(QPointF(*start), QPointF(*end))

        points = self._lasso_points
        if len(points) >= 2:
            pen = QPen(accent); pen.setStyle(Qt.PenStyle.DashLine); pen.setWidthF(2)
            painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(QPolygonF([
                QPointF(*self.world_to_screen(x, y)) for x, y in points]))

        ruler = self._ruler_drag
        if ruler:
            start, end = ruler["start"], ruler["current"]
        else:
            start, end = self._ruler_result or (None, None)
        if start is not None and end is not None:
            a = self.world_to_screen(*start)
            b = self.world_to_screen(*end)
            pen = QPen(accent); pen.setWidthF(2)
            painter.setPen(pen)
            painter.drawLine(QPointF(*a), QPointF(*b))
            painter.setBrush(accent)
            painter.drawEllipse(QPointF(*a), 4, 4)
            painter.drawEllipse(QPointF(*b), 4, 4)
            distance_px = math.dist(start, end)
            feet = distance_px / max(1, self.project.cell_size) * max(
                1, self.project.feet_per_square)
            label = f"{distance_px:.1f} px  ·  {feet:g} ft"
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            rect = QRectF(mx - 95, my - 26, 190, 22)
            painter.fillRect(rect, QColor(10, 14, 20, 210))
            painter.setPen(accent)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_zone_tool_preview(self, painter):
        if self._zone_preview:
            x0, y0, x1, y1 = self._zone_preview
            points = [self.world_to_screen(x0, y0),
                      self.world_to_screen(x1, y0),
                      self.world_to_screen(x1, y1),
                      self.world_to_screen(x0, y1)]
            polygon = QPolygonF([QPointF(x, y) for x, y in points])
            color = QColor(self.theme_accent)
            fill = QColor(color); fill.setAlpha(28)
            pen = QPen(color); pen.setStyle(Qt.PenStyle.DashLine); pen.setWidthF(2)
            painter.setPen(pen); painter.setBrush(QBrush(fill))
            painter.drawPolygon(polygon)
        if self.zone_tool == "polygon" and self._zone_polygon_points:
            points = [QPointF(*self.world_to_screen(x, y))
                      for x, y in self._zone_polygon_points]
            wx, wy = self._cursor_world
            if wx >= 0:
                points.append(QPointF(*self.world_to_screen(wx, wy)))
            color = QColor(self.theme_accent)
            pen = QPen(color); pen.setStyle(Qt.PenStyle.DashLine); pen.setWidthF(2)
            painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(QPolygonF(points))
            painter.setBrush(QBrush(color))
            for point in points[:len(self._zone_polygon_points)]:
                painter.drawEllipse(point, 4, 4)

    def _draw_zone_edit_overlay(self, painter, zone):
        if not zone.points:
            return
        points = [QPointF(*self.world_to_screen(x, y)) for x, y in zone.points]
        color = QColor(self.theme_accent)
        pen = QPen(color); pen.setStyle(Qt.PenStyle.DashLine); pen.setWidthF(2)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if len(points) >= 3:
            painter.drawPolygon(QPolygonF(points))
        elif len(points) == 2:
            painter.drawLine(points[0], points[1])
        painter.setPen(QPen(QColor("#10141c"), 1))
        painter.setBrush(QBrush(color))
        for point in points:
            painter.drawEllipse(point, 6, 6)
        painter.restore()

    def _draw_selection(self, painter, p: Piece):
        cx, cy = p.center
        scx, scy = self.world_to_screen(cx, cy)
        w = p.w * self.zoom * p.scale
        h = p.h * self.zoom * p.scale
        painter.save()
        painter.translate(scx, scy)
        painter.rotate(p.rotation)
        pen = QPen(QColor(self.theme_accent))
        pen.setWidthF(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(-w / 2, -h / 2, w, h))
        painter.restore()

    def _draw_rotate_handle(self, painter, p: Piece):
        hp = self._rotate_handle_screen(p)
        painter.setPen(QPen(QColor(self.theme_accent), 2))
        painter.setBrush(QBrush(QColor(self.theme_accent)))
        scx, scy = self.world_to_screen(*p.center)
        ang = math.radians(p.rotation)
        half_h = p.vis_h / 2.0 * self.zoom
        # stem starts exactly on the middle of the box's top edge
        edge = QPointF(scx + math.sin(ang) * half_h, scy - math.cos(ang) * half_h)
        painter.drawLine(edge, hp)
        painter.drawEllipse(hp, HANDLE_R, HANDLE_R)

    def _draw_group_arrows(self, painter):
        sel = self.selected_pieces()
        if not sel:
            return
        cx = sum(p.center[0] for p in sel) / len(sel)
        cy = sum(p.center[1] for p in sel) / len(sel)
        sx, sy = self.world_to_screen(cx, cy)
        r = 38
        painter.setPen(QPen(QColor(self.theme_accent), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(int(sx - r), int(sy - r), r * 2, r * 2, 30 * 16, 300 * 16)

    # ------------------------------------------------------------------
    def _piece_at_screen(self, sx, sy):
        wx, wy = self.screen_to_world(sx, sy)
        return self.level.selectable_at(wx, wy) if self.level else None

    def _tool_press(self, sx, sy, event):
        if self.cutout_tool:
            return self._cutout_press(sx, sy, event)
        if self.clone_tool:
            return self._clone_press(sx, sy, event)
        wx, wy = self.screen_to_world(sx, sy)
        left = event.button() == Qt.MouseButton.LeftButton
        right = event.button() == Qt.MouseButton.RightButton
        snap = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        # grid with Shift, otherwise pulled onto a nearby guide (Alt = free)
        pwx, pwy = self._snap_tool_point(wx, wy, event.modifiers())
        if snap:
            wx = snap_value(wx, self.project.cell_size)
            wy = snap_value(wy, self.project.cell_size)
        if self.stamp_tool:
            if right:
                self.cancel_extra_tool()
            elif left:
                self._place_stamp(pwx, pwy)
                self._stamp_drag_last = (pwx, pwy)
            else:
                return False
            event.accept()
            return True
        if self.crop_tool:
            if right:
                self.cancel_extra_tool()
            elif left:
                piece = next((p for p in self.level.pieces
                              if p.id == self._crop_target_id), None)
                if piece:
                    fx, fy = self._piece_local_fraction(piece, pwx, pwy)
                    if 0 <= fx <= 1 and 0 <= fy <= 1:
                        self._crop_drag = {"start": (fx, fy), "current": (fx, fy)}
                        self._crop_preview = (fx, fy, fx, fy)
                        self.update()
            else:
                return False
            event.accept()
            return True
        if self.copy_style_mode:
            if right:
                self.cancel_extra_tool()
            elif left:
                target = self._piece_at_screen(sx, sy)
                if target:
                    self._apply_copied_style(target)
            else:
                return False
            event.accept()
            return True
        if self.ruler_tool:
            if right:
                self.cancel_extra_tool()
            elif left:
                self._ruler_result = None
                self._ruler_drag = {"start": (pwx, pwy), "current": (pwx, pwy),
                                    "snap": snap}
                self.update()
            else:
                return False
            event.accept()
            return True
        if self.scale_tool:
            if right:
                self.cancel_extra_tool()
            elif left:
                self._scale_drag = {"start": (pwx, pwy), "current": (pwx, pwy),
                                    "snap": snap}
                self.update()
            else:
                return False
            event.accept()
            return True
        if self.connector_tool:
            if right:
                self.cancel_extra_tool()
            elif left:
                self._connector_drag = {"start": (pwx, pwy), "current": (pwx, pwy),
                                        "snap": snap}
                self.update()
            else:
                return False
            event.accept()
            return True
        if self.lasso_tool:
            if right:
                self.cancel_extra_tool()
            elif left:
                self._lasso_points = [(wx, wy)]
                self._lasso_add = bool(event.modifiers()
                                        & Qt.KeyboardModifier.ShiftModifier)
                self.update()
            else:
                return False
            event.accept()
            return True
        return False

    def _tool_move(self, sx, sy, event):
        wx, wy = self.screen_to_world(sx, sy)
        self._cursor_world = (wx, wy)
        if self.cutout_tool and self._cutout_motion(sx, sy, event):
            return True
        if self.clone_tool and self._clone_motion(sx, sy, event):
            return True
        if self.stamp_tool and self._stamp_drag_last is not None:
            if self._stamp_edge:
                self._place_stamp(wx, wy)      # skips spots already filled
                self.update()
                return True
            lx, ly = self._stamp_drag_last
            template = Piece.from_dict(self._stamp_template or {})
            spacing = max(8.0, min(template.vis_w, template.vis_h) * 0.75)
            if math.dist((wx, wy), (lx, ly)) >= spacing:
                self._place_stamp(wx, wy)
                self._stamp_drag_last = (wx, wy)
            self.update()
            return True
        if self._crop_drag:
            piece = next((p for p in self.level.pieces
                          if p.id == self._crop_target_id), None)
            if piece:
                fx, fy = self._piece_local_fraction(
                    piece, *self._snap_tool_point(wx, wy, event.modifiers()))
                fx, fy = self._clamp_crop_fraction(fx), self._clamp_crop_fraction(fy)
                self._crop_drag["current"] = (fx, fy)
                x0, y0 = self._crop_drag["start"]
                self._crop_preview = (min(x0, fx), min(y0, fy),
                                      max(x0, fx), max(y0, fy))
            self.cursorMoved.emit(wx, wy)
            self.update()
            return True
        for drag in (self._ruler_drag, self._scale_drag, self._connector_drag):
            if drag is not None:
                wx, wy = self._snap_tool_point(wx, wy, event.modifiers(),
                                               grid=bool(drag.get("snap")))
                drag["current"] = (wx, wy)
                self.cursorMoved.emit(wx, wy)
                self.update()
                return True
        if self.lasso_tool and self._lasso_points:
            last = self._lasso_points[-1]
            if math.dist((wx, wy), last) >= max(2.0, 4.0 / max(self.zoom, 1e-6)):
                self._lasso_points.append((wx, wy))
            self.cursorMoved.emit(wx, wy)
            self.update()
            return True
        return False

    @staticmethod
    def _point_in_polygon(point, polygon):
        x, y = point
        inside = False
        j = len(polygon) - 1
        for i, (xi, yi) in enumerate(polygon):
            xj, yj = polygon[j]
            if ((yi > y) != (yj > y)
                    and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
                inside = not inside
            j = i
        return inside

    def _tool_release(self, sx, sy, event):
        if self.cutout_tool and self._cutout_release(sx, sy, event):
            return True
        if self.clone_tool and self._clone_release(sx, sy, event):
            return True
        wx, wy = self.screen_to_world(sx, sy)
        if self.stamp_tool and self._stamp_drag_last is not None:
            self._stamp_drag_last = None
            self._stamp_last_spot = None
            event.accept()
            return True
        if self._crop_drag:
            drag = self._crop_drag
            self._crop_drag = None
            self._crop_preview = None
            piece = next((p for p in self.level.pieces
                          if p.id == self._crop_target_id), None)
            if piece:
                fx, fy = self._piece_local_fraction(
                    piece, *self._snap_tool_point(wx, wy, event.modifiers()))
                x0, y0 = drag["start"]
                if self._apply_crop(piece, (x0, y0, fx, fy)):
                    self.set_crop_tool(False)
                else:
                    self.update()
            event.accept()
            return True
        if self._ruler_drag:
            drag = self._ruler_drag
            wx, wy = self._snap_tool_point(wx, wy, event.modifiers(),
                                           grid=bool(drag.get("snap")))
            self._ruler_result = (drag["start"], (wx, wy))
            self._ruler_drag = None
            self.update()
            event.accept()
            return True
        if self._scale_drag:
            drag = self._scale_drag
            wx, wy = self._snap_tool_point(wx, wy, event.modifiers(),
                                           grid=bool(drag.get("snap")))
            start = drag["start"]
            self._scale_drag = None
            self._create_scale_bar(start, (wx, wy))
            self.set_scale_tool(False)
            event.accept()
            return True
        if self._connector_drag:
            drag = self._connector_drag
            wx, wy = self._snap_tool_point(wx, wy, event.modifiers(),
                                           grid=bool(drag.get("snap")))
            start = drag["start"]
            self._connector_drag = None
            if math.dist(start, (wx, wy)) >= 4:
                self._create_connector(start, (wx, wy))
            self.set_connector_tool(False)
            event.accept()
            return True
        if self.lasso_tool and self._lasso_points:
            points = list(self._lasso_points)
            if math.dist(points[-1], (wx, wy)) >= 1.0:
                points.append((wx, wy))
            if len(points) >= 3:
                hits = [piece for piece in self.level.pieces
                        if not piece.locked and self._point_in_polygon(piece.center, points)]
                hits = self._with_groups(hits)
                if not self._lasso_add:
                    self.select(hits)
                else:
                    self.select(list({p.id: p for p in
                                      self.selected_pieces() + hits}.values()))
            self.set_lasso_tool(False)
            event.accept()
            return True
        return False

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self.cancel_guide_drag():
            e.accept()
            return
        if self.handle_cutout_key(e) or self.handle_clone_key(e):
            e.accept()
            return
        if e.key() == Qt.Key.Key_Escape:
            if self.end_free_transform(False):
                e.accept()
                return
            if (self.cancel_color_pick() or self.cancel_zone_tool()
                    or self.cancel_patch_tool() or self.cancel_extra_tool()):
                e.accept()
                return
        if (e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and self.end_free_transform(True)):
            e.accept()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.zone_tool == "polygon":
            self.finish_zone_polygon()
            e.accept()
            return
        if e.key() == Qt.Key.Key_Delete and self.selected_zone:
            self.delete_selected_zone()
            e.accept()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if not self.project or not self.level:
            return
        sx, sy = e.position().x(), e.position().y()
        if self._guide_drag is not None:
            e.accept()               # finish the guide drag before anything else
            return
        if (e.button() == Qt.MouseButton.LeftButton
                and self._color_pick_callback is None):
            side = self._rail_at(sx, sy)
            if side:
                self._begin_guide_drag(sx, sy, self._rail_axis(side))
                e.accept()
                return
        if self._color_pick_callback is not None:
            if e.button() in (Qt.MouseButton.RightButton,
                              Qt.MouseButton.MiddleButton):
                self.cancel_color_pick()
                e.accept()
                return
            if e.button() == Qt.MouseButton.LeftButton:
                color = self._sample_color_at_screen(sx, sy)
                # Clicking outside the map frame leaves the eyedropper armed.
                if color.isValid():
                    self._finish_color_pick(color)
                e.accept()
                return

        if self._tool_press(sx, sy, e):
            return

        if self.patch_tool:
            if e.button() == Qt.MouseButton.RightButton:
                self.cancel_patch_tool()
                e.accept()
                return
            if e.button() == Qt.MouseButton.LeftButton:
                wx, wy = self._snap_tool_point(*self.screen_to_world(sx, sy), e.modifiers())
                self._cursor_world = (wx, wy)
                self._patch_drag_start = (wx, wy)
                self._patch_preview = (wx, wy, wx, wy)
                self.update()
                e.accept()
                return

        if self.zone_tool:
            if e.button() == Qt.MouseButton.RightButton:
                if self.zone_tool == "polygon":
                    self.finish_zone_polygon()
                else:
                    self.cancel_zone_tool()
                e.accept()
                return
            if e.button() == Qt.MouseButton.LeftButton:
                wx, wy = self._snap_tool_point(*self.screen_to_world(sx, sy), e.modifiers())
                self._cursor_world = (wx, wy)
                if self.zone_tool == "rectangle":
                    self._zone_drag_start = (wx, wy)
                    self._zone_preview = (wx, wy, wx, wy)
                else:
                    self._zone_polygon_points.append((wx, wy))
                self.update()
                e.accept()
                return

        if e.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._drag = {"mode": "pan", "last": (sx, sy), "origin": (sx, sy),
                          "moved": False, "button": e.button()}
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return
        if self._group_rotate:
            self._begin_group_rotate(sx, sy)
            return

        wx, wy = self.screen_to_world(sx, sy)
        zone = self.selected_zone
        if zone:
            vertex = self._zone_vertex_at_screen(zone, sx, sy)
            if vertex is not None:
                self._zone_edit_drag = {
                    "kind": "vertex", "zone": zone, "index": vertex,
                    "start": (wx, wy), "points": list(zone.points),
                    "history": False, "changed": False,
                }
                return
        zone_edge = self._zone_near_edge(sx, sy)
        if zone_edge:
            self.select_zone(zone_edge.id)
            self._zone_edit_drag = {
                "kind": "move", "zone": zone_edge, "start": (wx, wy),
                "points": list(zone_edge.points), "history": False,
                "changed": False,
            }
            return

        if e.button() == Qt.MouseButton.LeftButton:
            handle = self._resize_handle_at(sx, sy)
            if handle:
                self._begin_resize(handle)
                return
        if self.selection and len(self.selection) == 1:
            hp = self._rotate_handle_screen(next(iter(self.selected_pieces())))
            if (QPointF(sx, sy) - hp).manhattanLength() <= HANDLE_R + 5:
                self._drag = {"mode": "rotate", "center": next(iter(self.selected_pieces())).center}
                self.push_history("Rotate")
                return
        if e.button() == Qt.MouseButton.LeftButton and not self._any_tool_active():
            guide = self._guide_at(sx, sy)
            if guide is not None:
                self._begin_guide_drag(sx, sy, guide.axis, guide)
                return
        hit = self._piece_at_screen(sx, sy)
        mods = e.modifiers()
        if hit:
            # grouped nodes are picked together; Ctrl+click picks just one
            single = bool(mods & Qt.KeyboardModifier.ControlModifier)
            members = [hit] if single else self._with_groups([hit])
            if mods & Qt.KeyboardModifier.ShiftModifier:
                if hit.id in self.selection:
                    for member in members:
                        self.selection.discard(member.id)
                else:
                    for member in members:
                        self.selection.add(member.id)
                self.select(self.selected_pieces())
                self.update()
                return
            if single:
                if self.selection != {hit.id}:
                    self.select([hit])
            elif hit.id not in self.selection:
                self.select(members)
            self._start_move(sx, sy, hit)
        else:
            zone = next((item for item in reversed(self.level.zones)
                         if item.contains(wx, wy)), None)
            if zone:
                self.select_zone(zone.id)
                self._zone_edit_drag = {
                    "kind": "move", "zone": zone, "start": (wx, wy),
                    "points": list(zone.points), "history": False,
                    "changed": False,
                }
                return
            if not (mods & Qt.KeyboardModifier.ShiftModifier):
                self.clear_selection()
            self._drag = {"mode": "marquee", "start": (sx, sy), "add": bool(mods & Qt.KeyboardModifier.ShiftModifier)}
            self._marquee = QRectF(*self.screen_to_world(sx, sy), 0, 0)

    def _start_move(self, sx, sy, hit: Optional[Piece] = None):
        self.push_history("Move")
        wx, wy = self.screen_to_world(sx, sy)
        self._cursor_world = (wx, wy)
        offs = {}
        for p in self.selected_pieces():
            offs[p.id] = (wx - p.x, wy - p.y)
        # the piece actually grabbed drives the snap targets
        primary = hit if (hit is not None and hit.id in self.selection) \
            else self._primary()
        self._drag = {"mode": "move", "offs": offs, "primary": primary,
                      "origin": (wx, wy)}

    def _primary(self) -> Optional[Piece]:
        sel = self.selected_pieces()
        if not sel:
            return None
        # pick the piece under the cursor when possible; fallback first
        if self._cursor_world[0] >= 0:
            for p in reversed(sel):
                if p.hit_test(*self._cursor_world):
                    return p
        return sel[0]

    def mouseMoveEvent(self, e):
        sx, sy = e.position().x(), e.position().y()
        if not self.project or not self.level:
            return
        wx, wy = self.screen_to_world(sx, sy)
        self._cursor_world = (wx, wy)
        if self._guide_drag is not None:
            self._update_guide_drag(sx, sy, e.modifiers())
            self.cursorMoved.emit(wx, wy)
            return
        if self.stamp_tool:
            self._stamp_hover = self._snap_tool_point(wx, wy, e.modifiers())
            self.update()
        if self._tool_move(sx, sy, e):
            return
        if self._patch_drag_start is not None and self.patch_tool:
            x0, y0 = self._patch_drag_start
            wx, wy = self._snap_tool_point(wx, wy, e.modifiers())
            self._patch_preview = (min(x0, wx), min(y0, wy),
                                   max(x0, wx), max(y0, wy))
            self.cursorMoved.emit(wx, wy)
            self.update()
            return
        if self._zone_drag_start is not None and self.zone_tool == "rectangle":
            x0, y0 = self._zone_drag_start
            wx, wy = self._snap_tool_point(wx, wy, e.modifiers())
            self._zone_preview = (min(x0, wx), min(y0, wy),
                                  max(x0, wx), max(y0, wy))
            self.cursorMoved.emit(wx, wy)
            self.update()
            return
        if self._zone_edit_drag:
            edit = self._zone_edit_drag
            dx, dy = wx - edit["start"][0], wy - edit["start"][1]
            if abs(dx) + abs(dy) > 1e-6:
                if not edit["history"]:
                    self.push_history("Adjust zone geometry")
                    edit["history"] = True
                zone = edit["zone"]
                if edit["kind"] == "vertex":
                    points = list(edit["points"])
                    points[edit["index"]] = self._snap_tool_point(wx, wy, e.modifiers())
                    zone.points = points
                else:
                    zone.points = [(x + dx, y + dy)
                                   for x, y in edit["points"]]
                edit["changed"] = True
                self.update()
            self.cursorMoved.emit(wx, wy)
            return
        if self._drag is None:
            if self._update_guide_hover(sx, sy):
                self.cursorMoved.emit(wx, wy)
                return
            if self.selection and len(self.selection) == 1:
                hp = self._rotate_handle_screen(next(iter(self.selected_pieces())))
                self._hover_handle = (QPointF(sx, sy) - hp).manhattanLength() <= HANDLE_R + 5
                handle = None if self._any_tool_active() else self._resize_handle_at(sx, sy)
                if handle:
                    self.setCursor(QCursor(self._resize_cursor(
                        handle, next(iter(self.selected_pieces())).rotation)))
                elif not self._any_tool_active():
                    self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self.cursorMoved.emit(wx, wy)
            if self.zone_tool == "polygon" and self._zone_polygon_points:
                self.update()
            return
        mode = self._drag["mode"]
        if mode == "pan":
            lx, ly = self._drag["last"]
            self.pan_x += sx - lx
            self.pan_y += sy - ly
            self._drag["last"] = (sx, sy)
            ox, oy = self._drag["origin"]
            if abs(sx - ox) + abs(sy - oy) > 4:
                self._drag["moved"] = True
            self.update()
            self.viewChanged.emit()
        elif mode == "move":
            self._move_selected(sx, sy, e.modifiers())
            self.update()
        elif mode == "rotate":
            self._rotate_primary(sx, sy, e)
            self.update()
        elif mode == "resize":
            self._resize_primary(sx, sy, e)
            self.update()
        elif mode == "marquee":
            x0, y0 = self._drag["start"]
            r = QRectF(min(x0, sx), min(y0, sy), abs(sx - x0), abs(sy - y0))
            self._marquee = QRectF(*self.screen_to_world(r.x(), r.y()),
                                  r.width() / self.zoom, r.height() / self.zoom)
            self.update()
        elif mode == "group_rotate":
            self._apply_group_rotate(sx, sy, e.modifiers())
            self.update()
        wx, wy = self.screen_to_world(sx, sy)
        self._cursor_world = (wx, wy)
        self.cursorMoved.emit(wx, wy)

    def _move_selected(self, sx, sy, mods=Qt.KeyboardModifier.NoModifier):
        """Move the selection rigidly; the primary picks the nearest target
        (grid line, another piece's edge, a centerline or a placed guide) and
        everything follows it. Hold Alt to place freely without snapping."""
        wx, wy = self.screen_to_world(sx, sy)
        offs = self._drag["offs"]
        sel = self.selected_pieces()
        if not sel:
            return
        primary = self._drag.get("primary")
        if primary is None or primary.id not in offs:
            primary = self._primary()
            if primary is None or primary.id not in offs:
                primary = sel[0]
        cell = max(1, self.project.cell_size)
        raw_x = wx - offs[primary.id][0]
        raw_y = wy - offs[primary.id][1]
        self._guide_flash = set()
        if mods & Qt.KeyboardModifier.AltModifier:
            dx, dy = raw_x - primary.x, raw_y - primary.y
            for p in sel:
                p.x += dx
                p.y += dy
            self._smart_guides = []
            return

        # gather other pieces' visual edges (x and y), rotation-aware
        thr = GUIDE_DIST / max(self.zoom, 1e-6)
        x_offs, y_offs = self._edge_offsets(primary)
        x_edges, y_edges = [], []
        for o in self.level.pieces:
            if o.id == primary.id or o.id in self.selection:
                continue
            bx0, by0, bx1, by1 = self._aabb(o)
            x_edges += [bx0, (bx0 + bx1) / 2.0, bx1]
            y_edges += [by0, (by0 + by1) / 2.0, by1]

        grid = cell if primary.snap else None
        mid_x, mid_y = self._canvas_middle_lines()
        x_edges += mid_x
        y_edges += mid_y
        # placed guides go last so they win ties against neighbors and the grid
        guide_xs, guide_ys = self._guide_positions("v"), self._guide_positions("h")
        x_edges += guide_xs
        y_edges += guide_ys
        tgt_x, guide_x = self._nearest_target(raw_x, x_offs, x_edges, thr, grid)
        tgt_y, guide_y = self._nearest_target(raw_y, y_offs, y_edges, thr, grid)

        # rigid delta from the primary, applied to the whole selection
        dx = tgt_x - primary.x
        dy = tgt_y - primary.y
        for p in sel:
            p.x += dx
            p.y += dy

        self._smart_guides = []
        for axis, line, placed in (("v", guide_x, guide_xs), ("h", guide_y, guide_ys)):
            if line is None:
                continue
            if line in placed:
                self._flash_guides(axis, line)      # the guide itself lights up
            else:
                self._smart_guides.append((axis, line))

    @staticmethod
    def _aabb(p: Piece) -> tuple[float, float, float, float]:
        """Axis-aligned bounds of a piece on screen-aligned world axes."""
        cx, cy = p.center
        ang = math.radians(p.rotation)
        ca, sa = abs(math.cos(ang)), abs(math.sin(ang))
        hw, hh = p.vis_w / 2.0, p.vis_h / 2.0
        ex, ey = ca * hw + sa * hh, sa * hw + ca * hh
        return cx - ex, cy - ey, cx + ex, cy + ey

    @classmethod
    def _edge_offsets(cls, p: Piece):
        """Left/center/right and top/center/bottom edge offsets from p.x / p.y."""
        x0, y0, x1, y1 = cls._aabb(p)
        return ((x0 - p.x, (x0 + x1) / 2.0 - p.x, x1 - p.x),
                (y0 - p.y, (y0 + y1) / 2.0 - p.y, y1 - p.y))

    @staticmethod
    def _nearest_target(raw, offsets, edges, threshold, grid_step):
        """Pick the closest snap target for a dragged edge-set.

        offsets:   this piece's edge positions relative to its x (or y).
        edges:     absolute edge positions of neighboring pieces.
        grid_step: grid size (None when the piece does not snap to the grid).
        Any edge of the piece may land on a grid line or on any neighbor edge
        within ``threshold``; the nearest wins and neighbors win ties. When
        nothing is in range a gridded piece still rounds its origin to the grid.
        Returns (target_for_piece_origin, guide_line_or_None).
        """
        best_d, best_t, best_g = None, raw, None
        if grid_step:
            for off in offsets:
                line = round((raw + off) / grid_step) * grid_step
                t = line - off
                d = abs(t - raw)
                if d <= threshold and (best_d is None or d < best_d - 1e-9):
                    best_d, best_t = d, t
        for off in offsets:
            for e in edges:
                t = e - off                 # origin that puts this edge on e
                d = abs(t - raw)
                if d > threshold:
                    continue
                if best_d is None or d <= best_d + 1e-9:
                    best_d, best_t, best_g = d, t, e
        if best_d is None and grid_step:
            best_t = round(raw / grid_step) * grid_step
        return best_t, best_g

    ROTATE_STEP = 15.0        # degrees between snap stops
    ROTATE_SNAP_RANGE = 5.0   # how close counts as "on" a stop

    def _snap_angle(self, angle: float, mods) -> float:
        """Rotation snapping. Default: pull to the nearest 15° stop (so 0, 45,
        90… are easy to hit) when within a few degrees. Shift: always step by
        15°. Alt: fully free."""
        if mods & Qt.KeyboardModifier.AltModifier:
            return angle % 360
        stop = round(angle / self.ROTATE_STEP) * self.ROTATE_STEP
        if (mods & Qt.KeyboardModifier.ShiftModifier
                or abs(angle - stop) <= self.ROTATE_SNAP_RANGE):
            angle = stop
        return angle % 360

    def _rotate_primary(self, sx, sy, e):
        p = next(iter(self.selected_pieces()))
        cx, cy = p.center
        scx, scy = self.world_to_screen(cx, cy)
        ang = math.degrees(math.atan2(sy - scy, sx - scx)) + 90
        p.rotation = self._snap_angle(ang, e.modifiers())

    def _begin_group_rotate(self, sx, sy):
        self._drag = {"mode": "group_rotate", "center": self._centroid()}

    def _apply_group_rotate(self, sx, sy, mods=Qt.KeyboardModifier.NoModifier):
        sel = self.selected_pieces()
        if not sel:
            return
        cx, cy = self._centroid()
        scx, scy = self.world_to_screen(cx, cy)
        ang = math.degrees(math.atan2(sy - scy, sx - scx)) + 90
        base = getattr(self, "_gr_base", None)
        if base is None:
            self._gr_base = ang
            self._gr_start = {p.id: p.rotation for p in sel}
            return
        delta = self._snap_angle(ang - self._gr_base, mods)
        if delta > 180:
            delta -= 360
        for p in sel:
            p.rotation = (self._gr_start[p.id] + delta) % 360

    def _centroid(self):
        sel = self.selected_pieces()
        if not sel:
            return (0, 0)
        return (sum(p.center[0] for p in sel) / len(sel),
                sum(p.center[1] for p in sel) / len(sel))

    def mouseReleaseEvent(self, e):
        if self._guide_drag is not None:
            if e.button() == Qt.MouseButton.LeftButton:
                self._finish_guide_drag(e.position().x(), e.position().y())
            e.accept()
            return
        if self._tool_release(e.position().x(), e.position().y(), e):
            return
        if self._patch_drag_start is not None:
            x0, y0 = self._patch_drag_start
            wx, wy = self._snap_tool_point(
                *self.screen_to_world(e.position().x(), e.position().y()), e.modifiers())
            self._patch_drag_start = None
            self._patch_preview = None
            if abs(wx - x0) >= 2 and abs(wy - y0) >= 2:
                self.add_patch(min(x0, wx), min(y0, wy),
                               abs(wx - x0), abs(wy - y0))
            else:
                self.update()
            e.accept()
            return
        if self._zone_drag_start is not None:
            x0, y0 = self._zone_drag_start
            wx, wy = self._snap_tool_point(
                *self.screen_to_world(e.position().x(), e.position().y()), e.modifiers())
            self._zone_drag_start = None
            self._zone_preview = None
            if abs(wx - x0) >= 2 and abs(wy - y0) >= 2:
                self._create_zone([
                    (min(x0, wx), min(y0, wy)),
                    (max(x0, wx), min(y0, wy)),
                    (max(x0, wx), max(y0, wy)),
                    (min(x0, wx), max(y0, wy)),
                ])
            else:
                self.update()
            e.accept()
            return
        if self._zone_edit_drag:
            edit = self._zone_edit_drag
            self._zone_edit_drag = None
            if edit["changed"]:
                self.dirty.emit()
            self.update()
            e.accept()
            return
        if (self._drag and self._drag["mode"] == "pan"
                and self._drag.get("button") == Qt.MouseButton.RightButton
                and not self._drag.get("moved")):
            self._drag = None
            self._open_context_menu(e)
            return
        if self._drag and self._drag["mode"] == "resize":
            self._drag = None
            self._smart_guides = []
            self._guide_flash = set()
            self.selectionChanged.emit(self.selected_pieces())   # refresh inspector
            self.dirty.emit()
            self.update()
            return
        if self._drag and self._drag["mode"] in ("move", "rotate", "group_rotate"):
            if self._drag["mode"] == "group_rotate":
                self._group_rotate = False
                self._gr_base = None
            self.dirty.emit()
        if self._drag and self._drag["mode"] == "marquee":
            self._select_marquee(self._drag.get("add", False))
            self._marquee = None
        self._drag = None
        self._smart_guides = []
        self._guide_flash = set()
        if (self.zone_tool or self.patch_tool or self.stamp_tool or self.crop_tool
                or self.ruler_tool or self.scale_tool or self.connector_tool
                or self.lasso_tool or self.copy_style_mode or self.cutout_tool
                or self.clone_tool):
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    def _any_tool_active(self) -> bool:
        return bool(self.zone_tool or self.patch_tool or self.stamp_tool
                    or self.crop_tool or self.ruler_tool or self.scale_tool
                    or self.connector_tool or self.lasso_tool
                    or self.copy_style_mode or self.cutout_tool or self.clone_tool
                    or self._color_pick_callback)

    def _open_context_menu(self, e):
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        sx, sy = e.position().x(), e.position().y()
        side = self._rail_at(sx, sy)
        guide = None if side else self._guide_at(sx, sy, include_locked=True)
        if side or guide:
            self.guideMenuRequested.emit(e.globalPosition().toPoint(),
                                         {"rail": side} if side else {"guide": guide.id})
            return
        hit = self._piece_at_screen(e.position().x(), e.position().y())
        line = self._centerline_at(sx, sy) if hit is None else None
        if line:
            self.guideMenuRequested.emit(e.globalPosition().toPoint(),
                                         {"centerline": line})
            return
        if hit is not None and hit.id not in self.selection:
            self.select(self._with_groups([hit]))
        elif hit is None:
            self.clear_selection()
        self.update()
        self.contextMenuRequested.emit(e.globalPosition().toPoint(), hit)

    def set_quick_enabled(self, on: bool):
        self.quick_enabled = bool(on)
        self._update_quick()

    def set_solo_layer(self, layer_id: str | None):
        self.solo_layer_id = layer_id
        self.layerSoloChanged.emit(layer_id)
        self.update()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._cutout_double_click():
            e.accept()
            return
        if self.zone_tool == "polygon" and e.button() == Qt.MouseButton.LeftButton:
            self.finish_zone_polygon()
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton and not self._any_tool_active():
            guide = self._guide_at(e.position().x(), e.position().y())
            if guide is not None:
                self._guide_drag = None
                self.guideEditRequested.emit(guide.id)
                e.accept()
                return
        super().mouseDoubleClickEvent(e)

    def _select_marquee(self, add):
        if self._marquee is None:
            return
        m = self._marquee
        hits = [p for p in self.level.paint_order()
                if not (p.locked) and m.intersects(
                    QRectF(p.x, p.y, p.w * p.scale, p.h * p.scale))]
        hits = self._with_groups(hits)
        if not add:
            self.selection.clear()
        for p in hits:
            self.selection.add(p.id)
        self._update_quick()
        self.selectionChanged.emit(self.selected_pieces())

    def wheelEvent(self, e):
        if not self.project:
            return
        sx, sy = e.position().x(), e.position().y()
        factor = 1.1 if e.angleDelta().y() > 0 else 1 / 1.1
        self.zoom_at(sx, sy, factor)

    def leaveEvent(self, e):
        self.cursorMoved.emit(-1, -1)
        self._cursor_world = (-1, -1)
        if self._stamp_hover is not None:
            self._stamp_hover = None
            self.update()
        if self._rail_hover or self._guide_hover:
            self._rail_hover = self._guide_hover = None
            self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_quick()
        self.viewChanged.emit()

    # ------------------------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat("application/x-mapbuilder-asset") or e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        """Track the cursor while an asset is dragged over the canvas so the
        landing highlight shows exactly where the piece will drop."""
        if not self.project:
            return
        sx, sy = e.position().x(), e.position().y()
        wx, wy = self.screen_to_world(sx, sy)
        self._cursor_world = (wx, wy)
        md = e.mimeData()
        self._drop_target = None
        if md.hasFormat("application/x-mapbuilder-asset"):
            path = bytes(md.data("application/x-mapbuilder-asset")).decode("utf-8", "ignore")
            asset = self.library.get(path) if self.library else None
            self._drop_target = self.asset_world_size(path, asset)
        elif md.hasUrls():
            self._drop_target = (64, 64)   # unknown size until drop; rough box
        self.update()
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._drop_target = None
        self._cursor_world = (-1.0, -1.0)
        self.cursorMoved.emit(-1, -1)
        self.update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        if not self.project or not self.level:
            return
        self._drop_target = None
        sx, sy = e.position().x(), e.position().y()
        wx, wy = self.screen_to_world(sx, sy)
        md = e.mimeData()
        if md.hasFormat("application/x-mapbuilder-asset"):
            path = bytes(md.data("application/x-mapbuilder-asset")).decode("utf-8", "ignore")
            self.add_asset(path, wx, wy)
            e.acceptProposedAction()
        elif md.hasUrls():
            for u in md.urls():
                fp = u.toLocalFile()
                if fp.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
                    from core.project import embed_png
                    self.add_embedded(embed_png(fp), wx, wy, name=os.path.basename(fp))
                    break
            e.acceptProposedAction()
        self.update()

    # ------------------------------------------------------------------
    def delete_selected(self):
        if not self.level:
            return
        sel = self.selected_pieces()
        if not sel:
            return
        self.push_history("Delete")
        for p in sel:
            self.level.remove(p)
        self.select([])
        self.dirty.emit()

    def duplicate(self):
        if not self.level:
            return
        sel = self.selected_pieces()
        if not sel:
            return
        self.push_history("Duplicate")
        new_ids = []
        from core.project import uuid
        batch = [p.to_dict() for p in sorted(sel, key=lambda piece: piece.z)]
        remap_groups(batch)              # copies form their own groups
        for data in batch:
            np = Piece(**data)
            np.id = uuid.uuid4().hex
            np.x += self.project.cell_size
            np.y += self.project.cell_size
            self.level.add(np)
            new_ids.append(np.id)
        self.selection = set(new_ids)
        self._update_quick()
        self.selectionChanged.emit(self.selected_pieces())
        self.dirty.emit()

    def copy(self):
        self._clipboard = [p.to_dict() for p in self.selected_pieces()]

    def paste(self):
        if not self.level or not self._clipboard:
            return
        self.push_history("Paste")
        new_ids = []
        from core.project import uuid
        import copy as _copy
        batch = _copy.deepcopy(self._clipboard)
        remap_groups(batch)              # pasted copies form their own groups
        for d in batch:
            np = Piece(**d)
            np.id = uuid.uuid4().hex
            np.x += self.project.cell_size
            np.y += self.project.cell_size
            if np.embedded:
                pass  # keep bytes
            self.level.add(np)
            new_ids.append(np.id)
        self.selection = set(new_ids)
        self._update_quick()
        self.selectionChanged.emit(self.selected_pieces())
        self.dirty.emit()

    # align / distribute ---------------------------------------------------
    @staticmethod
    def _shift(p: Piece, dx: float = 0.0, dy: float = 0.0):
        p.x += dx
        p.y += dy

    @staticmethod
    def _boxes_overlap(a, b, eps=0.5) -> bool:
        return (a[0] < b[2] - eps and b[0] < a[2] - eps
                and a[1] < b[3] - eps and b[1] < a[3] - eps)

    def _separate_along(self, pieces: list[Piece], axis: str, order: dict):
        """Push pieces apart along ``axis`` ("x" or "y") until none overlap,
        keeping their previous order so the first stays where it is."""
        placed: list[Piece] = []
        for p in sorted(pieces, key=lambda q: order[q.id]):
            for _ in range(len(pieces) + 1):
                box = self._aabb(p)
                hit = next((q for q in placed
                            if self._boxes_overlap(box, self._aabb(q))), None)
                if hit is None:
                    break
                other = self._aabb(hit)
                if axis == "y":
                    self._shift(p, dy=other[3] - box[1])
                else:
                    self._shift(p, dx=other[2] - box[0])
            placed.append(p)

    def align(self, kind):
        """Align edges/centers. Nodes never end up overlapping unless
        ``allow_overlap`` is on: those that would collide are stacked along
        the other axis instead."""
        sel = self.selected_pieces()
        if len(sel) < 2:
            self.statusMessage.emit("Select at least 2 nodes to align.")
            return
        self.push_history(f"Align {kind}")
        boxes = {p.id: self._aabb(p) for p in sel}
        if kind == "left":
            m = min(b[0] for b in boxes.values())
            for p in sel:
                self._shift(p, dx=m - boxes[p.id][0])
        elif kind == "right":
            m = max(b[2] for b in boxes.values())
            for p in sel:
                self._shift(p, dx=m - boxes[p.id][2])
        elif kind == "top":
            m = min(b[1] for b in boxes.values())
            for p in sel:
                self._shift(p, dy=m - boxes[p.id][1])
        elif kind == "bottom":
            m = max(b[3] for b in boxes.values())
            for p in sel:
                self._shift(p, dy=m - boxes[p.id][3])
        elif kind == "hcenter":
            c = sum((b[0] + b[2]) / 2 for b in boxes.values()) / len(sel)
            for p in sel:
                self._shift(p, dx=c - (boxes[p.id][0] + boxes[p.id][2]) / 2)
        elif kind == "vcenter":
            c = sum((b[1] + b[3]) / 2 for b in boxes.values()) / len(sel)
            for p in sel:
                self._shift(p, dy=c - (boxes[p.id][1] + boxes[p.id][3]) / 2)
        if not self.allow_overlap:
            horizontal_line = kind in ("top", "bottom", "vcenter")
            axis = "x" if horizontal_line else "y"
            order = {p.id: (boxes[p.id][0] if horizontal_line else boxes[p.id][1])
                     for p in sel}
            self._separate_along(sel, axis, order)
        self.dirty.emit()
        self.update()

    def distribute(self, kind):
        """Equalize the gaps between nodes from the first to the last. When
        they cannot fit without overlapping they are laid edge to edge."""
        sel = self.selected_pieces()
        if len(sel) < 3:
            self.statusMessage.emit("Select at least 3 nodes to distribute.")
            return
        self.push_history(f"Distribute {kind}")
        horizontal = kind == "h"
        lo, hi = (0, 2) if horizontal else (1, 3)
        boxes = {p.id: self._aabb(p) for p in sel}
        ordered = sorted(sel, key=lambda p: boxes[p.id][lo] + boxes[p.id][hi])
        first, last = boxes[ordered[0].id], boxes[ordered[-1].id]
        span = last[hi] - first[lo]
        sizes = sum(boxes[p.id][hi] - boxes[p.id][lo] for p in ordered)
        gap = (span - sizes) / (len(ordered) - 1)
        if gap < 0 and not self.allow_overlap:
            gap = 0.0
        cursor = first[lo]
        for p in ordered:
            box = boxes[p.id]
            delta = cursor - box[lo]
            self._shift(p, dx=delta) if horizontal else self._shift(p, dy=delta)
            cursor += (box[hi] - box[lo]) + gap
        self.dirty.emit()
        self.update()

    def group(self):
        sel = self.selected_pieces()
        if len(sel) < 2:
            return
        self.push_history("Group")
        from core.project import uuid
        gid = uuid.uuid4().hex
        for p in sel:
            p.group_id = gid
        self.dirty.emit()

    def ungroup(self):
        grouped = [p for p in self.selected_pieces() if p.group_id]
        if not grouped:
            return
        self.push_history("Ungroup")
        for p in grouped:
            p.group_id = ""
        self.dirty.emit()

    def _raise(self, sel):
        """Bring forward: past the next node above that overlaps it."""
        self._step_order(sel, up=True)
        self.dirty.emit()

    def _lower(self, sel):
        """Send backward: below the next node underneath that overlaps it."""
        self._step_order(sel, up=False)
        self.dirty.emit()

    def nudge(self, dx, dy):
        """Move by (dx, dy). Snapping only applies when the nudge itself is a
        whole cell (Shift+arrow) — otherwise a 1px press would snap straight
        back and appear to do nothing."""
        sel = self.selected_pieces()
        if not sel:
            return
        self.push_history("Nudge", coalesce=True)
        cell = max(1, self.project.cell_size)
        whole_cell = (abs(dx) % cell == 0 and abs(dy) % cell == 0)
        for p in sel:
            p.x += dx
            p.y += dy
            if p.snap and whole_cell:
                p.x = snap_value(p.x, cell)
                p.y = snap_value(p.y, cell)
        self.update()
        self.dirty.emit()

    def set_group_rotate_mode(self, on):
        self._group_rotate = on
        if on:
            self._gr_base = None
        self.update()
