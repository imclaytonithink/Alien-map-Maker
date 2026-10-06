"""Launch screen: new / recent / open / templates, with theme picker."""
from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QIcon, QColor
from ui.branding import APP_NAME, APP_DESCRIPTOR, APP_TAGLINE
from ui import theme as thememod
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget,
    QListWidgetItem, QFrame, QSlider, QCheckBox,
)


class LaunchScreen(QDialog):
    def __init__(self, recent: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(520, 460)
        self.result_action = None     # ("new"|"open"|"template", path_or_None)
        self.recent = recent
        self._build()
        self._apply_style()

    def _apply_style(self):
        colors = thememod.theme_colors("dark")
        self.setStyleSheet(f"""
            QDialog{{background:{colors['bg']}; color:{colors['text']}; font-family:'Consolas','Courier New',monospace;}}
            QPushButton{{background:{colors['panel2']}; border:1px solid {colors['border']}; padding:8px; color:{colors['text']};}}
            QPushButton:hover{{background:{colors['hover']};}}
            QLabel{{color:{colors['text']};}}
            QListWidget{{background:{colors['panel']}; border:1px solid {colors['border']};}}
            QListWidget::item:selected{{background:{colors['selection']};}}
            QSlider::groove:horizontal{{background:{colors['panel2']};}} QSlider::handle:horizontal{{background:{colors['accent']};}}
        """)

    def _build(self):
        root = QVBoxLayout(self)
        title = QLabel(APP_NAME)
        title.setStyleSheet("font-size:26px; font-weight:bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel(f"{APP_DESCRIPTOR}\n{APP_TAGLINE}")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("font-size:11px; padding-bottom:6px;")
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
        self.theme_mode = "dark"
        self.accent = thememod.DEFAULT_ACCENT
        for mode, label in (("dark", "Dark"), ("light", "Light"),
                            ("alien", "Alien / MU-TH-UR")):
            b = QPushButton(label)
            b.clicked.connect(lambda _, value=mode: self._set_mode(value))
            th.addWidget(b)
        root.addLayout(th)

        accent_row = QHBoxLayout()
        for name, hexc in (("Green", "#9bff9b"), ("Amber", "#ffb000"), ("Red", "#ff5a5a")):
            b = QPushButton(f"Alien {name}")
            b.setStyleSheet(f"color:{hexc}; border:1px solid {hexc};")
            b.clicked.connect(lambda _, c=hexc, n=name: self._set_accent(c, n))
            accent_row.addWidget(b)
        root.addLayout(accent_row)

        tog = QHBoxLayout()
        self.cb_scan = QCheckBox("Scanlines")
        self.cb_boot = QCheckBox("Boot text")
        self.cb_cur = QCheckBox("Blink cursor")
        self.cb_scan.setChecked(False)
        self.cb_boot.setChecked(False)
        self.cb_cur.setChecked(False)
        tog.addWidget(self.cb_scan); tog.addWidget(self.cb_boot); tog.addWidget(self.cb_cur)
        root.addLayout(tog)

        ts = QHBoxLayout()
        ts.addWidget(QLabel("Text size"))
        self.sl_ts = QSlider(Qt.Orientation.Horizontal); self.sl_ts.setRange(8, 18); self.sl_ts.setValue(11)
        ts.addWidget(self.sl_ts, 1)
        root.addLayout(ts)

    def _set_mode(self, mode):
        self.theme_mode = mode

    def _set_accent(self, hexc, name):
        self.accent = hexc
        self.theme_mode = "alien"

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
        alien = self.theme_mode == "alien"
        return {"theme_mode": self.theme_mode,
                "accent": self.accent, "text_scale": self.sl_ts.value() / 11.0,
                "scanlines": alien and self.cb_scan.isChecked(),
                "boot": alien and self.cb_boot.isChecked(),
                "cursor": alien and self.cb_cur.isChecked()}
