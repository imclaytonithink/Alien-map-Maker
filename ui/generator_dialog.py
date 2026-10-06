"""Generator dialog: settings, tile-pool preview, seed / regenerate."""
from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QIcon
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QComboBox, QSlider, QSpinBox,
    QPushButton, QLabel, QHBoxLayout, QGroupBox, QScrollArea, QWidget,
    QMessageBox,
)

from core import generator as gen
from ui.branding import APP_NAME, ALIEN_NAME
from ui import theme as thememod

SETTING_TIPS = {
    "Random": "Let the seed pick a style at random.",
    "Starship": "Long corridors linking compact rooms — naval decks, "
                "bulkheads, terminals on walls.",
    "Colony base": "Big organic rooms with plenty of floor clutter "
                   "(crates, containers, furniture).",
    "Research lab": "Tidy grid of uniform lab rooms, consoles on walls, "
                    "more spills/hazards.",
}

LAYOUT_TIPS = {
    "Random": "Use the setting's preferred layout.",
    "Corridors": "Rooms chained along a corridor spine.",
    "Grid": "Rooms aligned to a regular grid.",
    "Organic": "Rooms scattered naturally, winding connections.",
}


def ensure_sizes(assets, library):
    for a in assets:
        if not a.width or not a.height:
            pm = QPixmap(library.abs_path(a.path))
            if not pm.isNull():
                a.width, a.height = pm.width(), pm.height()


