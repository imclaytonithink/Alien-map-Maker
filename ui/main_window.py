"""Main application window — launch screen, theme, panels, history, minimap."""
from __future__ import annotations

import json
import math
import os
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QRectF, QPoint, QPointF, QEvent, QSettings, QStandardPaths
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QColor, QPixmap, QPainter, QPen, QCursor
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QTabBar, QPushButton,
    QFileDialog, QInputDialog, QMessageBox, QLabel, QStatusBar, QToolBar,
    QTabWidget, QListWidget, QListWidgetItem, QGroupBox, QSlider, QCheckBox,
    QApplication, QDoubleSpinBox, QLineEdit, QTextEdit, QComboBox,
    QAbstractSpinBox, QDialog, QSplitter, QSplitterHandle,
)

from core.project import Project, Level, Piece, new_project, uuid
from core.history import History
from core import exporter, bundle
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
from core import generator as gen

RECENT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recent.json")
BMAP_EXT = ".bmap"


def load_recent() -> list:
    try:
        with open(RECENT_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def save_recent(items: list):
    try:
        with open(RECENT_FILE, "w") as f:
            json.dump(items[:12], f)
    except Exception:
        pass


# Tools shown on the toolbar by default; the rest live in the menus (and the
# right-click menu) and can be added back via View → Customize toolbar.
COMPACT_TOOLBAR = ("new", "open", "save", "export", "undo", "redo", "generate")


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
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave_tick)
        if self.autosave_interval_minutes:
            self._autosave_timer.start(self.autosave_interval_minutes * 60 * 1000)

        self.project = new_project()
        self.history = History()
        self.recent = load_recent()
        self._current_file: Optional[str] = None
        self._dirty = False
        self._gen_output = None    # tracks last Generate output for Regenerate
        self._build_ui()
        self._show_launch()
        QTimer.singleShot(0, self._install_bundled_asset_packs)

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
        self.canvas.historyDiscardLast.connect(self._discard_last_history)
        center_col.addWidget(self.canvas, 1)

        # The map overview floats over a corner of the canvas (View → Minimap)
        # instead of taking a permanent strip; zoom lives in the status bar,
        # the View menu and the mouse wheel.
        self.minimap = Minimap(self.canvas)
        self.minimap.setParent(self.canvas)
        self.minimap.raise_()
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
        right.setMinimumWidth(220)
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
    VIEW_ITEMS = ("toolbar", "status", "levels", "minimap", "quick",
                  "library", "inspector")
    VIEW_DEFAULTS = {"toolbar": True, "status": True, "levels": True,
                     "minimap": True, "quick": False, "library": True,
                     "inspector": True}
    VIEW_LABELS = {"toolbar": "Toolbar", "status": "Status bar",
                   "levels": "Level tabs", "minimap": "Minimap",
                   "quick": "Floating node buttons", "library": "Library panel",
                   "inspector": "Inspector panel"}
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
        margin = 10
        self.minimap.move(margin, self.canvas.height() - self.minimap.height() - margin)
        self.minimap.raise_()

    def eventFilter(self, obj, event):
        if obj is self.canvas and event.type() == QEvent.Type.Resize:
            self._place_minimap()
        return super().eventFilter(obj, event)

    def _toggle_focus_canvas(self):
        """Hide every bar and panel for a clean canvas; press again to restore."""
        if any(self._view_get(n) for n in ("toolbar", "status", "levels",
                                           "minimap", "library", "inspector")):
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
        menu = build_canvas_menu(self, hit_piece)
        menu.exec(global_pos)

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
        e.addAction("Copy", self.canvas.copy)
        e.addAction("Paste", self.canvas.paste)
        e.addAction("Duplicate", self.canvas.duplicate)
        e.addAction("Delete", self.canvas.delete_selected)
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
        t.addAction("Ruler / measure", self._start_ruler_tool)
        t.addAction("Add static scale bar", self._start_scale_tool)
        t.addAction("Add connection / transition marker", self._start_connector_tool)
        t.addAction("Lasso select", self._start_lasso_tool)
        t.addAction("Stamp selected node", self._start_stamp_tool)
        t.addAction("Crop selected image…", self._start_crop_tool)
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
            ("crop", "Crop", self._start_crop_tool,
             "Crop the selected image non-destructively."),
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
             "Generate a map from the asset library."),
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

    def _apply_theme(self):
        app = QApplication.instance()
        colors = thememod.theme_colors(self.theme_mode, self.theme_accent)
        accent = colors["accent"]
        text_scale = self.project.text_scale
        thememod.apply_stylesheet(app, self.theme_accent, text_scale,
                                  self.theme_mode)
        self.canvas.set_theme(self.theme_mode, self.theme_accent)
        self.minimap.set_theme(self.theme_mode, self.theme_accent)
        self.layers.set_theme(self.theme_mode, self.theme_accent)
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

    def _open(self):
        if self._dirty and not self._confirm_discard():
            return
        fn, _ = QFileDialog.getOpenFileName(
            self, "Open project or map pack", os.getcwd(),
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
            self._current_file = fn
            self._apply_project()
            self._mark_dirty(False)
            self._update_window_title()
            self._remove_recovery()
            self._push_recent(fn)
            if opened_bundle:
                missing = bundle_manifest.get("missing_assets", []) if bundle_manifest else []
                message = "Map pack opened as a local working copy. Save or export a new pack when ready."
                if missing:
                    message += f" {len(missing)} source asset(s) were missing from the pack."
                self.status.showMessage(message, 12000)
        except Exception as e:
            QMessageBox.critical(self, "Open failed", str(e))

    def _save(self):
        if not self._current_file:
            self._save_as()
            return
        self._write(self._current_file)

    def _save_as(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Save project", os.getcwd(),
                                            f"Map projects (*{BMAP_EXT})")
        if fn:
            if not fn.endswith(BMAP_EXT):
                fn += BMAP_EXT
            self._current_file = fn
            self._write(fn)
            self._update_window_title()

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
            self._atomic_json_write(self.project.to_dict(), fn)
            exporter.save_thumbnail(self.project, fn)
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
            if self._write(self._current_file, autosave=True):
                self.status.showMessage("Auto-saved project.", 4000)
            return
        try:
            os.makedirs(self._autosave_dir, exist_ok=True)
            self._atomic_json_write(self.project.to_dict(), self._recovery_file)
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

    def _push_recent(self, fn):
        self.recent = [fn] + [x for x in self.recent if x != fn]
        save_recent(self.recent)

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

    def _export_bundle(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export project bundle and PNG pack", os.getcwd(),
            "RPG Map Pack (*.rpgpack)")
        if not path:
            return
        if not path.lower().endswith(".rpgpack"):
            path += ".rpgpack"
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
    def _open_generator(self):
        dlg = GeneratorDialog(self.project, self.library, self.canvas,
                             self._run_generator, self)
        dlg.exec()

    def _ensure_layer(self, level, name):
        for l in level.layers:
            if l.name == name:
                return l.id
        from core.project import Layer, uuid
        l = Layer(name=name, id=uuid.uuid4().hex)
        level.layers.append(l)
        return l.id

    def _furnish_targets(self, opts) -> list[dict]:
        """Visible bounds (world px) of the rooms the furnisher should fill."""
        level = self.canvas.level
        if level is None:
            return []
        if opts.get("scope") == "selected":
            nodes = self.canvas.selected_pieces()
        else:
            roles = self.library.library.roles()
            nodes = [p for p in level.pieces if p.asset_path
                     and roles.get(p.asset_path)
                     and roles[p.asset_path].role == "empty_room"]
        targets = []
        for node in nodes:
            x0, y0, x1, y1 = self.canvas._aabb(node)
            targets.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0})
        return targets

    def _run_strategy(self, opts):
        """Run one of the role-driven strategies (assembly / tiles / furnish)."""
        from core import assembly
        from core.project import Piece
        cs = self.project.cell_size
        strategy = opts["strategy"]
        mode = opts.get("mode") or "new"
        key = "furnish" if strategy == "furnish" else mode
        run = dict(opts, cell_size=cs)
        new_level = False
        if strategy == "furnish":
            run["targets"] = self._furnish_targets(opts)
            level = self.canvas.level
        else:
            sel = self.canvas.selected_pieces()
            if mode == "area" and sel:
                minx = min(p.x for p in sel)
                maxx = max(p.x + p.vis_w for p in sel)
                miny = min(p.y for p in sel)
                maxy = max(p.y + p.vis_h for p in sel)
                region = (int(math.floor(minx / cs)), int(math.floor(miny / cs)),
                          int(math.floor(maxx / cs)), int(math.floor(maxy / cs)))
            else:
                new_level = True
                cols, rows = opts.get("size") or (self.project.map_cols,
                                                  self.project.map_rows)
                region = (0, 0, int(cols) - 1, int(rows) - 1)
            run["region"] = region
            run["rooms"] = max(3, ((region[2] - region[0] + 1)
                                   * (region[3] - region[1] + 1)) // 220)
        result = assembly.generate_map(run)
        if not isinstance(result, dict):
            raise TypeError("The map generator returned an invalid result.")
        pieces = result.get("pieces") or []
        if not pieces:
            return result

        # Only now touch the project: a failed or empty run changes nothing.
        self.canvas.push_history(f"Generate ({strategy})")
        if opts.get("replace_prev"):
            self._discard_gen_output(key)
        if self._gen_output is None:
            self._gen_output = {}
        self._gen_output.setdefault(key, {"ids": [], "levels": []})

        if new_level:
            need_cols, need_rows = result.get("canvas_cells") or (0, 0)
            cols = max(self.project.map_cols, int(need_cols), region[2] + 1)
            rows = max(self.project.map_rows, int(need_rows), region[3] + 1)
            if (cols, rows) != (self.project.map_cols, self.project.map_rows):
                self.project.map_cols, self.project.map_rows = cols, rows
                self.project._sync_canvas()
                self.props.set_project(self.project)
            base_name = f"{result.get('setting', 'Map')} {result.get('seed', '')}".strip()
            existing = {lv.name for lv in self.project.levels}
            name, suffix = base_name, 2
            while name in existing:
                name = f"{base_name} ({suffix})"
                suffix += 1
            level = self.project.add_level(name)
            self.level_bar.refresh()
            self.level_bar.tabs.setCurrentIndex(len(self.project.levels) - 1)
            self.canvas.set_level(len(self.project.levels) - 1)
            self.layers.set_project(
                self.project, self.project.levels[self.canvas.level_index])
        else:
            self.project._sync_canvas()

        layer_map = {"Base": "Floor", "Props": "Props", "Overlay": "Overlay",
                     "Geomorphs": "Floor", "Overlays": "Overlay",
                     "Symbols": "Symbols", "Hull": "Hull"}
        layer_ids = {}
        created = []
        for data in pieces:
            lname = data["layer_name"]
            if lname not in layer_ids:
                layer_ids[lname] = self._ensure_layer(level, layer_map.get(lname, lname))
            piece = Piece(
                asset_path=data["asset_path"], name=data["name"],
                x=data["x"], y=data["y"], w=data["w"], h=data["h"],
                scale=data["scale"], rotation=data["rotation"],
                layer=layer_ids[lname], snap=True)
            level.add(piece)
            created.append(piece)
        output = self._gen_output[key]
        output["ids"].extend(piece.id for piece in created)
        if new_level:
            output.setdefault("levels", []).append(level)

        if new_level:
            self.canvas.fit_to_view()
        self.level_bar.refresh()
        self.layers.set_project(self.project, level)
        self.zones.refresh_level()
        self.canvas.update()
        self._mark_dirty()
        return result

    def _run_generator(self, opts):
        """Generate a map into a new level or the selection's area.

        Generate keeps earlier generated outputs. Regenerate sends
        ``replace_prev=True`` and replaces all tracked output for the selected
        destination only after a non-empty new map has been generated.
        """
        if opts.get("strategy"):
            return self._run_strategy(opts)
        cs = self.project.cell_size
        generator_mode = opts.get("generator_mode", "tiles")
        geomorph_mode = generator_mode == "geomorph"
        cats = opts.get("categories", {})
        floor_pool = cats.get("floor", []) + cats.get("corridor", [])
        if not geomorph_mode and not floor_pool:
            QMessageBox.warning(self, "Generator",
                                "No floor/room tiles detected. Import floor/room PNGs first.")
            return None
        geomorph_cats = opts.get("geomorph_categories", {})
        if geomorph_mode and not geomorph_cats.get("core"):
            QMessageBox.warning(self, "Generator",
                                "No 100x100 Core geomorphs detected. Import the "
                                "Geomorphs or Custom Tiles ZIP first.")
            return None

        # Capture the target region before replacement: it may include a
        # selection on the output that Regenerate is about to remove.
        mode = opts.get("mode") or "new"
        sel = self.canvas.selected_pieces()
        if mode == "area" and sel:
            minx = min(min(p.x, p.x + p.vis_w) for p in sel)
            maxx = max(max(p.x + p.vis_w, p.x) for p in sel)
            miny = min(min(p.y, p.y + p.vis_h) for p in sel)
            maxy = max(max(p.y + p.vis_h, p.y) for p in sel)
            x0 = int(math.floor(minx / cs)); x1 = int(math.floor(maxx / cs))
            y0 = int(math.floor(miny / cs)); y1 = int(math.floor(maxy / cs))
            region = (x0, y0, x1, y1)
            new_level = False
        else:
            new_level = True

        # Geomorph assemblies can be larger than the current project canvas.
        # Keep the original dimensions so a failed/empty generation is harmless.
        old_map_size = (self.project.map_cols, self.project.map_rows)
        if new_level and geomorph_mode:
            grid = max(1, int(opts.get("geomorph_grid", 3) or 3))
            core = geomorph_cats.get("core", [])
            core_w = int(core[0].get("core_w", 20)) if core else 20
            core_h = int(core[0].get("core_h", 20)) if core else 20
            self.project.map_cols = max(self.project.map_cols, grid * core_w)
            self.project.map_rows = max(self.project.map_rows, grid * core_h)
            if old_map_size != (self.project.map_cols, self.project.map_rows):
                self.project._sync_canvas()
                self.props.set_project(self.project)
        if new_level:
            region = (0, 0, self.project.map_cols - 1,
                      self.project.map_rows - 1)

        W = region[2] - region[0] + 1
        H = region[3] - region[1] + 1
        rooms = max(3, (W * H) // 220)
        opts2 = dict(opts)
        opts2.update(cell_size=cs, region=region, rooms=rooms)
        try:
            result = (gen.generate_geomorphs(opts2) if geomorph_mode
                      else gen.generate(opts2))
        except Exception:
            if old_map_size != (self.project.map_cols, self.project.map_rows):
                self.project.map_cols, self.project.map_rows = old_map_size
                self.project._sync_canvas()
                self.props.set_project(self.project)
            raise

        if not isinstance(result, dict):
            if old_map_size != (self.project.map_cols, self.project.map_rows):
                self.project.map_cols, self.project.map_rows = old_map_size
                self.project._sync_canvas()
                self.props.set_project(self.project)
            raise TypeError("The map generator returned an invalid result.")
        pieces = result.get("pieces") or []
        if not pieces:
            if old_map_size != (self.project.map_cols, self.project.map_rows):
                self.project.map_cols, self.project.map_rows = old_map_size
                self.project._sync_canvas()
                self.props.set_project(self.project)
            return result

        # Only discard the old result after generation succeeded. This keeps a
        # working map intact if the new seed or asset set produces no output.
        if opts.get("replace_prev"):
            self._discard_gen_output(mode)
        track_output = opts.get("replace_prev") is not None
        if track_output:
            if self._gen_output is None:
                self._gen_output = {}
            self._gen_output.setdefault(mode, {"ids": [], "levels": []})
        else:
            self._gen_output = None

        if new_level:
            setting = result.get("setting", opts.get("setting", "Map"))
            seed = result.get("seed", opts.get("seed", ""))
            base_name = f"{setting} {seed}".strip()
            existing_names = {level.name for level in self.project.levels}
            level_name = base_name
            suffix = 2
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

        layer_map = {"Base": "Floor", "Props": "Props", "Overlay": "Overlay",
                     "Geomorphs": "Floor", "Overlays": "Overlay",
                     "Symbols": "Symbols"}
        output_layers = {piece["layer_name"] for piece in pieces}
        lid = {}
        for orig in ("Base", "Props", "Overlay", "Geomorphs", "Overlays", "Symbols"):
            if orig in output_layers:
                lid[orig] = self._ensure_layer(level, layer_map.get(orig, orig))

        from core.project import Piece
        created = []
        for data in pieces:
            piece = Piece(
                asset_path=data["asset_path"], name=data["name"],
                x=data["x"], y=data["y"], w=data["w"], h=data["h"],
                scale=data["scale"], rotation=data["rotation"],
                layer=lid[data["layer_name"]], snap=True)
            level.add(piece)
            created.append(piece)

        if track_output:
            output = self._gen_output[mode]
            output["ids"].extend(piece.id for piece in created)
            if new_level:
                output.setdefault("levels", []).append(level)

        self.canvas.fit_to_view()
        self.level_bar.refresh()
        self.layers.set_project(self.project, level)
        self.zones.refresh_level()
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

    def _confirm_discard(self):
        r = QMessageBox.question(self, "Unsaved changes", "Discard unsaved changes?")
        return r == QMessageBox.StandardButton.Yes

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

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            if self.canvas.end_free_transform(False):
                return
            if (self.canvas.cancel_color_pick() or self.canvas.cancel_zone_tool()
                    or self.canvas.cancel_patch_tool()):
                return
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
        # ZIP intake runs off the GUI thread; join it before its owning panel is
        # destroyed so closing during a large import cannot tear down a live thread.
        if hasattr(self, "library") and hasattr(self.library, "wait_for_zip_import"):
            self.library.wait_for_zip_import()
        e.accept()

    def _about(self):
        product = ALIEN_NAME if self.theme_mode == "alien" else APP_NAME
        QMessageBox.about(self, "About",
                          f"{product} — General-purpose PNG map and image studio\n\n"
                          "Arrange PNG assets on a canvas, recolor nodes with tint overlays, "
                          "manage levels and layers, and export to PNG, PDF, or Tabletop Simulator-sized images.")
