"""Main application window — launch screen, theme, panels, history, minimap."""
from __future__ import annotations

import json
import math
import os
import time
from typing import Optional

from PyQt6.QtCore import (Qt, QTimer, QRectF, QPoint, QPointF, QEvent, QSettings,
                          QStandardPaths, QUrl)
from PyQt6.QtGui import (QAction, QActionGroup, QKeySequence, QColor, QPixmap, QPainter,
                         QPen, QCursor, QDesktopServices, QIcon)
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QTabBar, QPushButton,
    QFileDialog, QInputDialog, QMessageBox, QLabel, QStatusBar, QToolBar,
    QTabWidget, QListWidget, QListWidgetItem, QGroupBox, QSlider, QCheckBox,
    QApplication, QDoubleSpinBox, QLineEdit, QTextEdit, QComboBox,
    QAbstractSpinBox, QDialog, QSplitter, QSplitterHandle, QMenu,
)

from core.project import Project, Level, Piece, choose_asset_store, new_project, uuid
from core.history import History
from core import exporter, bundle
from core.backups import BACKUP_SLOTS, backup_folder, list_backups, rotate_backup
from core.relink import relink_plan
from core.stamps import (SLOT_COUNT, StampError, asset_slot, is_edge_slot,
                         looks_like_door, node_slot, slot_label, slots_from_json,
                         slots_to_json, with_edge)
from core.userfiles import (load_path_list, map_base_name, recent_file_path,
                            save_path_list, thumbnail_path)
from ui.canvas import CanvasView
from ui.library import LibraryPanel
from ui.properties import PropertiesPanel
from ui.layers_panel import LayersPanel
from ui.zones_panel import ZonesPanel
from ui.canvas_size_dialog import CanvasSizeDialog
from ui.menu_overlay import MenuOverlay
from ui.generator_dialog import GeneratorDialog
from ui import theme as thememod
from ui.branding import (APP_NAME, ALIEN_NAME, SETTINGS_ID,
                         bundled_asset_pack_paths, default_asset_store_path,
                         seed_bundled_assets)
from ui.custom_toolbar import CustomizableToolBar, CustomizeToolbarDialog
from ui.color_picker import choose_color
from ui.app_icon import app_icon
from ui.stamp_bar import StampBar
from ui.cutout_bar import CutoutBar

# Older builds kept the recent-maps list beside this file. That works from a
# source checkout, but a one-file EXE runs from a temporary folder that is
# deleted on exit, so the list never survived. It now lives in the per-user
# app-data folder; MainWindow points RECENT_FILE there at startup.
LEGACY_RECENT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recent.json")
RECENT_FILE = LEGACY_RECENT_FILE
BMAP_EXT = ".bmap"


def destroy_before_qt_exits(window) -> None:
    """Delete ``window`` (and every widget in it) when Python exits, before
    Qt's own objects go away. Otherwise the window can outlive QApplication
    and crash on the way out (seen with styled floating bars on the canvas)."""
    import atexit
    import weakref
    ref = weakref.ref(window)

    def destroy():
        alive = ref()
        if alive is None:
            return
        try:
            from PyQt6 import sip
            if sip.isdeleted(alive):
                return
            # Drain background workers first: see _drain_background_work for
            # why sip.delete() below cannot be trusted to do this safely on
            # its own (a worker mid-run when a widget is deleted deadlocks
            # against the thread doing the deleting).
            try:
                alive._drain_background_work()
            except Exception:
                pass
            sip.delete(alive)
        except (ImportError, RuntimeError, TypeError):
            pass
    atexit.register(destroy)


def load_recent(path: Optional[str] = None) -> list:
    return load_path_list(path or RECENT_FILE)


def save_recent(items: list, path: Optional[str] = None) -> bool:
    return save_path_list(items, path or RECENT_FILE)


# Tools shown on the toolbar by default; the rest live in the menus (and the
# right-click menu) and can be added back via View → Customize toolbar.
COMPACT_TOOLBAR = ("new", "open", "save", "export", "undo", "redo", "generate")

# The inspector (Node / Layers / Zones / History) can be dragged this narrow
# at the normal text size; its rows wrap to fit. Larger text needs more room.
INSPECTOR_MIN_WIDTH = 220


def inspector_min_width(text_scale: float) -> int:
    return max(INSPECTOR_MIN_WIDTH, int(round(INSPECTOR_MIN_WIDTH * float(text_scale or 1.0))))


