"""Interactive map canvas — snapping grid, multi-select, layers, tint/text,
smart guides, cell highlight, group rotate, and a quick toolbar."""
from __future__ import annotations

import math
import os
from typing import Optional

from PyQt6.QtCore import Qt, QPointF, QRectF, QSize, pyqtSignal
from PyQt6.QtGui import (
    QImageReader, QPainter, QPixmap, QColor, QPen, QBrush, QCursor, QFont,
    QPolygonF,
)
from PyQt6.QtWidgets import QWidget, QFrame, QPushButton, QHBoxLayout

from core.project import Piece, Project, Level, ZoneRegion, snap_value
from core import exporter
from core.render import draw_node_border, draw_piece, draw_zone_borders
from ui.theme import theme_colors

HANDLE_DIST = 26
HANDLE_R = 7
GUIDE_DIST = 12  # screen px threshold for smart guides


class CanvasView(QWidget):
    selectionChanged = pyqtSignal(object)   # list[Piece]
    zoneSelectionChanged = pyqtSignal(object)  # ZoneRegion or None
    dirty = pyqtSignal()
    viewChanged = pyqtSignal()  # zoom, pan, or viewport size changed
    cursorMoved = pyqtSignal(float, float)
    historyPush = pyqtSignal(str)
    colorPickStateChanged = pyqtSignal(bool)

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
        self._cache: dict = exporter.PixmapCache()

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
        self._guides: list[tuple[str, float]] = []  # ("v", x) or ("h", y)

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
            b.setFixedSize(28, 26)
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

    def set_project(self, project: Project, library=None):
        self.project = project
        self.library = library
        self._cache.clear()
        self.selection.clear()
        self.selected_zone_id = None
        self._reset_tool_state()
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
                  or self.copy_style_mode)
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

    def push_history(self, label: str):
        self.historyPush.emit(label)

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

    def _place_stamp(self, wx: float, wy: float):
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
        piece = Piece.from_dict(data)
        if piece.snap:
            piece.x = snap_value(piece.x, self.project.cell_size)
            piece.y = snap_value(piece.y, self.project.cell_size)
        self.push_history("Stamp node")
        self.level.add(piece)
        self.dirty.emit()
        self.update()
        return piece

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
        if piece.crop_rect == [0.0, 0.0, 1.0, 1.0]:
            return False
        crop_w = max(1e-6, piece.crop_rect[2] - piece.crop_rect[0])
        crop_h = max(1e-6, piece.crop_rect[3] - piece.crop_rect[1])
        center_x, center_y = piece.center
        self.push_history("Reset image crop")
        piece.w /= crop_w
        piece.h /= crop_h
        piece.crop_rect = [0.0, 0.0, 1.0, 1.0]
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
        piece.x = center_x - piece.w * piece.scale / 2.0
        piece.y = center_y - piece.h * piece.scale / 2.0
        self._cache.pop("emb:" + piece.id, None)
        self._cache.clear()
        self.selectionChanged.emit(self.selected_pieces())
        self.dirty.emit()
        self.update()
        return True

    # ------------------------------------------------------------------
    def pixmap(self, piece: Piece, target_size=None) -> QPixmap:
        return exporter.piece_pixmap(piece, self.project, self._cache, target_size)

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
        self.zoom = min(vw / self.project.canvas_w, vh / self.project.canvas_h) * 0.95
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

    # ------------------------------------------------------------------
    def selected_pieces(self) -> list[Piece]:
        if not self.level:
            return []
        return [p for p in self.level.pieces if p.id in self.selection]

    def select(self, pieces: list[Piece]):
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
            self.quick.show()
            self.quick.move(self.width() - self.quick.width() - 6, 6)
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
    def add_asset(self, store_rel_path: str, world_x, world_y) -> Optional[Piece]:
        if not self.level:
            return None
        asset = self.library.get(store_rel_path) if self.library else None
        if asset and asset.width and asset.height:
            w, h = asset.width, asset.height
        else:
            reader = QImageReader(self.project.resolve_asset(store_rel_path))
            size = reader.size()
            if size.isValid():
                w, h = size.width(), size.height()
            else:
                pm = self.pixmap(Piece(asset_path=store_rel_path), (2048, 2048))
                w = pm.width() if not pm.isNull() else 64
                h = pm.height() if not pm.isNull() else 64
        is_overlay = bool(asset and asset.is_overlay)
        name = asset.name if asset else store_rel_path.split("/")[-1]
        p = Piece(asset_path=store_rel_path, name=name, w=w, h=h,
                  is_overlay=is_overlay, snap=not is_overlay)
        p.x = world_x - w / 2.0
        p.y = world_y - h / 2.0
        if p.snap:
            p.x = snap_value(p.x, self.project.cell_size)
            p.y = snap_value(p.y, self.project.cell_size)
        self.push_history("Add node")
        self.level.add(p)
        self.select([p])
        self.dirty.emit()
        return p

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
        ux = -math.sin(ang)
        uy = -math.cos(ang)
        half_h = (p.vis_h / 2.0) * self.zoom
        return QPointF(scx + ux * (half_h + HANDLE_DIST), scy + uy * (half_h + HANDLE_DIST))

    def _piece_rect_screen(self, p: Piece) -> QRectF:
        w = p.w * self.zoom * p.scale
        h = p.h * self.zoom * p.scale
        return QRectF(-w / 2, -h / 2, w, h)

    # ------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(
            getattr(self, "theme_colors", theme_colors("dark"))["bg"]))
        if not self.project or not self.level:
            return

        self._draw_bg(painter)
        if self.project.show_grid:
            self._draw_grid(painter, self.project.grid_color, self.project.grid_opacity)
        self._draw_cell_highlight(painter)
        if self.ref_enabled:
            self._draw_reference(painter)

        for p in self.level.paint_order():
            self._draw_piece(painter, p, 1.0)

        if self.project.show_zones:
            draw_zone_borders(
                painter, self.level.zones, self.project,
                lambda x, y: QPointF(*self.world_to_screen(x, y)), self.zoom)
        self._draw_zone_tool_preview(painter)
        self._draw_patch_tool_preview(painter)
        self._draw_compose_tool_previews(painter)
        if self.selected_zone:
            self._draw_zone_edit_overlay(painter, self.selected_zone)

        # selection outlines
        for p in self.selected_pieces():
            self._draw_selection(painter, p)
        if len(self.selection) == 1:
            self._draw_rotate_handle(painter, next(iter(self.selected_pieces())))

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

        for g in self._guides:
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

        painter.end()

    def _draw_bg(self, painter):
        x0, y0 = self.world_to_screen(0, 0)
        w = self.project.canvas_w * self.zoom
        h = self.project.canvas_h * self.zoom
        painter.fillRect(QRectF(x0, y0, w, h), QColor(self.level.background))

    def _draw_grid(self, painter, color, opacity):
        cell = self.project.cell_size
        if cell <= 0:
            return
        vw, vh = self.width(), self.height()
        wx0, wy0 = self.screen_to_world(0, 0)
        wx1, wy1 = self.screen_to_world(vw, vh)
        start_x = math.floor(wx0 / cell) * cell
        start_y = math.floor(wy0 / cell) * cell
        pen = QPen(QColor(color))
        pen.setWidthF(1)
        if self.project.grid_style == "dashed":
            pen.setStyle(Qt.PenStyle.DashLine)
        elif self.project.grid_style == "dotted":
            pen.setStyle(Qt.PenStyle.DotLine)
        painter.setOpacity(opacity * 0.6)
        painter.setPen(pen)
        for gx in range(int(start_x), int(wx1) + cell, cell):
            sx, _ = self.world_to_screen(gx, 0)
            painter.drawLine(int(sx), 0, int(sx), vh)
        for gy in range(int(start_y), int(wy1) + cell, cell):
            _, sy = self.world_to_screen(0, gy)
            painter.drawLine(0, int(sy), vw, int(sy))
        pen.setWidthF(max(2.0, self.zoom * 1.4))
        painter.setOpacity(opacity)
        step = cell * max(1, self.project.grid_major)
        for gx in range(int(start_x), int(wx1) + cell, step):
            sx, _ = self.world_to_screen(gx, 0)
            painter.drawLine(int(sx), 0, int(sx), vh)
        for gy in range(int(start_y), int(wy1) + cell, step):
            _, sy = self.world_to_screen(0, gy)
            painter.drawLine(0, int(sy), vw, int(sy))

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
            target = (snap_value(wx - dw / 2.0, cell),
                      snap_value(wy - dh / 2.0, cell), dw, dh)
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
        prev = painter.opacity()
        painter.setOpacity(self.ref_opacity)
        for p in lvl.paint_order():
            self._draw_piece(painter, p, 1.0)
        painter.setOpacity(prev)

    def _draw_piece(self, painter, p: Piece, opacity: float):
        target_size = (max(1, round(p.w * p.scale * self.zoom)),
                       max(1, round(p.h * p.scale * self.zoom)))
        pm = self.pixmap(p, target_size)
        cx, cy = p.center
        scx, scy = self.world_to_screen(cx, cy)
        lyr = self.level.layer_by_id(p.layer)
        lop = lyr.opacity if lyr else 1.0
        painter.save()
        painter.translate(scx, scy)
        painter.rotate(p.rotation)
        sx = self.zoom * p.scale * (-1 if p.flip_h else 1)
        sy = self.zoom * p.scale * (-1 if p.flip_v else 1)
        painter.scale(sx, sy)
        painter.setOpacity(lop * p.opacity)
        draw_piece(painter, p, pm, self.project)
        if self.project.show_node_borders:
            draw_node_border(painter, p, pm, self.project)
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
        painter.drawEllipse(hp, HANDLE_R, HANDLE_R)
        ang = math.radians(p.rotation)
        ex = (p.center[0] * self.zoom + self.pan_x) + (-math.sin(ang)) * (p.vis_h / 2 * self.zoom + 6)
        ey = (p.center[1] * self.zoom + self.pan_y) + (-math.cos(ang)) * (p.vis_h / 2 * self.zoom + 6)
        painter.drawLine(int(ex), int(ey), int(hp.x()), int(hp.y()))

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
        wx, wy = self.screen_to_world(sx, sy)
        left = event.button() == Qt.MouseButton.LeftButton
        right = event.button() == Qt.MouseButton.RightButton
        snap = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if snap:
            wx = snap_value(wx, self.project.cell_size)
            wy = snap_value(wy, self.project.cell_size)
        if self.stamp_tool:
            if right:
                self.cancel_extra_tool()
            elif left:
                self._place_stamp(wx, wy)
                self._stamp_drag_last = (wx, wy)
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
                    fx, fy = self._piece_local_fraction(piece, wx, wy)
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
                self._ruler_drag = {"start": (wx, wy), "current": (wx, wy),
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
                self._scale_drag = {"start": (wx, wy), "current": (wx, wy),
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
                self._connector_drag = {"start": (wx, wy), "current": (wx, wy),
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
        if self.stamp_tool and self._stamp_drag_last is not None:
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
                fx, fy = self._piece_local_fraction(piece, wx, wy)
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
                if drag.get("snap") or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    wx = snap_value(wx, self.project.cell_size)
                    wy = snap_value(wy, self.project.cell_size)
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
        wx, wy = self.screen_to_world(sx, sy)
        if self.stamp_tool and self._stamp_drag_last is not None:
            self._stamp_drag_last = None
            event.accept()
            return True
        if self._crop_drag:
            drag = self._crop_drag
            self._crop_drag = None
            self._crop_preview = None
            piece = next((p for p in self.level.pieces
                          if p.id == self._crop_target_id), None)
            if piece:
                fx, fy = self._piece_local_fraction(piece, wx, wy)
                x0, y0 = drag["start"]
                if self._apply_crop(piece, (x0, y0, fx, fy)):
                    self.set_crop_tool(False)
                else:
                    self.update()
            event.accept()
            return True
        if self._ruler_drag:
            drag = self._ruler_drag
            if drag.get("snap") or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                wx = snap_value(wx, self.project.cell_size)
                wy = snap_value(wy, self.project.cell_size)
            self._ruler_result = (drag["start"], (wx, wy))
            self._ruler_drag = None
            self.update()
            event.accept()
            return True
        if self._scale_drag:
            drag = self._scale_drag
            if drag.get("snap") or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                wx = snap_value(wx, self.project.cell_size)
                wy = snap_value(wy, self.project.cell_size)
            start = drag["start"]
            self._scale_drag = None
            self._create_scale_bar(start, (wx, wy))
            self.set_scale_tool(False)
            event.accept()
            return True
        if self._connector_drag:
            drag = self._connector_drag
            if drag.get("snap") or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                wx = snap_value(wx, self.project.cell_size)
                wy = snap_value(wy, self.project.cell_size)
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
        if e.key() == Qt.Key.Key_Escape:
            if (self.cancel_color_pick() or self.cancel_zone_tool()
                    or self.cancel_patch_tool() or self.cancel_extra_tool()):
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
                wx, wy = self.screen_to_world(sx, sy)
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
                wx, wy = self.screen_to_world(sx, sy)
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
            self._drag = {"mode": "pan", "last": (sx, sy)}
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

        if self.selection and len(self.selection) == 1:
            hp = self._rotate_handle_screen(next(iter(self.selected_pieces())))
            if (QPointF(sx, sy) - hp).manhattanLength() <= HANDLE_R + 5:
                self._drag = {"mode": "rotate", "center": next(iter(self.selected_pieces())).center}
                self.push_history("Rotate")
                return
        hit = self._piece_at_screen(sx, sy)
        mods = e.modifiers()
        if hit:
            if mods & Qt.KeyboardModifier.ShiftModifier:
                if hit.id in self.selection:
                    self.selection.discard(hit.id)
                else:
                    self.selection.add(hit.id)
                self.select(self.selected_pieces())
                self.update()
                return
            if hit.id not in self.selection:
                self.select([hit])
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
        if self._tool_move(sx, sy, e):
            return
        if self._patch_drag_start is not None and self.patch_tool:
            x0, y0 = self._patch_drag_start
            self._patch_preview = (min(x0, wx), min(y0, wy),
                                   max(x0, wx), max(y0, wy))
            self.cursorMoved.emit(wx, wy)
            self.update()
            return
        if self._zone_drag_start is not None and self.zone_tool == "rectangle":
            x0, y0 = self._zone_drag_start
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
                    points[edit["index"]] = (wx, wy)
                    zone.points = points
                else:
                    zone.points = [(x + dx, y + dy)
                                   for x, y in edit["points"]]
                edit["changed"] = True
                self.update()
            self.cursorMoved.emit(wx, wy)
            return
        if self._drag is None:
            if self.selection and len(self.selection) == 1:
                hp = self._rotate_handle_screen(next(iter(self.selected_pieces())))
                self._hover_handle = (QPointF(sx, sy) - hp).manhattanLength() <= HANDLE_R + 5
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
            self.update()
            self.viewChanged.emit()
        elif mode == "move":
            self._move_selected(sx, sy)
            self.update()
        elif mode == "rotate":
            self._rotate_primary(sx, sy, e)
            self.update()
        elif mode == "marquee":
            x0, y0 = self._drag["start"]
            r = QRectF(min(x0, sx), min(y0, sy), abs(sx - x0), abs(sy - y0))
            self._marquee = QRectF(*self.screen_to_world(r.x(), r.y()),
                                  r.width() / self.zoom, r.height() / self.zoom)
            self.update()
        elif mode == "group_rotate":
            self._apply_group_rotate(sx, sy)
            self.update()
        wx, wy = self.screen_to_world(sx, sy)
        self._cursor_world = (wx, wy)
        self.cursorMoved.emit(wx, wy)

    def _move_selected(self, sx, sy):
        """Move the selection rigidly; the primary picks the nearest target
        (grid line or another piece's edge) and everything follows it."""
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

        # gather other pieces' visual edges (x and y)
        thr = GUIDE_DIST / max(self.zoom, 1e-6)
        vw, vh = primary.vis_w, primary.vis_h
        x_offs = (0.0, vw / 2.0, vw)
        y_offs = (0.0, vh / 2.0, vh)
        x_edges, y_edges = [], []
        for o in self.level.pieces:
            if o.id == primary.id or o.id in self.selection:
                continue
            x_edges += [o.x, o.x + o.vis_w / 2.0, o.x + o.vis_w]
            y_edges += [o.y, o.y + o.vis_h / 2.0, o.y + o.vis_h]

        tgt_x, guide_x = self._nearest_target(
            raw_x, x_offs, x_edges, thr,
            snap_value(raw_x, cell) if primary.snap else None)
        tgt_y, guide_y = self._nearest_target(
            raw_y, y_offs, y_edges, thr,
            snap_value(raw_y, cell) if primary.snap else None)

        # rigid delta from the primary, applied to the whole selection
        dx = tgt_x - primary.x
        dy = tgt_y - primary.y
        for p in sel:
            p.x += dx
            p.y += dy

        self._guides = []
        if guide_x is not None:
            self._guides.append(("v", guide_x))
        if guide_y is not None:
            self._guides.append(("h", guide_y))

    @staticmethod
    def _nearest_target(raw, offsets, edges, threshold, grid_target):
        """Pick the closest snap target for a dragged edge-set.

        offsets: this piece's edge positions relative to its x (or y).
        edges:   absolute edge positions of neighboring pieces.
        Returns (target_for_piece_origin, guide_line_or_None).
        Grid wins when it is nearer than any guide; guides win ties.
        """
        best_d = None
        best_t = raw
        best_g = None
        if grid_target is not None:
            best_d = abs(grid_target - raw)
            best_t = grid_target
        # every edge of this piece may align OR touch every edge of a neighbor
        for off in offsets:
            for e in edges:
                t = e - off                 # origin that puts this edge on e
                d = abs(t - raw)
                if d > threshold:
                    continue
                # guides beat grid only when strictly closer, ties go to grid
                if best_d is None or d < best_d - 1e-9:
                    best_d = d
                    best_t = t
                    best_g = e
        return best_t, best_g

    def _rotate_primary(self, sx, sy, e):
        p = next(iter(self.selected_pieces()))
        cx, cy = p.center
        scx, scy = self.world_to_screen(cx, cy)
        ang = math.degrees(math.atan2(sy - scy, sx - scx)) + 90
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            ang = round(ang / 15.0) * 15.0
        p.rotation = ang % 360

    def _begin_group_rotate(self, sx, sy):
        self._drag = {"mode": "group_rotate", "center": self._centroid()}

    def _apply_group_rotate(self, sx, sy):
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
        delta = ang - self._gr_base
        for p in sel:
            p.rotation = (self._gr_start[p.id] + delta) % 360

    def _centroid(self):
        sel = self.selected_pieces()
        if not sel:
            return (0, 0)
        return (sum(p.center[0] for p in sel) / len(sel),
                sum(p.center[1] for p in sel) / len(sel))

    def mouseReleaseEvent(self, e):
        if self._tool_release(e.position().x(), e.position().y(), e):
            return
        if self._patch_drag_start is not None:
            x0, y0 = self._patch_drag_start
            wx, wy = self.screen_to_world(e.position().x(), e.position().y())
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
            wx, wy = self.screen_to_world(e.position().x(), e.position().y())
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
        if self._drag and self._drag["mode"] in ("move", "rotate", "group_rotate"):
            if self._drag["mode"] == "group_rotate":
                self._group_rotate = False
                self._gr_base = None
            self.dirty.emit()
        if self._drag and self._drag["mode"] == "marquee":
            self._select_marquee(self._drag.get("add", False))
            self._marquee = None
        self._drag = None
        self._guides = []
        if (self.zone_tool or self.patch_tool or self.stamp_tool or self.crop_tool
                or self.ruler_tool or self.scale_tool or self.connector_tool
                or self.lasso_tool or self.copy_style_mode):
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    def mouseDoubleClickEvent(self, e):
        if self.zone_tool == "polygon" and e.button() == Qt.MouseButton.LeftButton:
            self.finish_zone_polygon()
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
            if asset and asset.width and asset.height:
                self._drop_target = (asset.width, asset.height)
            else:
                pm = self.pixmap(Piece(asset_path=path))
                if not pm.isNull():
                    self._drop_target = (pm.width(), pm.height())
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
        for p in sel:
            np = Piece(**p.to_dict())
            np.id = None
            from core.project import uuid
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
        for d in self._clipboard:
            np = Piece(**d)
            np.id = None
            from core.project import uuid
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

    # align / distribute
    def align(self, kind):
        sel = self.selected_pieces()
        if len(sel) < 2:
            return
        self.push_history(f"Align {kind}")
        if kind == "left":
            m = min(p.x for p in sel)
            for p in sel:
                p.x = m
        elif kind == "right":
            m = max(p.x + p.vis_w for p in sel)
            for p in sel:
                p.x = m - p.vis_w
        elif kind == "top":
            m = min(p.y for p in sel)
            for p in sel:
                p.y = m
        elif kind == "bottom":
            m = max(p.y + p.vis_h for p in sel)
            for p in sel:
                p.y = m - p.vis_h
        elif kind == "hcenter":
            c = sum(p.center[0] for p in sel) / len(sel)
            for p in sel:
                p.x = c - p.vis_w / 2
        elif kind == "vcenter":
            c = sum(p.center[1] for p in sel) / len(sel)
            for p in sel:
                p.y = c - p.vis_h / 2
        self.dirty.emit()
        self.update()

    def distribute(self, kind):
        sel = self.selected_pieces()
        if len(sel) < 3:
            return
        self.push_history(f"Distribute {kind}")
        if kind == "h":
            sel.sort(key=lambda p: p.center[0])
            total = sel[-1].center[0] - sel[0].center[0]
            step = total / (len(sel) - 1)
            for i, p in enumerate(sel):
                p.x = sel[0].center[0] + i * step - p.vis_w / 2
        else:
            sel.sort(key=lambda p: p.center[1])
            total = sel[-1].center[1] - sel[0].center[1]
            step = total / (len(sel) - 1)
            for i, p in enumerate(sel):
                p.y = sel[0].center[1] + i * step - p.vis_h / 2
        self.dirty.emit()
        self.update()

    def group(self):
        sel = self.selected_pieces()
        if len(sel) < 2:
            return
        from core.project import uuid
        gid = uuid.uuid4().hex
        for p in sel:
            p.group_id = gid
        self.dirty.emit()

    def ungroup(self):
        for p in self.selected_pieces():
            p.group_id = ""
        self.dirty.emit()

    def _raise(self, sel):
        for p in sel:
            p.z += 1
        self.dirty.emit()

    def _lower(self, sel):
        for p in sel:
            p.z -= 1
        self.dirty.emit()

    def nudge(self, dx, dy):
        """Move by (dx, dy). Snapping only applies when the nudge itself is a
        whole cell (Shift+arrow) — otherwise a 1px press would snap straight
        back and appear to do nothing."""
        sel = self.selected_pieces()
        if not sel:
            return
        self.push_history("Nudge")
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