class GeneratorDialog(QDialog):
    def __init__(self, project, library, canvas, generate_cb, parent=None):
        super().__init__(parent)
        self.project = project
        # accept either the AssetLibrary itself or the LibraryPanel wrapper
        self.library = getattr(library, "library", library) \
            if not hasattr(library, "assets") else library
        self.canvas = canvas
        self.generate_cb = generate_cb
        self.theme_mode = getattr(parent, "theme_mode", "dark")
        self.theme_accent = getattr(parent, "theme_accent", thememod.DEFAULT_ACCENT)
        self.colors = thememod.theme_colors(self.theme_mode, self.theme_accent)
        product = ALIEN_NAME if self.theme_mode == "alien" else APP_NAME
        self.setWindowTitle(f"{product} — Map Generator")
        self.setMinimumSize(580, 640)
        self.cats = {}
        self._build()

    def _build(self):
        root = QVBoxLayout(self)

        # options ------------------------------------------------------
        opt = QGroupBox("Generation")
        f = QFormLayout(opt)
        self.cmb_setting = QComboBox()
        self.cmb_setting.addItems(gen.SETTINGS)
        self.cmb_setting.currentTextChanged.connect(
            lambda t: self.lbl_setting_tip.setText(SETTING_TIPS.get(t, "")))
        f.addRow("Setting", self.cmb_setting)
        self.lbl_setting_tip = QLabel(SETTING_TIPS[self.cmb_setting.currentText()])
        self.lbl_setting_tip.setWordWrap(True)
        self.lbl_setting_tip.setStyleSheet(f"color:{self.colors['muted']};")
        f.addRow("", self.lbl_setting_tip)

        self.cmb_layout = QComboBox()
        self.cmb_layout.addItems(["Random", "Corridors", "Grid", "Organic"])
        self.cmb_layout.currentTextChanged.connect(
            lambda t: self.lbl_layout_tip.setText(LAYOUT_TIPS.get(t, "")))
        f.addRow("Layout", self.cmb_layout)
        self.lbl_layout_tip = QLabel(LAYOUT_TIPS["Random"])
        self.lbl_layout_tip.setWordWrap(True)
        self.lbl_layout_tip.setStyleSheet(f"color:{self.colors['muted']};")
        f.addRow("", self.lbl_layout_tip)

        self.sl_clutter = QSlider(Qt.Orientation.Horizontal)
        self.sl_clutter.setRange(0, 100)
        self.sl_clutter.setValue(35)
        self.sl_clutter.setToolTip("How many floor props (crates, tables…) "
                                   "are scattered inside rooms.")
        f.addRow("Floor clutter", self.sl_clutter)

        seed_row = QHBoxLayout()
        self.spin_seed = QSpinBox()
        self.spin_seed.setRange(0, 999999)
        self.spin_seed.setValue(1)
        self.spin_seed.setToolTip("Same seed + same settings + same tiles "
                                  "= identical map. Share it to reproduce.")
        b_reroll = QPushButton("New seed")
        b_reroll.clicked.connect(self._reroll)
        seed_row.addWidget(self.spin_seed, 1)
        seed_row.addWidget(b_reroll)
        f.addRow("Seed", seed_row)

        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["New level", "Fill selected area"])
        if not self.canvas.selected_pieces():
            self.cmb_mode.setCurrentIndex(0)
            self.cmb_mode.setEnabled(False)
            self.cmb_mode.setToolTip("Select nodes on the canvas first "
                                     "to fill a specific area.")
        else:
            self.cmb_mode.setToolTip("New level = add a fresh level and "
                                     "generate into it.\nFill selected area "
                                     "= generate inside the selection's bounds.")
        f.addRow("Output", self.cmb_mode)
        root.addWidget(opt)

        # preview of classified tiles -----------------------------------
        prev = QGroupBox("Auto-classified tiles (verify before generating)")
        pv = QVBoxLayout(prev)
        self.prev_area = QScrollArea()
        self.prev_area.setWidgetResizable(True)
        self.prev_inner = QWidget()
        self.prev_layout = QVBoxLayout(self.prev_inner)
        self.prev_layout.setContentsMargins(4, 4, 4, 4)
        self.prev_area.setWidget(self.prev_inner)
        pv.addWidget(self.prev_area)
        root.addWidget(prev, 1)

        # status line ----------------------------------------------------
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        # buttons --------------------------------------------------------
        btns = QHBoxLayout()
        b_regen = QPushButton("↻ Regenerate (new seed)")
        b_regen.setToolTip("Bump the seed and generate again — replaces the "
                           "map from the previous Generate press.")
        b_regen.clicked.connect(self._regenerate)
        b_gen = QPushButton("Generate")
        b_gen.clicked.connect(self._generate)
        btns.addStretch(1)
        btns.addWidget(b_regen)
        btns.addWidget(b_gen)
        root.addLayout(btns)

        self._refresh_preview()

    # ------------------------------------------------------------------
    def _taxonomy(self):
        ensure_sizes(self.library.assets, self.library)
        return gen.classify_assets(self.library.assets)

    def _set_status_tone(self, tone: str):
        colors = {
            "success": (self.colors["accent"] if self.theme_mode == "alien"
                        else ("#347a4d" if self.theme_mode == "light" else "#83d6a3")),
            "warning": "#b47a08" if self.theme_mode == "light" else "#ffbf47",
            "error": "#b42332" if self.theme_mode == "light" else "#ff6b75",
        }
        self.lbl_status.setStyleSheet(f"color:{colors.get(tone, self.colors['text'])};")

    def _refresh_preview(self):
        self.cats = self._taxonomy()
        while self.prev_layout.count():
            item = self.prev_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for cat in gen.CATEGORIES:
            items = self.cats.get(cat, [])
            row = QHBoxLayout()
            row.addWidget(QLabel(f"<b>{cat}</b> ({len(items)})"))
            row.addStretch(1)
            shown = 0
            for a in items[:8]:
                pm = QPixmap(self.library.abs_path(a["path"]))
                if not pm.isNull():
                    ic = QLabel()
                    ic.setPixmap(pm.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
                    ic.setToolTip(a["name"])
                    row.addWidget(ic)
                    shown += 1
            if shown == 0:
                row.addWidget(QLabel("(none)"))
            self.prev_layout.addLayout(row)

        missing = [c for c in ("floor", "wall") if not self.cats.get(c)]
        if missing:
            self.lbl_status.setText(
                "! Missing tile types: " + ", ".join(missing) +
                " — import PNGs whose names contain room/floor/deck/tile "
                "(floor) or wall/bulkhead/hull (wall).")
            self._set_status_tone("warning")
        elif not any(self.cats.values()):
            self.lbl_status.setText(
                "! No assets classified yet - use Import Folder first.")
            self._set_status_tone("error")
        else:
            msg = "Ready: " + gen.summarize(self.cats)
            if not self.cats.get("floor_fixture"):
                msg += ("\nNote: no floor-furniture tiles (crate, table, "
                        "locker…) were found, so Floor clutter has nothing "
                        "to scatter yet — import PNGs with those names to "
                        "use the slider.")
                self._set_status_tone("warning")
            else:
                self._set_status_tone("success")
            self.lbl_status.setText(msg)
        self.prev_layout.addStretch(1)

    # ------------------------------------------------------------------
    def _opts(self):
        return {
            "setting": self.cmb_setting.currentText(),
            "layout": self.cmb_layout.currentText(),
            "clutter": self.sl_clutter.value() / 100.0,
            "seed": self.spin_seed.value(),
            "mode": "area" if self.cmb_mode.currentIndex() == 1 else "new",
            "replace_prev": True,
            "categories": self.cats,
        }

    def _reroll(self):
        self.spin_seed.setValue((self.spin_seed.value() + 1) % 1000000)

    def _regenerate(self):
        self._reroll()
        self._generate()

    def _generate(self):
        floorish = self.cats.get("floor") or self.cats.get("corridor")
        if not floorish:
            QMessageBox.warning(
                self, "No tiles",
                "No floor/room tiles were detected.\n\n"
                "Import some floor/room PNGs first (names containing "
                "'room', 'floor', 'tile', 'deck' or 'corridor').")
            return
        opts = self._opts()
        result = self.generate_cb(opts)
        if result:
            c = result.get("counts", {})
            msg = (f"OK  {c.get('pieces', 0)} nodes · {c.get('rooms', 0)} rooms"
                   f" · seed {result.get('seed')} · "
                   f"{result.get('setting', '')} / {result.get('layout', '')}")
            if result.get("warnings"):
                msg += "\n" + "  ".join("! " + w for w in result["warnings"])
                self._set_status_tone("warning")
            else:
                self._set_status_tone("success")
            self.lbl_status.setText(msg)
