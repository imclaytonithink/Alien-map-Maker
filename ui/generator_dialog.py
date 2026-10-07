"""Map generator: one window, one set of options.

1. Choose *what to build* (assemble rooms/decks, build from small tiles, or
   furnish rooms).
2. Choose which kinds of assets (roles) feed it. Assets are sorted into roles
   automatically; "Check asset sorting" opens the sorter to fix mistakes.
3. Set the size, style and seed, then Generate.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QGuiApplication, QImageReader
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton,
    QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from core import assembly
from core import generator as gen
from core.asset_roles import ROLE_ABOUT, ROLE_LABELS
from core.seeds import MAX_SEED_LENGTH, clean_seed, new_seed
from ui import theme as thememod
from ui.branding import ALIEN_NAME, APP_NAME

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

STRATEGY_INFO = {
    "assembly": (
        "Assemble a map from rooms and decks",
        "Packs complete deck plans, rooms and empty rooms together into a map of "
        "the size you choose. Best for ships, stations and bases."),
    "tiles": (
        "Build rooms and corridors from small tiles",
        "Draws rooms, corridors, walls and doors tile by tile from small floor, "
        "wall and door pieces, then adds props."),
    "furnish": (
        "Furnish existing rooms",
        "Scatters interior parts (furniture, consoles, equipment) inside rooms "
        "that are already on the map — ideal for empty rooms."),
}

# ingredient rows per strategy: (role id, label shown, explanation)
INGREDIENTS = {
    "assembly": (
        ("deck_plan", "Deck plans & large modules",
         "Full decks of 100 ft or more. The main building blocks."),
        ("room", "Room modules",
         "Self-contained rooms around 50 ft, tiled together to build zones."),
        ("empty_room", "Empty rooms",
         "Blank shells used as filler (see 'Empty-room share' below)."),
    ),
    "tiles": (
        ("floor_tile", "Floor tiles", "Fill the inside of rooms."),
        ("wall_tile", "Wall tiles", "Outline the rooms."),
        ("corridor", "Corridor pieces", "Also used as floor between rooms."),
        ("door", "Doors & hatches", "Placed where a corridor meets a room."),
        ("interior_part", "Props & fixtures", "Consoles, crates, furniture."),
        ("overlay", "Hazards (spills, glow)", "Occasional overlay on floors."),
    ),
    "furnish": (
        ("interior_part", "Interior parts",
         "Furniture, consoles, machinery and other small items."),
    ),
}

MAP_PRESETS = (("Small — 30 × 30 squares", (30, 30)),
               ("Medium — 60 × 60 squares", (60, 60)),
               ("Large — 100 × 100 squares", (100, 100)),
               ("Huge — 160 × 160 squares", (160, 160)),
               ("Custom", None))


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
    def __init__(self, project, library, canvas, generate_cb, parent=None):
        super().__init__(parent)
        self.project = project
        # accept either the AssetLibrary itself or the LibraryPanel wrapper
        self.panel = library if hasattr(library, "open_sort_dialog") else None
        self.library = getattr(library, "library", library) \
            if not hasattr(library, "assets") else library
        self.canvas = canvas
        self.generate_cb = generate_cb
        self.theme_mode = getattr(parent, "theme_mode", "dark")
        self.theme_accent = getattr(parent, "theme_accent", thememod.DEFAULT_ACCENT)
        self.colors = thememod.theme_colors(self.theme_mode, self.theme_accent)
        product = ALIEN_NAME if self.theme_mode == "alien" else APP_NAME
        self.setWindowTitle(f"{product} — Map Generator")
        self.setMinimumSize(640, 600)
        self.resize(720, 820)
        self.pools = {}
        self.tile_cats = {}
        self.role_checks: dict[str, QCheckBox] = {}
        self._build()
        self._reload_pools()
        self._on_strategy_changed()

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build(self):
        outer = QVBoxLayout(self)
        intro = _help(
            "Pick what to build, choose which assets feed it, set the size, style "
            "and seed, then press Generate. Every Generate adds a new level (or "
            "new nodes), so nothing you made is lost.", self.colors)
        outer.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 6, 0)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._build_strategy_section()
        self._build_ingredients_section()
        self._build_size_section()
        self._build_style_section()
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
            "Roll a new random seed and replace the previous result of this kind. "
            "With 'Keep this seed' ticked it rebuilds with the same seed instead, "
            "so you can compare the effect of other settings.")
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

    def _section(self, title: str, hint: str = "") -> QVBoxLayout:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        if hint:
            layout.addWidget(_help(hint, self.colors))
        self.body_layout.addWidget(box)
        box.layout_ref = layout
        return layout, box

    def _build_strategy_section(self):
        layout, _box = self._section("1 · What do you want to build?")
        self.strategy_group = QButtonGroup(self)
        self.strategy_radios = {}
        self.strategy_status = {}
        for key, (title, text) in STRATEGY_INFO.items():
            radio = QRadioButton(title)
            font = radio.font()
            font.setBold(True)
            radio.setFont(font)
            self.strategy_group.addButton(radio)
            self.strategy_radios[key] = radio
            layout.addWidget(radio)
            layout.addWidget(_help(text, self.colors))
            status = QLabel("")
            status.setWordWrap(True)
            self.strategy_status[key] = status
            layout.addWidget(status)
            radio.toggled.connect(self._on_strategy_changed)
        self.strategy_radios["assembly"].setChecked(True)

    def _build_ingredients_section(self):
        layout, self.ingredients_box = self._section(
            "2 · Which assets should it use?",
            "Every asset is sorted into one role (what it is for). Tick the kinds "
            "to include. If something looks misfiled, fix its role — the generator "
            "only uses what is sorted correctly.")
        self.ingredient_layout = QVBoxLayout()
        layout.addLayout(self.ingredient_layout)
        row = QHBoxLayout()
        self.btn_sort = QPushButton("Check asset sorting…")
        self.btn_sort.setToolTip(
            "Review how assets were sorted into roles and correct mistakes.")
        self.btn_sort.clicked.connect(self._open_sorter)
        row.addWidget(self.btn_sort)
        self.lbl_unsorted = QLabel("")
        self.lbl_unsorted.setWordWrap(True)
        row.addWidget(self.lbl_unsorted, 1)
        layout.addLayout(row)

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
        self.size_form.addRow("Build into", self.cmb_mode)

        self.cmb_preset = QComboBox()
        for label, size in MAP_PRESETS:
            self.cmb_preset.addItem(label, size)
        self.cmb_preset.setCurrentIndex(1)
        self.cmb_preset.currentIndexChanged.connect(self._preset_changed)
        self.size_form.addRow("Map size", self.cmb_preset)
        dims = QHBoxLayout()
        self.spin_cols = QSpinBox()
        self.spin_cols.setRange(10, 400)
        self.spin_cols.setValue(60)
        self.spin_cols.setSuffix(" wide")
        self.spin_rows = QSpinBox()
        self.spin_rows.setRange(10, 400)
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

        self.cmb_scope = QComboBox()
        self.cmb_scope.addItem("The nodes I have selected", "selected")
        self.cmb_scope.addItem("Every empty room on this level", "empty")
        self.cmb_scope.setToolTip(
            "Which rooms receive furniture. 'Empty room' means assets whose role "
            "is Empty rooms.")
        self.size_form.addRow("Furnish", self.cmb_scope)
        self.spin_margin = QSpinBox()
        self.spin_margin.setRange(0, 10)
        self.spin_margin.setValue(2)
        self.spin_margin.setSuffix(" squares")
        self.spin_margin.setToolTip("Keep parts this far from each room's walls.")
        self.size_form.addRow("Wall margin", self.spin_margin)

    def _build_style_section(self):
        layout, self.style_box = self._section("4 · Style")
        self.style_form = QFormLayout()
        layout.addLayout(self.style_form)

        # assembly
        self.cmb_packing = QComboBox()
        self.cmb_packing.addItem("Mixed sizes — decks and rooms of different sizes", "mixed")
        self.cmb_packing.addItem("Uniform — one size only, in a tidy grid", "uniform")
        self.style_form.addRow("Packing", self.cmb_packing)
        self.sl_empty, self.lbl_empty = self._slider(
            0, 100, 15, lambda v: f"{v}% empty rooms")
        self.style_form.addRow("Empty-room share", self._slider_row(
            self.sl_empty, self.lbl_empty))
        self.sl_overlay, self.lbl_overlay = self._slider(
            0, 100, 35, lambda v: f"{v}% get an overlay")
        self.style_form.addRow("Overlays", self._slider_row(
            self.sl_overlay, self.lbl_overlay))
        self.sl_symbols, self.lbl_symbols = self._slider(
            0, 100, 0, lambda v: "none" if v == 0 else f"{v}% density")
        self.style_form.addRow("Symbols", self._slider_row(
            self.sl_symbols, self.lbl_symbols))
        self.chk_rotate = QCheckBox("Rotate modules (90° steps)")
        self.chk_rotate.setChecked(True)
        self.style_form.addRow("", self.chk_rotate)
        self.chk_hull = QCheckBox("Add ship hull parts around the edge (experimental)")
        self.chk_hull.setToolTip(
            "Places a nose and matching port/starboard sections around the map. "
            "Needs assets sorted as Ship parts. The map grows to make room.")
        self.style_form.addRow("", self.chk_hull)

        # tiles
        self.cmb_setting = QComboBox()
        self.cmb_setting.addItems(gen.SETTINGS)
        self.lbl_setting_tip = _help(SETTING_TIPS["Random"], self.colors)
        self.cmb_setting.currentTextChanged.connect(
            lambda t: self.lbl_setting_tip.setText(SETTING_TIPS.get(t, "")))
        self.style_form.addRow("Setting", self.cmb_setting)
        self.style_form.addRow("", self.lbl_setting_tip)
        self.cmb_layout = QComboBox()
        self.cmb_layout.addItems(["Random", "Corridors", "Grid", "Organic"])
        self.lbl_layout_tip = _help(LAYOUT_TIPS["Random"], self.colors)
        self.cmb_layout.currentTextChanged.connect(
            lambda t: self.lbl_layout_tip.setText(LAYOUT_TIPS.get(t, "")))
        self.style_form.addRow("Layout", self.cmb_layout)
        self.style_form.addRow("", self.lbl_layout_tip)
        self.sl_clutter, self.lbl_clutter = self._slider(
            0, 100, 35, lambda v: f"{v}% props")
        self.style_form.addRow("Props & fixtures", self._slider_row(
            self.sl_clutter, self.lbl_clutter))

        # furnish
        self.sl_furnish, self.lbl_furnish = self._slider(
            0, 100, 50, lambda v: "sparse" if v < 25 else
            ("moderate" if v < 60 else ("full" if v < 85 else "packed")))
        self.style_form.addRow("How full", self._slider_row(
            self.sl_furnish, self.lbl_furnish))
        for slider in (self.sl_empty, self.sl_overlay, self.sl_symbols,
                       self.sl_clutter, self.sl_furnish):
            slider.valueChanged.emit(slider.value())

    def _build_seed_section(self):
        layout, _box = self._section(
            "5 · Seed",
            "The seed decides every random choice. The same seed with the same "
            "settings and assets always builds the same map. Press the dice for a "
            "new random seed, or type your own numbers or words (e.g. hangar-7).")
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
        label.setMinimumWidth(120)
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
    # state
    # ------------------------------------------------------------------
    @property
    def strategy(self) -> str:
        for key, radio in self.strategy_radios.items():
            if radio.isChecked():
                return key
        return "assembly"

    def _reload_pools(self):
        """Re-sort and re-pool the library (also after the sorter closes)."""
        ensure_sizes(self.library.assets, self.library)
        roles = self.library.roles()
        self.roles = roles
        fps = getattr(self.project, "feet_per_square", 5)
        self.pools = assembly.build_pools(self.library.assets, roles, fps)
        self.tile_cats = assembly.categories_from_roles(self.library.assets, roles)
        self.role_totals = {}
        for info in roles.values():
            self.role_totals[info.role] = self.role_totals.get(info.role, 0) + 1
        self._update_readiness()
        if hasattr(self, "ingredient_layout"):
            self._rebuild_ingredients()

    def _usable(self, role: str) -> int:
        counts = self.pools.get("counts", {})
        tile_map = {"floor_tile": "floor", "wall_tile": "wall",
                    "corridor": "corridor", "door": "door"}
        if role in tile_map:
            return len(self.tile_cats.get(tile_map[role], []))
        if role == "interior_part" and self.strategy == "tiles":
            return len(self.tile_cats.get("wall_fixture", [])) + \
                len(self.tile_cats.get("floor_fixture", []))
        if role == "overlay" and self.strategy == "tiles":
            return len(self.tile_cats.get("hazard", []))
        return int(counts.get(role, 0))

    def _rebuild_ingredients(self):
        previous = {role: box.isChecked() for role, box in self.role_checks.items()}
        while self.ingredient_layout.count():
            item = self.ingredient_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)      # gone immediately, no ghost painting
                widget.deleteLater()
        self.role_checks = {}
        for role, label, about in INGREDIENTS[self.strategy]:
            usable = self._usable(role)
            total = self.role_totals.get(role, 0)
            box = QCheckBox(f"{label.replace('&', '&&')}  ({usable:,} usable)")
            box.setToolTip(f"{ROLE_ABOUT[role]}\n{about}")
            box.setChecked(previous.get(role, usable > 0))
            box.setEnabled(usable > 0)
            box.toggled.connect(self._update_readiness)
            self.role_checks[role] = box
            row = QVBoxLayout()
            row.setSpacing(0)
            row.addWidget(box)
            note = about
            if total > usable and self.strategy == "assembly":
                note += (f"  {total - usable:,} more are sorted here but unusable "
                         "(no size in the name, or too small).")
            elif usable == 0:
                note += "  None found — import some or sort assets into this role."
            row.addWidget(_help(note, self.colors))
            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(18, 0, 0, 4)
            self.ingredient_layout.addWidget(holder)
        review = sum(1 for info in self.roles.values()
                     if info.confidence == "low" or info.role == "other")
        self.lbl_unsorted.setText(
            f"{review:,} asset(s) are unsorted or low-confidence." if review
            else "Everything was sorted with confidence.")

    def _enabled_roles(self) -> list[str]:
        return [role for role, box in self.role_checks.items()
                if box.isChecked() and box.isEnabled()]

    def _update_readiness(self, *_):
        def line(key, ok, good, bad):
            label = self.strategy_status[key]
            label.setText(("✔ " + good) if ok else ("✖ " + bad))
            label.setStyleSheet(
                f"color:{'#83d6a3' if ok else '#ffbf47'};")
        counts = self.pools.get("counts", {})
        modules = sum(counts.get(r, 0) for r in assembly.MODULE_ROLES)
        line("assembly", modules > 0 and self.pools.get("pixels_per_square", 0) > 0,
             f"Ready — {counts.get('deck_plan', 0):,} deck plans, "
             f"{counts.get('room', 0):,} rooms, {counts.get('empty_room', 0):,} empty rooms.",
             "Needs deck plans or rooms with sizes in their names (like [100x100]).")
        floors = len(self.tile_cats.get("floor", [])) + len(self.tile_cats.get("corridor", []))
        line("tiles", floors > 0,
             f"Ready — {floors:,} floor pieces, {len(self.tile_cats.get('wall', [])):,} walls.",
             "Needs small floor and wall tiles.")
        line("furnish", counts.get("interior_part", 0) > 0,
             f"Ready — {counts.get('interior_part', 0):,} interior parts.",
             "Needs assets sorted as Interior parts.")

    def _on_strategy_changed(self, *_):
        if not hasattr(self, "size_form"):
            return
        strategy = self.strategy
        self._rebuild_ingredients()

        def show(form, widget, visible):
            form.setRowVisible(widget, visible)

        build = strategy in ("assembly", "tiles")
        show(self.size_form, self.cmb_mode, build)
        for widget in (self.cmb_preset, self.lbl_size_hint):
            show(self.size_form, widget, build)
        show(self.size_form, self.spin_cols.parentWidget(), build)
        show(self.size_form, self.cmb_scope, strategy == "furnish")
        show(self.size_form, self.spin_margin, strategy == "furnish")
        asm = strategy == "assembly"
        for widget in (self.cmb_packing, self.sl_empty.parentWidget(),
                       self.sl_overlay.parentWidget(), self.sl_symbols.parentWidget(),
                       self.chk_rotate, self.chk_hull):
            show(self.style_form, widget, asm)
        tiles = strategy == "tiles"
        for widget in (self.cmb_setting, self.lbl_setting_tip, self.cmb_layout,
                       self.lbl_layout_tip, self.sl_clutter.parentWidget()):
            show(self.style_form, widget, tiles)
        show(self.style_form, self.sl_furnish.parentWidget(), strategy == "furnish")
        self.size_box.setTitle("3 · Size and placement" if build else "3 · Which rooms")
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
            size = self.cmb_preset.itemData(index)
            if size == (self.spin_cols.value(), self.spin_rows.value()):
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
        cs = self.project.cell_size
        cols, rows = self.spin_cols.value(), self.spin_rows.value()
        text = (f"{cols} × {rows} squares = {cols * cs:,} × {rows * cs:,} px "
                f"at {cs} px per square.")
        if self.strategy == "assembly":
            text += " Sizes that are multiples of 10 squares (50 ft) pack with no gaps."
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

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def _open_sorter(self):
        if self.panel is not None:
            self.panel.open_sort_dialog()
        else:
            from ui.sort_dialog import SortDialog
            SortDialog(self.library, parent=self).exec()
        self._reload_pools()

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

    def _opts(self, replace_previous=False) -> dict:
        strategy = self.strategy
        roles = self._enabled_roles()
        cats = {name: list(items) for name, items in self.tile_cats.items()}
        if strategy == "tiles":
            keep = {"floor": "floor_tile", "wall": "wall_tile",
                    "corridor": "corridor", "door": "door"}
            for cat, role in keep.items():
                if role not in roles:
                    cats[cat] = []
            if "interior_part" not in roles:
                cats["wall_fixture"], cats["floor_fixture"] = [], []
            if "overlay" not in roles:
                cats["hazard"] = []
        return {
            "strategy": strategy,
            "seed": clean_seed(self.edit_seed.text()),
            "feet_per_square": getattr(self.project, "feet_per_square", 5),
            "pools": self.pools,
            "categories": cats,
            "roles": roles,
            "packing": self.cmb_packing.currentData(),
            "empty_share": self.sl_empty.value() / 100.0,
            "overlay_density": self.sl_overlay.value() / 100.0,
            "symbol_density": self.sl_symbols.value() / 100.0,
            "rotate": self.chk_rotate.isChecked(),
            "hull": self.chk_hull.isChecked(),
            "setting": self.cmb_setting.currentText(),
            "layout": self.cmb_layout.currentText(),
            "clutter": self.sl_clutter.value() / 100.0,
            "furnish_density": self.sl_furnish.value() / 100.0,
            "wall_margin": float(self.spin_margin.value()),
            "scope": self.cmb_scope.currentData(),
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
        strategy = self.strategy
        self.edit_seed.setText(clean_seed(self.edit_seed.text()))
        if (strategy != "furnish" and self.cmb_mode.currentData() == "area"
                and not self.canvas.selected_pieces()):
            return self._problem(
                "Select the nodes whose area you want to fill on the canvas first, "
                "or choose 'A new level'.")
        if (strategy == "furnish" and self.cmb_scope.currentData() == "selected"
                and not self.canvas.selected_pieces()):
            return self._problem(
                "Select the rooms you want to furnish on the canvas first, or "
                "choose 'Every empty room on this level'.")

        counts = self.pools.get("counts", {})
        if strategy == "assembly":
            if not self._enabled_roles():
                return self._problem(
                    "Tick at least one kind of module (deck plans, rooms or empty "
                    "rooms) under step 2.")
        elif strategy == "tiles":
            if not (self.tile_cats["floor"] or self.tile_cats["corridor"]):
                return self._problem(
                    "No floor tiles were found. Import small floor pieces, or sort "
                    "some assets as Floor tiles (Check asset sorting).")
            if not self._enabled_roles():
                return self._problem("Tick at least one kind of tile under step 2.")
        else:
            if not counts.get("interior_part"):
                return self._problem(
                    "No interior parts found. Sort some assets as Interior parts "
                    "(Check asset sorting).")
        try:
            result = self.generate_cb(self._opts(replace_previous))
        except Exception as exc:
            return self._problem(f"Generation failed: {exc}")
        if not isinstance(result, dict):
            return self._problem(
                "Generation did not return a map. Check the settings and try again.")
        pieces = result.get("pieces") or []
        warnings = result.get("warnings") or []
        if not pieces:
            self._tone("warning")
            detail = "\n" + "  ".join("! " + str(w) for w in warnings) if warnings else ""
            self.lbl_status.setText(
                "Nothing was generated; any previous result was kept." + detail)
            return False

        c = result.get("counts", {})
        mode = result.get("mode")
        if mode == "assembly":
            msg = (f"{c.get('modules', 0)} modules · {c.get('overlays', 0)} overlays · "
                   f"{c.get('symbols', 0)} symbols · {c.get('hull', 0)} hull parts")
        elif mode == "furnish":
            msg = f"{c.get('pieces', 0)} parts in {c.get('rooms', 0)} room(s)"
        else:
            msg = (f"{c.get('pieces', len(pieces))} nodes · {c.get('rooms', 0)} rooms · "
                   f"{result.get('setting', '')} / {result.get('layout', '')}")
        msg += f" · seed {result.get('seed')}"
        if warnings:
            msg += "\n" + "  ".join("! " + str(w) for w in warnings)
            self._tone("warning")
        else:
            self._tone("success")
        self.lbl_status.setText(("Regenerated: " if replace_previous else "Generated: ") + msg)
        return True
