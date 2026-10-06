"""In-window system menu overlay.

Covers the whole main window with a blurred snapshot of the editor behind a
dark scrim, and a centered menu card: New/Open/Save/Export/Quit, recent maps
(with open/remove/delete-file), templates, and the theme picker.

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
    QGraphicsBlurEffect,
)

ACCENTS = (("Green", "#9bff9b"), ("Amber", "#ffb000"), ("Red", "#ff5a5a"))


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

        title = QLabel("MU-TH-UR 6000")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 30px; font-weight: bold; "
                            "color: #9bff9b; background: transparent; "
                            "border: none;")
        sub = QLabel("> SYSTEM MENU — PRESS ESC TO RESUME BUILDING")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #6f8a86; background: transparent; "
                          "border: none;")
        root.addWidget(title)
        root.addWidget(sub)

        cols = QHBoxLayout()
        cols.setSpacing(18)

        # ---- column 1: main actions ----------------------------------
        c1 = QVBoxLayout()
        c1.setSpacing(7)
        self._actions = []
        for label, slot, tip in [
            ("▶  Resume (ESC)", self.close_menu, "Close this menu (Esc)"),
            ("+  New Map", self.main._new_project, "Start an empty map"),
            ("…  Open…", self.main._open, "Open a .bmap project (Ctrl+O)"),
            ("■  Save", self.main._save, "Save project (Ctrl+S)"),
            ("■  Save As…", self.main._save_as, "Save to a new file"),
            ("↓  Export PNG…", self.main._export, "Export PNG image"),
            ("↓  Export PDF…", self.main._export_pdf, "Export PDF, one page per level"),
            ("#  Generate Map…", self.main._open_generator,
             "Procedurally generate a map from your tiles"),
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

        # ---- column 3: templates + theme ------------------------------
        c3 = QVBoxLayout()
        c3.setSpacing(7)
        c3.addWidget(self._lbl("TEMPLATES"))
        for name in ("Blank 30×30", "Blank 40×40", "Sci-Fi Room"):
            b = QPushButton(name)
            b.clicked.connect(self._wrap_template(name))
            c3.addWidget(b)

        c3.addSpacing(10)
        c3.addWidget(self._lbl("THEME"))
        th = QHBoxLayout()
        self._accent_btns = {}
        for name, hexc in ACCENTS:
            b = QPushButton(name)
            b.clicked.connect(self._wrap_accent(hexc, th))
            th.addWidget(b)
            self._accent_btns[hexc] = b
        c3.addLayout(th)

        tog = QHBoxLayout()
        self.cb_scan = QCheckBox("Scanlines")
        self.cb_boot = QCheckBox("Boot text")
        self.cb_cur = QCheckBox("Cursor")
        for cb in (self.cb_scan, self.cb_boot, self.cb_cur):
            cb.toggled.connect(self._toggle_flourish)
            tog.addWidget(cb)
        c3.addLayout(tog)

        ts = QHBoxLayout()
        ts.addWidget(self._lbl("Text"))
        self.sl_ts = QSlider(Qt.Orientation.Horizontal)
        self.sl_ts.setRange(8, 18)
        self.sl_ts.setValue(11)
        self.sl_ts.valueChanged.connect(self._text_scale)
        ts.addWidget(self.sl_ts, 1)
        c3.addLayout(ts)
        c3.addStretch(1)
        cols.addLayout(c3, 3)

        root.addLayout(cols, 1)

        hint = QLabel("Ctrl+S save · Ctrl+O open · Ctrl+Z undo · "
                      "arrows nudge · Shift+arrows = one square · ESC menu")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #6f8a86; font-size: 10px; "
                           "background: transparent; border: none;")
        root.addWidget(hint)

        # catch ESC (and shortcuts) from child widgets too
        for w in self._card.findChildren(QWidget):
            w.installEventFilter(self)
        self._card.installEventFilter(self)
        self.hide()

    # ------------------------------------------------------------------
    @staticmethod
    def _lbl(text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet("color: #6f8a86; background: transparent; "
                        "border: none; font-size: 10px;")
        return l

    def _wrap(self, fn):
        def run(*_):
            self.close_menu()
            fn()
        return run

    def _wrap_template(self, name):
        def run(*_):
            self.close_menu()
            self.main._template(name)
        return run

    def _wrap_accent(self, hexc, row):
        def run(*_):
            self.main._set_accent(hexc)
            self._sync_theme()
        return run

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
            thumb = os.path.splitext(p)[0] + ".png"
            if os.path.exists(thumb):
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
                thumb = os.path.splitext(path)[0] + ".png"
                if os.path.exists(thumb):
                    os.remove(thumb)
            except OSError as e:
                QMessageBox.critical(self, "Delete failed", str(e))
                return
        self._forget_recent(path, delete_file=delete_file)

    def _forget_recent(self, path: str, delete_file: bool):
        self.main.recent = [p for p in self.main.recent if p != path]
        from ui.main_window import save_recent
        save_recent(self.main.recent)
        self._refresh_recent()

    # ------------------------------------------------------------------
    # Theme controls
    # ------------------------------------------------------------------
    def _sync_theme(self):
        proj = self.main.project
        self.cb_scan.blockSignals(True)
        self.cb_boot.blockSignals(True)
        self.cb_cur.blockSignals(True)
        self.sl_ts.blockSignals(True)
        self.cb_scan.setChecked(proj.flourish_scanlines)
        self.cb_boot.setChecked(proj.flourish_boot)
        self.cb_cur.setChecked(proj.flourish_cursor)
        self.sl_ts.setValue(int(round(proj.text_scale * 11)))
        self.cb_scan.blockSignals(False)
        self.cb_boot.blockSignals(False)
        self.cb_cur.blockSignals(False)
        self.sl_ts.blockSignals(False)
        for hexc, btn in self._accent_btns.items():
            active = proj.accent.lower() == hexc.lower()
            btn.setStyleSheet(
                f"font-weight: {'bold' if active else 'normal'}; "
                f"color: {hexc}; border: 2px solid {hexc}; "
                f"background: {'rgba(155,255,155,30)' if active else 'transparent'};")

    def _toggle_flourish(self, *_):
        proj = self.main.project
        proj.flourish_scanlines = self.cb_scan.isChecked()
        proj.flourish_boot = self.cb_boot.isChecked()
        proj.flourish_cursor = self.cb_cur.isChecked()
        self.main.scanlines.setVisible(proj.flourish_scanlines)
        self.main._mark_dirty()

    def _text_scale(self, v):
        self.main.project.text_scale = v / 11.0
        self.main._apply_theme()
        self.main._mark_dirty()
