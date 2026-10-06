"""Main application window — launch screen, theme, panels, history, minimap."""
from __future__ import annotations

import json
import math
import os
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QRectF, QPoint, QEvent
from PyQt6.QtGui import QAction, QKeySequence, QColor, QPixmap, QPainter, QPen, QCursor
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QTabBar, QPushButton,
    QFileDialog, QInputDialog, QMessageBox, QLabel, QStatusBar, QToolBar,
    QTabWidget, QListWidget, QListWidgetItem, QGroupBox, QSlider, QCheckBox,
    QApplication, QDoubleSpinBox, QLineEdit, QTextEdit, QComboBox,
    QAbstractSpinBox,
)

from core.project import Project, Level, Piece, new_project, uuid
from core.history import History
from core import exporter
from ui.canvas import CanvasView
from ui.library import LibraryPanel
from ui.properties import PropertiesPanel
from ui.layers_panel import LayersPanel
from ui.launch_screen import LaunchScreen
from ui.menu_overlay import MenuOverlay
from ui.generator_dialog import GeneratorDialog
from ui import theme as thememod
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
        self.setStyleSheet("background:#0d1219; border:1px solid #2e6fdf;")
        canvas.dirty.connect(self.update)
        canvas.selectionChanged.connect(lambda _: self.update())

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
        p.fillRect(self.rect(), QColor("#0d1219"))
        if not self.canvas.project:
            return
        proj = self.canvas.project
        sx = self.width() / proj.canvas_w
        sy = self.height() / proj.canvas_h
        s = min(sx, sy)
        ox = (self.width() - proj.canvas_w * s) / 2
        oy = (self.height() - proj.canvas_h * s) / 2
        p.setPen(QColor(proj.accent))
        p.drawRect(int(ox), int(oy), int(proj.canvas_w * s), int(proj.canvas_h * s))
        for pc in self.canvas.level.paint_order():
            p.setBrush(QColor(proj.accent))
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
        if self.project.levels[idx].pieces:
            ok = QMessageBox.question(self, "Remove level",
                                      f"Remove '{self.project.levels[idx].name}' and its "
                                      f"{len(self.project.levels[idx].pieces)} piece(s)?")
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
    def __init__(self):
        super().__init__()
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
        self.setWindowTitle("MU-TH-UR 6000 — Battlemap Builder")
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
        self.hist = HistoryPanel(self)
        right.addTab(self.props, "Piece")
        right.addTab(self.layers, "Layers")
        right.addTab(self.hist, "History")
        right.setMinimumWidth(260)
        right.setMaximumWidth(340)
        root.addWidget(right, 0)

        self._build_menu()
        self._build_toolbar()
        self._build_status()

        self.scanlines = thememod.ScanlineOverlay(self, self.project.accent)
        self.boot = thememod.BootOverlay(self, self.project.accent)
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
        e.addAction("Rotate 90°", lambda: self._rotate_sel(90))
        e.addAction("Group rotate…", self._toggle_group_rotate)

        v = mb.addMenu("&View")
        v.addAction("Zoom 50%", lambda: self.canvas.set_zoom(0.5))
        v.addAction("Zoom 100%", lambda: self.canvas.set_zoom(1.0))
        v.addAction("Zoom 200%", lambda: self.canvas.set_zoom(2.0))
        v.addAction("Fit", self.canvas.fit_to_view)

        t = mb.addMenu("&Theme")
        for name, hexc in (("Green", "#9bff9b"), ("Amber", "#ffb000"), ("Red", "#ff5a5a")):
            t.addAction(name, lambda h=hexc: self._set_accent(h))
        t.addSeparator()
        t.addAction("Toggle scanlines", lambda: self._toggle_flourish("flourish_scanlines"))
        t.addAction("Toggle boot text", lambda: self._toggle_flourish("flourish_boot"))
        t.addAction("Toggle blink cursor", lambda: self._toggle_flourish("flourish_cursor"))

        t = mb.addMenu("&Tools")
        t.addAction("Generate Map…", self._open_generator)

        hm = mb.addMenu("&Help")
        hm.addAction("About", self._about)

    def _build_toolbar(self):
        tb = QToolBar()
        self.addToolBar(tb)
        for name, fn in [("New", self._new_project), ("Open", self._open),
                         ("Save", self._save), ("Export", self._export),
                         ("Undo", self.undo), ("Redo", self.redo),
                         ("Text", self._add_text), ("Group Rot", self._toggle_group_rotate),
                         ("Gen", self._open_generator),
                         ("Import", self.library._import_folder)]:
            b = QPushButton(name); b.clicked.connect(fn); tb.addWidget(b)

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

    # ------------------------------------------------------------------
    # Launch
    # ------------------------------------------------------------------
    def _show_launch(self):
        """Open the in-window system menu over the editor (replaces the old
        modal launcher; the map behind it starts as a fresh empty project)."""
        self._new_project()
        self._apply_theme()
        if self.project.flourish_boot:
            self.boot.show_boot(1600)
            # reveal the menu once the boot text has finished
            QTimer.singleShot(1650, self.overlay.open_menu)
        else:
            # let the window paint once so the overlay snapshot isn't blank
            QTimer.singleShot(0, self.overlay.open_menu)

    # ------------------------------------------------------------------
    def _apply_project(self):
        self.canvas.set_project(self.project, self.library.library)
        self.props.set_project(self.project)
        self.library.set_project(self.project, self.library.library, self._add_at_center)
        self.level_bar.set_project(self.project)
        self.layers.set_project(self.project, self.project.levels[self.canvas.level_index])
        self.canvas.level_index = 0
        self._resync_history()

    def _apply_theme(self):
        app = QApplication.instance()
        thememod.apply_stylesheet(app, self.project.accent, self.project.text_scale)
        self.scanlines.set_accent(self.project.accent)
        self.boot.accent = self.project.accent
        self.scanlines.setVisible(self.project.flourish_scanlines)

    # ------------------------------------------------------------------
    # Theme helpers
    # ------------------------------------------------------------------
    def _set_accent(self, hexc):
        self.project.accent = hexc
        self._apply_theme()
        self._mark_dirty()

    def _toggle_flourish(self, attr):
        setattr(self.project, attr, not getattr(self.project, attr))
        if attr == "flourish_scanlines":
            self.scanlines.setVisible(self.project.flourish_scanlines)
        if attr == "flourish_boot" and self.project.flourish_boot:
            self.boot.show_boot(1600)
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Project lifecycle
    # ------------------------------------------------------------------
    def _new_project(self):
        self.project = new_project()
        self._current_file = None
        self._apply_project()
        self._mark_dirty(False)
        self.setWindowTitle("MU-TH-UR 6000 — Untitled")

    def _template(self, name):
        self.project = new_project()
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
        self.setWindowTitle("MU-TH-UR 6000 — Template")

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
        fn, _ = QFileDialog.getOpenFileName(self, "Open project", os.getcwd(),
                                            f"Map projects (*{BMAP_EXT})")
        if fn:
            self._load_file(fn)

    def _load_file(self, fn):
        try:
            with open(fn, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.project = Project.from_dict(data)
            self._current_file = fn
            self._apply_project()
            self._mark_dirty(False)
            self.setWindowTitle(f"MU-TH-UR 6000 — {os.path.basename(fn)}")
            self._push_recent(fn)
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
            self.setWindowTitle(f"MU-TH-UR 6000 — {os.path.basename(fn)}")

    def _write(self, fn):
        try:
            with open(fn, "w", encoding="utf-8") as fh:
                json.dump(self.project.to_dict(), fh, indent=2)
            exporter.save_thumbnail(self.project, fn)
            self._push_recent(fn)
            self._mark_dirty(False)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def _push_recent(self, fn):
        self.recent = [fn] + [x for x in self.recent if x != fn]
        save_recent(self.recent)

    # ------------------------------------------------------------------
    def _export(self):
        from ui.export_dialog import ExportDialog
        ExportDialog(self.project, self.canvas, self).exec()

    def _export_pdf(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Export PDF", "map.pdf", "PDF (*.pdf)")
        if fn:
            try:
                exporter.export_pdf(self.project, fn, self.project.export_grid,
                                    exporter.preset_scale(self.project, "Original (1×)"))
                QMessageBox.information(self, "Export", f"Saved {fn}")
            except Exception as e:
                QMessageBox.critical(self, "Export failed", str(e))

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
        self.canvas.update()

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
                                    "Select two or more pieces first.")
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
        self.canvas.update()
        self._resync_history()

    def _resync_history(self):
        self.hist.refresh()

    # ------------------------------------------------------------------
    def _mark_dirty(self, dirty=True):
        self._dirty = dirty
        title = self.windowTitle()
        base = title.rstrip("*")
        if dirty and not base.endswith("*"):
            self.setWindowTitle(base + "*")
        elif not dirty and base.endswith("*"):
            self.setWindowTitle(base[:-1])

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
            # ESC toggles the in-window system menu (close if open, else show)
            self.overlay.toggle_menu()
            return
        # don't hijack keys while the user is typing in a form field
        fw = self.focusWidget()
        typing = isinstance(fw, (QLineEdit, QTextEdit, QComboBox,
                                 QAbstractSpinBox))
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
        e.accept()

    def _about(self):
        QMessageBox.about(self, "About",
                          "MU-TH-UR 6000 — Sci-Fi Battlemap Builder\n\n"
                          "Assemble PNG map pieces on a snapping grid, manage floors/levels,\n"
                          "overlay a reference floor, tint/label pieces, and export to PNG/PDF.")
