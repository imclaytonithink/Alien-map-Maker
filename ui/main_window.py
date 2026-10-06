"""Main application window — launch screen, theme, panels, history, minimap."""
from __future__ import annotations

import json
import math
import os
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QRectF, QPoint, QEvent, QSettings, QStandardPaths
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QColor, QPixmap, QPainter, QPen, QCursor
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QTabBar, QPushButton,
    QFileDialog, QInputDialog, QMessageBox, QLabel, QStatusBar, QToolBar,
    QTabWidget, QListWidget, QListWidgetItem, QGroupBox, QSlider, QCheckBox,
    QApplication, QDoubleSpinBox, QLineEdit, QTextEdit, QComboBox,
    QAbstractSpinBox,
)

from core.project import Project, Level, Piece, new_project, uuid
from core.history import History
from core import exporter, bundle
from ui.canvas import CanvasView
from ui.library import LibraryPanel
from ui.properties import PropertiesPanel
from ui.layers_panel import LayersPanel
from ui.zones_panel import ZonesPanel
from ui.launch_screen import LaunchScreen
from ui.menu_overlay import MenuOverlay
from ui.generator_dialog import GeneratorDialog
from ui import theme as thememod
from ui.branding import APP_NAME, ALIEN_NAME, SETTINGS_ID
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