class GripSplitterHandle(QSplitterHandle):
    """Splitter bar with three grip dots so it is obviously draggable."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        color = QColor(self.palette().color(self.palette().ColorRole.WindowText))
        color.setAlpha(170)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        for i in (-1, 0, 1):
            painter.drawEllipse(QPointF(cx, cy + i * 9), 1.6, 1.6)
        painter.end()

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(10)
        return hint


class GripSplitter(QSplitter):
    def createHandle(self):
        return GripSplitterHandle(self.orientation(), self)


class Minimap(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self._drag = False
        self.setFixedSize(150, 150)
        self.setToolTip("Map overview. Click or drag to center the main view.")
        self.set_theme("dark", "#69b7f5")
        canvas.dirty.connect(self.update)
        canvas.selectionChanged.connect(lambda _: self.update())
        canvas.viewChanged.connect(self.update)

    def set_theme(self, mode: str, accent: str):
        colors = thememod.theme_colors(mode, accent)
        self._theme_colors = colors
        self.setStyleSheet(
            f"background:{colors['panel']}; border:1px solid {colors['border_hot']};")
        self.update()

    def _map_geometry(self):
        project = self.canvas.project
        if not project:
            return None
        map_w = max(1.0, float(project.canvas_w))
        map_h = max(1.0, float(project.canvas_h))
        available_w = max(1.0, float(self.width() - 2))
        available_h = max(1.0, float(self.height() - 2))
        scale = min(available_w / map_w, available_h / map_h)
        width, height = map_w * scale, map_h * scale
        ox = (self.width() - width) / 2.0
        oy = (self.height() - height) / 2.0
        return map_w, map_h, scale, ox, oy

    def _world_from_pos(self, px, py):
        geometry = self._map_geometry()
        if not geometry:
            return 0.0, 0.0
        map_w, map_h, scale, ox, oy = geometry
        wx = (px - ox) / scale
        wy = (py - oy) / scale
        return min(map_w, max(0.0, wx)), min(map_h, max(0.0, wy))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self._pan(e)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag:
            self._pan(e)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = False
        super().mouseReleaseEvent(e)

    def _pan(self, e):
        wx, wy = self._world_from_pos(e.position().x(), e.position().y())
        self.canvas.center_on_world(wx, wy)

    def paintEvent(self, e):
        p = QPainter(self)
        colors = getattr(self, "_theme_colors", thememod.theme_colors("dark"))
        p.fillRect(self.rect(), QColor(colors["panel"]))
        geometry = self._map_geometry()
        if not geometry or not self.canvas.level:
            p.end()
            return

        map_w, map_h, scale, ox, oy = geometry
        frame = QRectF(ox, oy, map_w * scale, map_h * scale)
        p.fillRect(frame, QColor(colors["bg"]))

        # Keep map contents inside the map frame. Account for visual scale and
        # rotation so oversized or rotated nodes appear where they are drawn.
        p.save()
        p.setClipRect(frame)
        selected = self.canvas.selection
        for piece in self.canvas.level.paint_order():
            cx, cy = piece.center
            angle = math.radians(piece.rotation)
            c, s = abs(math.cos(angle)), abs(math.sin(angle))
            node_w = max(1.0, c * piece.vis_w + s * piece.vis_h)
            node_h = max(1.0, s * piece.vis_w + c * piece.vis_h)
            rect = QRectF(
                ox + (cx - node_w / 2.0) * scale,
                oy + (cy - node_h / 2.0) * scale,
                max(1.0, node_w * scale), max(1.0, node_h * scale))
            color = QColor(colors["accent"])
            color.setAlpha(225 if piece.id in selected else 155)
            p.fillRect(rect, color)

        # Clip the viewport marker to the project canvas. Without this, panning
        # past an edge can draw a huge red rectangle over the minimap margins.
        vx0, vy0 = self.canvas.screen_to_world(0, 0)
        vx1, vy1 = self.canvas.screen_to_world(
            self.canvas.width(), self.canvas.height())
        viewport = QRectF(vx0, vy0, vx1 - vx0, vy1 - vy0).normalized()
        visible_viewport = viewport.intersected(QRectF(0, 0, map_w, map_h))
        if not visible_viewport.isEmpty():
            marker = QRectF(
                ox + visible_viewport.x() * scale,
                oy + visible_viewport.y() * scale,
                visible_viewport.width() * scale,
                visible_viewport.height() * scale)
            p.setPen(QPen(QColor("#ff5a5a"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(marker)
        p.restore()

        p.setPen(QPen(QColor(colors["accent"]), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(frame.adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()


class HistoryPanel(QWidget):
    """Undo/redo buttons plus a readable list of the steps on each stack."""

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 8, 6, 6)
        v.setSpacing(8)
        row = QHBoxLayout()
        self.b_undo = QPushButton("Undo")
        self.b_undo.clicked.connect(main.undo)
        self.b_redo = QPushButton("Redo")
        self.b_redo.clicked.connect(main.redo)
        for b in (self.b_undo, self.b_redo):
            b.setMinimumHeight(34)
        row.addWidget(self.b_undo)
        row.addWidget(self.b_redo)
        v.addLayout(row)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        v.addWidget(self.label)
        self.steps = QListWidget()
        self.steps.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.steps.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        v.addWidget(self.steps, 1)
        self.refresh()

    def refresh(self):
        h = self.main.history
        self.b_undo.setEnabled(h.can_undo())
        self.b_redo.setEnabled(h.can_redo())
        undo_labels = [label for label, _ in h.undos]
        redo_labels = [label for label, _ in h.redos]
        if undo_labels:
            self.label.setText(f"Next undo: {undo_labels[-1]}")
        else:
            self.label.setText("Nothing to undo yet — edits will be listed here.")
        self.steps.clear()
        for label in reversed(redo_labels):
            item = QListWidgetItem(f"↷  {label}")
            item.setForeground(QColor("#808a96"))
            item.setToolTip("Undone — Redo brings it back")
            self.steps.addItem(item)
        for i, label in enumerate(reversed(undo_labels)):
            item = QListWidgetItem(("●  " if i == 0 else "○  ") + label)
            self.steps.addItem(item)


class LevelBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Optional[Project] = None
        self.changed = None
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self.tabs = QTabBar()
        self.tabs.setMovable(True)
        self.tabs.tabMoved.connect(self._on_moved)
        self.tabs.currentChanged.connect(self._on_current)
        self.tabs.tabBarDoubleClicked.connect(self._rename)
        layout.addWidget(self.tabs, 1)
        # One small "+" button; everything else is on the tabs' right-click menu
        # (and Edit → Levels), which keeps the strip slim.
        button = QPushButton("+")
        button.setObjectName("PanelIconButton")
        button.setFixedSize(34, 30)
        button.setToolTip("Add a level (right-click a tab for more)")
        button.setAccessibleName("Add a level")
        button.clicked.connect(self._add)
        layout.addWidget(button)
        self.tabs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._tab_menu)

    def _tab_menu(self, pos):
        index = self.tabs.tabAt(pos)
        if index >= 0:
            self.tabs.setCurrentIndex(index)
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction("Add level", self._add)
        if index >= 0:
            menu.addAction("Rename…", lambda: self._rename(index))
            menu.addSeparator()
            earlier = menu.addAction("Move earlier", lambda: self._shift(-1))
            earlier.setEnabled(index > 0)
            later = menu.addAction("Move later", lambda: self._shift(1))
            later.setEnabled(index < self.tabs.count() - 1)
            menu.addSeparator()
            menu.addAction("Delete level", self._remove)
        menu.exec(self.tabs.mapToGlobal(pos))

    def set_project(self, project: Project):
        self.project = project
        self.refresh()
        if project.levels:
            self.tabs.setCurrentIndex(0)
            self._emit(0)

    def refresh(self):
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for lv in self.project.levels:
            self.tabs.addTab(lv.name)
        self.tabs.blockSignals(False)

    def _emit(self, idx):
        if self.changed:
            self.changed(idx)

    def _on_current(self, idx):
        self._emit(idx)

    def _on_moved(self, fi, ti):
        if self.project:
            self.project.levels[fi], self.project.levels[ti] = \
                self.project.levels[ti], self.project.levels[fi]
            self.refresh()
            self.tabs.setCurrentIndex(ti)
            self._emit(ti)

    def _add(self):
        self.project.add_level()
        self.refresh()
        self.tabs.setCurrentIndex(len(self.project.levels) - 1)
        self._emit(self.tabs.currentIndex())

    def _remove(self):
        if len(self.project.levels) <= 1:
            QMessageBox.information(self, "Levels", "At least one level is required.")
            return
        idx = self.tabs.currentIndex()
        level = self.project.levels[idx]
        if level.pieces or level.zones:
            ok = QMessageBox.question(
                self, "Remove level",
                f"Remove '{level.name}' and its {len(level.pieces)} node(s) and "
                f"{len(level.zones)} gameplay zone(s)?")
            if ok != QMessageBox.StandardButton.Yes:
                return
        self.project.remove_level(idx)
        self.refresh()
        self.tabs.setCurrentIndex(min(idx, len(self.project.levels) - 1))
        self._emit(self.tabs.currentIndex())

    def _shift(self, delta):
        idx = self.tabs.currentIndex()
        self.project.move_level(idx, delta)
        new_idx = idx + delta
        self.refresh()
        self.tabs.setCurrentIndex(new_idx)
        self._emit(new_idx)

    def _rename(self, idx):
        name, ok = QInputDialog.getText(self, "Rename level", "Level name:",
                                        text=self.project.levels[idx].name)
        if ok and name.strip():
            self.project.levels[idx].name = name.strip()
            self.refresh()
            self.tabs.setCurrentIndex(idx)


class MainWindow(QMainWindow):
    AUTOSAVE_CHOICES = (0, 1, 5, 10)
    DEFAULT_PANEL_SIZES = [280, 800, 300]

    def __init__(self):
        super().__init__()
        self.settings = QSettings("ArenaMaps", SETTINGS_ID)
        self.theme_mode = str(self.settings.value("appearance/theme", "dark")).lower()
        if self.theme_mode not in thememod.THEME_MODES:
            self.theme_mode = "dark"
        self.theme_accent = str(self.settings.value("appearance/alien_accent",
                                                     thememod.DEFAULT_ACCENT))
        if not QColor(self.theme_accent).isValid():
            self.theme_accent = thememod.DEFAULT_ACCENT
        self.alien_scanlines = self.settings.value(
            "appearance/alien_scanlines", True, type=bool)
        try:
            self.autosave_interval_minutes = int(
                self.settings.value("autosave/interval_minutes", 5))
        except (TypeError, ValueError):
            self.autosave_interval_minutes = 5
        if self.autosave_interval_minutes not in self.AUTOSAVE_CHOICES:
            self.autosave_interval_minutes = 5

        app_data = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation)
        if not app_data:
            app_data = os.path.join(os.path.expanduser("~"), ".map-studio")
        self._app_data_dir = app_data
        self._bundle_extract_root = os.path.join(app_data, "bundles")
        self._autosave_dir = os.path.join(app_data, "autosave")
        self._recovery_file = os.path.join(self._autosave_dir, "recovery.bmap")
        self._recovery_meta = os.path.join(self._autosave_dir, "recovery.json")
        self._recent_file = recent_file_path(app_data)
        self._backup_root = os.path.join(app_data, "backups")
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave_tick)
        if self.autosave_interval_minutes:
            self._autosave_timer.start(self.autosave_interval_minutes * 60 * 1000)

        self.project = new_project()
        self.history = History()
        self.recent = self._load_recent_list()
        self.stamp_slots = slots_from_json(self.settings.value("stamps/slots", "[]"))
        self._current_file: Optional[str] = None
        self._dirty = False
        self._gen_output = None    # tracks last Generate output for Regenerate
        self.setWindowIcon(app_icon())
        self._build_ui()
        destroy_before_qt_exits(self)
        self._show_launch()
        QTimer.singleShot(0, self._install_bundled_asset_packs)
        QTimer.singleShot(1500, self._sync_legend)
        from ui.warmup import Warmup
        self._warmup = Warmup(self, lambda text, ms=0: self.status.showMessage(text, ms))
        QTimer.singleShot(50, self._start_warmup)

    # ------------------------------------------------------------------
    def _build_ui(self):
        self.setWindowTitle(APP_NAME)
        self.resize(1360, 820)
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        self._panel_memory: dict[int, int] = {}
        self.splitter = GripSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(True)
        self.splitter.setHandleWidth(10)
        root.addWidget(self.splitter)

        self.library = LibraryPanel()
        self.library.setMinimumWidth(180)
        self.library.assetActivated.connect(self._add_at_center)
        self.library.collectionsChanged.connect(self._mark_dirty)
        self.library.pinStampRequested.connect(self._pin_asset_stamp)
        self.library.stamp_labels = lambda: [slot_label(slot) for slot in self.stamp_slots]
        self.library.swapRequested.connect(self._swap_selected_to)
        self.library.swapAllRequested.connect(self._swap_every_copy_to)
        self.library.backdropRequested.connect(self._use_backdrop_texture)
        self.library.generateRequested.connect(self._generate_from_paths)
        self.library.canvas_swap_info = self._canvas_swap_info
        self.splitter.addWidget(self.library)

        center_col = QVBoxLayout()
        center_col.setContentsMargins(0, 0, 0, 0)
        center_col.setSpacing(0)
        self.level_bar = LevelBar()
        self.level_bar.changed = self._on_level_changed
        center_col.addWidget(self.level_bar)

        self.canvas = CanvasView()
        self.canvas.dirty.connect(self._mark_dirty)
        self.canvas.cursorMoved.connect(self._on_cursor)
        self.canvas.historyPush.connect(self._push_history)
        self.canvas.auto_tighten = self.settings.value(
            "editing/auto_tighten", True, type=bool)
        self._load_tighten_options()
        self.canvas.contextMenuRequested.connect(self._show_canvas_menu)
        self.canvas.guideMenuRequested.connect(self._show_guide_menu)
        self.canvas.guideEditRequested.connect(
            lambda guide_id: QTimer.singleShot(0, lambda: self._edit_guide_position(guide_id)))
        self.canvas.guideSettingsChanged.connect(self._sync_guide_actions)
        self.canvas.railsChanged.connect(self._place_minimap)
        self.canvas.historyDiscardLast.connect(self._discard_last_history)
        center_col.addWidget(self.canvas, 1)

        # The map overview floats over a corner of the canvas (View → Minimap)
        # instead of taking a permanent strip; zoom lives in the status bar,
        # the View menu and the mouse wheel.
        self.minimap = Minimap(self.canvas)
        self.minimap.setParent(self.canvas)
        self.minimap.raise_()
        # quick-stamp hotbar (keys 1-9) floats at the bottom of the canvas
        self.stamp_bar = StampBar(self.canvas)
        self.stamp_bar.slotClicked.connect(self._arm_stamp)
        self.stamp_bar.slotMenuRequested.connect(self._stamp_slot_menu)
        self.stamp_bar.assetDropped.connect(self._pin_asset_stamp)
        self.canvas.stampToolChanged.connect(self.stamp_bar.set_active)
        self._stamps_enabled = True
        # cut-out tool options and actions float at the top of the canvas
        self.cutout_bar = CutoutBar(self.canvas)
        self.cutout_bar.shapeChosen.connect(self._set_cutout_shape)
        self.cutout_bar.snapToggled.connect(self._set_cutout_snap)
        self.cutout_bar.actionRequested.connect(self._cutout_action)
        self.canvas.cutout_shape = str(self.settings.value("tools/cutout_shape", "rect"))
        if self.canvas.cutout_shape not in ("rect", "ellipse", "lasso", "polygon"):
            self.canvas.cutout_shape = "rect"
        self.canvas.cutout_snap = self.settings.value("tools/cutout_snap", True, type=bool)
        self.canvas.cutoutChanged.connect(self._refresh_cutout_bar)
        self.canvas.cutoutMenuRequested.connect(self._show_cutout_menu)
        self.canvas.installEventFilter(self)
        center_widget = QWidget()
        cb = QVBoxLayout(center_widget)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.addLayout(center_col, 1)
        self.splitter.addWidget(center_widget)

        right = QTabWidget()
        self.inspector = right
        self.props = PropertiesPanel(self.canvas)
        self.layers = LayersPanel(self.canvas)
        self.zones = ZonesPanel(self.canvas)
        self.zones.defaultsChanged.connect(
            lambda: self.props.load_selection(self.canvas.selected_pieces()))
        self.hist = HistoryPanel(self)
        right.addTab(self.props, "Node")
        right.addTab(self.layers, "Layers")
        right.addTab(self.zones, "Zones")
        right.addTab(self.hist, "History")
        right.setMinimumWidth(INSPECTOR_MIN_WIDTH)
        right.tabBar().setUsesScrollButtons(False)
        right.tabBar().setExpanding(True)
        right.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        self.splitter.addWidget(right)

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes(self.DEFAULT_PANEL_SIZES)
        self._restore_layout()
        self.splitter.splitterMoved.connect(lambda *_: self._save_layout())

        self._build_menu()
        self._build_toolbar()
        self._build_status()

        self.scanlines = thememod.ScanlineOverlay(self, self.theme_accent)
        # in-window system menu (ESC) — created last so it stacks on top
        self.overlay = MenuOverlay(self)
        self._restore_view()
        self._sync_view_actions()
        self.splitter.splitterMoved.connect(lambda *_: self._sync_view_actions())

    # -- panel layout -------------------------------------------------------
    def _save_layout(self):
        sizes = self.splitter.sizes()
        self.settings.setValue("layout/panel_sizes", json.dumps(sizes))

    def _restore_layout(self):
        try:
            sizes = [int(x) for x in json.loads(
                str(self.settings.value("layout/panel_sizes", "")))]
        except (TypeError, ValueError):
            return
        if len(sizes) == 3 and sum(sizes) > 0:
            self.splitter.setSizes(sizes)

    def _reset_layout(self):
        self.splitter.setSizes(self.DEFAULT_PANEL_SIZES)
        self._save_layout()

    def _panel_visible(self, index) -> bool:
        return self.splitter.sizes()[index] > 0

    def _set_panel(self, index, on: bool, save=True):
        sizes = self.splitter.sizes()
        if on and sizes[index] == 0:
            want = self._panel_memory.get(index, self.DEFAULT_PANEL_SIZES[index])
            sizes[1] = max(200, sizes[1] - want)
            sizes[index] = want
        elif not on and sizes[index] > 0:
            self._panel_memory[index] = sizes[index]
            sizes[1] += sizes[index]
            sizes[index] = 0
        else:
            return
        self.splitter.setSizes(sizes)
        if save:
            self._save_layout()

    def _toggle_panel(self, index):
        self._set_panel(index, not self._panel_visible(index))
        self._sync_view_actions()

    # -- view toggles (Photoshop-style Window menu) ------------------------
    VIEW_ITEMS = ("toolbar", "status", "levels", "minimap", "quick", "rails",
                  "stamps", "library", "inspector")
    VIEW_DEFAULTS = {"toolbar": True, "status": True, "levels": True,
                     "minimap": True, "quick": False, "rails": True,
                     "stamps": True, "library": True, "inspector": True}
    VIEW_LABELS = {"toolbar": "Toolbar", "status": "Status bar",
                   "levels": "Level tabs", "minimap": "Minimap",
                   "quick": "Floating node buttons", "rails": "Guide rails",
                   "stamps": "Stamp hotbar (keys 1–9)",
                   "library": "Library panel", "inspector": "Inspector panel"}
    WORKSPACES = {
        "Standard": dict(VIEW_DEFAULTS),
        "Minimal": {**VIEW_DEFAULTS, "toolbar": False, "minimap": False},
        "Canvas only": {name: False for name in VIEW_DEFAULTS},
    }

    def _view_get(self, name: str) -> bool:
        if name == "toolbar":
            return self.toolbar.isVisibleTo(self)
        if name == "status":
            return self.statusBar().isVisibleTo(self)
        if name == "levels":
            return self.level_bar.isVisibleTo(self)
        if name == "minimap":
            return self.minimap.isVisibleTo(self.canvas)
        if name == "quick":
            return self.canvas.quick_enabled
        if name == "rails":
            return self.canvas.rails_visible
        if name == "stamps":
            return self._stamps_enabled
        if name == "library":
            return self._panel_visible(0)
        return self._panel_visible(2)

    def _view_set(self, name: str, on: bool, save=True):
        on = bool(on)
        if name == "toolbar":
            self.toolbar.setVisible(on)
        elif name == "status":
            self.statusBar().setVisible(on)
        elif name == "levels":
            self.level_bar.setVisible(on)
        elif name == "minimap":
            self.minimap.setVisible(on)
            self._place_minimap()
        elif name == "quick":
            self.canvas.set_quick_enabled(on)
        elif name == "rails":
            self.canvas.set_rails_visible(on)
            self._place_minimap()
        elif name == "stamps":
            self._stamps_enabled = on
            self._refresh_stamp_bar()
        elif name == "library":
            self._set_panel(0, on, save=False)
        elif name == "inspector":
            self._set_panel(2, on, save=False)
        if save:
            self.settings.setValue(f"view/{name}", on)
            if name in ("library", "inspector"):
                self._save_layout()
        self._sync_view_actions()

    def _view_snapshot(self) -> dict:
        return {name: self._view_get(name) for name in self.VIEW_ITEMS}

    def _apply_view(self, state: dict):
        for name in self.VIEW_ITEMS:
            self._view_set(name, state.get(name, self.VIEW_DEFAULTS[name]))

    def _restore_view(self):
        state = {}
        for name in self.VIEW_ITEMS:
            state[name] = self.settings.value(
                f"view/{name}", self.VIEW_DEFAULTS[name], type=bool)
        # panels are restored from the saved splitter sizes instead
        for name in ("library", "inspector"):
            state[name] = self._view_get(name)
        for name in self.VIEW_ITEMS:
            if name not in ("library", "inspector"):
                self._view_set(name, state[name], save=False)

    def _sync_view_actions(self):
        for name, action in getattr(self, "view_actions", {}).items():
            action.blockSignals(True)
            action.setChecked(self._view_get(name))
            action.blockSignals(False)

    def _place_minimap(self):
        margin = 10 + self.canvas.rail_thickness()   # keep the guide rails clear
        self.minimap.move(margin, self.canvas.height() - self.minimap.height() - margin)
        self.minimap.raise_()
        self._place_stamp_bar()

    def _place_cutout_bar(self):
        if not hasattr(self, "cutout_bar") or not self.cutout_bar.isVisible():
            return
        bar = self.cutout_bar
        margin = 8 + self.canvas.rail_thickness()
        bar.fit_width(self.canvas.width() - 2 * margin)
        left = max(margin, (self.canvas.width() - bar.width()) // 2)
        bar.move(left, margin)
        bar.raise_()

    def _place_stamp_bar(self):
        self._place_cutout_bar()
        if not hasattr(self, "stamp_bar"):
            return
        bar = self.stamp_bar
        bar.adjustSize()
        margin = 10 + self.canvas.rail_thickness()
        left = (self.canvas.width() - bar.width()) // 2
        if self.minimap.isVisibleTo(self.canvas):
            # stay clear of the minimap in the bottom-left corner
            left = max(left, self.minimap.x() + self.minimap.width() + 10)
        bar.move(max(margin, left), self.canvas.height() - bar.height() - margin)
        bar.raise_()

    def eventFilter(self, obj, event):
        if obj is self.canvas and event.type() == QEvent.Type.Resize:
            self._place_minimap()
        return super().eventFilter(obj, event)

    def _toggle_focus_canvas(self):
        """Hide every bar and panel for a clean canvas; press again to restore."""
        if any(self._view_get(n) for n in ("toolbar", "status", "levels", "minimap",
                                           "rails", "stamps", "library", "inspector")):
            self._focus_restore = self._view_snapshot()
            self._apply_view(self.WORKSPACES["Canvas only"])
        else:
            self._apply_view(getattr(self, "_focus_restore", None)
                             or self.WORKSPACES["Standard"])

    # -- command discovery --------------------------------------------------
    def _open_palette(self):
        from ui.command_palette import CommandPalette
        CommandPalette(self, self.menuBar()).exec()

    def _show_canvas_menu(self, global_pos, hit_piece):
        from ui.context_menu import build_canvas_menu
        local = self.canvas.mapFromGlobal(global_pos)
        world = self.canvas.screen_to_world(local.x(), local.y())
        menu = build_canvas_menu(self, hit_piece, world)
        menu.exec(global_pos)
        menu.deleteLater()      # menus are rebuilt each time; don't pile them up

    def _show_backdrop_settings(self):
        """Show the level's Backdrop settings (Node tab, nothing selected)."""
        self.canvas.clear_selection()
        if not self._view_get("inspector"):
            self._view_set("inspector", True)
        self.inspector.setCurrentWidget(self.props)
        self.props.refresh_backdrop()
        self.props.show_backdrop()

    # -- placed guides --------------------------------------------------------
    def _show_guide_menu(self, global_pos, target):
        from ui.context_menu import build_guide_menu
        build_guide_menu(self, target).exec(global_pos)

    def _set_guide_option(self, key, on):
        if key == "show_coordinates":
            self.canvas.set_show_coordinates(on)
        else:
            self.canvas.set_guide_flag(key, on)

    def _sync_guide_actions(self):
        """Reflect the project's guide settings in the menu and inspector."""
        for key, action in getattr(self, "guide_actions", {}).items():
            action.blockSignals(True)
            action.setChecked(bool(getattr(self.project, key, False)))
            action.blockSignals(False)
        if hasattr(self, "props") and hasattr(self.props, "refresh_guide_options"):
            self.props.refresh_guide_options()

    def _edit_guide_position(self, guide_id):
        from ui.guide_dialogs import GuidePositionDialog
        level = self.canvas.level
        guide = level.find_guide(guide_id) if level else None
        if guide is None:
            return
        extent = self.project.canvas_w if guide.axis == "v" else self.project.canvas_h
        dialog = GuidePositionDialog(guide.axis, guide.pos, self.project.cell_size,
                                     self.project.feet_per_square, extent, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.canvas.set_guide_position(guide_id, dialog.position())

    def _guide_layout_dialog(self):
        from ui.guide_dialogs import GuideLayoutDialog
        dialog = GuideLayoutDialog(self.project, len(self.project.levels), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        vertical, horizontal = dialog.positions()
        added = self.canvas.apply_guide_layout(vertical, horizontal, dialog.replace(),
                                               dialog.all_levels())
        self.status.showMessage(f"Guide layout applied ({added} guide(s)).", 5000)

    def _add_guides_around_selection(self, kind):
        if not self.canvas.selected_pieces():
            self.status.showMessage("Select one or more nodes first.", 4000)
            return
        added = self.canvas.add_guides_around_selection(kind)
        self.status.showMessage(f"Added {added} guide(s).", 4000)

    def _copy_guides_to_all_levels(self):
        level = self.canvas.level
        others = [lv for lv in self.project.levels if lv is not level]
        if not level or not others:
            self.status.showMessage("This map has only one level.", 4000)
            return
        mine = sorted((g.axis, g.pos) for g in level.guides)
        overwrite = [lv for lv in others
                     if lv.guides and sorted((g.axis, g.pos) for g in lv.guides) != mine]
        if overwrite:
            answer = QMessageBox.question(
                self, "Copy guides to all levels",
                f"Replace the guides on {len(overwrite)} other level(s) with this "
                "level's guides? You can undo this.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        count = self.canvas.copy_guides_to_all_levels()
        self.status.showMessage(f"Copied {len(level.guides)} guide(s) to {count} level(s).", 5000)

    def _pick_guide_color(self):
        color = choose_color(QColor(self.project.guide_color), self, self.canvas,
                             "Choose guide color")
        if color.isValid():
            self.project.guide_color = color.name()
            self.canvas.update()
            self._mark_dirty()

    def _act(self, label, slot, shortcut=None):
        """Menu action with an optional shortcut."""
        a = QAction(label, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(slot)
        return a

    def _build_menu(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction(self._act("New map", self._new_project, "Ctrl+N"))
        f.addAction(self._act("Open…", self._open, "Ctrl+O"))
        f.addAction(self._act("Save", self._save, "Ctrl+S"))
        f.addAction(self._act("Save As…", self._save_as, "Ctrl+Shift+S"))
        f.addAction("Restore from backup…", self._restore_backup_dialog)
        f.addAction("Find missing images…", self._find_missing_images)
        f.addSeparator()
        f.addAction("Export PNG…", self._export)
        f.addAction("Export PDF…", self._export_pdf)
        f.addAction("Export for Tabletop Simulator…", self._export_tts)
        f.addAction("Export project bundle / PNG pack…", self._export_bundle)
        autosave_menu = f.addMenu("Auto-save")
        self.autosave_actions = {}
        autosave_group = QActionGroup(self)
        autosave_group.setExclusive(True)
        for minutes, label in ((0, "Off"), (1, "Every minute"),
                               (5, "Every 5 minutes"), (10, "Every 10 minutes")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(minutes == self.autosave_interval_minutes)
            action.triggered.connect(
                lambda checked=False, value=minutes: self._set_autosave_interval(value))
            autosave_group.addAction(action)
            autosave_menu.addAction(action)
            self.autosave_actions[minutes] = action
        f.addSeparator()
        f.addAction("System menu (Esc)", lambda: self.overlay.open_menu())
        f.addAction("Exit", self.close)

        e = mb.addMenu("&Edit")
        e.addAction(self._act("Undo", self.undo, "Ctrl+Z"))
        e.addAction(self._act("Redo", self.redo, "Ctrl+Y"))
        e.addSeparator()
        # Ctrl+X/C/V/D/A are handled in keyPressEvent so text boxes and the
        # library keep their own copy/paste/select-all; the menu shows them.
        e.addAction("Cut\tCtrl+X", self._cut)
        e.addAction("Copy\tCtrl+C", self._copy)
        e.addAction("Paste\tCtrl+V", self.canvas.paste)
        e.addAction("Duplicate\tCtrl+D", self.canvas.duplicate)
        e.addAction(self._act("Duplicate as grid…", self._duplicate_as_grid_dialog,
                              "Ctrl+Shift+D"))
        mirror_menu = e.addMenu("Mirror copy")
        mirror_menu.addAction("Across the vertical center line",
                              lambda: self._mirror_selection_center("v"))
        mirror_menu.addAction("Across the horizontal center line",
                              lambda: self._mirror_selection_center("h"))
        mirror_menu.addAction("Across the nearest vertical guide",
                              lambda: self._mirror_selection_nearest("v"))
        mirror_menu.addAction("Across the nearest horizontal guide",
                              lambda: self._mirror_selection_nearest("h"))
        e.addAction("Delete\tDel", self.canvas.delete_selected)
        e.addSeparator()
        select_menu = e.addMenu("Select")
        select_menu.addAction("Select all\tCtrl+A", self._select_all)
        select_menu.addAction("Invert selection\tCtrl+Shift+I", self._invert_selection)
        select_menu.addAction("Deselect\tCtrl+Shift+A", self.canvas.clear_selection)
        select_menu.addAction("Everything on the active layer", self._select_active_layer)
        select_menu.addAction("Similar nodes", self._select_similar)
        arrange_menu = e.addMenu("Arrange")
        arrange_menu.addAction("Bring to front", self.canvas.bring_to_front)
        arrange_menu.addAction("Bring forward", lambda: self.canvas._quick("up"))
        arrange_menu.addAction("Send backward", lambda: self.canvas._quick("down"))
        arrange_menu.addAction("Send to back", self.canvas.send_to_back)
        self.send_level_menu = e.addMenu("Send to level")
        self.send_level_menu.aboutToShow.connect(
            lambda: self._fill_send_level_menu(self.send_level_menu))
        self.swap_menu = e.addMenu("Swap image")
        self.swap_menu.aboutToShow.connect(lambda: self._fill_swap_menu(self.swap_menu))
        e.addSeparator()
        e.addAction("Select similar", self._select_similar)
        e.addAction("Copy style…", self._start_copy_style)
        e.addAction("Replace selected image…", self._replace_selected_image)
        center_menu = e.addMenu("Center selection on canvas")
        center_menu.addAction("Horizontally", lambda: self.canvas.center_selection_on_canvas("h"))
        center_menu.addAction("Vertically", lambda: self.canvas.center_selection_on_canvas("v"))
        center_menu.addAction("Both", lambda: self.canvas.center_selection_on_canvas("both"))
        levels_menu = e.addMenu("Levels")
        levels_menu.addAction("Add level", lambda: self.level_bar._add())
        levels_menu.addAction("Delete current level", lambda: self.level_bar._remove())
        levels_menu.addAction("Move current level earlier", lambda: self.level_bar._shift(-1))
        levels_menu.addAction("Move current level later", lambda: self.level_bar._shift(1))
        e.addAction(self._act("Tighten to visible pixels…", self._tighten_dialog))
        self.act_auto_tighten = QAction("Auto-tighten new nodes to visible pixels", self)
        self.act_auto_tighten.setCheckable(True)
        self.act_auto_tighten.setChecked(self.canvas.auto_tighten)
        self.act_auto_tighten.toggled.connect(self._set_auto_tighten)
        e.addAction(self.act_auto_tighten)
        self.act_allow_overlap = QAction("Allow overlap when aligning / distributing", self)
        self.act_allow_overlap.setCheckable(True)
        self.act_allow_overlap.toggled.connect(self._set_allow_overlap)
        e.addAction(self.act_allow_overlap)
        e.addAction(self._act("Free transform", self._toggle_free_transform, "Ctrl+T"))
        e.addAction("Rotate 90°", lambda: self._rotate_sel(90))
        e.addAction("Group rotate…", self._toggle_group_rotate)

        v = mb.addMenu("&View")
        v.addAction("Canvas size…", self._edit_canvas_size)
        v.addAction("Backdrop (color, floor texture or none)…", self._show_backdrop_settings)
        v.addSeparator()
        self.act_legend_show = QAction("Symbol legend on canvas", self)
        self.act_legend_show.setCheckable(True)
        self.act_legend_show.setShortcut("F6")
        self.act_legend_show.setToolTip("Show the Geomorphs 'Symbols & Abbreviations' legend over the canvas")
        self.act_legend_show.triggered.connect(self._toggle_legend_show)
        v.addAction(self.act_legend_show)
        self.act_legend_export = QAction("Include symbol legend in exports", self)
        self.act_legend_export.setCheckable(True)
        self.act_legend_export.triggered.connect(self._toggle_legend_export)
        v.addAction(self.act_legend_export)
        v.addSeparator()
        # Window-style toggles: everything on screen can be switched off.
        self.view_actions = {}
        shortcuts = {"library": "F2", "inspector": "F3", "toolbar": "F4",
                     "minimap": "F5"}
        for name in self.VIEW_ITEMS:
            action = QAction(self.VIEW_LABELS[name], self)
            action.setCheckable(True)
            if name in shortcuts:
                action.setShortcut(QKeySequence(shortcuts[name]))
            action.toggled.connect(
                lambda checked, n=name: self._view_set(n, checked))
            v.addAction(action)
            self.view_actions[name] = action
        workspace_menu = v.addMenu("Workspace")
        for label in self.WORKSPACES:
            workspace_menu.addAction(
                label, lambda checked=False, k=label: self._apply_view(self.WORKSPACES[k]))
        self.act_focus_canvas = self._act(
            "Canvas only (toggle everything else)", self._toggle_focus_canvas, "Ctrl+\\")
        v.addAction(self.act_focus_canvas)
        v.addAction("Reset panel layout", lambda: (self._reset_layout(),
                                                   self._sync_view_actions()))
        v.addSeparator()
        guides_menu = v.addMenu("Guides")
        self.guide_actions = {}
        for key, label, shortcut in (
                ("show_guides", "Show guides", "Ctrl+;"),
                ("snap_to_guides", "Snap to guides", "Ctrl+Shift+;"),
                ("lock_guides", "Lock guides", "Ctrl+Alt+;"),
                ("show_coordinates", "Grid coordinates on rails", None)):
            action = QAction(label, self)
            action.setCheckable(True)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.toggled.connect(lambda checked, k=key: self._set_guide_option(k, checked))
            guides_menu.addAction(action)
            self.guide_actions[key] = action
        guides_menu.addSeparator()
        guides_menu.addAction("Guide layout…", self._guide_layout_dialog)
        around = guides_menu.addMenu("Add guides around selection")
        around.addAction("At the edges", lambda: self._add_guides_around_selection("edges"))
        around.addAction("Through the center", lambda: self._add_guides_around_selection("center"))
        around.addAction("Edges and center", lambda: self._add_guides_around_selection("both"))
        guides_menu.addAction("Copy guides to all levels", self._copy_guides_to_all_levels)
        guides_menu.addAction("Clear guides on this level", self.canvas.clear_guides)
        guides_menu.addSeparator()
        guides_menu.addAction("Guide color…", self._pick_guide_color)
        zoom_menu = v.addMenu("Zoom")
        for label, shortcut, fn in (
                ("Zoom in", "Ctrl+=", lambda: self.canvas.set_zoom(self.canvas.zoom * 1.25)),
                ("Zoom out", "Ctrl+-", lambda: self.canvas.set_zoom(self.canvas.zoom / 1.25)),
                ("Fit map in view", "Ctrl+0", self.canvas.fit_to_view),
                ("Zoom 50%", None, lambda: self.canvas.set_zoom(0.5)),
                ("Zoom 100%", "Ctrl+1", lambda: self.canvas.set_zoom(1.0)),
                ("Zoom 200%", None, lambda: self.canvas.set_zoom(2.0))):
            zoom_menu.addAction(self._act(label, fn, shortcut))
        v.addSeparator()
        v.addAction("Customize toolbar…", self._customize_toolbar)
        v.addAction("Reset toolbar", self._reset_toolbar)

        t = mb.addMenu("&Theme")
        self.theme_actions = {}
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        for mode, label in (("dark", "Dark"), ("light", "Light"),
                            ("alien", "Alien / MU-TH-UR")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(mode == self.theme_mode)
            action.triggered.connect(
                lambda checked=False, value=mode: self._set_theme_mode(value))
            theme_group.addAction(action)
            t.addAction(action)
            self.theme_actions[mode] = action
        alien_menu = t.addMenu("Alien accent")
        self.alien_accent_actions = {}
        alien_accent_group = QActionGroup(self)
        alien_accent_group.setExclusive(True)
        for name, hexc in (("Green", "#9bff9b"), ("Amber", "#ffb000"),
                           ("Red", "#ff5a5a")):
            action = QAction(name, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, color=hexc: self._set_accent(color))
            alien_accent_group.addAction(action)
            alien_menu.addAction(action)
            self.alien_accent_actions[hexc] = action
        t.addSeparator()
        self.flourish_actions = {}
        for label, attr in (("Scanlines", "flourish_scanlines"),):
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, key=attr: self._toggle_flourish(key))
            t.addAction(action)
            self.flourish_actions[attr] = action

        t = mb.addMenu("&Tools")
        t.addAction("Generate Map…", self._open_generator)
        t.addAction("Geomorph Generator (ships && sites)…", self._open_geomorph)
        t.addAction("Ruler / measure", self._start_ruler_tool)
        t.addAction("Add static scale bar", self._start_scale_tool)
        t.addAction("Add connection / transition marker", self._start_connector_tool)
        t.addAction("Lasso select", self._start_lasso_tool)
        t.addAction("Stamp selected node", self._start_stamp_tool)
        t.addAction("Crop selected image…", self._start_crop_tool)
        cut_menu = t.addMenu("Cut out part of an image")
        for shape, label in (("rect", "Rectangle"), ("ellipse", "Ellipse / circle"),
                             ("lasso", "Lasso (freehand)"), ("polygon", "Polygon")):
            cut_menu.addAction(label, lambda s=shape: self._start_cutout_tool(s))
        t.addAction("Clone patch over a label", self._start_clone_tool)
        t.addSeparator()
        t.addAction("Draw rectangle gameplay zone",
                    lambda: self.canvas.set_zone_tool("rectangle"))
        t.addAction("Draw polygon gameplay zone",
                    lambda: self.canvas.set_zone_tool("polygon"))
        t.addAction("Finish polygon zone", self.canvas.finish_zone_polygon)
        t.addAction("Draw raster-label patch",
                    lambda: self.canvas.set_patch_tool(True))

        hm = mb.addMenu("&Help")
        hm.addAction(self._act("Command palette…", self._open_palette, "Ctrl+Shift+P"))
        hm.addSeparator()
        hm.addAction("About", self._about)

    def _build_toolbar(self):
        specs = [
            ("new", "New", self._new_project, "Create a new map."),
            ("open", "Open", self._open, "Open a saved map."),
            ("save", "Save", self._save, "Save the current map."),
            ("export", "Export", self._export, "Export the current map."),
            ("bundle", "Pack", self._export_bundle,
             "Create a portable project bundle with assets and PNG renders."),
            ("undo", "Undo", self.undo, "Undo the last change."),
            ("redo", "Redo", self.redo, "Redo the last undone change."),
            ("text", "Text", self._add_text, "Add an editable text label."),
            ("stamp", "Stamp", self._start_stamp_tool,
             "Repeatedly place copies of the selected node."),
            ("mirror", "Mirror", self._show_mirror_menu_at_cursor,
             "Place mirrored copies of the selection across a guide or the "
             "map's center line."),
            ("grid_copy", "Grid Copy", self._duplicate_as_grid_dialog,
             "Repeat the selection in rows and columns."),
            ("crop", "Crop", self._start_crop_tool,
             "Crop the selected image non-destructively."),
            ("cutout", "Cut Out", self._start_cutout_tool,
             "Select part of an image (rectangle, ellipse, lasso or polygon), then "
             "delete it, cut or copy it, or make it a new node."),
            ("clone", "Clone", self._start_clone_tool,
             "Cover a baked-in label with a clean piece of the same picture."),
            ("ruler", "Ruler", self._start_ruler_tool,
             "Measure map distance; hold Shift to snap endpoints to the grid."),
            ("scale", "Scale", self._start_scale_tool,
             "Draw a static scale bar with the current map calibration."),
            ("connector", "Link", self._start_connector_tool,
             "Draw a labeled transition / connection marker."),
            ("lasso", "Lasso", self._start_lasso_tool,
             "Lasso-select nodes by their centers."),
            ("similar", "Similar", self._select_similar,
             "Select other nodes similar to the current selection."),
            ("copy_style", "Copy Style", self._start_copy_style,
             "Apply the selected node's formatting to other nodes."),
            ("patch", "Patch",
             lambda: self.canvas.set_patch_tool(True),
             "Draw a separate cover patch over rasterized image lettering."),
            ("zone_rect", "Rect Zone",
             lambda: self.canvas.set_zone_tool("rectangle"),
             "Draw an independent rectangular gameplay zone."),
            ("zone_poly", "Poly Zone",
             lambda: self.canvas.set_zone_tool("polygon"),
             "Draw an independent polygonal gameplay zone."),
            ("zone_finish", "Finish Zone", self.canvas.finish_zone_polygon,
             "Finish the polygon gameplay zone currently being drawn."),
            ("group_rotate", "Group Rot", self._toggle_group_rotate,
             "Rotate a multi-node selection around its center."),
            ("generate", "Generate", self._open_generator,
             "Build a map from the assets selected in the library."),
            ("import", "Import", self.library._import_folder,
             "Import an asset folder into the library."),
        ]
        actions = {}
        for tool_id, label, callback, tooltip in specs:
            action = QAction(label, self)
            action.setToolTip(tooltip)
            action.triggered.connect(
                lambda checked=False, fn=callback: fn())
            actions[tool_id] = action
        self.toolbar = CustomizableToolBar(
            "Main toolbar", actions, self.settings, self,
            default_visible=COMPACT_TOOLBAR, layout_version=2)
        self.toolbar.customizeRequested.connect(self._customize_toolbar)
        self.addToolBar(self.toolbar)

    def _customize_toolbar(self):
        if not hasattr(self, "toolbar"):
            return
        CustomizeToolbarDialog(self.toolbar, self).exec()

    def _reset_toolbar(self):
        if hasattr(self, "toolbar"):
            self.toolbar.reset_to_default()

    def _build_status(self):
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.lbl_cursor = QLabel("")
        self.lbl_zoom = QLabel("")
        self.lbl_sel = QLabel("")
        self.lbl_hist = QLabel("")
        self.status.addPermanentWidget(self.lbl_sel, 2)
        self.status.addPermanentWidget(self.lbl_cursor, 1)
        for text, tip, fn in (("−", "Zoom out", lambda: self.canvas.set_zoom(self.canvas.zoom / 1.25)),
                              ("+", "Zoom in", lambda: self.canvas.set_zoom(self.canvas.zoom * 1.25)),
                              ("Fit", "Fit the whole map in view", self.canvas.fit_to_view)):
            zoom_button = QPushButton(text)
            zoom_button.setObjectName("StatusZoomButton")
            zoom_button.setFlat(True)
            zoom_button.setToolTip(tip)
            zoom_button.setMinimumWidth(30)
            zoom_button.clicked.connect(lambda checked=False, f=fn: f())
            self.status.addPermanentWidget(zoom_button, 0)
        self.status.addPermanentWidget(self.lbl_zoom, 0)
        self.status.addPermanentWidget(self.lbl_hist, 1)
        self.canvas.colorPickStateChanged.connect(self._on_color_pick_state)
        self.canvas.statusMessage.connect(lambda msg: self.status.showMessage(msg, 5000))

    def _on_color_pick_state(self, active: bool):
        if active:
            self.status.showMessage(
                "Eyedropper active — click a map pixel; Esc or right-click cancels.")
        else:
            self.status.clearMessage()

    # ------------------------------------------------------------------
    # Launch
    # ------------------------------------------------------------------
    def _show_launch(self):
        """Open the in-window system menu over the editor (replaces the old
        modal launcher; the map behind it starts as a fresh empty project)."""
        self._new_project(preserve_recovery=True, choose_canvas_size=False)
        self._apply_theme()
        if os.path.exists(self._recovery_file):
            QTimer.singleShot(0, self._offer_recovery)
        else:
            QTimer.singleShot(0, self.overlay.open_menu)

    def _start_warmup(self):
        """Build the slow one-time caches (tile previews, image sizes) off the
        GUI thread; a loading screen shows only when there is real work."""
        if getattr(self, "_warmup", None) is not None and self.project.asset_store:
            self._warmup.start(self.project.asset_store)

    def _bundled_pack_is_installed(self, archive_path: str) -> bool:
        """Avoid re-extracting a bundled pack after its first successful import."""
        store = self.project.asset_store
        if not store or not os.path.isdir(store):
            return False
        stem = os.path.splitext(os.path.basename(archive_path))[0]
        source_name = os.path.basename(archive_path)
        try:
            candidates = os.listdir(store)
        except OSError:
            return False
        for folder in candidates:
            if folder != stem and not folder.startswith(stem + " ("):
                continue
            marker = os.path.join(store, folder, ".sceneboard-import.json")
            try:
                with open(marker, encoding="utf-8") as fh:
                    metadata = json.load(fh)
                if (metadata.get("format") == "sceneboard-asset-archive" and
                        metadata.get("source") == source_name):
                    return True
            except (OSError, ValueError, TypeError):
                continue
        return False

    def _install_bundled_asset_packs(self):
        """Install ZIP packs embedded in a packaged build on first launch."""
        archives = bundled_asset_pack_paths(__file__)
        if not archives:
            return
        self._ensure_store()
        pending = [path for path in archives
                   if not self._bundled_pack_is_installed(path)]
        if not pending:
            return
        self._bundled_pack_install_active = True
        started = self.library.import_zip_paths(
            pending,
            status_text=("Installing built-in high-resolution assets. "
                         "First launch may take a few minutes…"),
            progress_callback=self._bundled_pack_progress,
            completed_callback=self._bundled_packs_imported)
        if not started:
            self._bundled_pack_install_active = False

    def _bundled_pack_progress(self, archive_name: str, current: int, total: int):
        if getattr(self, "_bundled_pack_install_active", False):
            self.status.showMessage(
                f"Installing built-in asset pack {current}/{total}: "
                f"{archive_name} — one-time setup.")

    def _bundled_packs_imported(self, reports, errors):
        if not getattr(self, "_bundled_pack_install_active", False):
            return
        self._bundled_pack_install_active = False
        if errors:
            self.status.showMessage(
                "Some built-in asset packs could not be installed; see the warning.",
                15000)
        else:
            count = sum(report.imported + report.already_imported
                        for report in reports)
            self.status.showMessage(
                f"Built-in high-resolution assets ready ({count:,} images).", 12000)
            self._start_warmup()

    def _offer_recovery(self):
        if not os.path.isfile(self._recovery_file):
            self.overlay.open_menu()
            return
        answer = QMessageBox.question(
            self, "Recover auto-saved map?",
            "A recent auto-save recovery copy was found. Restore it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Yes:
            try:
                with open(self._recovery_file, "r", encoding="utf-8") as fh:
                    self.project = Project.from_dict(json.load(fh))
                self._settle_asset_store()
                self._current_file = None
                self._apply_project()
                self._mark_dirty(True)
                self.status.showMessage(
                    "Recovered auto-saved work. Use Save As to choose a project file.", 10000)
                self._remove_recovery()
                return
            except Exception as exc:
                QMessageBox.warning(self, "Recovery failed", str(exc))
        self._remove_recovery()
        self.overlay.open_menu()

    # ------------------------------------------------------------------
    def _apply_project(self):
        # Generated-output ownership is session-local and must never point into
        # levels from a project that was just replaced or reloaded.
        self._gen_output = None
        if not self.project.asset_store:
            self._ensure_store()
        self.canvas.set_project(self.project, self.library.library)
        self.props.set_project(self.project)
        self.library.set_project(self.project, self.library.library, self._add_at_center)
        self.level_bar.set_project(self.project)
        self.layers.set_project(self.project, self.project.levels[self.canvas.level_index])
        self.zones.set_project(self.project)
        self.canvas.level_index = 0
        self._resync_history()
        self._apply_theme()
        self._sync_guide_actions()
        self._place_minimap()
        self._refresh_stamp_bar()

    def _apply_theme(self):
        app = QApplication.instance()
        colors = thememod.theme_colors(self.theme_mode, self.theme_accent)
        accent = colors["accent"]
        text_scale = self.project.text_scale
        thememod.apply_stylesheet(app, self.theme_accent, text_scale,
                                  self.theme_mode)
        # bigger text needs a wider inspector before its rows can fit
        self.inspector.setMinimumWidth(inspector_min_width(text_scale))
        self.canvas.set_theme(self.theme_mode, self.theme_accent)
        self.minimap.set_theme(self.theme_mode, self.theme_accent)
        self.layers.set_theme(self.theme_mode, self.theme_accent)
        if hasattr(self, "stamp_bar"):
            self.stamp_bar.set_theme(self.theme_mode, self.theme_accent)
            self._refresh_stamp_bar()          # text/marker glyphs follow the theme
        if hasattr(self, "cutout_bar"):
            self.cutout_bar.set_theme(self.theme_mode, self.theme_accent)
        self.scanlines.set_accent(accent)
        self.scanlines.setVisible(
            self.theme_mode == "alien" and self.alien_scanlines)
        if hasattr(self, "overlay"):
            self.overlay._sync_theme()
        self._sync_theme_actions()
        self._update_window_title()

    def _sync_theme_actions(self):
        for mode, action in getattr(self, "theme_actions", {}).items():
            action.setChecked(mode == self.theme_mode)
        for attr, action in getattr(self, "flourish_actions", {}).items():
            action.setChecked({
                "flourish_scanlines": self.alien_scanlines,
            }.get(attr, False))
            action.setEnabled(self.theme_mode == "alien")
        for hexc, action in getattr(self, "alien_accent_actions", {}).items():
            action.setChecked(hexc.lower() == self.theme_accent.lower())

    # ------------------------------------------------------------------
    # Theme and preferences helpers
    # ------------------------------------------------------------------
    def _set_theme_mode(self, mode: str):
        mode = mode.lower()
        if mode not in thememod.THEME_MODES:
            return
        self.theme_mode = mode
        self.settings.setValue("appearance/theme", mode)
        self._apply_theme()

    def _set_accent(self, hexc):
        color = QColor(hexc)
        if not color.isValid():
            return
        self.theme_accent = color.name()
        self.settings.setValue("appearance/alien_accent", self.theme_accent)
        self._set_theme_mode("alien")

    def _set_alien_effects(self, scanlines: bool):
        self.alien_scanlines = bool(scanlines)
        self.settings.setValue("appearance/alien_scanlines", self.alien_scanlines)
        self._apply_theme()

    def _set_autosave_interval(self, minutes: int):
        if minutes not in self.AUTOSAVE_CHOICES:
            return
        self.autosave_interval_minutes = minutes
        self.settings.setValue("autosave/interval_minutes", minutes)
        if minutes:
            self._autosave_timer.start(minutes * 60 * 1000)
        else:
            self._autosave_timer.stop()
        for value, action in getattr(self, "autosave_actions", {}).items():
            action.setChecked(value == minutes)
        if hasattr(self, "overlay"):
            self.overlay._sync_theme()
        if hasattr(self, "status"):
            label = "off" if minutes == 0 else f"every {minutes} min"
            self.status.showMessage(f"Auto-save set to {label}.", 3000)

    def _toggle_flourish(self, attr):
        if self.theme_mode != "alien":
            return
        if attr == "flourish_scanlines":
            self._set_alien_effects(not self.alien_scanlines)

    # ------------------------------------------------------------------
    # Project lifecycle
    # ------------------------------------------------------------------
    def _new_project(self, preserve_recovery=False, choose_canvas_size=True):
        canvas_size = None
        if choose_canvas_size:
            dialog = CanvasSizeDialog(
                title="New map canvas", accept_label="Create map", parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            canvas_size = dialog.canvas_size()

        if not preserve_recovery and self._dirty and not self._confirm_discard():
            return False
        if not preserve_recovery:
            self._remove_recovery()
        self.project = new_project()
        try:
            self.project.text_scale = float(self.settings.value(
                "appearance/text_scale", self.project.text_scale))
        except (TypeError, ValueError):
            pass
        if canvas_size:
            self.project.map_cols = canvas_size["map_cols"]
            self.project.map_rows = canvas_size["map_rows"]
            self.project.cell_size = canvas_size["cell_size"]
            self.project._sync_canvas()
        self._current_file = None
        self._apply_project()
        self._mark_dirty(False)
        self._update_window_title()
        return True

    def _edit_canvas_size(self):
        """Resize the existing canvas without removing or moving any nodes."""
        dialog = CanvasSizeDialog(
            self.project.map_cols, self.project.map_rows, self.project.cell_size,
            title="Resize canvas", accept_label="Apply", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        canvas_size = dialog.canvas_size()
        if (canvas_size["map_cols"] == self.project.map_cols
                and canvas_size["map_rows"] == self.project.map_rows
                and canvas_size["cell_size"] == self.project.cell_size):
            return False
        self.canvas.push_history("Resize canvas")
        self.project.map_cols = canvas_size["map_cols"]
        self.project.map_rows = canvas_size["map_rows"]
        self.project.cell_size = canvas_size["cell_size"]
        self.project._sync_canvas()
        self.props.refresh_canvas_size()
        self.canvas.fit_to_view()
        self.canvas.dirty.emit()
        return True

    def _ensure_store(self):
        default_store = default_asset_store_path(__file__, self._app_data_dir)
        if not self.project.asset_store:
            self.project.asset_store = default_store
        os.makedirs(self.project.asset_store, exist_ok=True)
        if os.path.normcase(os.path.abspath(self.project.asset_store)) == \
                os.path.normcase(default_store):
            seed_bundled_assets(self.project.asset_store, __file__)

    def _default_store(self) -> str:
        return default_asset_store_path(__file__, self._app_data_dir)

    @staticmethod
    def _same_path(a: str, b: str) -> bool:
        return bool(a) and bool(b) and (os.path.normcase(os.path.abspath(a)) ==
                                        os.path.normcase(os.path.abspath(b)))

    def _settle_asset_store(self) -> str:
        """Pick the asset folder for a just-loaded map. A map records the folder
        it was made with; when that folder isn't on this computer (another PC,
        another Windows account, a moved folder) the map uses this app's own
        library instead, and image lookups fall back to it either way.
        Returns a note for the status bar, or ""."""
        default_store = self._default_store()
        saved = self.project.asset_store
        note = ""
        if saved:
            chosen = choose_asset_store(saved, default_store,
                                        self.project.referenced_assets())
            if not self._same_path(chosen, saved):
                self.project.asset_store = chosen
                note = ("This map's asset folder isn't on this computer, so it uses "
                        "this app's asset library instead.")
        store = self.project.asset_store
        self.project.fallback_asset_stores = (
            [default_store] if store and not self._same_path(store, default_store) else [])
        return note

    def _project_data_for_disk(self) -> dict:
        """Saved form of the map. The app's own asset library is stored as ""
        ("this app's library"), so the map finds its images on any computer
        where the same packs are installed, instead of a path from this PC."""
        data = self.project.to_dict()
        if self._same_path(data.get("asset_store") or "", self._default_store()):
            data["asset_store"] = ""
        return data

    def _last_dir(self, key: str = "files/last_dir") -> str:
        saved = str(self.settings.value(key, "") or "")
        if saved and os.path.isdir(saved):
            return saved
        if key != "files/last_dir":
            return self._last_dir()
        documents = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation)
        return documents if documents and os.path.isdir(documents) else os.path.expanduser("~")

    def _remember_dir(self, path: str, key: str = "files/last_dir"):
        if not path:
            return
        folder = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
        if folder:
            self.settings.setValue(key, folder)

    def _map_base_name(self) -> str:
        return map_base_name(self.project.name, self._current_file)

    def _open(self):
        if self._dirty and not self._confirm_discard():
            return
        fn, _ = QFileDialog.getOpenFileName(
            self, "Open project or map pack", self._last_dir(),
            f"Map projects and packs (*{BMAP_EXT} *.rpgpack);;Map projects (*{BMAP_EXT});;RPG Map Packs (*.rpgpack)")
        if fn:
            self._load_file(fn)

    def _load_file(self, fn):
        try:
            opened_bundle = str(fn).lower().endswith(".rpgpack")
            bundle_manifest = None
            if opened_bundle:
                pack_path = fn
                bundle_manifest = bundle.read_bundle_manifest(pack_path)
                fn = bundle.unpack_project_bundle(pack_path, self._bundle_extract_root)
            with open(fn, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.project = Project.from_dict(data)
            if self.project.asset_store and not os.path.isabs(self.project.asset_store):
                self.project.asset_store = os.path.abspath(os.path.join(
                    os.path.dirname(fn), self.project.asset_store))
            store_note = self._settle_asset_store()
            self._current_file = fn
            self._apply_project()
            self._mark_dirty(False)
            self._update_window_title()
            self._remove_recovery()
            self._push_recent(fn)
            self._remember_dir(pack_path if opened_bundle else fn)
            if opened_bundle:
                missing = bundle_manifest.get("missing_assets", []) if bundle_manifest else []
                message = "Map pack opened as a local working copy. Save or export a new pack when ready."
                if missing:
                    message += f" {len(missing)} source asset(s) were missing from the pack."
                self.status.showMessage(message, 12000)
            elif store_note:
                self.status.showMessage(store_note, 12000)
        except Exception as e:
            QMessageBox.critical(self, "Open failed", str(e))
            return False
        # tell the user right away if some images can't be found
        QTimer.singleShot(0, lambda: self._find_missing_images(quiet_if_none=True))
        return True

    def _save(self) -> bool:
        if not self._current_file:
            return self._save_as()
        return self._write(self._current_file)

    def _save_as(self) -> bool:
        start = os.path.join(self._last_dir(), self._map_base_name() + BMAP_EXT)
        fn, _ = QFileDialog.getSaveFileName(self, "Save project", start,
                                            f"Map projects (*{BMAP_EXT})")
        if not fn:
            return False
        if not fn.lower().endswith(BMAP_EXT):
            fn += BMAP_EXT
        self._current_file = fn
        saved = self._write(fn)
        self._remember_dir(fn)
        self._update_window_title()
        return saved

    @staticmethod
    def _atomic_json_write(data, path):
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        temp_path = path + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _write(self, fn, autosave=False):
        try:
            self._atomic_json_write(self._project_data_for_disk(), fn)
            try:
                # kept in app data: a "<map>.png" beside the map could overwrite
                # an export with the same name
                exporter.save_thumbnail(self.project, fn,
                                        thumb_path=thumbnail_path(self._app_data_dir, fn))
            except Exception:
                pass                     # a preview must never block saving
            if not autosave:
                self._push_recent(fn)
                self._remove_recovery()
            self._mark_dirty(False)
            return True
        except Exception as exc:
            if autosave:
                if hasattr(self, "status"):
                    self.status.showMessage(f"Auto-save failed: {exc}", 10000)
            else:
                QMessageBox.critical(self, "Save failed", str(exc))
            return False

    def _autosave_tick(self):
        if not self._dirty:
            return
        if self._current_file:
            self._backup_before_autosave(self._current_file)
            if self._write(self._current_file, autosave=True):
                self.status.showMessage("Auto-saved project.", 4000)
            return
        try:
            os.makedirs(self._autosave_dir, exist_ok=True)
            self._atomic_json_write(self._project_data_for_disk(), self._recovery_file)
            meta = {"project_name": self.project.name,
                    "updated": time.time()}
            self._atomic_json_write(meta, self._recovery_meta)
            self.status.showMessage(
                "Auto-saved recovery copy. Use Save As to keep this map permanently.",
                7000)
        except Exception as exc:
            self.status.showMessage(f"Auto-save failed: {exc}", 10000)

    def _remove_recovery(self):
        for path in (self._recovery_file, self._recovery_meta,
                     self._recovery_file + ".tmp", self._recovery_meta + ".tmp"):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    # -- recent maps, thumbnails and backups (all kept in app data) -------------
    def _load_recent_list(self) -> list:
        global RECENT_FILE
        RECENT_FILE = self._recent_file
        items = load_path_list(self._recent_file)
        if (not items and not os.path.exists(self._recent_file)
                and os.path.isfile(LEGACY_RECENT_FILE)):
            items = load_path_list(LEGACY_RECENT_FILE)   # carry over a source checkout's list
            if items:
                save_path_list(items, self._recent_file)
        return items

    def _save_recent(self) -> bool:
        return save_path_list(self.recent, self._recent_file)

    def _push_recent(self, fn):
        fn = os.path.abspath(fn)
        self.recent = [fn] + [x for x in self.recent if not self._same_path(x, fn)]
        self.recent = self.recent[:12]
        self._save_recent()

    def _thumbnail_for(self, map_path: str) -> str:
        """Preview for a recent map: the app's own copy, else the "<map>.png"
        that older versions wrote beside the map."""
        own = thumbnail_path(self._app_data_dir, map_path)
        if os.path.isfile(own):
            return own
        legacy = os.path.splitext(map_path)[0] + ".png"
        return legacy if os.path.isfile(legacy) else ""

    def _delete_map_thumbnails(self, map_path: str):
        """Remove a deleted map's previews. A "<map>.png" beside it is only
        removed when it is thumbnail-sized, so an export is never deleted."""
        from PyQt6.QtGui import QImageReader
        own = thumbnail_path(self._app_data_dir, map_path)
        legacy = os.path.splitext(map_path)[0] + ".png"
        candidates = [own]
        size = QImageReader(legacy).size() if os.path.isfile(legacy) else None
        if size is not None and size.isValid() and max(size.width(), size.height()) <= 256:
            candidates.append(legacy)
        for path in candidates:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass

    def _backup_folder(self) -> Optional[str]:
        return (backup_folder(self._backup_root, self._current_file)
                if self._current_file else None)

    def _backup_before_autosave(self, fn: str):
        """Keep the version that is on disk before auto-save replaces it, in the
        oldest of the four backup slots."""
        try:
            rotate_backup(fn, backup_folder(self._backup_root, fn), BACKUP_SLOTS)
        except OSError as exc:
            self.status.showMessage(f"Could not write a backup: {exc}", 8000)

    def _open_folder(self, folder: str):
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _restore_backup_dialog(self):
        if not self._current_file:
            QMessageBox.information(
                self, "Restore from backup",
                "Backups are kept for maps that have been saved. Save this map "
                f"first; auto-save then keeps its last {BACKUP_SLOTS} versions.")
            return
        from ui.tool_dialogs import BackupDialog
        folder = self._backup_folder()
        dialog = BackupDialog(os.path.basename(self._current_file),
                              list_backups(folder, BACKUP_SLOTS),
                              self.autosave_interval_minutes,
                              lambda: self._open_folder(folder), self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.chosen_path:
            self._restore_backup(dialog.chosen_path)

    def _restore_backup(self, path: str) -> bool:
        """Load a backup into the editor as one undoable step; the map file on
        disk is only replaced when the user saves."""
        from ui.tool_dialogs import describe_time
        try:
            with open(path, encoding="utf-8") as fh:
                restored = Project.from_dict(json.load(fh))
            when = describe_time(os.path.getmtime(path))
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Restore failed", str(exc))
            return False
        store = self.project.asset_store
        fallbacks = list(self.project.fallback_asset_stores)
        self.canvas.push_history("Restore backup")
        self.project.restore_from(restored)
        if not self.project.asset_store:
            self.project.asset_store = store
        self.project.fallback_asset_stores = fallbacks
        self._after_history(fit_canvas=True)
        self._mark_dirty(True)
        self.status.showMessage(
            f"Restored the backup from {when}. Save to keep it, or undo to go back.", 12000)
        return True

    # -- missing images -----------------------------------------------------------
    def _relink_missing(self):
        """Relink missing images by file name; returns
        (relinked, ambiguous, not found, images still missing)."""
        missing = self.project.missing_assets()
        known = [asset.path for asset in self.library.library.assets]
        plan, ambiguous, not_found = relink_plan(list(missing), known)
        if plan:
            self.canvas.push_history("Relink missing images")
            self.project.relink_assets(plan)
            self.canvas.update()
            self.canvas.dirty.emit()
            self._refresh_stamp_bar()
        return len(plan), len(ambiguous), len(not_found), self.project.missing_assets()

    def _find_missing_images(self, quiet_if_none: bool = False):
        missing = self.project.missing_assets()
        if not missing:
            if not quiet_if_none:
                used = len(self.project.referenced_assets())
                QMessageBox.information(
                    self, "Missing images",
                    f"All {used} library image(s) this map uses were found." if used
                    else "This map doesn't use any library images yet.")
            return
        from ui.tool_dialogs import MissingAssetsDialog
        MissingAssetsDialog(missing, self.project.asset_store, self._relink_missing,
                            self).exec()

    # ------------------------------------------------------------------
    def _export(self):
        from ui.export_dialog import ExportDialog
        ExportDialog(self.project, self.canvas, self, file_format="png").exec()

    def _export_pdf(self):
        from ui.export_dialog import ExportDialog
        ExportDialog(self.project, self.canvas, self, file_format="pdf").exec()

    def _export_tts(self):
        from ui.export_dialog import ExportDialog
        ExportDialog(self.project, self.canvas, self, file_format="png",
                     default_preset="Tabletop Sim (2048px)").exec()

    def export_defaults(self) -> tuple[str, str]:
        """(base file name, folder) for export dialogs: named after the map, in
        the folder used for the last export."""
        return self._map_base_name(), self._last_dir("files/last_export_dir")

    def remember_export_dir(self, path: str):
        self._remember_dir(path, "files/last_export_dir")

    def _export_bundle(self):
        base, folder = self.export_defaults()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export project bundle and PNG pack",
            os.path.join(folder, base + ".rpgpack"), "RPG Map Pack (*.rpgpack)")
        if not path:
            return
        if not path.lower().endswith(".rpgpack"):
            path += ".rpgpack"
        self.remember_export_dir(path)
        try:
            bundle.export_project_bundle(self.project, path, include_renders=True)
            manifest = bundle.read_bundle_manifest(path)
            missing = manifest.get("missing_assets", [])
            if missing:
                QMessageBox.warning(
                    self, "Bundle exported with missing sources",
                    f"The pack was created, but {len(missing)} referenced source asset(s) "
                    "could not be included. Check the manifest inside the pack.")
            else:
                self.status.showMessage(f"Project bundle exported: {path}", 8000)
        except Exception as exc:
            QMessageBox.critical(self, "Bundle export failed", str(exc))

    # ------------------------------------------------------------------
    def _open_generator(self, _checked=False, paths=None):
        """Open the map generator for the assets selected in the library.

        ``paths`` overrides that selection with an explicit list of asset
        paths (the library's right-click menu uses it, so the entry works even
        when the list has since been filtered to another folder).
        """
        selection = None
        if paths:
            wanted = set(paths)
            selection = [asset for asset in self.library.library.assets
                         if asset.path in wanted]
        dlg = GeneratorDialog(self.project, self.library, self.canvas,
                              self._run_generator, self, selection=selection)
        dlg.exec()

    # ---- symbol legend (Geomorphs 'Symbols & Abbreviations') ----
    def _sync_legend(self, warn=False):
        from core import legend
        legend.STATE["path"] = legend.find_in_library(
            self.library.library, getattr(getattr(self, "_geomorph_dialog", None), "tiles_dir", ""))
        legend.STATE["show"] = bool(self.settings.value("legend/show", False, bool)) and bool(legend.STATE["path"])
        legend.STATE["export"] = bool(self.settings.value("legend/export", False, bool)) and bool(legend.STATE["path"])
        self.act_legend_show.setChecked(legend.STATE["show"])
        self.act_legend_export.setChecked(legend.STATE["export"])
        if warn and not legend.STATE["path"]:
            QMessageBox.information(self, "Symbol legend",
                                    "The legend image ('Symbols & Abbreviations.png') comes with the Geomorphs ZIP. "
                                    "Import that ZIP into the library and try again.")
        self.canvas.update()

    def _toggle_legend_show(self, on):
        self.settings.setValue("legend/show", bool(on))
        self._sync_legend(warn=bool(on))

    def _toggle_legend_export(self, on):
        self.settings.setValue("legend/export", bool(on))
        self._sync_legend(warn=bool(on))

    def _open_geomorph(self, _checked=False):
        """Ships and sites built from the Starship Geomorphs tiles."""
        from ui.geomorph_dialog import GeomorphDialog
        dlg = GeomorphDialog(self, self)
        self._geomorph_dialog = dlg
        dlg.exec()

    def _place_geomorph(self, res, registry, images, replace_prev=True):
        """Add a generated ship/site to the map: one new level per deck.

        Tiles use the library's copy of each tile PNG (found by file name);
        procedural filler is embedded; the key and markers are text nodes on
        their own layers. Nothing is touched until the result is non-empty.
        """
        import tempfile
        from geomorph import canvas_export
        cs = self.project.cell_size
        resolver = canvas_export.library_resolver(self.library.library.assets)
        out = canvas_export.to_canvas(res, cs, resolver, images, tempfile.mkdtemp(prefix="geomorph-filler-"))
        if out["wanted"] and not out["tiles"]:
            QMessageBox.warning(self, "Geomorph generator",
                                "Nothing could be placed. " + " ".join(out["warnings"]) +
                                " Import the tile pack ZIP into the library, or set the tile folder in the generator.")
            return None
        self.canvas.push_history("Generate geomorph map")
        if replace_prev and self._gen_output and "geomorph" in self._gen_output:
            self._discard_gen_output("geomorph")
        if self._gen_output is None:
            self._gen_output = {}
        output = self._gen_output.setdefault("geomorph", {"ids": [], "levels": []})
        cols, rows = out["grid"]
        if cols > self.project.map_cols or rows > self.project.map_rows:
            self.project.map_cols = max(self.project.map_cols, cols)
            self.project.map_rows = max(self.project.map_rows, rows)
            self.project._sync_canvas()
            self.props.set_project(self.project)
        from core.project import Piece
        existing = {level.name for level in self.project.levels}
        first_new = len(self.project.levels)
        for lv in out["levels"]:
            base = f"{out['title']} — {lv['name']}".strip(" —")
            name, n = base, 2
            while name in existing:
                name, n = f"{base} ({n})", n + 1
            existing.add(name)
            level = self.project.add_level(name)
            layer_ids = {}
            for d in lv["pieces"]:
                lname = d.get("layer_name") or "Geomorph"
                if lname not in layer_ids:
                    layer_ids[lname] = self._ensure_layer(level, lname)
                fields = {k: v for k, v in d.items() if k in Piece.__dataclass_fields__ and k not in ("layer", "id")}
                piece = Piece(layer=layer_ids[lname], **fields)
                level.add(piece)
                output["ids"].append(piece.id)
            output["levels"].append(level)
        self.level_bar.refresh()
        self.level_bar.tabs.setCurrentIndex(first_new)
        self.canvas.set_level(first_new)
        self.layers.set_project(self.project, self.project.levels[first_new])
        self.canvas.fit_to_view()
        self.zones.refresh_level()
        self.canvas.update()
        self._mark_dirty()
        if out["warnings"]:
            QMessageBox.information(self, "Geomorph generator", "\n".join(out["warnings"]))
        return out

    def _generate_from_paths(self, paths):
        """Library right-click: build a map from exactly these assets."""
        if paths:
            self.library.select_paths(paths)
        self._open_generator(paths=list(paths or ()))

    def _ensure_layer(self, level, name):
        for l in level.layers:
            if l.name == name:
                return l.id
        from core.project import Layer, uuid
        l = Layer(name=name, id=uuid.uuid4().hex)
        level.layers.append(l)
        return l.id

    def _run_generator(self, opts) -> Optional[dict]:
        """Build a map from the selected assets.

        ``opts`` comes from the generator dialog: the selected assets, the
        layout, the size and the seed. Generate keeps earlier generated
        outputs; Regenerate sends ``replace_prev=True`` and replaces the output
        tracked for that destination, but only after a non-empty new map has
        been generated.
        """
        from core import mapbuilder
        from core.project import Piece
        cs = self.project.cell_size
        selection = opts.get("selection") or []
        if not selection:
            QMessageBox.warning(
                self, "Generator",
                "Select the assets you want on the map in the library panel "
                "first.")
            return None

        mode = opts.get("mode") or "new"
        sel = self.canvas.selected_pieces()
        if mode == "area" and sel:
            minx = min(min(p.x, p.x + p.vis_w) for p in sel)
            maxx = max(max(p.x + p.vis_w, p.x) for p in sel)
            miny = min(min(p.y, p.y + p.vis_h) for p in sel)
            maxy = max(max(p.y + p.vis_h, p.y) for p in sel)
            region = (int(math.floor(minx / cs)), int(math.floor(miny / cs)),
                      int(math.floor(maxx / cs)), int(math.floor(maxy / cs)))
            new_level = False
        else:
            new_level = True
            if opts.get("auto_size"):
                # Size the map to the selection so large rooms always fit.
                region = mapbuilder.suggest_region(dict(opts, cell_size=cs))
                opts = dict(opts, quiet_unused=True)
            else:
                cols, rows = opts.get("size") or (self.project.map_cols,
                                                  self.project.map_rows)
                region = (0, 0, int(cols) - 1, int(rows) - 1)

        result = mapbuilder.build_map(dict(opts, cell_size=cs, region=region))
        if opts.get("auto_size"):
            # Random scatter can still drop a piece; grow the automatic area
            # until everything the user picked actually lands.
            for _growth in range(4):
                if not (result.get("counts") or {}).get("skipped"):
                    break
                region = (0, 0, int((region[2] + 1) * 1.5) - 1,
                          int((region[3] + 1) * 1.5) - 1)
                result = mapbuilder.build_map(
                    dict(opts, cell_size=cs, region=region))
        if not isinstance(result, dict):
            raise TypeError("The map generator returned an invalid result.")
        pieces = result.get("pieces") or []
        if not pieces:
            return result

        # Only now touch the project: a failed or empty run changes nothing.
        self.canvas.push_history("Generate map")
        if opts.get("replace_prev"):
            self._discard_gen_output(mode)
        if self._gen_output is None:
            self._gen_output = {}
        self._gen_output.setdefault(mode, {"ids": [], "levels": []})

        if new_level:
            need_cols, need_rows = result.get("canvas_cells") or (0, 0)
            cols = max(self.project.map_cols, int(need_cols), region[2] + 1)
            rows = max(self.project.map_rows, int(need_rows), region[3] + 1)
            if (cols, rows) != (self.project.map_cols, self.project.map_rows):
                self.project.map_cols, self.project.map_rows = cols, rows
                self.project._sync_canvas()
                self.props.set_project(self.project)
            base_name = f"Generated {result.get('seed', '')}".strip()
            existing_names = {level.name for level in self.project.levels}
            level_name, suffix = base_name, 2
            while level_name in existing_names:
                level_name = f"{base_name} ({suffix})"
                suffix += 1
            level = self.project.add_level(level_name)
            self.level_bar.refresh()
            self.level_bar.tabs.setCurrentIndex(len(self.project.levels) - 1)
            self.canvas.set_level(len(self.project.levels) - 1)
            self.layers.set_project(
                self.project, self.project.levels[self.canvas.level_index])
        else:
            self.project._sync_canvas()
            level = self.canvas.level

        layer_ids: dict[str, str] = {}
        created = []
        for data in pieces:
            lname = data.get("layer_name") or "Generated"
            if lname not in layer_ids:
                layer_ids[lname] = self._ensure_layer(level, lname)
            piece = Piece(
                asset_path=data["asset_path"], name=data["name"],
                x=data["x"], y=data["y"], w=data["w"], h=data["h"],
                scale=data["scale"], rotation=data["rotation"],
                flip_h=bool(data.get("flip_h", False)),
                flip_v=bool(data.get("flip_v", False)),
                layer=layer_ids[lname], snap=True)
            level.add(piece)
            created.append(piece)

        output = self._gen_output[mode]
        output["ids"].extend(piece.id for piece in created)
        if new_level:
            output.setdefault("levels", []).append(level)

        self.canvas.fit_to_view()
        self.level_bar.refresh()
        self.layers.set_project(self.project, level)
        self.zones.refresh_level()
        self.canvas.update()
        self._mark_dirty()
        return result

    def _discard_gen_output(self, mode=None):
        """Remove tracked generated pieces and levels.

        With a mode, discard only outputs made for that destination; without
        one, discard every generated output still tracked by this window.
        """
        out_all = self._gen_output
        if not out_all:
            return

        keys = [mode] if mode is not None else list(out_all.keys())
        ids = set()
        levels_to_remove = []
        for key in keys:
            output = out_all.get(key)
            if not output:
                continue
            ids.update(output.get("ids", []))
            tracked_levels = output.get("levels")
            if tracked_levels is None:
                old_level = output.get("level")
                tracked_levels = [old_level] if old_level is not None else []
            for level in tracked_levels:
                if level is None:
                    continue
                in_project = any(level is current
                                 for current in self.project.levels)
                already_listed = any(level is existing
                                     for existing in levels_to_remove)
                if in_project and not already_listed:
                    levels_to_remove.append(level)
            out_all.pop(key, None)

        active_level = self.canvas.level
        removed_level = False
        for level in levels_to_remove:
            index = next((i for i, current in enumerate(self.project.levels)
                          if current is level), -1)
            if index >= 0 and len(self.project.levels) > 1:
                self.project.remove_level(index)
                removed_level = True

        # Remove generated area-fill nodes and clean up any output level that
        # could not be removed because the project was down to its last level.
        if ids:
            for level in self.project.levels:
                level.pieces = [piece for piece in level.pieces
                                if piece.id not in ids]

        if not out_all:
            self._gen_output = None

        self.canvas.selection.clear()
        self.canvas.selected_zone_id = None
        self.canvas._update_quick()
        if removed_level:
            index = next((i for i, current in enumerate(self.project.levels)
                          if current is active_level), -1)
            if index < 0:
                index = min(self.canvas.level_index,
                            len(self.project.levels) - 1)
            self.level_bar.refresh()
            self.level_bar.tabs.setCurrentIndex(index)
            self.canvas.set_level(index)
            self.layers.set_project(self.project, self.project.levels[index])
        else:
            self.project._sync_canvas()
            self.canvas.selectionChanged.emit([])
        self.zones.refresh_level()
        self.canvas.update()

    # -- quick-stamp hotbar (keys 1-9) ----------------------------------------
    def _save_stamp_slots(self):
        self.settings.setValue("stamps/slots", slots_to_json(self.stamp_slots))

    def _stamp_glyph(self, template: dict) -> QPixmap:
        """Icon for a pinned node that has no image (text, patch, marker)."""
        colors = thememod.theme_colors(self.theme_mode, self.theme_accent)
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if template.get("is_patch"):
            painter.setPen(QPen(QColor(colors["text"]), 2))
            painter.setBrush(QColor(template.get("patch_color") or "#10141c"))
            painter.drawRoundedRect(QRectF(10, 14, 44, 36), 5, 5)
        elif template.get("is_connector") or template.get("is_scale_bar"):
            pen = QPen(QColor(template.get("connector_color") or template.get("scale_color")
                              or colors["text"]), 5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(12, 46), QPointF(52, 18))
        else:
            font = painter.font()
            font.setPixelSize(40)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(template.get("text_color") or colors["text"])
                           if template.get("is_text") else QColor("#ff6b6b"))
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter,
                             "T" if template.get("is_text") else "?")
        painter.end()
        return pixmap

    def _stamp_icon(self, slot) -> Optional[QIcon]:
        if not slot:
            return None
        path = slot.get("asset_path") or ""
        if path:
            from ui.image_utils import load_scaled_pixmap
            pixmap = load_scaled_pixmap(self.project.resolve_asset(path), 64)
            if not pixmap.isNull():
                return QIcon(pixmap)
        return QIcon(self._stamp_glyph(slot.get("template") or {}))

    def _stamp_tip(self, index: int, slot) -> str:
        key = index + 1
        if not slot:
            return (f"Stamp key {key} — empty.\nRight-click a library asset or a node "
                    "and choose “Pin to stamp key”, or drag an asset onto this slot.")
        door = ("\nDoor mode: each copy sits on the nearest grid line and turns "
                "to match it." if is_edge_slot(slot) else "")
        return (f"{key}: {slot_label(slot)}\nPress {key} (or click here), then click "
                "the map to place copies. Esc or right-click stops."
                f"{door}\nRight-click this slot to change or clear it.")

    def _refresh_stamp_bar(self):
        if not hasattr(self, "stamp_bar"):
            return
        icons = [self._stamp_icon(slot) for slot in self.stamp_slots]
        tips = [self._stamp_tip(index, slot) for index, slot in enumerate(self.stamp_slots)]
        self.stamp_bar.set_slots(icons, tips, [is_edge_slot(slot) for slot in self.stamp_slots])
        self.stamp_bar.setVisible(self._stamps_enabled and any(self.stamp_slots))
        self._place_stamp_bar()

    def _pin_stamp(self, index: int, slot: dict):
        if not (0 <= index < SLOT_COUNT) or not slot:
            return
        self.stamp_slots[index] = slot
        self._save_stamp_slots()
        self._refresh_stamp_bar()
        if hasattr(self, "status"):
            self.status.showMessage(
                f"Pinned “{slot_label(slot)}” to stamp key {index + 1}. Press "
                f"{index + 1}, then click the map to place it.", 7000)

    def _is_door_asset(self, path: str) -> bool:
        """Doors start out in door mode when pinned to a stamp key: they sit on
        the nearest grid line and turn to match it. Recognised from the name,
        which is all the app knows about an asset now (see core.stamps)."""
        if not path:
            return False
        asset = self.library.library.get(path)
        name = asset.name if asset else path.rsplit("/", 1)[-1]
        return looks_like_door(name) or looks_like_door(path)

    def _pin_asset_stamp(self, index: int, path: str):
        asset = self.library.library.get(path) if path else None
        try:
            slot = asset_slot(path, asset.name if asset else "", self._is_door_asset(path))
        except StampError as exc:
            QMessageBox.information(self, "Stamp keys", str(exc))
            return
        self._pin_stamp(index, slot)

    def _pin_selected_stamp(self, index: int):
        selected = self.canvas.selected_pieces()
        if len(selected) != 1:
            self.status.showMessage(
                "Select exactly one node to pin it to a stamp key.", 5000)
            return
        piece = selected[0]
        door = bool(piece.asset_path) and self._is_door_asset(piece.asset_path)
        try:
            slot = node_slot(piece.to_dict(), door)
        except StampError as exc:
            QMessageBox.information(self, "Stamp keys", str(exc))
            return
        self._pin_stamp(index, slot)

    def _set_stamp_door_mode(self, index: int, on: bool):
        slot = self.stamp_slots[index] if 0 <= index < SLOT_COUNT else None
        if not slot:
            return
        self.stamp_slots[index] = with_edge(slot, on)
        self._save_stamp_slots()
        self._refresh_stamp_bar()
        if self.canvas.stamp_tool and self.canvas.stamp_slot == index:
            self.canvas._stamp_edge = bool(on)
            self.canvas.update()
        self.status.showMessage(
            f"Stamp key {index + 1}: door mode {'on' if on else 'off'}.", 4000)

    def _clear_stamp(self, index: int):
        if self.canvas.stamp_tool and self.canvas.stamp_slot == index:
            self.canvas.cancel_extra_tool()
        self.stamp_slots[index] = None
        self._save_stamp_slots()
        self._refresh_stamp_bar()

    def _clear_all_stamps(self):
        if self.canvas.stamp_tool and self.canvas.stamp_slot is not None:
            self.canvas.cancel_extra_tool()
        self.stamp_slots = [None] * SLOT_COUNT
        self._save_stamp_slots()
        self._refresh_stamp_bar()

    def _arm_stamp(self, index: int):
        """Key 1-9 / slot click: pick up that stamp (again: put it away)."""
        if not (0 <= index < SLOT_COUNT):
            return
        canvas = self.canvas
        key = index + 1
        if canvas.stamp_tool and canvas.stamp_slot == index:
            canvas.cancel_extra_tool()
            self.status.showMessage(f"Stamp {key} put away.", 3000)
            return
        slot = self.stamp_slots[index]
        if not slot:
            self.status.showMessage(
                f"Stamp key {key} is empty — right-click a library asset or a node "
                "and choose “Pin to stamp key”.", 7000)
            return
        if slot["kind"] == "asset":
            template, tighten = canvas.asset_template(slot["asset_path"]), True
        else:
            template, tighten = dict(slot["template"]), False
            image = template.get("asset_path")
            if image and not os.path.isfile(self.project.resolve_asset(image)):
                template = None
        if template is None:
            self.status.showMessage(
                f"Stamp {key}: “{slot_label(slot)}” isn't in this map's asset library.",
                7000)
            return
        door = is_edge_slot(slot)
        if canvas.set_stamp_template(template, slot=index, tighten=tighten, edge=door):
            where = " on grid lines (door mode)" if door else ""
            self.status.showMessage(
                f"Stamp {key}: {slot_label(slot)} — click the map to place copies"
                f"{where}; Esc or right-click to stop.", 8000)

    def _stamp_slot_menu(self, index: int, global_pos):
        key = index + 1
        menu = QMenu(self)
        slot = self.stamp_slots[index]
        if slot:
            title = menu.addAction(f"{key}: {slot_label(slot)}")
            title.setEnabled(False)
            menu.addSeparator()
        pin = menu.addAction(f"Pin the selected node to key {key}")
        pin.setEnabled(len(self.canvas.selected_pieces()) == 1)
        pin.triggered.connect(lambda: self._pin_selected_stamp(index))
        current = self.library.list.current_path()
        asset = menu.addAction(f"Pin the highlighted library asset to key {key}")
        asset.setEnabled(bool(current))
        asset.triggered.connect(lambda: self._pin_asset_stamp(index, current))
        door = menu.addAction("Door mode — sit on grid lines and turn to match")
        door.setCheckable(True)
        door.setChecked(is_edge_slot(slot))
        door.setEnabled(bool(slot))
        door.setToolTip("For doors, hatches, airlocks and vents: each copy is centered "
                        "on the nearest grid line and runs along it.")
        door.triggered.connect(lambda checked=False: self._set_stamp_door_mode(index, checked))
        clear = menu.addAction(f"Clear key {key}")
        clear.setEnabled(bool(slot))
        clear.triggered.connect(lambda: self._clear_stamp(index))
        menu.addSeparator()
        clear_all = menu.addAction("Clear all stamp keys")
        clear_all.setEnabled(any(self.stamp_slots))
        clear_all.triggered.connect(self._clear_all_stamps)
        hide = menu.addAction("Hide the hotbar")
        hide.triggered.connect(lambda: self._view_set("stamps", False))
        menu.exec(global_pos)

    def _stamp_key(self, event) -> bool:
        """Plain 1-9 (top row or keypad) picks up that stamp key."""
        if event.modifiers() not in (Qt.KeyboardModifier.NoModifier,
                                     Qt.KeyboardModifier.KeypadModifier):
            return False
        key = getattr(event.key(), "value", event.key())
        first = getattr(Qt.Key.Key_1, "value", Qt.Key.Key_1)
        if first <= key < first + SLOT_COUNT:
            self._arm_stamp(key - first)
            return True
        return False

    # -- clipboard & selection basics ----------------------------------------
    def _cut(self):
        if self.canvas.cutout_tool:
            self.canvas.cutout_cut()
            return
        count = self.canvas.cut()
        if count:
            self.status.showMessage(f"Cut {count} node(s). Ctrl+V pastes them.", 4000)

    def _copy(self):
        if self.canvas.cutout_tool:
            self.canvas.cutout_copy()
            return
        if self.canvas.selected_pieces():
            self.canvas.copy()
            self.status.showMessage(
                f"Copied {len(self.canvas.selected_pieces())} node(s).", 3000)

    def _select_all(self):
        pieces = self.canvas.select_all()
        self.status.showMessage(f"Selected {len(pieces)} node(s).", 3000)

    def _invert_selection(self):
        pieces = self.canvas.invert_selection()
        self.status.showMessage(f"Selected {len(pieces)} node(s).", 3000)

    def _select_active_layer(self):
        level = self.canvas.level
        if level is None:
            return
        pieces = self.canvas.select_layer(level.current_layer)
        layer = level.layer_by_id(level.current_layer)
        if pieces and layer is not None:
            self.status.showMessage(
                f"Selected {len(pieces)} node(s) on “{layer.name}”.", 3000)

    def _edit_key(self, event) -> bool:
        """Ctrl+A/C/X/V/D and [ ] (outside text boxes)."""
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        key = event.key()
        if alt:
            return False
        if ctrl and not shift:
            actions = {Qt.Key.Key_A: self._select_all, Qt.Key.Key_C: self._copy,
                       Qt.Key.Key_X: self._cut, Qt.Key.Key_V: self.canvas.paste,
                       Qt.Key.Key_D: self.canvas.duplicate}
            if key in actions:
                actions[key]()
                return True
        if ctrl and shift:
            if key == Qt.Key.Key_A:
                self.canvas.clear_selection()
                return True
            if key == Qt.Key.Key_I:
                self._invert_selection()
                return True
        if not ctrl and key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight):
            self._cycle_variant(1 if key == Qt.Key.Key_BracketRight else -1)
            return True
        return False

    # -- send to another level --------------------------------------------------
    def _fill_send_level_menu(self, menu: QMenu):
        menu.clear()
        level_index = self.canvas.level_index
        others = [(i, level) for i, level in enumerate(self.project.levels)
                  if i != level_index]
        has_selection = bool(self.canvas.selected_pieces())
        add = self._menu_action
        if not others:
            add(menu, "This map has only one level", lambda: None, False)
            return
        if not has_selection:
            add(menu, "Select the nodes to send first", lambda: None, False)
        move = menu.addMenu("Move to")
        copy_menu = menu.addMenu("Copy to")
        for index, level in others:
            add(move, level.name, lambda i=index: self._send_to_levels([i], False),
                has_selection)
            add(copy_menu, level.name, lambda i=index: self._send_to_levels([i], True),
                has_selection)
        if len(others) > 1:
            copy_menu.addSeparator()
            add(copy_menu, "Every other level",
                lambda ids=[i for i, _lv in others]: self._send_to_levels(ids, True),
                has_selection)

    def _send_to_levels(self, indexes, copy_nodes: bool):
        if not self.canvas.selected_pieces():
            self.status.showMessage("Select the nodes to send first.", 4000)
            return 0
        return self.canvas.send_to_levels(indexes, copy_nodes)

    # -- swapping pictures --------------------------------------------------
    def _canvas_swap_info(self) -> tuple[int, str]:
        """(selected image nodes, file name of the first one's picture)."""
        images = [piece for piece in self.canvas.selected_pieces()
                  if not (piece.is_text or piece.is_patch or piece.is_scale_bar
                          or piece.is_connector) and (piece.asset_path or piece.embedded)]
        first = images[0].asset_path if images and not images[0].embedded else ""
        return len(images), first

    def _swap_selected_to(self, path: str):
        count, _first = self._canvas_swap_info()
        if not count:
            self.status.showMessage("Select the node(s) on the map to swap first.", 5000)
            return 0
        changed = self.canvas.swap_image(self.canvas.selected_pieces(), path)
        asset = self.library.library.get(path)
        name = asset.name if asset else os.path.basename(path)
        self.status.showMessage(f"Swapped {changed} node(s) to “{name}”.", 5000)
        return changed

    def _swap_every_copy_to(self, path: str):
        _count, old = self._canvas_swap_info()
        if not old:
            self.status.showMessage(
                "Select a node on the map whose picture should be swapped everywhere.", 5000)
            return 0
        changed = self.canvas.swap_every_copy(old, path)
        asset = self.library.library.get(path)
        name = asset.name if asset else os.path.basename(path)
        self.status.showMessage(
            f"Swapped {changed} node(s) on every level to “{name}”.", 6000)
        return changed

    def _cycle_variant(self, step: int):
        changed, label = self.canvas.cycle_variant(step)
        if changed:
            self.status.showMessage(f"Swapped to {label}.  [ and ] step through the "
                                    "images in its library folder.", 5000)
        elif self.canvas.selected_pieces():
            self.status.showMessage("No other images in that library folder.", 4000)
        return changed

    @staticmethod
    def _menu_action(menu: QMenu, label: str, slot, enabled: bool = True) -> QAction:
        """A menu entry owned by ``menu`` (slot called without arguments)."""
        action = QAction(label, menu)
        action.setEnabled(bool(enabled))
        action.triggered.connect(lambda _=False: slot())
        menu.addAction(action)
        return action

    def _fill_swap_menu(self, menu: QMenu):
        menu.clear()
        count, first = self._canvas_swap_info()
        current = self.library.list.current_path()
        asset = self.library.library.get(current) if current else None
        add = self._menu_action
        add(menu, "Next image in its folder\t]", lambda: self._cycle_variant(1), count)
        add(menu, "Previous image in its folder\t[", lambda: self._cycle_variant(-1), count)
        menu.addSeparator()
        label = f"“{asset.name}”" if asset else "the highlighted library image"
        add(menu, f"Swap to {label}", lambda: self._swap_selected_to(current),
            count and asset)
        add(menu, f"Swap every copy on the map to {label}",
            lambda: self._swap_every_copy_to(current),
            first and asset and first != current)
        menu.addSeparator()
        add(menu, "Replace with a file from disk (embedded)…",
            self._replace_selected_image, count == 1)

    # -- cut-out tool ---------------------------------------------------------
    def _start_cutout_tool(self, shape=None):
        shape = shape if shape in ("rect", "ellipse", "lasso", "polygon") else None
        self.canvas.set_cutout_tool(True, shape)
        if shape:
            self.settings.setValue("tools/cutout_shape", shape)
        targets = len(self.canvas.cutout_target_pieces())
        what = (f"{targets} selected image(s)" if targets
                else "the image under the area you draw")
        self.status.showMessage(
            f"Cut out: draw an area over {what}. Ctrl+click adds or removes an image; "
            "Esc puts the tool away.", 9000)

    def _set_cutout_shape(self, shape: str):
        self.canvas.set_cutout_shape(shape)
        self.settings.setValue("tools/cutout_shape", shape)

    def _set_cutout_snap(self, on: bool):
        self.canvas.set_cutout_snap(on)
        self.settings.setValue("tools/cutout_snap", bool(on))

    def _refresh_cutout_bar(self):
        if not hasattr(self, "cutout_bar"):
            return
        canvas = self.canvas
        bar = self.cutout_bar
        bar.set_state(canvas.cutout_shape, canvas.cutout_snap,
                      canvas.has_cutout_area(), len(canvas.cutout_target_pieces()))
        bar.setVisible(canvas.cutout_tool)
        self._place_cutout_bar()

    def _cutout_action(self, name: str):
        canvas = self.canvas
        if name == "delete":
            canvas.cutout_delete()
        elif name == "cut":
            canvas.cutout_cut()
        elif name == "copy":
            canvas.cutout_copy()
        elif name == "new_node":
            canvas.cutout_to_new_node(cut=True)
        elif name == "copy_new_node":
            canvas.cutout_to_new_node(cut=False)
        elif name == "keep":
            canvas.cutout_keep_only()
        elif name == "clear":
            canvas.clear_cutout_area()
        elif name == "done":
            canvas.set_cutout_tool(False)
        canvas.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_cutout_menu(self, global_pos):
        menu = QMenu(self)
        for name, label in (("delete", "Delete the area\tDel"),
                            ("cut", "Cut\tCtrl+X"),
                            ("copy", "Copy\tCtrl+C"),
                            (None, None),
                            ("new_node", "Cut out to a new node"),
                            ("copy_new_node", "Copy to a new node"),
                            ("keep", "Keep only the area"),
                            (None, None),
                            ("clear", "Clear the outline\tEsc"),
                            ("done", "Done")):
            if name is None:
                menu.addSeparator()
                continue
            self._menu_action(menu, label, lambda n=name: self._cutout_action(n))
        menu.exec(global_pos)
        menu.deleteLater()

    def _start_clone_tool(self):
        self.canvas.set_clone_tool(True)

    def _repick_clone_source(self):
        selected = self.canvas.selected_pieces()
        if len(selected) == 1 and selected[0].clone_home:
            self.canvas.begin_clone_repick(selected[0])

    # -- level backdrop ---------------------------------------------------------
    def _use_backdrop_texture(self, path: str, all_levels: bool = False):
        levels = list(self.project.levels) if all_levels else [self.canvas.level]
        levels = [level for level in levels if level is not None]
        if not levels or not path:
            return 0
        self._push_history("Backdrop texture")
        for level in levels:
            level.backdrop = "texture"
            level.backdrop_texture = path
        self.canvas.update()
        self._mark_dirty()
        if hasattr(self.props, "refresh_backdrop"):
            self.props.refresh_backdrop()
        asset = self.library.library.get(path)
        name = asset.name if asset else os.path.basename(path)
        where = "every level" if all_levels else f"“{levels[0].name}”"
        self.status.showMessage(
            f"Backdrop of {where} is now the floor texture “{name}”. Change its tile "
            "size in the Node tab → Backdrop (with nothing selected).", 7000)
        return len(levels)

    def _upload_backdrop_texture(self, all_levels: bool = False) -> str:
        """Backdrop → Upload an image…: pick a floor texture from a file on this
        computer. It is copied into the library's Backdrops folder (so the map
        can find it again, and bundles carry it) and becomes the backdrop.
        Returns the texture's library path, or "" when no picture was used."""
        from PyQt6.QtGui import QImageReader
        from core.asset_manager import SUPPORTED_EXTS
        from core.project import BACKDROP_FOLDER
        title = "Upload a floor texture"
        path, _ = QFileDialog.getOpenFileName(
            self, title, self._last_dir("files/last_import_dir"),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff)")
        if not path:
            return ""
        self._remember_dir(path, "files/last_import_dir")
        name = os.path.basename(path)
        if (not path.lower().endswith(SUPPORTED_EXTS)
                or not QImageReader(path).size().isValid()):
            QMessageBox.warning(
                self, title, f"“{name}” isn't a picture SceneBoard can use as a "
                "floor texture. Choose a PNG, JPG, WEBP, BMP or TIFF image.")
            return ""
        library = self.library.library
        revision = library._scan_revision
        try:
            self._ensure_store()
            store = self.project.asset_store
            if not self._same_path(library.root, store):
                library.scan(store)
            rel = library.import_into_folder(path, BACKDROP_FOLDER)
        except OSError as error:
            QMessageBox.warning(self, title,
                                f"“{name}” couldn't be copied into the library.\n\n{error}")
            return ""
        if library._scan_revision != revision:
            self.library.library_changed()      # list the new picture right away
        if not rel:
            QMessageBox.warning(self, title, f"“{name}” couldn't be added to the library.")
            return ""
        levels = list(self.project.levels) if all_levels else [self.canvas.level]
        if all(level is not None and level.backdrop == "texture"
               and level.backdrop_texture == rel for level in levels):
            self.status.showMessage(f"“{name}” is already the floor texture.", 5000)
            return rel
        self._use_backdrop_texture(rel, all_levels)
        where = ("every level" if all_levels else
                 f"“{self.canvas.level.name}”" if self.canvas.level else "this level")
        kept = (f"a copy is in the library's {BACKDROP_FOLDER} folder"
                if rel.split("/")[0] == BACKDROP_FOLDER else "it is already in the library")
        self.status.showMessage(
            f"Floor texture of {where}: “{rel.split('/')[-1]}” ({kept}).", 8000)
        return rel

    # -- mirror copies and grid copies -----------------------------------------
    def _mirror_selection(self, axis: str, pos: float):
        if not self.canvas.selected_pieces():
            self.status.showMessage("Select the nodes to mirror first.", 5000)
            return []
        return self.canvas.mirror_selection(axis, pos)

    def _mirror_selection_center(self, axis: str):
        pos = self.project.canvas_w / 2.0 if axis == "v" else self.project.canvas_h / 2.0
        return self._mirror_selection(axis, pos)

    def _mirror_selection_nearest(self, axis: str):
        if not self.canvas.selected_pieces():
            self.status.showMessage("Select the nodes to mirror first.", 5000)
            return []
        level = self.canvas.level
        if not level or not any(guide.axis == axis for guide in level.guides):
            kind, rails = (("vertical", "left or right") if axis == "v"
                           else ("horizontal", "top or bottom"))
            self.status.showMessage(
                f"This level has no {kind} guides yet — drag one out of the {rails} "
                "rail first.", 7000)
            return []
        line = self.canvas.nearest_mirror_line(axis)
        return self._mirror_selection(line[0], line[1])

    def _show_mirror_menu_at_cursor(self):
        from ui.context_menu import fill_mirror_menu
        menu = QMenu(self)
        fill_mirror_menu(self, menu)
        menu.exec(QCursor.pos())

    def _set_show_centerlines(self, on: bool):
        self.project.show_centerlines = bool(on)
        self.props.chk_centerlines.blockSignals(True)
        self.props.chk_centerlines.setChecked(bool(on))
        self.props.chk_centerlines.blockSignals(False)
        self.canvas.update()
        self._mark_dirty()

    def _duplicate_as_grid_dialog(self):
        bounds = self.canvas.selection_bounds()
        if bounds is None:
            self.status.showMessage("Select the nodes to repeat first.", 5000)
            return
        from ui.tool_dialogs import GridCopyDialog
        try:
            defaults = json.loads(str(self.settings.value("tools/grid_copy", "{}")))
        except (TypeError, ValueError):
            defaults = {}
        dialog = GridCopyDialog(bounds, self.project.cell_size,
                                (self.project.canvas_w, self.project.canvas_h),
                                defaults if isinstance(defaults, dict) else {}, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self.settings.setValue("tools/grid_copy", json.dumps(values))
        cell = self.project.cell_size
        self.canvas.duplicate_as_grid(values["rows"], values["cols"],
                                      values["gap_x"] * cell, values["gap_y"] * cell)

    def _start_stamp_tool(self):
        if not self.canvas.set_stamp_tool(True):
            QMessageBox.information(self, "Stamp tool",
                                    "Select one node before starting the stamp tool.")
            return
        self.status.showMessage(
            "Stamp tool active — click or drag to place copies; right-click or Esc to finish.",
            8000)

    def _start_crop_tool(self):
        if not self.canvas.set_crop_tool(True):
            QMessageBox.information(self, "Crop image",
                                    "Select one image node (not text or a patch) first.")
            return
        self.status.showMessage(
            "Crop tool active — drag the area to keep; the source image remains unchanged.",
            8000)

    def _start_ruler_tool(self):
        self.canvas.set_ruler_tool(True)
        self.status.showMessage(
            "Ruler active — drag to measure; hold Shift to snap endpoints to the grid. Esc to finish.",
            8000)

    def _start_scale_tool(self):
        self.canvas.set_scale_tool(True)
        self.status.showMessage(
            "Scale-bar tool active — drag to set its static pixel length. Esc to cancel.",
            8000)

    def _start_connector_tool(self):
        self.canvas.set_connector_tool(True)
        self.status.showMessage(
            "Connection-marker tool active — drag between points, then edit its label in Node properties.",
            9000)

    def _start_lasso_tool(self):
        self.canvas.set_lasso_tool(True)
        self.status.showMessage(
            "Lasso active — draw around node centers; hold Shift to add to the selection.",
            8000)

    def _start_copy_style(self):
        if not self.canvas.set_copy_style_mode(True):
            QMessageBox.information(self, "Copy style",
                                    "Select one source node before copying its style.")
            return
        self.status.showMessage(
            "Copy-style mode active — click compatible nodes to apply formatting; Esc to finish.",
            9000)

    def _select_similar(self):
        matches = self.canvas.select_similar()
        if not matches:
            self.status.showMessage("Select a node first to select similar nodes.", 5000)

    def _replace_selected_image(self):
        self.props._replace_image()

    def _add_at_center(self, path):
        w = self.canvas.width(); h = self.canvas.height()
        wx, wy = self.canvas.screen_to_world(w / 2, h / 2)
        self.canvas.add_asset(path, wx, wy)

    def _add_text(self):
        w = self.canvas.width(); h = self.canvas.height()
        wx, wy = self.canvas.screen_to_world(w / 2, h / 2)
        self.canvas.add_text(wx, wy)

    def _on_level_changed(self, idx):
        self.canvas.set_level(idx)
        self.layers.set_project(self.project, self.project.levels[idx])
        self.zones.refresh_level()
        self.props.refresh_backdrop()

    def _rotate_sel(self, d):
        if self.canvas.selected_pieces():
            self.canvas.push_history("Rotate")
            for p in self.canvas.selected_pieces():
                p.rotation = (p.rotation + d) % 360
            self.canvas.update()
            self.canvas.dirty.emit()

    def _toggle_group_rotate(self):
        if not self.canvas.selected_pieces():
            QMessageBox.information(self, "Group rotate",
                                    "Select two or more nodes first.")
            return
        if not self.canvas._group_rotate:
            QMessageBox.information(self, "Group rotate",
                                    "Drag on the canvas to rotate the selection "
                                    "around its center. Click Group Rot again when done.")
        self.canvas.set_group_rotate_mode(not self.canvas._group_rotate)

    # ------------------------------------------------------------------
    def _push_history(self, label, coalesce=False):
        self.history.push(self.project, label, coalesce)
        self._resync_history()
        self._mark_dirty()

    def _discard_last_history(self):
        self.history.drop_last()
        self._resync_history()

    def _load_tighten_options(self):
        try:
            threshold = int(self.settings.value("editing/tighten_threshold", 16))
            padding = float(self.settings.value("editing/tighten_padding", 0.0))
        except (TypeError, ValueError):
            threshold, padding = 16, 0.0
        raw_sides = str(self.settings.value("editing/tighten_sides", "1111"))
        sides = tuple(ch == "1" for ch in raw_sides.ljust(4, "1")[:4])
        self.canvas.tighten_options = {
            "threshold": threshold, "padding": max(0.0, padding), "sides": sides}

    def _tighten_dialog(self):
        from ui.tighten_dialog import TightenDialog
        selected = [p for p in self.canvas.selected_pieces()
                    if not (p.is_text or p.is_patch or p.is_connector or p.is_scale_bar)]
        if not selected:
            self.status.showMessage("Select one or more image nodes to tighten.", 4000)
            return
        dialog = TightenDialog(self.canvas.tighten_options, len(selected),
                               self.project.cell_size, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        options = dialog.options()
        if dialog.make_default():
            self.canvas.tighten_options = dict(options)
            self.settings.setValue("editing/tighten_threshold", options["threshold"])
            self.settings.setValue("editing/tighten_padding", options["padding"])
            self.settings.setValue("editing/tighten_sides",
                                   "".join("1" if v else "0" for v in options["sides"]))
        if dialog.result_action == "restore":
            self.canvas.restore_selected_images()
        else:
            self.canvas.tighten_selected(options)

    def _set_auto_tighten(self, on: bool):
        self.canvas.auto_tighten = bool(on)
        self.settings.setValue("editing/auto_tighten", bool(on))

    def _set_allow_overlap(self, on: bool):
        self.canvas.allow_overlap = bool(on)
        self.props.chk_allow_overlap.blockSignals(True)
        self.props.chk_allow_overlap.setChecked(bool(on))
        self.props.chk_allow_overlap.blockSignals(False)
        self.act_allow_overlap.blockSignals(True)
        self.act_allow_overlap.setChecked(bool(on))
        self.act_allow_overlap.blockSignals(False)

    def _toggle_free_transform(self):
        if self.canvas.free_transform:
            self.canvas.end_free_transform(True)
        else:
            self.canvas.begin_free_transform()

    def undo(self):
        label = self.history.undo(self.project)
        if label:
            self._after_history(fit_canvas=label == "Resize canvas")

    def redo(self):
        label = self.history.redo(self.project)
        if label:
            self._after_history(fit_canvas=label == "Resize canvas")

    def _after_history(self, fit_canvas=False):
        self._sync_guide_actions()
        self._place_minimap()
        self.canvas._guide_hover = None
        self.canvas.selection.clear()
        self.canvas.selectionChanged.emit([])
        idx = min(self.canvas.level_index, len(self.project.levels) - 1)
        self.canvas.level_index = idx
        self.level_bar.refresh()
        self.layers.set_project(self.project, self.project.levels[idx])
        self.props.refresh_canvas_size()
        if self.canvas.selected_zone_id and not self.canvas.selected_zone:
            self.canvas.select_zone(None)
        self.zones.set_project(self.project)
        if fit_canvas:
            self.canvas.fit_to_view()
        else:
            self.canvas.update()
        self.canvas.dirty.emit()
        self._resync_history()

    def _resync_history(self):
        self.hist.refresh()

    # ------------------------------------------------------------------
    def _update_window_title(self):
        product = ALIEN_NAME if self.theme_mode == "alien" else APP_NAME
        subject = (os.path.basename(self._current_file) if self._current_file
                   else (self.project.name or "Untitled"))
        marker = "*" if self._dirty else ""
        self.setWindowTitle(f"{product} — {subject}{marker}")

    def _mark_dirty(self, dirty=True):
        self._dirty = bool(dirty)
        self._update_window_title()

    def _confirm_discard(self) -> bool:
        """Before closing / New / Open with unsaved changes, offer
        Save / Don't Save / Cancel. Returns True when it is fine to go on."""
        if not self._dirty:
            return True
        choice = self._ask_unsaved_changes()
        if choice == "save":
            return bool(self._save())       # a cancelled Save As keeps the map open
        return choice == "discard"

    def _ask_unsaved_changes(self) -> str:
        name = (os.path.basename(self._current_file) if self._current_file
                else (self.project.name or "Untitled map"))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsaved changes")
        box.setText(f"Save the changes to “{name}”?")
        box.setInformativeText("If you don't save, changes since the last save are lost.")
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Don't Save", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return "save"
        if clicked is discard:
            return "discard"
        return "cancel"

    # ------------------------------------------------------------------
    def _on_cursor(self, wx, wy):
        if wx < 0:
            self.lbl_cursor.setText("")
        else:
            cell = self.project.cell_size
            self.lbl_cursor.setText(f"x:{wx:.0f} y:{wy:.0f}  ({wx/cell:.1f}, {wy/cell:.1f})")
        self.lbl_zoom.setText(f"zoom {self.canvas.zoom*100:.0f}%")
        sel = self.canvas.selected_pieces()
        self.lbl_sel.setText(f"Selected: {len(sel)}")
        self.lbl_hist.setText("Undo: " + (self.history.undos[-1][0] if self.history.can_undo() else "—"))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.scanlines.setGeometry(self.rect())
        if hasattr(self, "overlay") and self.overlay.isVisible():
            self.overlay.setGeometry(self.rect())
        if getattr(self, "_warmup", None) is not None and self._warmup.overlay.isVisible():
            self._warmup.overlay.fit()

    def keyPressEvent(self, e):
        if self.canvas.handle_cutout_key(e) or self.canvas.handle_clone_key(e):
            return
        if e.key() == Qt.Key.Key_Escape:
            if self.canvas.end_free_transform(False):
                return
            if (self.canvas.cancel_color_pick() or self.canvas.cancel_zone_tool()
                    or self.canvas.cancel_patch_tool() or self.canvas.cancel_extra_tool()):
                return          # Esc first puts down an active tool (stamp, ruler, …)
            # ESC toggles the in-window system menu (close if open, else show)
            self.overlay.toggle_menu()
            return
        # don't hijack keys while the user is typing in a form field
        fw = self.focusWidget()
        typing = isinstance(fw, (QLineEdit, QTextEdit, QComboBox,
                                 QAbstractSpinBox))
        if not typing and self.canvas.selected_zone and e.key() == Qt.Key.Key_Delete:
            self.canvas.delete_selected_zone()
            return
        if not typing and self._stamp_key(e):
            return
        if not typing and self._edit_key(e):
            return
        if (not typing and self.canvas.selected_pieces()
                and not isinstance(fw, QDoubleSpinBox)):
            step = self.project.cell_size if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            if e.key() == Qt.Key.Key_Left:
                self.canvas.nudge(-step, 0); return
            if e.key() == Qt.Key.Key_Right:
                self.canvas.nudge(step, 0); return
            if e.key() == Qt.Key.Key_Up:
                self.canvas.nudge(0, -step); return
            if e.key() == Qt.Key.Key_Down:
                self.canvas.nudge(0, step); return
            if e.key() == Qt.Key.Key_Delete:
                self.canvas.delete_selected(); return
            if e.key() == Qt.Key.Key_R:
                self._rotate_sel(90); return
        if (e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not typing
                and self.canvas.end_free_transform(True)):
            return
        if e.key() == Qt.Key.Key_Z and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.undo(); return
        if e.key() == Qt.Key.Key_Y and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.redo(); return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        if self._dirty and not self._confirm_discard():
            e.ignore()
            return
        self._drain_background_work()
        e.accept()

    def _drain_background_work(self):
        """Finish any in-flight background worker (ZIP import, thumbnail
        decode, image-bounds calc) before this window's widgets are torn down.

        Several widgets own a QThreadPool (or QThread) as a Qt child object.
        If one is destroyed while a worker is still mid-run, Qt's own child
        teardown cascade calls that pool's blocking destructor deep inside a
        chain of nested C++ destructors, with no chance for PyQt to release
        the GIL first; the worker thread can then never reacquire the GIL to
        finish, and the thread doing the deleting waits for it forever. Each
        ``drain_background_work`` below makes an explicit, outer Python call
        instead, which uses the ordinary GIL-releasing path and so cannot
        deadlock against its own worker.
        """
        if getattr(self, "_warmup", None) is not None:
            self._warmup.drain()
        if hasattr(self, "library") and hasattr(self.library, "drain_background_work"):
            self.library.drain_background_work()
        if hasattr(self, "canvas") and hasattr(self.canvas, "drain_background_work"):
            self.canvas.drain_background_work()

    def _about(self):
        product = ALIEN_NAME if self.theme_mode == "alien" else APP_NAME
        QMessageBox.about(self, "About",
                          f"{product} — General-purpose PNG map and image studio\n\n"
                          "Arrange PNG assets on a canvas, recolor nodes with tint overlays, "
                          "manage levels and layers, and export to PNG, PDF, or Tabletop Simulator-sized images.\n\n"
                          "Geomorph tiles: Starship Geomorphs 2.0 by Robert Pearce (Pearce Design Studio, LLC), "
                          "CC BY-NC 4.0; PNGs by Eric Smith / RPG Mobius. Non-commercial fan tool; "
                          "Traveller is a trademark of Far Future Enterprises.")
