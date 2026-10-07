"""Generator dialog: settings, tile-pool preview, seed / regenerate."""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImageReader
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QComboBox, QSlider, QSpinBox,
    QPushButton, QLabel, QHBoxLayout, QGridLayout, QGroupBox, QScrollArea,
    QWidget,
)

from core import generator as gen
from ui.branding import APP_NAME, ALIEN_NAME
from ui.image_utils import load_scaled_pixmap
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
    """Fill missing pixel dimensions from image headers, never full decoding."""
    for a in assets:
        if not a.width or not a.height:
            reader = QImageReader(library.abs_path(a.path))
            size = reader.size()
            if size.isValid():
                a.width, a.height = size.width(), size.height()


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
        self.geomorph_cats = {"core": [], "overlays": {}, "symbols": [],
                              "pixels_per_square": 0.0, "core_cells": (0, 0),
                              "border_cells": 2}
        self._mode_user_changed = False
        self._build()

    def _build(self):
        root = QVBoxLayout(self)

        # options ------------------------------------------------------
        opt = QGroupBox("Generation")
        f = QFormLayout(opt)
        self.cmb_asset_mode = QComboBox()
        self.cmb_asset_mode.addItem("Tile-by-tile", "tiles")
        self.cmb_asset_mode.addItem("Geomorph assembly", "geomorph")
        self.cmb_asset_mode.setToolTip(
            "Tile-by-tile builds floors and walls from small assets. "
            "Geomorph assembly arranges compatible prebuilt deck-plan modules.")
        self.cmb_asset_mode.currentIndexChanged.connect(self._on_asset_mode_changed)
        f.addRow("Generator", self.cmb_asset_mode)

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

        self.cmb_geomorph_grid = QComboBox()
        for size in (2, 3, 4):
            self.cmb_geomorph_grid.addItem(f"{size} × {size} modules", size)
        self.cmb_geomorph_grid.setCurrentIndex(1)
        self.cmb_geomorph_grid.setToolTip(
            "Each 100x100 Core module covers 20x20 five-foot squares. "
            "A 3x3 assembly creates a 60x60-square map.")
        f.addRow("Geomorph layout", self.cmb_geomorph_grid)

        self.sl_clutter = QSlider(Qt.Orientation.Horizontal)
        self.sl_clutter.setRange(0, 100)
        self.sl_clutter.setValue(35)
        self.sl_clutter.setToolTip("Controls floor props in tile mode, or optional "
                                   "geomorph overlays and symbols in assembly mode.")
        self.lbl_clutter_caption = QLabel("Floor clutter")
        f.addRow(self.lbl_clutter_caption, self.sl_clutter)

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
        prev = QGroupBox("Auto-classified assets (verify before generating)")
        pv = QVBoxLayout(prev)
        self.prev_area = QScrollArea()
        self.prev_area.setWidgetResizable(True)
        self.prev_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.prev_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.prev_inner = QWidget()
        self.prev_inner.setMinimumWidth(0)
        self.prev_layout = QVBoxLayout(self.prev_inner)
        self.prev_layout.setContentsMargins(4, 4, 4, 4)
        self.prev_layout.setSpacing(8)
        self.prev_area.setWidget(self.prev_inner)
        pv.addWidget(self.prev_area)
        root.addWidget(prev, 1)

        # status line ----------------------------------------------------
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        # buttons --------------------------------------------------------
        btns = QHBoxLayout()
        self.btn_regenerate = QPushButton("↻ Regenerate (new seed)")
        self.btn_regenerate.setToolTip(
            "Increase the seed and replace previous generated output(s) "
            "for this output mode. If there is no previous output, this makes "
            "a new map.")
        self.btn_regenerate.clicked.connect(self._regenerate)
        self.btn_generate = QPushButton("Generate")
        self.btn_generate.setToolTip(
            "Generate with the seed shown above and keep any earlier generated "
            "outputs. Use Regenerate to roll a new seed and replace the prior result.")
        self.btn_generate.clicked.connect(
            lambda _checked=False: self._generate(replace_previous=False))
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        btns.addWidget(self.btn_close)
        btns.addStretch(1)
        btns.addWidget(self.btn_regenerate)
        btns.addWidget(self.btn_generate)
        root.addLayout(btns)

        self._refresh_preview()

    def _on_asset_mode_changed(self, *_args):
        self._mode_user_changed = True
        self._apply_mode_options()
        self._refresh_preview()

    def _apply_mode_options(self):
        geomorph_mode = self.cmb_asset_mode.currentData() == "geomorph"
        self.cmb_setting.setEnabled(not geomorph_mode)
        self.lbl_setting_tip.setEnabled(not geomorph_mode)
        self.cmb_layout.setEnabled(not geomorph_mode)
        self.lbl_layout_tip.setEnabled(not geomorph_mode)
        self.cmb_geomorph_grid.setEnabled(geomorph_mode)
        self.lbl_clutter_caption.setText(
            "Overlay / symbol density" if geomorph_mode else "Floor clutter")

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
        self.geomorph_cats = gen.classify_geomorph_assets(
            self.library.assets, feet_per_square=self.project.feet_per_square)
        has_tile_floor = bool(self.cats.get("floor") or self.cats.get("corridor"))
        if (not self._mode_user_changed and self.geomorph_cats.get("core")
                and not has_tile_floor):
            self.cmb_asset_mode.blockSignals(True)
            self.cmb_asset_mode.setCurrentIndex(1)
            self.cmb_asset_mode.blockSignals(False)
        self._apply_mode_options()

        while self.prev_layout.count():
            item = self.prev_layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                while child_layout.count():
                    child_item = child_layout.takeAt(0)
                    child_widget = child_item.widget()
                    if child_widget:
                        child_widget.deleteLater()
                child_layout.deleteLater()

        def add_preview_row(title, assets, limit=8):
            row_widget = QWidget()
            row_layout = QVBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            caption = QLabel(f"<b>{title}</b> ({len(assets)})")
            caption.setWordWrap(True)
            row_layout.addWidget(caption)

            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(6)
            shown = 0
            for asset in assets[:limit]:
                pm = load_scaled_pixmap(
                    self.library.abs_path(asset["path"]), 64)
                if pm.isNull():
                    continue
                icon = QLabel()
                icon.setFixedSize(68, 68)
                icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
                icon.setPixmap(pm)
                icon.setToolTip(asset["name"])
                grid.addWidget(icon, shown // 4, shown % 4)
                shown += 1
            if not shown:
                empty = QLabel("No compatible assets")
                empty.setStyleSheet(f"color:{self.colors['muted']};")
                grid.addWidget(empty, 0, 0)
            row_layout.addLayout(grid)
            self.prev_layout.addWidget(row_widget)

        geomorph_mode = self.cmb_asset_mode.currentData() == "geomorph"
        if geomorph_mode:
            core = self.geomorph_cats.get("core", [])
            overlays = [asset for pool in
                        self.geomorph_cats.get("overlays", {}).values()
                        for asset in pool]
            symbols = self.geomorph_cats.get("symbols", [])
            add_preview_row("100x100 Core geomorphs", core, 8)
            add_preview_row("Paired overlays", overlays, 4)
            add_preview_row("Symbols", symbols, 6)
            if not core:
                self.lbl_status.setText(
                    "! No 100x100 Core geomorphs found. Import the Geomorphs "
                    "or Custom Tiles ZIP, including its folder structure.")
                self._set_status_tone("error")
            else:
                msg = (f"Ready: {len(core)} compatible geomorphs; "
                       f"{len(overlays)} paired overlays; {len(symbols)} symbols.")
                unpaired = self.geomorph_cats.get("unpaired_overlay_count", 0)
                if unpaired:
                    msg += (f" Ignoring {unpaired} overlay(s) without a "
                            "matching Core tile at the selected resolution.")
                if not symbols:
                    msg += " Maps will assemble without symbol dressing."
                self._set_status_tone(
                    "warning" if (unpaired or not symbols) else "success")
                self.lbl_status.setText(msg)
        else:
            for category in gen.CATEGORIES:
                add_preview_row(category, self.cats.get(category, []), 8)
            missing = [category for category in ("floor", "wall")
                       if not self.cats.get(category)]
            if missing:
                self.lbl_status.setText(
                    "! Missing tile types: " + ", ".join(missing) +
                    " — import small PNGs whose names contain room/floor/deck/tile "
                    "(floor) or wall/bulkhead/hull (wall).")
                self._set_status_tone("warning")
            elif not any(self.cats.values()):
                self.lbl_status.setText(
                    "! No small tile assets classified yet — import a floor/wall "
                    "tile folder, or switch to Geomorph assembly.")
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
    def _opts(self, replace_previous=False):
        return {
            "setting": self.cmb_setting.currentText(),
            "layout": self.cmb_layout.currentText(),
            "clutter": self.sl_clutter.value() / 100.0,
            "seed": self.spin_seed.value(),
            "mode": "area" if self.cmb_mode.currentIndex() == 1 else "new",
            "replace_prev": bool(replace_previous),
            "categories": self.cats,
            "generator_mode": self.cmb_asset_mode.currentData(),
            "geomorph_grid": self.cmb_geomorph_grid.currentData(),
            "geomorph_categories": self.geomorph_cats,
        }

    def _reroll(self):
        self.spin_seed.setValue((self.spin_seed.value() + 1) % 1000000)

    def _regenerate(self):
        previous_seed = self.spin_seed.value()
        self._reroll()
        if not self._generate(replace_previous=True):
            self.spin_seed.setValue(previous_seed)

    def _generate(self, replace_previous=False):
        geomorph_mode = self.cmb_asset_mode.currentData() == "geomorph"
        if geomorph_mode:
            if not self.geomorph_cats.get("core"):
                self._set_status_tone("error")
                self.lbl_status.setText(
                    "No 100x100 Core geomorphs were detected. Import the "
                    "Geomorphs or Custom Tiles ZIP first.")
                return False
        else:
            floorish = self.cats.get("floor") or self.cats.get("corridor")
            if not floorish:
                self._set_status_tone("error")
                self.lbl_status.setText(
                    "No floor/room tiles were detected. Import small floor or "
                    "room PNGs first (names containing room, floor, tile, deck, "
                    "or corridor).")
                return False

        try:
            result = self.generate_cb(self._opts(replace_previous))
        except Exception as exc:
            self._set_status_tone("error")
            self.lbl_status.setText(f"Generation failed: {exc}")
            return False

        if not isinstance(result, dict):
            self._set_status_tone("error")
            self.lbl_status.setText(
                "Generation did not return a map. Check the selected assets "
                "and try again.")
            return False
        pieces = result.get("pieces") or []
        if not pieces:
            warnings = result.get("warnings") or []
            detail = "\n" + "  ".join("! " + str(w) for w in warnings) \
                if warnings else ""
            self._set_status_tone("warning")
            self.lbl_status.setText(
                "No map was generated; any previous output was kept." + detail)
            return False

        c = result.get("counts", {})
        if geomorph_mode:
            msg = (f"{c.get('geomorphs', 0)} geomorphs · "
                   f"{c.get('overlays', 0)} overlays · "
                   f"{c.get('symbols', 0)} symbols · seed "
                   f"{result.get('seed')} · {result.get('layout', '')}")
        else:
            msg = (f"{c.get('pieces', len(pieces))} nodes · "
                   f"{c.get('rooms', 0)} rooms · seed {result.get('seed')} · "
                   f"{result.get('setting', '')} / {result.get('layout', '')}")
        warnings = result.get("warnings") or []
        if warnings:
            msg += "\n" + "  ".join("! " + str(w) for w in warnings)
            self._set_status_tone("warning")
        else:
            self._set_status_tone("success")
        prefix = "Regenerated" if replace_previous else "Generated"
        self.lbl_status.setText(f"{prefix}: {msg}")
        return True