class Minimap(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.setFixedSize(170, 170)
        self.set_theme("dark", "#69b7f5")
        canvas.dirty.connect(self.update)
        canvas.selectionChanged.connect(lambda _: self.update())

    def set_theme(self, mode: str, accent: str):
        colors = thememod.theme_colors(mode, accent)
        self._theme_colors = colors
        self.setStyleSheet(
            f"background:{colors['panel']}; border:1px solid {colors['border_hot']};")
        self.update()

    def _world_from_pos(self, px, py):
        sx = self.width() / max(self.canvas.project.canvas_w, 1)
        sy = self.height() / max(self.canvas.project.canvas_h, 1)
        s = min(sx, sy)
        wx = (px - (self.width() - self.canvas.project.canvas_w * s) / 2) / s
        wy = (py - (self.height() - self.canvas.project.canvas_h * s) / 2) / s
        return wx, wy

    def mousePressEvent(self, e):
        self._pan(e)
        self._drag = True

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag", False):
            self._pan(e)

    def mouseReleaseEvent(self, e):
        self._drag = False

    def _pan(self, e):
        wx, wy = self._world_from_pos(e.position().x(), e.position().y())
        cv = self.canvas
        cv.pan_x = cv.width() / 2 - wx * cv.zoom
        cv.pan_y = cv.height() / 2 - wy * cv.zoom
        cv.update()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        colors = getattr(self, "_theme_colors", thememod.theme_colors("dark"))
        p.fillRect(self.rect(), QColor(colors["panel"]))
        if not self.canvas.project:
            p.end()
            return
        proj = self.canvas.project
        sx = self.width() / proj.canvas_w
        sy = self.height() / proj.canvas_h
        s = min(sx, sy)
        ox = (self.width() - proj.canvas_w * s) / 2
        oy = (self.height() - proj.canvas_h * s) / 2
        p.setPen(QColor(colors["accent"]))
        p.drawRect(int(ox), int(oy), int(proj.canvas_w * s), int(proj.canvas_h * s))
        for pc in self.canvas.level.paint_order():
            p.setBrush(QColor(colors["accent"]))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(int(ox + pc.x * s), int(oy + pc.y * s),
                       max(1, int(pc.w * s)), max(1, int(pc.h * s)))
        # viewport rect
        vx0, vy0 = self.canvas.screen_to_world(0, 0)
        vx1, vy1 = self.canvas.screen_to_world(self.canvas.width(), self.canvas.height())
        p.setPen(QPen(QColor("#ff5a5a"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(int(ox + vx0 * s), int(oy + vy0 * s),
                   int((vx1 - vx0) * s), int((vy1 - vy0) * s))
        p.end()


class HistoryPanel(QGroupBox):
    def __init__(self, main, parent=None):
        super().__init__("History", parent)
        self.main = main
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.b_undo = QPushButton("Undo"); self.b_undo.clicked.connect(main.undo)
        self.b_redo = QPushButton("Redo"); self.b_redo.clicked.connect(main.redo)
        row.addWidget(self.b_undo); row.addWidget(self.b_redo)
        v.addLayout(row)
        self.label = QLabel("(no actions)")
        v.addWidget(self.label)
        self.refresh()

    def refresh(self):
        h = self.main.history
        self.b_undo.setEnabled(h.can_undo())
        self.b_redo.setEnabled(h.can_redo())
        if h.can_undo():
            self.label.setText("Next undo: " + h.undos[-1][0])
        else:
            self.label.setText("(no actions)")


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
        b_add = QPushButton("+"); b_add.setMaximumWidth(28); b_add.clicked.connect(self._add)
        b_del = QPushButton("–"); b_del.setMaximumWidth(28); b_del.clicked.connect(self._remove)
        b_up = QPushButton("▲"); b_up.setMaximumWidth(28); b_up.clicked.connect(lambda: self._shift(-1))
        b_down = QPushButton("▼"); b_down.setMaximumWidth(28); b_down.clicked.connect(lambda: self._shift(1))
        layout.addWidget(b_add); layout.addWidget(b_del); layout.addWidget(b_up); layout.addWidget(b_down)

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
        self.alien_boot_text = self.settings.value(
            "appearance/alien_boot_text", True, type=bool)
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

    # ------------------------------------------------------------------
    def _build_ui(self):
        self.setWindowTitle(APP_NAME)
        self.resize(1360, 820)
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)

        self.library = LibraryPanel()
        self.library.setMinimumWidth(250)
        self.library.setMaximumWidth(340)
        self.library.assetActivated.connect(self._add_at_center)
        self.library.collectionsChanged.connect(self._mark_dirty)
        root.addWidget(self.library, 0)

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
        center_col.addWidget(self.canvas, 1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(4, 4, 4, 4)
        for lbl, fn in [("−", lambda: self.canvas.set_zoom(self.canvas.zoom / 1.2)),
                        ("+", lambda: self.canvas.set_zoom(self.canvas.zoom * 1.2)),
                        ("Fit", self.canvas.fit_to_view),
                        ("50%", lambda: self.canvas.set_zoom(0.5)),
                        ("100%", lambda: self.canvas.set_zoom(1.0)),
                        ("200%", lambda: self.canvas.set_zoom(2.0))]:
            b = QPushButton(lbl); b.clicked.connect(fn); bottom.addWidget(b)
        self.minimap = Minimap(self.canvas)
        bottom.addWidget(self.minimap)
        bottom.addStretch(1)
        center_widget = QWidget()
        cb = QVBoxLayout(center_widget)
        cb.addLayout(center_col, 1)
        cb.addLayout(bottom)
        root.addWidget(center_widget, 1)

        right = QTabWidget()
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
        right.setMinimumWidth(260)
        right.setMaximumWidth(340)
        root.addWidget(right, 0)

        self._build_menu()
        self._build_toolbar()
        self._build_status()

        self.scanlines = thememod.ScanlineOverlay(self, self.theme_accent)
        self.boot = thememod.BootOverlay(self, self.theme_accent)
        # in-window system menu (ESC) — created last so it stacks on top
        self.overlay = MenuOverlay(self)

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
        e.addAction("Rotate 90°", lambda: self._rotate_sel(90))
        e.addAction("Group rotate…", self._toggle_group_rotate)

        v = mb.addMenu("&View")
        v.addAction("Zoom 50%", lambda: self.canvas.set_zoom(0.5))
        v.addAction("Zoom 100%", lambda: self.canvas.set_zoom(1.0))
        v.addAction("Zoom 200%", lambda: self.canvas.set_zoom(2.0))
        v.addAction("Fit", self.canvas.fit_to_view)
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
        for label, attr in (("Scanlines", "flourish_scanlines"),
                            ("Boot text", "flourish_boot")):
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
            "Main toolbar", actions, self.settings, self)
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
        self.status.addPermanentWidget(self.lbl_zoom, 0)
        self.status.addPermanentWidget(self.lbl_hist, 1)
        self.canvas.colorPickStateChanged.connect(self._on_color_pick_state)

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
        self._new_project(preserve_recovery=True)
        self._apply_theme()
        if os.path.exists(self._recovery_file):
            QTimer.singleShot(0, self._offer_recovery)
        elif self.theme_mode == "alien" and self.alien_boot_text:
            self.boot.show_boot(1600)
            QTimer.singleShot(1650, self.overlay.open_menu)
        else:
            QTimer.singleShot(0, self.overlay.open_menu)

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
        self.boot.accent = accent
        self.boot.label.setStyleSheet(
            f"color:{accent}; background:transparent;")
        self.scanlines.setVisible(
            self.theme_mode == "alien" and self.alien_scanlines)
        if self.theme_mode != "alien":
            self.boot.timer.stop()
            self.boot.hide()
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
                "flourish_boot": self.alien_boot_text,
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
        if mode == "alien" and self.alien_boot_text:
            self.boot.show_boot(1600)
        self._apply_theme()

    def _set_accent(self, hexc):
        color = QColor(hexc)
        if not color.isValid():
            return
        self.theme_accent = color.name()
        self.settings.setValue("appearance/alien_accent", self.theme_accent)
        self._set_theme_mode("alien")

    def _set_alien_effects(self, scanlines: bool, boot_text: bool):
        old_boot_text = self.alien_boot_text
        self.alien_scanlines = bool(scanlines)
        self.alien_boot_text = bool(boot_text)
        self.settings.setValue("appearance/alien_scanlines", self.alien_scanlines)
        self.settings.setValue("appearance/alien_boot_text", self.alien_boot_text)
        self._apply_theme()
        if self.theme_mode == "alien" and self.alien_boot_text and not old_boot_text:
            self.boot.show_boot(1600)
        elif old_boot_text and not self.alien_boot_text:
            self.boot.timer.stop()
            self.boot.hide()

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
        scanlines = self.alien_scanlines
        boot_text = self.alien_boot_text
        if attr == "flourish_scanlines":
            scanlines = not scanlines
        elif attr == "flourish_boot":
            boot_text = not boot_text
        self._set_alien_effects(scanlines, boot_text)

    # ------------------------------------------------------------------
    # Project lifecycle
    # ------------------------------------------------------------------
    def _new_project(self, preserve_recovery=False):
        if not preserve_recovery:
            self._remove_recovery()
        self.project = new_project()
        try:
            self.project.text_scale = float(self.settings.value(
                "appearance/text_scale", self.project.text_scale))
        except (TypeError, ValueError):
            pass
        self._current_file = None
        self._apply_project()
        self._mark_dirty(False)
        self._update_window_title()

    def _template(self, name):
        self._remove_recovery()
        self.project = new_project()
        try:
            self.project.text_scale = float(self.settings.value(
                "appearance/text_scale", self.project.text_scale))
        except (TypeError, ValueError):
            pass
        if name and name.startswith("Blank 40"):
            self.project.map_cols = 40
            self.project.map_rows = 40
            self.project._sync_canvas()
        if name == "Sci-Fi Room":
            self._ensure_store()
            n = self.library.library.import_folder(
                os.path.join(os.path.dirname(__file__), "..", "sample_assets"))
            if n:
                self.library.library.scan(self.project.asset_store)
                self._place_template_room()
        self._current_file = None
        self._apply_project()
        self._mark_dirty(False)
        self._update_window_title()

    def _place_template_room(self):
        by_name = {os.path.splitext(a.name)[0]: a for a in self.library.library.assets}
        def add(nm, x, y, rot=0):
            a = by_name.get(nm)
            if a:
                self.canvas.add_asset(a.path, x, y)
                sel = self.canvas.selected_pieces()
                if sel:
                    sel[0].rotation = rot
        add("room_200x100", 200, 150)
        add("corridor_40x120", 420, 200, 90)
        add("wall_10x50", 210, 160)
        add("terminal_25x50", 260, 190)
        add("computer_10x50", 360, 190)
        add("overlay_glow_100x100", 300, 210, 30)

    def _ensure_store(self):
        if not self.project.asset_store:
            self.project.asset_store = os.path.abspath(os.path.join(
                os.path.dirname(__file__), "..", "asset_store"))
        os.makedirs(self.project.asset_store, exist_ok=True)

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

    def _run_generator(self, opts):
        """Generate a map into a new level or the selection's area.

        With opts['replace_prev'] the level/pieces created by the previous
        Generate press are replaced instead of piling up (Regenerate).
        Returns the generator result dict (for the dialog status line).
        """
        cs = self.project.cell_size
        cats = opts.get("categories", {})
        floor_pool = cats.get("floor", []) + cats.get("corridor", [])
        if not floor_pool:
            QMessageBox.warning(self, "Generator",
                                "No floor/room tiles detected. Import floor/room PNGs first.")
            return None

        # capture the target region FIRST — discarding the previous output
        # may remove the level the selection lives on (mode switches).
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
            region = (0, 0, self.project.map_cols - 1, self.project.map_rows - 1)
            new_level = True

        # replace only THIS mode's previous output (new-level vs area fills
        # are tracked separately so switching modes never eats the other one)
        if opts.get("replace_prev"):
            prev = (self._gen_output or {}).get(mode)
            if prev:
                self._discard_gen_output(mode)
        if opts.get("replace_prev") is None:
            self._gen_output = None
        else:
            if self._gen_output is None:
                self._gen_output = {}
            self._gen_output[mode] = {"ids": [], "level": None}

        W = region[2] - region[0] + 1
        H = region[3] - region[1] + 1
        rooms = max(3, (W * H) // 220)
        opts2 = dict(opts)
        opts2.update(cell_size=cs, region=region, rooms=rooms)
        result = gen.generate(opts2)

        if new_level:
            lvl = self.project.add_level(
                f"{result.get('setting', opts.get('setting', 'Map'))} "
                f"{result['seed']}")
            self.level_bar.refresh()
            self.level_bar.tabs.setCurrentIndex(len(self.project.levels) - 1)
            self.canvas.set_level(len(self.project.levels) - 1)
            self.layers.set_project(self.project, self.project.levels[self.canvas.level_index])
        else:
            self.project._sync_canvas()
        level = self.canvas.level

        layer_map = {"Base": "Floor", "Props": "Props", "Overlay": "Overlay"}
        lid = {orig: self._ensure_layer(level, mapped) for orig, mapped in layer_map.items()}

        from core.project import Piece
        created = []
        for d in result["pieces"]:
            p = Piece(asset_path=d["asset_path"], name=d["name"], x=d["x"], y=d["y"],
                      w=d["w"], h=d["h"], scale=d["scale"], rotation=d["rotation"],
                      layer=lid[d["layer_name"]], snap=True)
            level.add(p)
            created.append(p)

        if opts.get("replace_prev") is not None:
            out = self._gen_output.get(mode)
            if out is not None:
                out["ids"].extend(p.id for p in created)
                if new_level:
                    out["level"] = level

        self.canvas.fit_to_view()
        self.level_bar.refresh()
        self.layers.set_project(self.project, level)
        self.zones.refresh_level()
        self._mark_dirty()
        return result

    def _discard_gen_output(self, mode=None):
        """Remove pieces/level produced by the previous Generate press.

        mode=None discards every tracked output; otherwise only that mode's.
        """
        out_all = self._gen_output
        if not out_all:
            return
        keys = [mode] if mode else list(out_all.keys())
        for key in keys:
            out = out_all.get(key)
            if not out:
                continue
            ids = set(out.get("ids", []))
            # a record owns a whole level whenever it created one (even if
            # requested as "area" with nothing selected -> fallback full map)
            if out.get("level") is not None and out.get("level") in self.project.levels:
                idx = self.project.levels.index(out["level"])
                if len(self.project.levels) > 1:
                    self.project.remove_level(idx)
                    idx = min(idx, len(self.project.levels) - 1)
                    self.level_bar.refresh()
                    self.level_bar.tabs.setCurrentIndex(idx)
                    self.canvas.set_level(idx)
                    self.layers.set_project(self.project,
                                            self.project.levels[self.canvas.level_index])
            else:
                for lvl in self.project.levels:
                    lvl.pieces = [p for p in lvl.pieces if p.id not in ids]
            out_all.pop(key, None)
        if not out_all:
            self._gen_output = None
        self.canvas.selection.clear()
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
    def _push_history(self, label):
        self.history.push(self.project, label)
        self._resync_history()
        self._mark_dirty()

    def undo(self):
        if self.history.undo(self.project):
            self._after_history()

    def redo(self):
        if self.history.redo(self.project):
            self._after_history()

    def _after_history(self):
        self.canvas.selection.clear()
        self.canvas.selectionChanged.emit([])
        idx = min(self.canvas.level_index, len(self.project.levels) - 1)
        self.canvas.level_index = idx
        self.level_bar.refresh()
        self.layers.set_project(self.project, self.project.levels[idx])
        if self.canvas.selected_zone_id and not self.canvas.selected_zone:
            self.canvas.select_zone(None)
        self.zones.set_project(self.project)
        self.canvas.update()
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
        self.boot.setGeometry(self.rect())
        if hasattr(self, "overlay") and self.overlay.isVisible():
            self.overlay.setGeometry(self.rect())

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
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
