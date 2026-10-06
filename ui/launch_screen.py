"""Launch screen: new / recent / open / templates, with theme picker."""
from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QIcon, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget,
    QListWidgetItem, QFrame, QSlider, QCheckBox,
)


class LaunchScreen(QDialog):
    def __init__(self, recent: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("MU-TH-UR 6000 — Battlemap Builder")
        self.setMinimumSize(520, 460)
        self.result_action = None     # ("new"|"open"|"template", path_or_None)
        self.recent = recent
        self._build()
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog{background:#080b10; color:#9bff9b; font-family:'Consolas','Courier New',monospace;}
            QPushButton{background:#111824; border:1px solid #2e6fdf; padding:8px; color:#9bff9b;}
            QPushButton:hover{background:#1b2a3a;}
            QLabel{color:#9bff9b;}
            QListWidget{background:#0d1219; border:1px solid #2e6fdf;}
            QListWidget::item:selected{background:#1b2a3a;}
            QSlider::groove:horizontal{background:#111824;} QSlider::handle:horizontal{background:#9bff9b;}
        """)

    def _build(self):
        root = QVBoxLayout(self)
        title = QLabel("MU-TH-UR 6000")
        title.setStyleSheet("font-size:26px; font-weight:bold; color:#9bff9b;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("> SHIP SYSTEMS ONLINE — SELECT OPERATION")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)
        root.addWidget(sub)

        # recent list
        root.addWidget(QLabel("RECENT MAPS"))
        self.recent_list = QListWidget()
        for p in self.recent:
            it = QListWidgetItem(os.path.basename(p))
            thumb = os.path.splitext(p)[0] + ".png"
            if os.path.exists(thumb):
                it.setIcon(QIcon(QPixmap(thumb).scaled(
                    48, 48, Qt.AspectRatioMode.KeepAspectRatio)))
            self.recent_list.addItem(it)
        if self.recent:
            self.recent_list.itemDoubleClicked.connect(
                lambda it: self._choose("open", self.recent[self.recent_list.row(it)]))
        root.addWidget(self.recent_list, 1)

        # templates
        tpl = QHBoxLayout()
        tpl.addWidget(QLabel("Template:"))
        for name in ("Blank 30×30", "Blank 40×40", "Sci-Fi Room"):
            b = QPushButton(name)
            b.clicked.connect(lambda _, n=name: self._choose("template", n))
            tpl.addWidget(b)
        root.addLayout(tpl)

        # actions
        act = QHBoxLayout()
        b_new = QPushButton("NEW MAP")
        b_new.clicked.connect(lambda: self._choose("new", None))
        b_open = QPushButton("OPEN…")
        b_open.clicked.connect(self._open_dialog)
        act.addWidget(b_new, 1)
        act.addWidget(b_open, 1)
        root.addLayout(act)

        # theme picker
        root.addWidget(QLabel("THEME"))
        th = QHBoxLayout()
        self.accent = "green"
        for name, hexc in (("Green", "#9bff9b"), ("Amber", "#ffb000"), ("Red", "#ff5a5a")):
            b = QPushButton(name)
            b.setStyleSheet(f"color:{hexc}; border:1px solid {hexc};")
            b.clicked.connect(lambda _, c=hexc, n=name: self._set_accent(c, n))
            th.addWidget(b)
        root.addLayout(th)

        tog = QHBoxLayout()
        self.cb_scan = QCheckBox("Scanlines"); self.cb_scan.setChecked(True)
        self.cb_boot = QCheckBox("Boot text"); self.cb_boot.setChecked(True)
        self.cb_cur = QCheckBox("Blink cursor"); self.cb_cur.setChecked(True)
        tog.addWidget(self.cb_scan); tog.addWidget(self.cb_boot); tog.addWidget(self.cb_cur)
        root.addLayout(tog)

        ts = QHBoxLayout()
        ts.addWidget(QLabel("Text size"))
        self.sl_ts = QSlider(Qt.Orientation.Horizontal); self.sl_ts.setRange(8, 18); self.sl_ts.setValue(11)
        ts.addWidget(self.sl_ts, 1)
        root.addLayout(ts)

    def _set_accent(self, hexc, name):
        self.accent = hexc

    def _open_dialog(self):
        from PyQt6.QtWidgets import QFileDialog
        fn, _ = QFileDialog.getOpenFileName(self, "Open project", os.getcwd(),
                                            "Map projects (*.bmap)")
        if fn:
            self._choose("open", fn)

    def _choose(self, kind, path):
        self.result_action = (kind, path)
        self.accept()

    def theme_opts(self) -> dict:
        return {"accent": self.accent, "text_scale": self.sl_ts.value() / 11.0,
                "scanlines": self.cb_scan.isChecked(),
                "boot": self.cb_boot.isChecked(),
                "cursor": self.cb_cur.isChecked()}
