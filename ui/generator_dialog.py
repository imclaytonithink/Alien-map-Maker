"""Map generator: build a map out of the assets you selected.

The generator does not decide anything for you. It has no keyword classifier,
no automatic asset sorting and no strategies to choose between - it lays out
**exactly the assets you selected in the library**, and nothing else.

1. Check the selection: the listed assets are the only ones used.
2. Choose the layout (tidy rows, random scatter, or fill the area).
3. Choose the size and where to build.
4. Set the seed and press Generate.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QGuiApplication, QIcon, QImageReader
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from core import mapbuilder
from core.seeds import MAX_SEED_LENGTH, clean_seed, new_seed
from ui import theme as thememod
from ui.branding import ALIEN_NAME, APP_NAME
from ui.image_utils import load_scaled_pixmap

MAP_PRESETS = (("Small — 30 × 30 squares", (30, 30)),
               ("Medium — 60 × 60 squares", (60, 60)),
               ("Large — 100 × 100 squares", (100, 100)),
               ("Huge — 160 × 160 squares", (160, 160)),
               ("Custom", None))

SELECTION_PREVIEW_MAX = 400      # rows listed before the list is summarised


def ensure_sizes(assets, library):
    """Fill missing pixel dimensions from image headers, never full decoding."""
    for a in assets:
        if not a.width or not a.height:
            reader = QImageReader(library.abs_path(a.path))
            size = reader.size()
            if size.isValid():
                a.width, a.height = size.width(), size.height()


def _help(text: str, colors: dict) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color:{colors['muted']};")
    return label


class GeneratorDialog(QDialog):
    def __init__(self, project, library, canvas, generate_cb, parent=None,
                 selection=None):
        super().__init__(parent)
        self.project = project
        # Accept either the LibraryPanel (live selection) or a bare AssetLibrary.
        self.panel = library if hasattr(library, "selected_assets") else None
        self.library = getattr(library, "library", library) \
            if not hasattr(library, "assets") else library
        self.canvas = canvas
        self.generate_cb = generate_cb
        self.theme_mode = getattr(parent, "theme_mode", "dark")
        self.theme_accent = getattr(parent, "theme_accent", thememod.DEFAULT_ACCENT)
        self.colors = thememod.theme_colors(self.theme_mode, self.theme_accent)
        product = ALIEN_NAME if self.theme_mode == "alien" else APP_NAME
        self.setWindowTitle(f"{product} — Map Generator")
        self.setMinimumSize(620, 620)
        self.resize(700, 820)
        self.assets = []
        self._build()
        self.set_selection(selection if selection is not None
                           else self._panel_selection())
        self._on_layout_changed()

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build(self):
        outer = QVBoxLayout(self)
        outer.addWidget(_help(
            "The map is built from the assets you selected in the library — "
            "nothing else is used, and nothing is guessed about what they are. "
            "Every Generate adds a new level (or new nodes), so nothing you "
            "made is lost.", self.colors))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 6, 0)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._build_selection_section()
        self._build_layout_section()
        self._build_size_section()
        self._build_seed_section()
        self.body_layout.addStretch(1)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        outer.addWidget(self.lbl_status)

        buttons = QHBoxLayout()
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        self.btn_regenerate = QPushButton("↻ Regenerate (new seed)")
        self.btn_regenerate.setToolTip(
            "Roll a new random seed and replace the previous result of this "
            "kind. With 'Keep this seed' ticked it rebuilds with the same seed "
            "instead, so you can compare the effect of other settings.")
        self.btn_regenerate.clicked.connect(self._regenerate)
        self.btn_generate = QPushButton("Generate")
        self.btn_generate.setDefault(True)
        self.btn_generate.setToolTip(
            "Build with the seed shown above and keep earlier results.")
        self.btn_generate.clicked.connect(
            lambda _checked=False: self._generate(replace_previous=False))
        buttons.addWidget(self.btn_close)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_regenerate)
        buttons.addWidget(self.btn_generate)
        outer.addLayout(buttons)

    def _section(self, title: str, hint: str = ""):
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        if hint:
            layout.addWidget(_help(hint, self.colors))
        self.body_layout.addWidget(box)
        return layout, box

    def _build_selection_section(self):
        layout, self.selection_box = self._section(
            "1 · The assets to use",
            "These are the assets selected in the library panel. Select more "
            "(Shift-click or drag a marquee) and press “Refresh from the "
            "library” to bring them in.")
        self.selection_list = QListWidget()
        self.selection_list.setIconSize(QSize(44, 44))
        self.selection_list.setMinimumHeight(132)
        self.selection_list.setMaximumHeight(210)
        self.selection_list.setUniformItemSizes(True)
        self.selection_list.setWordWrap(True)
        layout.addWidget(self.selection_list)
        row = QHBoxLayout()
        self.lbl_selection = QLabel("")
        self.lbl_selection.setWordWrap(True)
        row.addWidget(self.lbl_selection, 1)
        self.btn_refresh = QPushButton("Refresh from the library")
        self.btn_refresh.setToolTip(
            "Re-read the current selection in the library panel.")
        self.btn_refresh.clicked.connect(
            lambda: self.set_selection(self._panel_selection()))
        row.addWidget(self.btn_refresh)
        self.btn_all = QPushButton("Use everything shown")
        self.btn_all.setToolTip(
            "Use every asset the library is showing right now (the folder you "
            "are browsing, with any search applied).")
        self.btn_all.clicked.connect(
            lambda: self.set_selection(self._panel_visible()))
        row.addWidget(self.btn_all)
        layout.addLayout(row)

    def _build_layout_section(self):
        layout, _box = self._section(
            "2 · How to lay them out")
        form = QFormLayout()
        layout.addLayout(form)
        self.cmb_layout = QComboBox()
        for key in mapbuilder.LAYOUTS:
            self.cmb_layout.addItem(mapbuilder.LAYOUT_LABELS[key], key)
        self.cmb_layout.currentIndexChanged.connect(self._on_layout_changed)
        form.addRow("Layout", self.cmb_layout)
        self.lbl_layout_tip = _help("", self.colors)
        form.addRow("", self.lbl_layout_tip)

        self.spin_copies = QSpinBox()
        self.spin_copies.setRange(1, 500)
        self.spin_copies.setValue(1)
        self.spin_copies.setSuffix(" ×")
        self.spin_copies.setToolTip(
            "How many copies of each selected asset to place.")
        form.addRow("Copies of each asset", self.spin_copies)

        self.spin_gap = QSpinBox()
        self.spin_gap.setRange(0, 20)
        self.spin_gap.setValue(0)
        self.spin_gap.setSuffix(" squares")
        self.spin_gap.setToolTip("Empty grid squares left between placements.")
        form.addRow("Spacing", self.spin_gap)

        self.sl_scale, self.lbl_scale = self._slider(
            25, 400, 100, lambda v: f"{v}% size")
        self.sl_scale.setToolTip(
            "Resize every asset. 100% is the size it gets when you drag it "
            "onto the canvas yourself.")
        form.addRow("Asset size", self._slider_row(self.sl_scale, self.lbl_scale))

        self.chk_rotate = QCheckBox("Allow 90° rotations")
        self.chk_rotate.setToolTip(
            "Place some copies turned sideways to fill gaps and add variety.")
        form.addRow("", self.chk_rotate)
        self.chk_shuffle = QCheckBox("Shuffle the placement order")
        self.chk_shuffle.setToolTip(
            "Off: assets are placed in the order the library lists them.\n"
            "On: the seed decides the order.")
        form.addRow("", self.chk_shuffle)
        self.sl_scale.valueChanged.emit(self.sl_scale.value())

    def _build_size_section(self):
        layout, self.size_box = self._section("3 · Size and placement")
        self.size_form = QFormLayout()
        layout.addLayout(self.size_form)

        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem("A new level", "new")
        self.cmb_mode.addItem("Inside the selected nodes' area", "area")
        self.cmb_mode.setToolTip(
            "New level adds a fresh level and builds into it.\n"
            "Selected area builds inside the bounds of the nodes you have "
            "selected on the canvas.")
        self.cmb_mode.currentIndexChanged.connect(self._on_mode_changed)
        self.size_form.addRow("Build into", self.cmb_mode)

        self.cmb_preset = QComboBox()
        for label, size in MAP_PRESETS:
            self.cmb_preset.addItem(label, size)
        self.cmb_preset.setCurrentIndex(1)
        self.cmb_preset.currentIndexChanged.connect(self._preset_changed)
        self.size_form.addRow("Map size", self.cmb_preset)

        dims = QHBoxLayout()
        self.spin_cols = QSpinBox()
        self.spin_cols.setRange(4, 400)
        self.spin_cols.setValue(60)
        self.spin_cols.setSuffix(" wide")
        self.spin_rows = QSpinBox()
        self.spin_rows.setRange(4, 400)
        self.spin_rows.setValue(60)
        self.spin_rows.setSuffix(" tall")
        for spin in (self.spin_cols, self.spin_rows):
            spin.valueChanged.connect(self._custom_size_changed)
            dims.addWidget(spin)
        dims.addStretch(1)
        dim_holder = QWidget()
        dim_holder.setLayout(dims)
        dims.setContentsMargins(0, 0, 0, 0)
        self.size_form.addRow("", dim_holder)
        self.lbl_size_hint = _help("", self.colors)
        self.size_form.addRow("", self.lbl_size_hint)

    def _build_seed_section(self):
        layout, _box = self._section(
            "4 · Seed",
            "The seed decides every random choice. The same seed with the same "
            "settings and the same selection always builds the same map. Press "
            "the dice for a new random seed, or type your own numbers or words "
            "(e.g. hangar-7).")
        row = QHBoxLayout()
        self.edit_seed = QLineEdit()
        self.edit_seed.setMaxLength(MAX_SEED_LENGTH)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.edit_seed.setFont(mono)
        self.edit_seed.setPlaceholderText("12 random digits, or any words")
        self.edit_seed.setText(new_seed())
        row.addWidget(self.edit_seed, 1)
        self.btn_dice = QPushButton("🎲 New random seed")
        self.btn_dice.setToolTip("Pick a fresh random 12-digit seed.")
        self.btn_dice.clicked.connect(self._reroll)
        row.addWidget(self.btn_dice)
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setToolTip("Copy the seed so you can share or save it.")
        self.btn_copy.clicked.connect(self._copy_seed)
        row.addWidget(self.btn_copy)
        layout.addLayout(row)
        self.chk_keep_seed = QCheckBox("Keep this seed when I press Regenerate")
        self.chk_keep_seed.setToolTip(
            "Regenerate normally rolls a new random seed. Tick this to rebuild "
            "with the same seed after changing other settings.")
        layout.addWidget(self.chk_keep_seed)

    def _slider(self, low, high, value, formatter):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setValue(value)
        label = QLabel()
        label.setMinimumWidth(110)
        slider.valueChanged.connect(lambda v: label.setText(formatter(v)))
        return slider, label

    @staticmethod
    def _slider_row(slider, label) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(slider, 1)
        row.addWidget(label)
        return holder

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------
    @property
    def layout_key(self) -> str:
        return self.cmb_layout.currentData() or "grid"

    def _panel_selection(self) -> list:
        if self.panel is None:
            return list(self.library.assets)
        return list(self.panel.selected_assets())

    def _panel_visible(self) -> list:
        if self.panel is None:
            return list(self.library.assets)
        return list(self.panel.visible_assets())

    def set_selection(self, assets) -> int:
        """Show these assets as the ones the map will be built from."""
        self.assets = [asset for asset in (assets or [])
                       if getattr(asset, "path", "")]
        ensure_sizes(self.assets, self.library)
        self.selection_list.clear()
        shown = self.assets[:SELECTION_PREVIEW_MAX]
        cell = max(1, int(getattr(self.project, "cell_size", 70)))
        fps = max(1, int(getattr(self.project, "feet_per_square", 5) or 5))
        scale = self.sl_scale.value() / 100.0
        footprints = {row["path"]: row for row in mapbuilder.describe_selection(
            self.assets, cell, fps, scale)}
        for asset in shown:
            info = footprints.get(asset.path)
            text = asset.name
            if info:
                text += (f"   —   {info['cells_w']} × {info['cells_h']} squares"
                         f"   ({info['px_w']} × {info['px_h']} px)")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, asset.path)
            item.setToolTip(asset.path + (f"\n{asset.folder}"
                                          if asset.folder not in ("", ".") else ""))
            try:
                pixmap = load_scaled_pixmap(self.library.abs_path(asset.path), 44)
                if not pixmap.isNull():
                    item.setIcon(QIcon(pixmap))
            except Exception:
                pass
            self.selection_list.addItem(item)
        total = len(self.assets)
        if total > len(shown):
            self.selection_list.addItem(
                QListWidgetItem(f"… and {total - len(shown):,} more"))
        self.selection_list.setVisible(total > 0)
        squares = 0
        if footprints:
            squares = sum(row["cells_w"] * row["cells_h"]
                          for row in footprints.values())
        self.lbl_selection.setText(
            f"{total:,} asset(s) selected · together they cover about "
            f"{squares:,} grid squares at 100% size." if total
            else "Nothing is selected yet. Pick assets in the library panel "
                 "(Shift-click for several), then press “Refresh from the "
                 "library” — or use “Use everything shown”.")
        self.selection_box.setTitle(
            f"1 · The assets to use ({total:,})" if total
            else "1 · The assets to use — nothing selected yet")
        self._update_size_hint()
        return total

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def _on_layout_changed(self, *_):
        key = self.layout_key
        self.lbl_layout_tip.setText(mapbuilder.LAYOUT_ABOUT.get(key, ""))
        filling = key == "fill"
        self.spin_copies.setEnabled(not filling)
        if filling:
            self.spin_copies.setToolTip(
                "Not used by “Fill the area”: the selection repeats until the "
                "area is covered.")
        else:
            self.spin_copies.setToolTip(
                "How many copies of each selected asset to place.")
        if not self.chk_shuffle.isChecked():
            self.chk_shuffle.setChecked(key == "scatter")

    def _on_mode_changed(self, *_):
        new_level = self.cmb_mode.currentData() == "new"
        for widget in (self.cmb_preset, self.lbl_size_hint):
            self.size_form.setRowVisible(widget, new_level)
        self.size_form.setRowVisible(self.spin_cols.parentWidget(), new_level)
        self._update_size_hint()

    def _preset_changed(self, *_):
        size = self.cmb_preset.currentData()
        if size:
            for spin, value in ((self.spin_cols, size[0]), (self.spin_rows, size[1])):
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        self._update_size_hint()

    def _custom_size_changed(self, *_):
        for index in range(self.cmb_preset.count()):
            if self.cmb_preset.itemData(index) == (self.spin_cols.value(),
                                                   self.spin_rows.value()):
                self.cmb_preset.blockSignals(True)
                self.cmb_preset.setCurrentIndex(index)
                self.cmb_preset.blockSignals(False)
                break
        else:
            self.cmb_preset.blockSignals(True)
            self.cmb_preset.setCurrentIndex(self.cmb_preset.count() - 1)
            self.cmb_preset.blockSignals(False)
        self._update_size_hint()

    def _update_size_hint(self):
        cs = max(1, int(getattr(self.project, "cell_size", 70)))
        cols, rows = self.spin_cols.value(), self.spin_rows.value()
        text = (f"{cols} × {rows} squares = {cols * cs:,} × {rows * cs:,} px "
                f"at {cs} px per square.")
        self.lbl_size_hint.setText(text)

    # ------------------------------------------------------------------
    # seed
    # ------------------------------------------------------------------
    def _reroll(self):
        self.edit_seed.setText(new_seed())

    def _copy_seed(self):
        QGuiApplication.clipboard().setText(self.edit_seed.text())
        self._tone("success")
        self.lbl_status.setText("Seed copied.")

    def _tone(self, tone: str):
        colors = {
            "success": (self.colors["accent"] if self.theme_mode == "alien"
                        else ("#347a4d" if self.theme_mode == "light" else "#83d6a3")),
            "warning": "#b47a08" if self.theme_mode == "light" else "#ffbf47",
            "error": "#b42332" if self.theme_mode == "light" else "#ff6b75",
        }
        self.lbl_status.setStyleSheet(f"color:{colors.get(tone, self.colors['text'])};")

    # kept for older callers
    _set_status_tone = _tone

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def _opts(self, replace_previous=False) -> dict:
        return {
            "selection": list(self.assets),
            "seed": clean_seed(self.edit_seed.text()),
            "cell_size": max(1, int(getattr(self.project, "cell_size", 70))),
            "feet_per_square": getattr(self.project, "feet_per_square", 5),
            "layout": self.layout_key,
            "copies": self.spin_copies.value(),
            "gap": self.spin_gap.value(),
            "scale": self.sl_scale.value() / 100.0,
            "rotate": self.chk_rotate.isChecked(),
            "shuffle": self.chk_shuffle.isChecked(),
            "mode": self.cmb_mode.currentData(),
            "size": (self.spin_cols.value(), self.spin_rows.value()),
            "replace_prev": bool(replace_previous),
        }

    def _reroll_for_regenerate(self):
        if not self.chk_keep_seed.isChecked():
            self._reroll()

    def _regenerate(self):
        previous = self.edit_seed.text()
        self._reroll_for_regenerate()
        ok = self._generate(replace_previous=True)
        if not ok:
            self.edit_seed.setText(previous)
        return ok

    def _problem(self, message: str) -> bool:
        self._tone("error")
        self.lbl_status.setText(message)
        return False

    def _generate(self, replace_previous=False) -> bool:
        self.edit_seed.setText(clean_seed(self.edit_seed.text()))
        if not self.assets:
            return self._problem(
                "No assets are selected. Pick them in the library panel and "
                "press “Refresh from the library”, or use “Use everything "
                "shown”.")
        if self.cmb_mode.currentData() == "area" and not self.canvas.selected_pieces():
            return self._problem(
                "Select the nodes whose area you want to fill on the canvas "
                "first, or choose 'A new level'.")
        try:
            result = self.generate_cb(self._opts(replace_previous))
        except Exception as exc:
            return self._problem(f"Generation failed: {exc}")
        if not isinstance(result, dict):
            return self._problem(
                "Generation did not return a map. Check the settings and try "
                "again.")
        pieces = result.get("pieces") or []
        warnings = result.get("warnings") or []
        if not pieces:
            self._tone("warning")
            detail = ("\n" + "  ".join("! " + str(w) for w in warnings)
                      if warnings else "")
            self.lbl_status.setText(
                "Nothing was generated; any previous result was kept." + detail)
            return False

        counts = result.get("counts", {})
        used = result.get("used_cells") or (0, 0)
        msg = (f"{counts.get('pieces', len(pieces)):,} nodes from "
               f"{counts.get('assets', 0):,} asset(s) · covers "
               f"{used[0]} × {used[1]} squares · seed {result.get('seed')}")
        if warnings:
            msg += "\n" + "  ".join("! " + str(w) for w in warnings)
            self._tone("warning")
        else:
            self._tone("success")
        self.lbl_status.setText(
            ("Regenerated: " if replace_previous else "Generated: ") + msg)
        return True
