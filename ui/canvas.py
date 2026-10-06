"""Interactive map canvas — snapping grid, multi-select, layers, tint/text,
smart guides, cell highlight, group rotate, and a quick toolbar."""
from __future__ import annotations

import math
import os
from typing import Optional

from PyQt6.QtCore import Qt, QPointF, QRectF, QSize, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPixmap, QColor, QPen, QBrush, QCursor, QFont, QPolygonF,
)
from PyQt6.QtWidgets import QWidget, QFrame, QPushButton, QHBoxLayout

from core.project import Piece, Project, Level, snap_value
from core import exporter
from core.render import draw_piece

HANDLE_DIST = 26
HANDLE_R = 7
GUIDE_DIST = 12  # screen px threshold for smart guides


class CanvasView(QWidget):
    selectionChanged = pyqtSignal(object)   # list[Piece]
    dirty = pyqtSignal()
    cursorMoved = pyqtSignal(float, float)
    historyPush = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

        self.project: Optional[Project] = None
        self.level_index: int = 0
        self.library = None

        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._cache: dict = {}

        self.selection: set[str] = set()
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

        # quick toolbar (J3)
        self.quick = QFrame(self)
        self.quick.setStyleSheet("background: rgba(13,18,25,220); border:1px solid #2e6fdf;")
        ql = QHBoxLayout(self.quick)
        ql.setContentsMargins(2, 2, 2, 2)
        ql.setSpacing(2)
        self._qb = {}
        for name, lbl in [("rotL", "⟲"), ("rotR", "⟳"), ("fh", "H"), ("fv", "V"),
                         ("up", "▲"), ("down", "▼"), ("lock", "■"), ("copy", "C"),
                         ("dup", "+"), ("del", "×")]:
            b = QPushButton(lbl)
            b.setMaximumWidth(26); b.setMinimumHeight(22)
            b.clicked.connect(lambda _, n=name: self._quick(n))
            ql.addWidget(b)
            self._qb[name] = b
        self.quick.hide()

    # ------------------------------------------------------------------
    def set_project(self, project: Project, library=None):
        self.project = project
        self.library = library
        self._cache.clear()
        self.selection.clear()
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.fit_to_view()
        self.selectionChanged.emit([])
        self.update()

    def set_level(self, index: int):
        if self.project and 0 <= index < len(self.project.levels):
            self.level_index = index
            self.selection.clear()
            self.selectionChanged.emit([])
            self.update()

    @property
    def level(self) -> Optional[Level]:
        if self.project and 0 <= self.level_index < len(self.project.levels):
            return self.project.levels[self.level_index]
        return None

    def set_ref(self, enabled, offset, opacity):
        self.ref_enabled = enabled
        self.ref_offset = offset
        self.ref_opacity = opacity
        self.update()

    def push_history(self, label: str):
        self.historyPush.emit(label)

    # ------------------------------------------------------------------
    def pixmap(self, piece: Piece) -> QPixmap:
        return exporter.piece_pixmap(piece, self.project, self._cache)

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

    def set_zoom(self, z):
        cx, cy = self.width() / 2, self.height() / 2
        self.zoom_at(cx, cy, z / max(self.zoom, 1e-6))

    def zoom_at(self, sx, sy, factor):
        wx, wy = self.screen_to_world(sx, sy)
        self.zoom = max(0.05, min(8.0, self.zoom * factor))
        self.pan_x = sx - wx * self.zoom
        self.pan_y = sy - wy * self.zoom
        self.update()

    # ------------------------------------------------------------------
    def selected_pieces(self) -> list[Piece]:
        if not self.level:
            return []
        return [p for p in self.level.pieces if p.id in self.selection]

    def select(self, pieces: list[Piece]):
        self.selection = {p.id for p in pieces}
        self._update_quick()
        self.selectionChanged.emit(self.selected_pieces())
        self.update()

    def clear_selection(self):
        self.select([])

    def _update_quick(self):
        if self.selection:
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
        self.update()
        self.dirty.emit()

    # ------------------------------------------------------------------
    def add_asset(self, store_rel_path: str, world_x, world_y) -> Optional[Piece]:
        if not self.level:
            return None
        pm = self.pixmap(Piece(asset_path=store_rel_path)) if store_rel_path else QPixmap()
        # resolve size
        if store_rel_path:
            probe = Piece(asset_path=store_rel_path)
            pm = self.pixmap(probe)
        w = pm.width() if not pm.isNull() else 64
        h = pm.height() if not pm.isNull() else 64
        asset = self.library.get(store_rel_path) if self.library else None
        is_overlay = bool(asset and asset.is_overlay)
        name = asset.name if asset else store_rel_path.split("/")[-1]
        p = Piece(asset_path=store_rel_path, name=name, w=w, h=h,
                  is_overlay=is_overlay, snap=not is_overlay)
        p.x = world_x - w / 2.0
        p.y = world_y - h / 2.0
        if p.snap:
            p.x = snap_value(p.x, self.project.cell_size)
            p.y = snap_value(p.y, self.project.cell_size)
        self.push_history("Add piece")
        self.level.add(p)
        self.select([p])
        self.dirty.emit()
        return p

    def add_embedded(self, b64: str, world_x, world_y, name="Custom") -> Piece:
        pm = QPixmap()
        pm.loadFromData(exporter.decode_embed(b64))
        w = pm.width() or 64
        h = pm.height() or 64
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
                  text_color=self.project.accent)
        p.x = world_x - 40
        p.y = world_y - 15
        p.w, p.h = compute_text_size(p.text, p.font_family, p.font_size, p.font_bold)
        self.push_history("Add text")
        self.level.add(p)
        self.select([p])
        self.dirty.emit()
        return p

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
        painter.fillRect(self.rect(), QColor("#06080c"))
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
            pen = QPen(QColor(self.project.accent))
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
            fill = QColor(self.project.accent)
            fill.setAlpha(46)
            painter.setOpacity(1.0)
            painter.fillRect(r, fill)
            pen = QPen(QColor(self.project.accent))
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
        pen = QPen(QColor(self.project.accent))
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
        pm = self.pixmap(p)
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
        draw_piece(painter, p, pm)
        painter.restore()

    def _draw_selection(self, painter, p: Piece):
        cx, cy = p.center
        scx, scy = self.world_to_screen(cx, cy)
        w = p.w * self.zoom * p.scale
        h = p.h * self.zoom * p.scale
        painter.save()
        painter.translate(scx, scy)
        painter.rotate(p.rotation)
        pen = QPen(QColor(self.project.accent))
        pen.setWidthF(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(-w / 2, -h / 2, w, h))
        painter.restore()

    def _draw_rotate_handle(self, painter, p: Piece):
        hp = self._rotate_handle_screen(p)
        painter.setPen(QPen(QColor(self.project.accent), 2))
        painter.setBrush(QBrush(QColor(self.project.accent)))
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
        painter.setPen(QPen(QColor(self.project.accent), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(int(sx - r), int(sy - r), r * 2, r * 2, 30 * 16, 300 * 16)

    # ------------------------------------------------------------------
    def _piece_at_screen(self, sx, sy):
        wx, wy = self.screen_to_world(sx, sy)
        return self.level.selectable_at(wx, wy) if self.level else None

    def mousePressEvent(self, e):
        if not self.project or not self.level:
            return
        sx, sy = e.position().x(), e.position().y()
        if e.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._drag = {"mode": "pan", "last": (sx, sy)}
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return
        if self._group_rotate:
            self._begin_group_rotate(sx, sy)
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
                self._update_quick()
                self.selectionChanged.emit(self.selected_pieces())
                self.update()
                return
            if hit.id not in self.selection:
                self.select([hit])
            self._start_move(sx, sy, hit)
        else:
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
        if self._drag is None:
            if self.selection and len(self.selection) == 1:
                hp = self._rotate_handle_screen(next(iter(self.selected_pieces())))
                self._hover_handle = (QPointF(sx, sy) - hp).manhattanLength() <= HANDLE_R + 5
            wx, wy = self.screen_to_world(sx, sy)
            self._cursor_world = (wx, wy)
            self.cursorMoved.emit(wx, wy)
            return
        mode = self._drag["mode"]
        if mode == "pan":
            lx, ly = self._drag["last"]
            self.pan_x += sx - lx
            self.pan_y += sy - ly
            self._drag["last"] = (sx, sy)
            self.update()
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
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

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
