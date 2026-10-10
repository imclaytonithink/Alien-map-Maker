"""In-window system menu overlay.

Covers the whole main window with a blurred snapshot of the editor behind a
dark scrim, and a centered menu card: New/Open/Save/Export/Quit, recent maps
(with open/remove/delete-file), and the theme picker.

ESC opens it from anywhere in the editor and closes it again.
"""
from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt, QEvent, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap, QIcon
from PyQt6.QtWidgets import (
    QWidget, QLabel, QFrame, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QListWidgetItem, QSlider, QCheckBox, QMessageBox,
    QGraphicsBlurEffect, QComboBox,
)

from ui.branding import APP_NAME, ALIEN_NAME
from ui import theme as thememod

ACCENTS = (("Green", "#9bff9b"), ("Amber", "#ffb000"), ("Red", "#ff5a5a"))
AUTOSAVE_OPTIONS = ((0, "Off"), (1, "Every minute"),
                    (5, "Every 5 minutes"), (10, "Every 10 minutes"))


class MenuOverlay(QWidget):
    """Full-window blurred system menu (ESC-toggleable)."""

    def __init__(self, main, parent=None):
        super().__init__(parent or main)
        self.main = main
        self.setObjectName("MenuOverlay")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("#MenuOverlay { background: transparent; }")
        self._src: Optional[QPixmap] = None

        # blurred snapshot of the editor + dark scrim
        self._bg = QLabel(self)
        self._bg.setScaledContents(True)
        self._bg.setStyleSheet("background: #05070a;")
        self._blur = QGraphicsBlurEffect(self._bg)
        self._blur.setBlurRadius(22)
        self._bg.setGraphicsEffect(self._blur)
        self._scrim = QLabel(self)
        self._scrim.setStyleSheet(
            "background: rgba(4, 7, 11, 150);")

        # menu card
        self._card = QFrame(self)
        self._card.setObjectName("MenuCard")
        self._card.setMaximumWidth(860)
        self._card.setMinimumWidth(560)
        root = QVBoxLayout(self._card)
        root.setContentsMargins(26, 22, 26, 22)
        root.setSpacing(12)

        self.title_label = QLabel(APP_NAME)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet("font-size: 30px; font-weight: bold; background: transparent; border: none;")
        self.subtitle_label = QLabel("PROJECT MENU — PRESS ESC TO RETURN TO EDITOR")
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setStyleSheet("background: transparent; border: none;")
        root.addWidget(self.title_label)
        root.addWidget(self.subtitle_label)

        cols = QHBoxLayout()
        cols.setSpacing(18)

        # ---- column 1: main actions ----------------------------------
        c1 = QVBoxLayout()
        c1.setSpacing(7)
        self._actions = []
        for label, slot, tip in [
            ("▶  Resume (ESC)", self.close_menu, "Close this menu (Esc)"),
            ("+  New Map", self.main._new_project, "Start an empty map"),
            ("…  Open…", self.main._open, "Open a .bmap project or .rpgpack (Ctrl+O)"),
            ("■  Save", self.main._save, "Save project (Ctrl+S)"),
            ("■  Save As…", self.main._save_as, "Save to a new file"),
            ("↓  Export PNG…", self.main._export, "Export PNG image"),
            ("↓  Export PDF…", self.main._export_pdf, "Export a PDF with level and grid options"),
            ("♟  Export for Tabletop Simulator…", self.main._export_tts,
             "Create an opaque, sized PNG for a Tabletop Simulator Custom Board"),
            ("⌨  Export MU/TH/UR terminal code…", self.main._export_muthur,
             "Check the terminals you placed and get the code for the MU/TH/UR terminal in Tabletop Simulator"),
            ("▣  Export project bundle / PNG pack…", self.main._export_bundle,
             "Package the project, its referenced assets, and PNG level renders"),
            ("#  Generate Map…", self.main._open_generator,
             "Build a map from the assets you selected in the library"),
            ("★  Geomorph Generator…", self.main._open_geomorph,
             "Build a ship or a site (colony, mine, lab, prison, station…) from the Starship Geomorphs tiles"),
            ("×  Quit", self.main.close, "Exit the application"),
        ]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setMinimumHeight(30)
            b.clicked.connect(self._wrap(slot))
            c1.addWidget(b)
            self._actions.append(b)
        c1.addStretch(1)
        cols.addLayout(c1, 2)

        # ---- column 2: recent maps -----------------------------------
        c2 = QVBoxLayout()
        c2.setSpacing(7)
        c2.addWidget(self._lbl("RECENT MAPS"))
        self.recent_list = QListWidget()
        self.recent_list.setMinimumWidth(230)
        self.recent_list.setAlternatingRowColors(False)
        self.recent_list.itemDoubleClicked.connect(
            lambda it: self._open_recent(it.data(Qt.ItemDataRole.UserRole)))
        c2.addWidget(self.recent_list, 1)
        rr = QHBoxLayout()
        b_open = QPushButton("Open")
        b_open.clicked.connect(self._open_selected)
        b_rm = QPushButton("Remove")
        b_rm.setToolTip("Remove from this list (keeps the file)")
        b_rm.clicked.connect(lambda: self._remove_selected(delete_file=False))
        b_del = QPushButton("Delete…")
        b_del.setToolTip("Delete the map file from disk (asks first)")
        b_del.clicked.connect(lambda: self._remove_selected(delete_file=True))
        rr.addWidget(b_open)
        rr.addWidget(b_rm)
        rr.addWidget(b_del)
        c2.addLayout(rr)
        cols.addLayout(c2, 3)

        # ---- column 3: appearance and project settings -----------------
        c3 = QVBoxLayout()
        c3.setSpacing(7)
        self.theme_heading = self._lbl("APPEARANCE")
        c3.addWidget(self.theme_heading)
        theme_row = QHBoxLayout()
        self._mode_btns = {}
        for mode, label in (("dark", "Dark"), ("light", "Light"),
                            ("alien", "Alien")):
            b = QPushButton(label)
            b.clicked.connect(lambda _, value=mode: self._set_mode(value))
            theme_row.addWidget(b)
            self._mode_btns[mode] = b
        c3.addLayout(theme_row)

        self.alien_controls = QWidget()
        alien_layout = QVBoxLayout(self.alien_controls)
        alien_layout.setContentsMargins(0, 0, 0, 0)
        alien_layout.setSpacing(5)
        accent_row = QHBoxLayout()
        self._accent_btns = {}
        for name, hexc in ACCENTS:
            b = QPushButton(name)
            b.clicked.connect(self._wrap_accent(hexc, accent_row))
            accent_row.addWidget(b)
            self._accent_btns[hexc] = b
        alien_layout.addLayout(accent_row)

        tog = QHBoxLayout()
        self.cb_scan = QCheckBox("Scanlines")
        self.cb_scan.toggled.connect(self._toggle_flourish)
        tog.addWidget(self.cb_scan)
        alien_layout.addLayout(tog)
        c3.addWidget(self.alien_controls)

        autosave_row = QHBoxLayout()
        autosave_row.addWidget(self._lbl("Auto-save"))
        self.cmb_autosave = QComboBox()
        for minutes, label in AUTOSAVE_OPTIONS:
            self.cmb_autosave.addItem(label, minutes)
        self.cmb_autosave.currentIndexChanged.connect(self._autosave_changed)
        autosave_row.addWidget(self.cmb_autosave, 1)
        c3.addLayout(autosave_row)

        ts = QHBoxLayout()
        ts.addWidget(self._lbl("Text size"))
        self.sl_ts = QSlider(Qt.Orientation.Horizontal)
        self.sl_ts.setRange(8, 18)
        self.sl_ts.setValue(11)
        self.sl_ts.valueChanged.connect(self._text_scale)
        ts.addWidget(self.sl_ts, 1)
        c3.addLayout(ts)
        c3.addStretch(1)
        cols.addLayout(c3, 3)

        root.addLayout(cols, 1)

        self.hint_label = QLabel("Ctrl+S save · Ctrl+O open · Ctrl+Z undo · "
                                  "arrows nudge · Shift+arrows = one square · ESC menu")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setStyleSheet("font-size: 10px; background: transparent; border: none;")
        root.addWidget(self.hint_label)

        # catch ESC (and shortcuts) from child widgets too
        for w in self._card.findChildren(QWidget):
            w.installEventFilter(self)
        self._card.installEventFilter(self)
        self.hide()

    # ------------------------------------------------------------------
    @staticmethod
    def _lbl(text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet("background: transparent; border: none; font-size: 10px;")
        return l

    def _wrap(self, fn):
        def run(*_):
            self.close_menu()
            fn()
        return run

    def _wrap_accent(self, hexc, row=None):
        def run(*_):
            self.main._set_accent(hexc)
        return run

    def _set_mode(self, mode):
        self.main._set_theme_mode(mode)

    def _autosave_changed(self, index):
        if index < 0:
            return
        minutes = self.cmb_autosave.itemData(index)
        self.main._set_autosave_interval(int(minutes))

    # ------------------------------------------------------------------
    def open_menu(self):
        """Snapshot the editor, blur it behind the card, and take focus."""
        if self.isVisible():
            return
        self._src = self.main.grab()
        self._bg.setPixmap(self._src)
        self.setGeometry(self.main.rect())
        self._layout_children()
        self._refresh_recent()
        self._sync_theme()
        self.show()
        self.raise_()
        self.setFocus()
        # highlight the Resume button as the default choice
        if self._actions:
            self._actions[0].setDefault(True)

    def close_menu(self):
        if not self.isVisible():
            return
        self.hide()
        self.main.canvas.setFocus()

    def toggle_menu(self):
        if self.isVisible():
            self.close_menu()
        else:
            self.open_menu()

    # ------------------------------------------------------------------
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.setGeometry(self.main.rect())
        self._layout_children()

    def _layout_children(self):
        r = self.rect()
        self._bg.setGeometry(r)
        self._scrim.setGeometry(r)
        self._card.adjustSize()
        w, h = self._card.width(), self._card.height()
        self._card.move((r.width() - w) // 2, max(8, (r.height() - h) // 2))

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
            self.close_menu()
            return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close_menu()
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------------
    # Recent
    # ------------------------------------------------------------------
    def _refresh_recent(self):
        self.recent_list.clear()
        for p in getattr(self.main, "recent", []):
            it = QListWidgetItem(os.path.basename(p))
            it.setData(Qt.ItemDataRole.UserRole, p)
            it.setToolTip(p)
            finder = getattr(self.main, "_thumbnail_for", None)
            thumb = finder(p) if finder else os.path.splitext(p)[0] + ".png"
            if thumb and os.path.exists(thumb):
                it.setIcon(QIcon(QPixmap(thumb).scaled(
                    40, 40, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)))
            self.recent_list.addItem(it)

    def _selected_recent(self) -> Optional[str]:
        it = self.recent_list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _open_selected(self):
        path = self._selected_recent()
        if not path:
            QMessageBox.information(self, "Recent",
                                    "Pick a map from the list first.")
            return
        self._open_recent(path)

    def _open_recent(self, path):
        if not os.path.exists(path):
            QMessageBox.warning(self, "Missing file",
                                f"That map no longer exists:\n{path}")
            self._forget_recent(path, delete_file=False)
            return
        self.close_menu()
        if self.main._dirty and not self.main._confirm_discard():
            return
        self.main._load_file(path)

    def _remove_selected(self, delete_file: bool):
        path = self._selected_recent()
        if not path:
            QMessageBox.information(self, "Recent",
                                    "Pick a map from the list first.")
            return
        if delete_file:
            if not os.path.exists(path):
                self._forget_recent(path, delete_file=False)
                return
            r = QMessageBox.question(
                self, "Delete map",
                f"Permanently delete this map file?\n\n{path}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
            try:
                os.remove(path)
                # previews live in app data now; a "<map>.png" beside the map is
                # only removed when it is thumbnail-sized (never an export)
                if hasattr(self.main, "_delete_map_thumbnails"):
                    self.main._delete_map_thumbnails(path)
            except OSError as e:
                QMessageBox.critical(self, "Delete failed", str(e))
                return
        self._forget_recent(path, delete_file=delete_file)

    def _forget_recent(self, path: str, delete_file: bool):
        self.main.recent = [p for p in self.main.recent if p != path]
        if hasattr(self.main, "_save_recent"):
            self.main._save_recent()
        else:
            from ui.main_window import save_recent
            save_recent(self.main.recent)
        self._refresh_recent()

    # ------------------------------------------------------------------
    # Theme controls
    # ------------------------------------------------------------------
    def _sync_theme(self):
        proj = self.main.project
        mode = self.main.theme_mode
        colors = thememod.theme_colors(mode, self.main.theme_accent)
        is_alien = mode == "alien"

        if is_alien:
            self.title_label.setText(ALIEN_NAME)
            self.subtitle_label.setText(
                "> SYSTEM MENU — PRESS ESC TO RESUME BUILDING")
        else:
            self.title_label.setText(APP_NAME)
            self.subtitle_label.setText(
                "PROJECT MENU — PRESS ESC TO RETURN TO THE EDITOR")
        self.title_label.setStyleSheet(
            f"font-size:30px; font-weight:bold; color:{colors['accent']}; "
            "background:transparent; border:none;")
        self.subtitle_label.setStyleSheet(
            f"color:{colors['muted']}; background:transparent; border:none;")
        self.hint_label.setStyleSheet(
            f"color:{colors['muted']}; font-size:10px; "
            "background:transparent; border:none;")
        self._bg.setStyleSheet(f"background:{colors['bg']};")
        scrim = "rgba(4,7,11,150)" if mode != "light" else "rgba(230,235,242,145)"
        self._scrim.setStyleSheet(f"background:{scrim};")

        for key, btn in self._mode_btns.items():
            active = key == mode
            btn.setStyleSheet(
                f"font-weight:{'bold' if active else 'normal'}; "
                f"border:2px solid {colors['accent'] if active else colors['border']};")
        self.alien_controls.setVisible(is_alien)
        self.cb_scan.blockSignals(True)
        self.sl_ts.blockSignals(True)
        self.cmb_autosave.blockSignals(True)
        self.cb_scan.setChecked(self.main.alien_scanlines)
        self.cb_scan.setEnabled(is_alien)
        self.sl_ts.setValue(int(round(proj.text_scale * 11)))
        save_index = self.cmb_autosave.findData(self.main.autosave_interval_minutes)
        if save_index >= 0:
            self.cmb_autosave.setCurrentIndex(save_index)
        self.cb_scan.blockSignals(False)
        self.sl_ts.blockSignals(False)
        self.cmb_autosave.blockSignals(False)
        for hexc, btn in self._accent_btns.items():
            active = is_alien and self.main.theme_accent.lower() == hexc.lower()
            btn.setStyleSheet(
                f"font-weight:{'bold' if active else 'normal'}; color:{hexc}; "
                f"border:2px solid {hexc}; "
                f"background:{colors['selection'] if active else 'transparent'};")
        if self.isVisible():
            self._layout_children()

    def _toggle_flourish(self, *_):
        if self.main.theme_mode != "alien":
            return
        self.main._set_alien_effects(self.cb_scan.isChecked())

    def _text_scale(self, v):
        self.main.project.text_scale = v / 11.0
        self.main.settings.setValue("appearance/text_scale", v / 11.0)
        self.main._apply_theme()
        self.main._mark_dirty()
