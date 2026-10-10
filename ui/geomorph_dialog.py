"""Geomorph generator: believable, fully keyed ships and sites from the
Starship Geomorphs 2.0 tiles.

Pick *Ship* or *Site*, set the options, press Generate. The preview shows each
level; **Place on canvas** adds the levels to the open map, **Export…** writes
PNG per level, a multi-page PDF package and JSON (GM and player versions).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PyQt6.QtCore import QObject, QStandardPaths, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QListWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from core.seeds import MAX_SEED_LENGTH, clean_seed, new_seed
from ui import theme as thememod
from ui.branding import ALIEN_NAME, APP_NAME

PACK_FOLDERS = ("100x100 Core", "100x50 Edge", "50x50 Corner", "100x100 End")


def user_data_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) or str(Path.home() / ".sceneboard")
    d = Path(base) / "geomorph"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_tiles_dir(*roots) -> str:
    """Folder that contains the pack's tile folders, searched under the given roots."""
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        if os.path.isdir(os.path.join(root, PACK_FOLDERS[0])):
            return root
        for dirpath, dirnames, _files in os.walk(root):
            if PACK_FOLDERS[0] in dirnames:
                return dirpath
            if dirpath.count(os.sep) - root.count(os.sep) >= 3:
                dirnames[:] = []
    return ""


def find_symbols_dir(*roots) -> str:
    """Folder of the Symbols pack (has 'Staterooms', 'Furniture, Consoles, & Equipment'...)."""
    for root in roots:
        if root and os.path.isdir(root):
            for dirpath, dirnames, _f in os.walk(root):
                if "Furniture, Consoles, & Equipment" in dirnames and "Machinery" in dirnames:
                    return dirpath
                if dirpath.count(os.sep) - root.count(os.sep) >= 3:
                    dirnames[:] = []
    return ""


def pil_to_pixmap(im) -> QPixmap:
    im = im.convert("RGBA")
    qi = QImage(im.tobytes("raw", "RGBA"), im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qi)


class _Job(QThread):
    """Runs one generation/render off the UI thread."""
    done = pyqtSignal(object, object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), None)
        except Exception as exc:                       # surfaced in the dialog, never lost
            import traceback
            self.done.emit(None, f"{exc}\n{traceback.format_exc()}")


def _help(text, colors):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color:{colors['muted']};")
    return label


class _BestOfDialog(QDialog):
    """Six candidate maps with their scores; click "Use this one" under the one you want."""

    def __init__(self, parent, candidates):
        super().__init__(parent)
        from PyQt6.QtWidgets import QGridLayout
        from geomorph import learning
        self.setWindowTitle("Best of 6 — pick a map")
        self.chosen = None
        grid = QGridLayout(self)
        for i, res in enumerate(candidates):
            card = QVBoxLayout()
            pic = QLabel()
            pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pm = pil_to_pixmap(res._thumb)
            pic.setPixmap(pm.scaled(340, 260, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            q = res.quality
            taste = learning.taste(res)
            text = f"{'★ ' if i == 0 else ''}Score {q['score']} · {q['label']}"
            if abs(taste) > 0.02:
                text += f" · your taste {'+' if taste > 0 else ''}{taste * 100:.0f}%"
            cap = QLabel(text)
            cap.setWordWrap(True)
            sub = QLabel(f"{len(q['dead_ends'])} dead ends, {len(q['unreachable'])} unreachable · seed {res.options.get('seed', '')}")
            sub.setStyleSheet("color: #8fa3ad;")
            btn = QPushButton("Use this one")
            btn.clicked.connect(lambda _c=False, k=i: self._pick(k))
            for w in (pic, cap, sub, btn):
                card.addWidget(w)
            grid.addLayout(card, i // 3, i % 3)

    def _pick(self, k):
        self.chosen = k
        self.accept()


class _ClickLabel(QLabel):
    """Preview label that reports where it was clicked."""
    clicked = pyqtSignal(int, int, int)          # x, y, button (1 left, 2 right)

    def mousePressEvent(self, e):
        if e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self.clicked.emit(int(e.position().x()), int(e.position().y()), int(e.button().value))
        super().mousePressEvent(e)


def _set_combo(cb, data):
    i = cb.findData(data)
    if i < 0 and isinstance(data, str):
        i = cb.findText(data)
    if i >= 0:
        cb.setCurrentIndex(i)


class GeomorphDialog(QDialog):
    def __init__(self, main, parent=None, sync=False):
        super().__init__(parent or main)
        from geomorph import archetype as archmod
        from geomorph import dressing, names, ship
        from geomorph.registry import Registry
        self.main = main
        self.sync = sync                    # tests run jobs inline
        self.theme_mode = getattr(main, "theme_mode", "dark")
        self.colors = thememod.theme_colors(self.theme_mode, getattr(main, "theme_accent", thememod.DEFAULT_ACCENT))
        product = ALIEN_NAME if self.theme_mode == "alien" else APP_NAME
        self.setWindowTitle(f"{product} — Geomorph Generator (ships & sites)")
        self.resize(1180, 820)
        self.settings = main.settings
        self.user_dir = user_data_dir()
        self.archmod, self.names, self.dressing, self.ship = archmod, names, dressing, ship
        ov = self.user_dir / "edge_overrides.json"
        self.registry = Registry.load(overrides=ov if ov.exists() else None)
        custom = self.user_dir / "custom_tiles.json"
        if custom.exists():
            from geomorph.registry import Tile
            for d in json.loads(custom.read_text(encoding="utf-8")).get("tiles", []):
                self.registry.add(Tile.from_json(d))
        self.archetypes = {}
        self.result = None
        self.images = None
        self.job = None
        self.level_index = 0
        self.candidates = []
        self.undo_stack = []                # [("snap", snapshot, label) | ("result", result, seed, label)]
        self.locked = set()                 # zone ids kept by every re-roll
        self.selected_zone = None
        self._applying = False
        self._live_pending = False
        self._scenario_hooks = []
        self._build()
        self._reload_archetypes()
        self._wire_live_preview()
        self._set_tiles_dir(self.settings.value("geomorph/tiles_dir", "", str) or self._autodetect())

    # ------------------------------------------------------------------
    def _autodetect(self):
        lib = getattr(getattr(self.main.library, "library", None), "root", "") or ""
        return find_tiles_dir(lib, getattr(self.main.project, "asset_store", ""))

    def _build(self):
        outer = QHBoxLayout(self)
        left = QVBoxLayout()
        right = QVBoxLayout()
        outer.addLayout(left, 0)
        outer.addLayout(right, 1)

        left.addWidget(_help("Builds a connected deck plan from the Geomorphs tiles: a starship, or a site "
                             "(colony, mine, lab, prison, station, wreck…). Same seed + options = same map.",
                             self.colors))
        box = QGroupBox("Tile pack")
        f = QFormLayout(box)
        row = QHBoxLayout()
        self.ed_tiles = QLineEdit()
        self.ed_tiles.setPlaceholderText("Folder with '100x100 Core', '100x50 Edge', … (optional, for the preview)")
        self.ed_tiles.editingFinished.connect(lambda: self._set_tiles_dir(self.ed_tiles.text()))
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse_tiles)
        row.addWidget(self.ed_tiles, 1)
        row.addWidget(b)
        f.addRow(row)
        self.lbl_pack = QLabel("")
        self.lbl_pack.setWordWrap(True)
        f.addRow(self.lbl_pack)
        left.addWidget(box)

        box = QGroupBox("Scenarios & presets")
        v = QVBoxLayout(box)
        self.cb_preset = QComboBox()
        v.addWidget(self.cb_preset)
        row = QHBoxLayout()
        b_apply = QPushButton("Apply")
        b_apply.clicked.connect(self._apply_preset)
        b_save = QPushButton("Save current…")
        b_save.clicked.connect(self._save_preset)
        b_del = QPushButton("Delete")
        b_del.clicked.connect(self._delete_preset)
        for wdg in (b_apply, b_save, b_del):
            row.addWidget(wdg)
        v.addLayout(row)
        self.lbl_preset = _help("", self.colors)
        v.addWidget(self.lbl_preset)
        left.addWidget(box)
        self._fill_presets()

        self.tabs = QTabWidget()
        left.addWidget(self.tabs)
        # ---- ship ----
        w = QWidget()
        f = QFormLayout(w)
        self.cb_ship_type = QComboBox()
        self.cb_ship_type.addItems(list(self.ship.SHIP_TYPES))
        self.sp_tonnage = QSpinBox()
        self.sp_tonnage.setRange(100, 20000)
        self.sp_tonnage.setSingleStep(100)
        self.sp_tonnage.setValue(1000)
        self.cb_ship_mode = QComboBox()
        for key, label in (("planned", "Planned — required rooms guaranteed"), ("random", "Random — tiles in any order"),
                           ("selective", "Selectively random — reject placements that make no sense"),
                           ("movie", "Movie set — only the connected action area")):
            self.cb_ship_mode.addItem(label, key)
        self.cb_orient = QComboBox()
        for key, label in (("N", "Bow up"), ("E", "Bow right"), ("S", "Bow down"), ("W", "Bow left")):
            self.cb_orient.addItem(label, key)
        self.ck_sym = QCheckBox("Port / starboard symmetry")
        self.ck_sym.setChecked(True)
        self.ck_fins = QCheckBox("Aerofin (wing) tiles")
        self.ck_fins.setChecked(True)
        f.addRow("Ship type", self.cb_ship_type)
        f.addRow("Target tonnage", self.sp_tonnage)
        f.addRow("Layout approach", self.cb_ship_mode)
        f.addRow("Orientation", self.cb_orient)
        f.addRow(self.ck_sym)
        f.addRow(self.ck_fins)
        # ship parts: every part can be left on Auto, chosen, or removed
        reg = self.registry
        self.cb_wing = QComboBox()
        self.cb_wing.addItem("Auto (matched to ship type)", None)
        self.cb_wing.addItem("No wings", "none")
        for port, _star in reg.wing_pairs():
            self.cb_wing.addItem(f"{port.number} {port.title.replace('Wing Port ', '')}"[:70], port.id)
        self.cb_nose = QComboBox()
        self.cb_nose.addItem("Auto", None)
        for st in reg.nose_styles("bridge"):
            self.cb_nose.addItem(st.replace("Bridge, ", ""), "style:" + st)
        self.cb_tail = QComboBox()
        self.cb_tail.addItem("Auto", None)
        for st in reg.nose_styles("engineering"):
            self.cb_tail.addItem(st.replace("Engineering, ", ""), "style:" + st)
        self.cb_trans = QComboBox()
        self.cb_trans.addItem("Auto", None)
        self.cb_trans.addItem("None (single-piece nose and tail)", "none")
        for t in sorted((t for t in reg.tiles.values() if t.type == "trans" and not t.mirror_of), key=lambda t: t.id):
            self.cb_trans.addItem(f"{t.id}  {t.title.replace('Nose transition ', '')}"[:70], t.id)
        self.ck_square = QCheckBox("Square shoulders (no corner pieces beside a lone nose/tail)")
        self.ck_square.setChecked(True)
        f.addRow(self.ck_square)
        f.addRow("Wings", self.cb_wing)
        f.addRow("Nose (bridge)", self.cb_nose)
        f.addRow("Tail (engineering)", self.cb_tail)
        f.addRow("Nose/tail transition", self.cb_trans)
        self.count_spins = {}
        for tag, label in (("escape", "Escape pods"), ("weapons", "Guns / barbettes"), ("hangar", "Launch bays / hangars"),
                           ("scoop", "Fuel scoops"), ("vehicle_bay", "Vehicle bays"), ("cargo", "Cargo holds"),
                           ("medical", "Medical"), ("lab", "Labs"), ("staterooms", "Staterooms"),
                           ("recreation", "Lounges / recreation"), ("hydroponics", "Hydroponics")):
            sp = QSpinBox()
            sp.setRange(-1, 20)
            sp.setValue(-1)
            sp.setSpecialValueText("Auto")
            sp.setToolTip("Auto lets the ship type decide. 0 leaves this part out. A number asks for that many "
                          "rooms. Escape pods, guns, launch bays and fuel scoops always come in mirrored pairs.")
            self.count_spins[tag] = sp
            f.addRow(label, sp)
        self.craft_checks = {}
        from geomorph.overlays import CATEGORIES
        for key, (label, _rx) in CATEGORIES.items():
            c = QCheckBox("Show: " + label)
            self.craft_checks[key] = c
            f.addRow(c)
        self.tabs.addTab(w, "Ship")
        # ---- site ----
        w = QWidget()
        f = QFormLayout(w)
        self.cb_arch = QComboBox()
        self.cb_arch.currentIndexChanged.connect(self._arch_changed)
        b = QPushButton("Edit / add archetypes…")
        b.clicked.connect(self._edit_archetypes)
        self.cb_scale = QComboBox()
        for s, label in (("small", "Small"), ("medium", "Medium"), ("large", "Large")):
            self.cb_scale.addItem(label, s)
        self.cb_scale.setCurrentIndex(1)
        self.cb_env = QComboBox()
        self.cb_site_mode = QComboBox()
        for key, label in (("planned", "Planned — required zones guaranteed"), ("random", "Random"),
                           ("selective", "Selectively random"), ("movie", "Movie set — action area only")):
            self.cb_site_mode.addItem(label, key)
        f.addRow("Archetype", self.cb_arch)
        f.addRow(b)
        f.addRow("Scale", self.cb_scale)
        f.addRow("Environment", self.cb_env)
        f.addRow("Layout approach", self.cb_site_mode)
        self.lbl_arch = _help("", self.colors)
        f.addRow(self.lbl_arch)
        self.tabs.addTab(w, "Site")

        box = QGroupBox("Look and feel")
        f = QFormLayout(box)
        self.cb_cond = QComboBox()
        self.cb_cond.addItem("Archetype default", "")
        for c in self.dressing.conditions():
            self.cb_cond.addItem(c, c)
        self.ck_mixed = QCheckBox("Mix conditions between zones")
        self.cb_theme = QComboBox()
        for k, label in self.names.theme_labels().items():
            self.cb_theme.addItem(label, k)
        self.sp_pec = QSpinBox()
        self.sp_pec.setRange(0, 6)
        self.sp_pec.setValue(2)
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("Leave empty for a generated name")
        f.addRow("Name", self.ed_name)
        f.addRow("Condition", self.cb_cond)
        f.addRow(self.ck_mixed)
        f.addRow("Theme", self.cb_theme)
        f.addRow("Quirks & perks", self.sp_pec)
        self.sl_group = QSlider(Qt.Orientation.Horizontal)
        self.sl_group.setRange(0, 100)
        self.sl_group.setValue(60)
        self.sl_group.setToolTip("Smart grouping: related rooms are generated close together (medical with labs and the "
                                 "morgue, cargo with loading bays, quarters with freshers, canteens with the galley...). "
                                 "0 turns it off. The rules are in geomorph/data/affinity.json.")
        f.addRow("Room grouping", self.sl_group)
        left.addWidget(box)

        box = QGroupBox("Symbols (furniture, machinery, cargo…)")
        v = QVBoxLayout(box)
        self.ck_decor = QCheckBox("Furnish open rooms with symbols at real size")
        self.ck_decor.setToolTip("Uses the Symbols pack. Items match the room's function, keep to the walls and never "
                                 "block corridors. Needs the Symbols ZIP in the library to place on the canvas.")
        v.addWidget(self.ck_decor)
        self.ck_outdoor = QCheckBox("Outdoor features on sites: trees, bushes, boulders, benches, fields")
        self.ck_outdoor.setChecked(True)
        self.ck_outdoor.setToolTip("Only where they make sense: trees need a breathable atmosphere, a hostile world gets rocks "
                                   "and scrub, an airless one only rocks; a garrison stays bare, a farm gets fields, and "
                                   "ships and stations get nothing.")
        v.addWidget(self.ck_outdoor)
        row = QHBoxLayout()
        row.addWidget(QLabel("Amount"))
        self.sl_decor = QSlider(Qt.Orientation.Horizontal)
        self.sl_decor.setRange(10, 100)
        self.sl_decor.setValue(50)
        row.addWidget(self.sl_decor)
        v.addLayout(row)
        self.cb_incident = QComboBox()
        for key, label in (("none", "Tidy — as if nothing happened"), ("struggle", "Signs of a struggle"),
                           ("ransacked", "Ransacked — things missing and overturned"),
                           ("overrun", "Overrun — barricades, burns, resin, drag marks")):
            self.cb_incident.addItem(label, key)
        self.cb_where = QComboBox()
        for key, label in (("all", "In every room"), ("overlay", "Only lockdown / quarantine / threat zones"),
                           ("random", "In about a third of the rooms"),
                           ("spread", "Spreading from a starting room (nest, breach…)")):
            self.cb_where.addItem(label, key)
        self.cb_origin = QComboBox()
        for key, label in (("random", "A random room"), ("entrance", "The entrance"), ("medical", "A medical room"),
                           ("lab", "A laboratory"), ("cargo", "A cargo hold"), ("engineering", "Engineering"),
                           ("staterooms", "Crew quarters")):
            self.cb_origin.addItem(label, key)
        self.cb_origin.setToolTip("Where it started. The worst damage is here (resin and burns all over); it thins out "
                                  "with every door away, barricades stand on the doors facing it, and drag marks lead toward it.")
        self.cb_reach = QComboBox()
        for key, label in (("short", "Short — about 2 doors"), ("medium", "Medium — about 3 doors"), ("far", "Far — about 5 doors")):
            self.cb_reach.addItem(label, key)
        self.cb_reach.setCurrentIndex(1)
        f2 = QFormLayout()
        f2.addRow("Something bad happened", self.cb_incident)
        f2.addRow("Where", self.cb_where)
        f2.addRow("Starting from", self.cb_origin)
        f2.addRow("How far it spreads", self.cb_reach)
        v.addLayout(f2)
        left.addWidget(box)

        box = QGroupBox("State overlays (seeded)")
        v = QVBoxLayout(box)
        self.overlay_checks = {}
        for key, label in (("lockdown", "Lockdown"), ("power_failure", "Power failure"), ("breach", "Breach / decompression"),
                           ("quarantine", "Quarantine"), ("salvage", "Salvage-stripped"), ("battle", "Battle damage"),
                           ("threat", "Unknown-threat markers (GM only)"), ("secrets", "Secrets (GM only)")):
            c = QCheckBox(label)
            self.overlay_checks[key] = c
            v.addWidget(c)
        row = QHBoxLayout()
        row.addWidget(QLabel("Intensity"))
        self.sl_int = QSlider(Qt.Orientation.Horizontal)
        self.sl_int.setRange(0, 100)
        self.sl_int.setValue(50)
        row.addWidget(self.sl_int)
        v.addLayout(row)
        left.addWidget(box)

        from geomorph import atmosphere as _atmo
        self.light_color, self.fixture_color = _atmo.DEFAULT_LIGHT, _atmo.DEFAULT_FIXTURE
        self.my_light_color, self.my_fixture_color = "#ffd27a", "#fff1c9"
        box = QGroupBox("Lights")
        v = QVBoxLayout(box)
        row = QHBoxLayout()
        row.addWidget(QLabel("Emergency lamps"))
        self.btn_light_color = self._color_button("light_color", "Colour of the emergency light")
        self.btn_fixture_color = self._color_button("fixture_color", "Colour of the emergency fixtures")
        b_reset = QPushButton("Red")
        b_reset.setToolTip("Back to the standard red emergency lamps.")
        b_reset.clicked.connect(self._reset_lamp_colors)
        for wdg in (QLabel("light"), self.btn_light_color, QLabel("fixture"), self.btn_fixture_color, b_reset):
            row.addWidget(wdg)
        row.addStretch(1)
        v.addLayout(row)
        row = QHBoxLayout()
        self.btn_place_light = QPushButton("Place lights")
        self.btn_place_light.setCheckable(True)
        self.btn_place_light.setToolTip("Click the map preview to add a light of your own: wall lights snap to the nearest wall, "
                                        "ceiling lights go where you click. Right-click a light to remove it.")
        self.btn_place_light.toggled.connect(self._place_mode_changed)
        self.cb_light_kind = QComboBox()
        self.cb_light_kind.addItem("Wall light", "wall")
        self.cb_light_kind.addItem("Ceiling light", "ceiling")
        self.sp_light_radius = QSpinBox()
        self.sp_light_radius.setRange(1, 12)
        self.sp_light_radius.setValue(4)
        self.sp_light_radius.setSuffix(" sq")
        self.sp_light_radius.setToolTip("How far the light reaches, in grid squares.")
        self.sp_light_strength = QSpinBox()
        self.sp_light_strength.setRange(10, 100)
        self.sp_light_strength.setValue(100)
        self.sp_light_strength.setSuffix(" %")
        self.sp_light_strength.setToolTip("How bright the light is.")
        for wdg in (self.btn_place_light, self.cb_light_kind, QLabel("reach"), self.sp_light_radius,
                    QLabel("bright"), self.sp_light_strength):
            row.addWidget(wdg)
        v.addLayout(row)
        row = QHBoxLayout()
        self.btn_my_light_color = self._color_button("my_light_color", "Colour of the lights you place")
        self.btn_my_fixture_color = self._color_button("my_fixture_color", "Colour of the fixtures you place")
        self.btn_clear_lights = QPushButton("Clear my lights")
        self.btn_clear_lights.clicked.connect(self._clear_lights)
        for wdg in (QLabel("My lights: light"), self.btn_my_light_color, QLabel("fixture"), self.btn_my_fixture_color,
                    self.btn_clear_lights):
            row.addWidget(wdg)
        row.addStretch(1)
        v.addLayout(row)
        left.addWidget(box)

        box = QGroupBox("Seed")
        row = QHBoxLayout(box)
        self.ed_seed = QLineEdit(new_seed())
        self.ed_seed.setMaxLength(MAX_SEED_LENGTH)
        b1 = QPushButton("New")
        b1.clicked.connect(lambda: self.ed_seed.setText(new_seed()))
        row.addWidget(self.ed_seed, 1)
        row.addWidget(b1)
        left.addWidget(box)
        left.addStretch(1)

        # ---- right: preview, text, reports ----
        top = QHBoxLayout()
        self.btn_gen = QPushButton("Generate")
        self.btn_gen.clicked.connect(self.generate)
        self.btn_regen = QPushButton("New seed + Generate")
        self.btn_regen.clicked.connect(self._regenerate)
        self.cb_level = QComboBox()
        self.cb_level.currentIndexChanged.connect(self._level_changed)
        self.ck_gm = QCheckBox("GM view")
        self.ck_gm.setChecked(True)
        self.ck_gm.toggled.connect(lambda _c: self._show_level())
        self.ck_show_decor = QCheckBox("Furniture & outdoors")
        self.ck_show_decor.setChecked(True)
        self.ck_show_decor.setToolTip("Show the symbols and outdoor features in the preview, or the bare layout.")
        self.ck_show_decor.toggled.connect(lambda _c: self._show_level())
        self.ck_atmo = QCheckBox("Atmosphere")
        self.ck_atmo.setChecked(True)
        self.ck_atmo.setToolTip("Dim rooms with the power out, red emergency lamps over their doors, a red shutter "
                                "across locked-down doors (GM view only) and hazard borders on quarantined rooms.")
        self.ck_atmo.toggled.connect(lambda _c: self._show_level())
        self.btn_best = QPushButton("Best of 6")
        self.btn_best.setToolTip("Make 6 maps from the current settings, score them (and your taste), and pick one.")
        self.btn_best.clicked.connect(self._best_of)
        self.ck_live = QCheckBox("Live preview")
        self.ck_live.setToolTip("Regenerate automatically a moment after any option changes.")
        for wdg in (self.btn_gen, self.btn_regen, self.btn_best, self.ck_live, QLabel("Level"), self.cb_level, self.ck_gm, self.ck_show_decor, self.ck_atmo):
            top.addWidget(wdg)
        top.addStretch(1)
        right.addLayout(top)
        self.preview = _ClickLabel("Press Generate.")
        self.preview.clicked.connect(self._preview_clicked)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(400, 300)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.preview)
        right.addWidget(sc, 3)
        self.info_tabs = QTabWidget()
        self.txt_main = QPlainTextEdit()
        self.txt_key = QPlainTextEdit()
        self.txt_report = QPlainTextEdit()
        for t, name in ((self.txt_main, "Description & hooks"), (self.txt_key, "Key"), (self.txt_report, "Checks & gaps")):
            t.setReadOnly(True)
            self.info_tabs.addTab(t, name)
        # editable per-room notes (what the GM sees / what players get)
        w = QWidget()
        h = QHBoxLayout(w)
        self.lst_notes = QListWidget()
        self.lst_notes.setMaximumWidth(260)
        self.lst_notes.currentRowChanged.connect(self._note_selected)
        h.addWidget(self.lst_notes)
        col = QVBoxLayout()
        col.addWidget(QLabel("GM note (never in the player version)"))
        self.ed_note_gm = QPlainTextEdit()
        self.ed_note_gm.textChanged.connect(lambda: self._note_edited("text", self.ed_note_gm))
        col.addWidget(self.ed_note_gm)
        col.addWidget(QLabel("Player note (shown in the player key)"))
        self.ed_note_player = QPlainTextEdit()
        self.ed_note_player.textChanged.connect(lambda: self._note_edited("player", self.ed_note_player))
        col.addWidget(self.ed_note_player)
        h.addLayout(col, 1)
        self.info_tabs.addTab(w, "Room notes")
        right.addWidget(self.info_tabs, 2)
        row = QHBoxLayout()
        self.cb_zone = QComboBox()
        self.btn_reroll = QPushButton("Reroll this zone")
        self.btn_reroll.clicked.connect(self._reroll_zone)
        self.btn_place = QPushButton("Place on canvas")
        self.btn_place.clicked.connect(self.place_on_canvas)
        self.btn_export = QPushButton("Export…")
        self.btn_export.clicked.connect(self.export)
        self.btn_save = QPushButton("Save layout…")
        self.btn_save.clicked.connect(self._save_layout)
        self.btn_load = QPushButton("Load layout…")
        self.btn_load.clicked.connect(self._load_layout)
        b_edge = QPushButton("Edge editor…")
        b_edge.clicked.connect(self._edge_editor)
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.accept)
        self.cb_zone.currentIndexChanged.connect(self._zone_changed)
        self.ck_lock = QCheckBox("Lock")
        self.ck_lock.setToolTip("Locked tiles stay put when you re-roll the level or everything else.")
        self.ck_lock.toggled.connect(self._lock_toggled)
        self.btn_reroll_level = QPushButton("Re-roll level")
        self.btn_reroll_level.setToolTip("New tiles for every room on this level except locked ones and stair/lift cores.")
        self.btn_reroll_level.clicked.connect(self._reroll_level)
        self.btn_reroll_all = QPushButton("Re-roll all unlocked")
        self.btn_reroll_all.clicked.connect(self._reroll_all)
        for wdg in (self.cb_zone, self.ck_lock, self.btn_reroll, self.btn_reroll_level, self.btn_reroll_all):
            row.addWidget(wdg)
        row2 = QHBoxLayout()
        right.addLayout(row)
        row = row2
        for wdg in (self.btn_place, self.btn_export, self.btn_save, self.btn_load, b_edge, b_close):
            row.addWidget(wdg)
        right.addLayout(row)
        self.box_check = QGroupBox("Map check")
        vc = QVBoxLayout(self.box_check)
        hc = QHBoxLayout()
        vc.addLayout(hc)
        self.lbl_score = QLabel("–")
        self.lbl_score.setMinimumWidth(64)
        self.lbl_score.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_score.setStyleSheet("font-size: 26px; font-weight: bold;")
        self.lbl_quality = QLabel("")
        self.lbl_quality.setWordWrap(True)
        self.lbl_changes = QLabel("")
        self.lbl_changes.setWordWrap(True)
        self.lbl_changes.setStyleSheet("color: #8fd3ff;")
        col = QVBoxLayout()
        col.addWidget(self.lbl_quality)
        col.addWidget(self.lbl_changes)
        self.btn_undo = QPushButton("Undo")
        self.btn_undo.setToolTip("Go back to how the map was before the last re-roll or new map.")
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self._undo)
        hc.addWidget(self.lbl_score)
        hc.addLayout(col, 1)
        hc.addWidget(self.btn_undo)
        lr = QHBoxLayout()
        self.btn_like = QPushButton("👍 Like this map")
        self.btn_like.setToolTip("The tiles in this map will be picked a little more often from now on.")
        self.btn_like.clicked.connect(lambda: self._rate(+1))
        self.btn_dislike = QPushButton("👎 Not for me")
        self.btn_dislike.setToolTip("The tiles in this map will be picked a little less often from now on.")
        self.btn_dislike.clicked.connect(lambda: self._rate(-1))
        self.ck_learn = QCheckBox("Learn from my ratings")
        self.ck_learn.setChecked(True)
        self.ck_learn.setToolTip("Tiles from maps you like are used more, tiles from maps you do not like less. Maps you "
                                 "place or export also count a little, by their score. Taste never bends the rules.")
        self.btn_forget = QPushButton("Reset")
        self.btn_forget.setToolTip("Forget everything learned so far.")
        self.btn_forget.clicked.connect(self._forget)
        self.lbl_learn = QLabel("")
        self.lbl_learn.setStyleSheet("color: #8fd3ff;")
        for wdg in (self.btn_like, self.btn_dislike, self.ck_learn, self.btn_forget):
            lr.addWidget(wdg)
        lr.addWidget(self.lbl_learn, 1)
        vc.addLayout(lr)
        right.addWidget(self.box_check)
        self.lbl_gaps = QLabel("")
        self.lbl_gaps.setWordWrap(True)
        self.lbl_gaps.setStyleSheet("color: #f0b040;")
        self.lbl_gaps.hide()
        right.addWidget(self.lbl_gaps)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        right.addWidget(self.lbl_status)
        credit = _help("Tiles: Starship Geomorphs 2.0 by Robert Pearce (Pearce Design Studio, LLC), CC BY-NC 4.0; "
                       "PNG renderings by Eric Smith / RPG Mobius. Non-commercial fan tool; Traveller is a trademark of "
                       "Far Future Enterprises.", self.colors)
        right.addWidget(credit)
        self._enable(False)

    def _enable(self, has):
        for b in (self.btn_place, self.btn_export, self.btn_save, self.btn_reroll, self.ck_lock,
                  self.btn_reroll_level, self.btn_reroll_all, self.btn_like, self.btn_dislike,
                  self.btn_place_light, self.btn_clear_lights):
            b.setEnabled(has)

    # ---- presets & scenarios ------------------------------------------
    def _presets_file(self):
        return self.user_dir / "presets.json"

    def _user_presets(self) -> dict:
        try:
            return json.loads(self._presets_file().read_text(encoding="utf-8")).get("presets", {})
        except (OSError, ValueError):
            return {}

    def _scenarios(self) -> list:
        try:
            return json.loads((Path(__file__).resolve().parent.parent / "geomorph" / "data" /
                               "scenarios.json").read_text(encoding="utf-8")).get("scenarios", [])
        except (OSError, ValueError):
            return []

    def _fill_presets(self, select=None):
        self.cb_preset.blockSignals(True)
        self.cb_preset.clear()
        self.cb_preset.addItem("Choose a scenario or preset…", None)
        for sc in self._scenarios():
            self.cb_preset.addItem("Scenario: " + sc["name"], "scenario:" + sc["name"])
        for name in sorted(self._user_presets()):
            self.cb_preset.addItem("My preset: " + name, "user:" + name)
        if select:
            _set_combo(self.cb_preset, select)
        self.cb_preset.blockSignals(False)

    def _apply_preset(self):
        key = self.cb_preset.currentData()
        if not key:
            return
        kind, _, name = key.partition(":")
        self._scenario_hooks = []
        if kind == "scenario":
            sc = next((x for x in self._scenarios() if x["name"] == name), None)
            if sc is None:
                return
            o = dict(sc["options"])
            o["kind"] = sc["tab"]
            self._scenario_hooks = list(sc.get("hooks", []))
            self.lbl_preset.setText(sc.get("description", ""))
        else:
            o = self._user_presets().get(name)
            if o is None:
                return
            self.lbl_preset.setText("")
        self.apply_options(o)
        self.generate()

    def _save_preset(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save preset", "Name for these settings:")
        name = name.strip()
        if not ok or not name:
            return
        o = self.options()
        o.pop("seed", None)
        presets = self._user_presets()
        presets[name] = o
        try:
            self.user_dir.mkdir(parents=True, exist_ok=True)
            self._presets_file().write_text(json.dumps({"presets": presets}, indent=1), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Geomorph generator", f"Could not save the preset: {exc}")
            return
        self._fill_presets("user:" + name)
        self.lbl_status.setText(f"Preset '{name}' saved.")

    def _delete_preset(self):
        key = self.cb_preset.currentData()
        if not key or not key.startswith("user:"):
            self.lbl_status.setText("Only your own presets can be deleted.")
            return
        presets = self._user_presets()
        presets.pop(key[5:], None)
        try:
            self._presets_file().write_text(json.dumps({"presets": presets}, indent=1), encoding="utf-8")
        except OSError:
            pass
        self._fill_presets()

    def apply_options(self, o: dict):
        """Set every control from an options dict (the inverse of :meth:`options`)."""
        self._applying = True
        try:
            self.tabs.setCurrentIndex(0 if o.get("kind") == "ship" else 1)
            for key, cb in (("theme", self.cb_theme), ("condition", self.cb_cond), ("mode", None)):
                if cb is not None and key in o:
                    _set_combo(cb, o[key] or "")
            if "name" in o:
                self.ed_name.setText(o["name"])
            a = o.get("atmosphere") or {}
            if a.get("light"):
                self._set_color("light_color", a["light"])
            if a.get("fixture"):
                self._set_color("fixture_color", a["fixture"])
            if "mixed_conditions" in o:
                self.ck_mixed.setChecked(bool(o["mixed_conditions"]))
            if "peculiarities" in o:
                self.sp_pec.setValue(int(o["peculiarities"]))
            if "grouping" in o:
                self.sl_group.setValue(int(round(o["grouping"] * 100)))
            if "intensity" in o:
                self.sl_int.setValue(int(round(o["intensity"] * 100)))
            for k, c in self.overlay_checks.items():
                c.setChecked(k in (o.get("overlays") or []))
            d = o.get("decor") or {}
            self.ck_decor.setChecked(bool(d.get("enabled")))
            self.ck_outdoor.setChecked(bool(d.get("exterior", d.get("enabled", False))))
            if "density" in d:
                self.sl_decor.setValue(int(round(d["density"] * 100)))
            _set_combo(self.cb_incident, d.get("incident", "none"))
            _set_combo(self.cb_where, d.get("where", "all"))
            _set_combo(self.cb_origin, d.get("origin", "random"))
            _set_combo(self.cb_reach, d.get("reach", "medium"))
            if o.get("kind") == "ship":
                if "ship_type" in o:
                    self.cb_ship_type.setCurrentText(o["ship_type"])
                if "tonnage" in o:
                    self.sp_tonnage.setValue(int(o["tonnage"]))
                _set_combo(self.cb_ship_mode, o.get("mode", "planned"))
                _set_combo(self.cb_orient, o.get("orientation", "N"))
                self.ck_sym.setChecked(bool(o.get("symmetric", True)))
                self.ck_fins.setChecked(bool(o.get("fins", True)))
                parts = o.get("parts") or {}
                for key, cb in (("wing", self.cb_wing), ("nose", self.cb_nose), ("tail", self.cb_tail),
                                ("transition", self.cb_trans)):
                    _set_combo(cb, parts.get(key))
                self.ck_square.setChecked(bool(parts.get("square_shoulders", True)))
                for t, sp in self.count_spins.items():
                    sp.setValue(int((parts.get("counts") or {}).get(t, -1)))
                for k, c in self.craft_checks.items():
                    c.setChecked(k in (o.get("craft") or []))
            else:
                _set_combo(self.cb_arch, o.get("archetype"))
                _set_combo(self.cb_scale, o.get("scale", "medium"))
                self._arch_changed()
                _set_combo(self.cb_env, o.get("environment"))
                _set_combo(self.cb_site_mode, o.get("mode", "planned"))
        finally:
            self._applying = False

    # ---- live preview ---------------------------------------------------
    def _wire_live_preview(self):
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(600)
        self._live_timer.timeout.connect(self._live_fire)
        skip = {self.cb_level, self.ck_gm, self.ck_show_decor, self.ck_atmo, self.ck_learn, self.btn_place_light, self.cb_light_kind, self.sp_light_radius, self.sp_light_strength, self.cb_zone, self.ed_tiles, self.cb_preset, self.ck_live, self.ck_lock}
        for w in self.findChildren(QWidget):
            if w in skip or w.parent() is None:
                continue
            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self._option_changed)
            elif isinstance(w, QCheckBox):
                w.toggled.connect(self._option_changed)
            elif isinstance(w, QAbstractSpinBox):
                w.valueChanged.connect(self._option_changed)
            elif isinstance(w, QSlider):
                w.valueChanged.connect(self._option_changed)
            elif isinstance(w, QLineEdit) and w is not self.ed_tiles:
                w.textChanged.connect(self._option_changed)

    def _option_changed(self, *_a):
        if self.ck_live.isChecked() and not self._applying and self.registry is not None:
            self._live_timer.start()

    def _live_fire(self):
        if not self.ck_live.isChecked():
            return
        if self.job is not None and self.job.isRunning():
            self._live_pending = True
            return
        self.generate()

    # ---- selecting, locking, re-rolling ------------------------------------
    def _color_button(self, attr, tip):
        b = QPushButton("")
        b.setFixedSize(46, 22)
        b.setToolTip(tip)
        b.clicked.connect(lambda _c=False, a=attr, btn=b: self._pick_color(a, btn))
        self._paint_swatch(b, getattr(self, attr))
        return b

    @staticmethod
    def _paint_swatch(btn, color):
        btn.setStyleSheet(f"background-color: {color}; border: 1px solid #8fa3ad;")

    def _pick_color(self, attr, btn):
        from ui.color_picker import choose_color
        color = choose_color(getattr(self, attr), self, None, "Choose a colour")
        if color is not None and color.isValid():
            self._set_color(attr, color.name())

    def _set_color(self, attr, value):
        setattr(self, attr, value)
        btn = {"light_color": self.btn_light_color, "fixture_color": self.btn_fixture_color,
               "my_light_color": self.btn_my_light_color, "my_fixture_color": self.btn_my_fixture_color}[attr]
        self._paint_swatch(btn, value)
        if attr in ("light_color", "fixture_color") and self.result is not None:
            self.result.options["atmosphere"] = {"light": self.light_color, "fixture": self.fixture_color}
            self._redraw()

    def _reset_lamp_colors(self):
        from geomorph import atmosphere as A
        self._set_color("light_color", A.DEFAULT_LIGHT)
        self._set_color("fixture_color", A.DEFAULT_FIXTURE)

    def _redraw(self):
        """Lights changed: drop the cached previews and draw again."""
        if self.result is not None:
            self.result._previews = {}
            self._show_level()

    def _place_mode_changed(self, on):
        self.lbl_status.setText("Click the map to place a light (right-click a light to remove it)." if on else "")

    def _clear_lights(self):
        from geomorph import pipeline
        res = self.result
        if res is None or not getattr(res, "lights", None):
            return
        self._push_undo(("snap", pipeline.snapshot(res), "Clear lights"))
        n = len(res.lights)
        res.lights = []
        self._redraw()
        self.lbl_changes.setText(f"Removed {n} light(s).")

    def _light_click(self, gx, gy, button):
        """Place mode: left-click adds a light where there is a wall (or ceiling), right-click removes the nearest one."""
        from geomorph import atmosphere, pipeline
        res = self.result
        g = res.grids[self.level_index]
        if button == 2:
            mine = [L for L in getattr(res, "lights", []) if L.get("level") == g.index]
            near = min(mine, key=lambda L: (L["x"] - gx) ** 2 + (L["y"] - gy) ** 2, default=None)
            if near is None or (near["x"] - gx) ** 2 + (near["y"] - gy) ** 2 > 2.5 ** 2:
                return
            self._push_undo(("snap", pipeline.snapshot(res), "Remove light"))
            res.lights.remove(near)
            self._redraw()
            self.lbl_changes.setText(f"Removed a light ({len(res.lights)} placed).")
            return
        light = atmosphere.snap_light(res, g, gx, gy, self.images, self.cb_light_kind.currentData(),
                                      self.sp_light_radius.value(), self.sp_light_strength.value() / 100.0,
                                      self.my_light_color, self.my_fixture_color)
        if light is None:
            self.lbl_status.setText("No wall there: click beside a wall (or use a ceiling light inside a building).")
            return
        self._push_undo(("snap", pipeline.snapshot(res), "Place light"))
        if not hasattr(res, "lights") or res.lights is None:
            res.lights = []
        res.lights.append(light)
        self._redraw()
        self.lbl_changes.setText(f"Placed a {light['kind']} light ({len(res.lights)} placed).")

    def _preview_clicked(self, px, py, button=1):
        res = self.result
        if res is None:
            return
        from geomorph import render
        x0, y0, _x1, _y1 = render.shared_bounds(res)
        pps, head = 8, 3 * 8
        gx, gy = px / pps + x0, (py - head) / pps + y0
        if self.btn_place_light.isChecked():
            self._light_click(gx, gy, button)
            return
        for p in res.grids[self.level_index].placed:
            if p.zone and p.x <= gx < p.x + p.w and p.y <= gy < p.y + p.h:
                _set_combo(self.cb_zone, p.zone)
                return

    def _fill_notes(self):
        self.lst_notes.blockSignals(True)
        self.lst_notes.clear()
        for e in self.result.key:
            self.lst_notes.addItem(f"{e['n']}. {e['title']}")
        self.lst_notes.blockSignals(False)
        self.ed_note_gm.clear()
        self.ed_note_player.clear()

    def _note_selected(self, row):
        key = self.result.key if self.result is not None else []
        if not 0 <= row < len(key):
            return
        e = key[row]
        for ed, field in ((self.ed_note_gm, "text"), (self.ed_note_player, "player")):
            ed.blockSignals(True)
            ed.setPlainText(e.get(field, ""))
            ed.blockSignals(False)

    def _note_edited(self, field, editor):
        row = self.lst_notes.currentRow()
        if self.result is not None and 0 <= row < len(self.result.key):
            self.result.key[row][field] = editor.toPlainText()
            if field == "text":
                self._rebuild_key_text()

    def _rebuild_key_text(self):
        res = self.result
        self.txt_key.setPlainText("\n".join(f"{e['n']}. {e['title']} (level {e['level'] + 1}) — {e.get('text', '')}"
                                            for e in res.key))

    def _zone_changed(self, *_a):
        self.selected_zone = self.cb_zone.currentData()
        if self.result is not None:
            for i, e in enumerate(self.result.key):
                if e.get("zone") == self.selected_zone and self.selected_zone:
                    self.lst_notes.setCurrentRow(i)
                    break
        self.ck_lock.blockSignals(True)
        self.ck_lock.setChecked(self.selected_zone in self.locked)
        self.ck_lock.blockSignals(False)
        self._show_level()

    def _lock_toggled(self, on):
        zid = self.cb_zone.currentData()
        if not zid:
            return
        (self.locked.add if on else self.locked.discard)(zid)
        self._show_level()

    def _reroll(self, label, work):
        """Run a re-roll with an undo snapshot, redo the furniture and key for what changed, and say what changed."""
        from geomorph import pipeline, quality
        res = self.result
        if res is None:
            return
        before = pipeline.snapshot(res)
        n = work(res)
        pipeline.refresh_dressing(res)
        after = pipeline.snapshot(res)
        changes = pipeline.tile_changes(before, after, res)
        dropped = pipeline.drop_lights_on_changed(res, before, after)
        self._push_undo(("snap", before, label))
        self._prerender(res)
        self._update_quality()
        self._show_level()
        self._fill_text()
        shown = ", ".join(f"{room}" for _lvl, room, _a, _b in changes[:4]) + (f" +{len(changes) - 4} more" if len(changes) > 4 else "")
        levels = sorted({lvl for lvl, *_ in changes})
        where = f" on {levels[0]}" if len(levels) == 1 else f" on {len(levels)} levels" if levels else ""
        head = f"{len(changes)} tile(s) changed{where}" + (f" ({shown})" if shown else "")
        diff = quality.compare(before["quality"], res.quality)
        self.lbl_changes.setText(f"{label}: {head}. {diff}." + (f" {dropped} of your lights were on replaced tiles and were removed."
                                                                 if dropped else ""))
        self.lbl_status.setText(f"{label}. {len(changes)} tile(s) changed; locked tiles kept." if changes else
                                f"{label}: 0 tile(s) changed, nothing else fits here.")

    def _push_undo(self, entry):
        self.undo_stack.append(entry)
        del self.undo_stack[:-20]
        self.btn_undo.setEnabled(True)

    def _undo(self):
        from geomorph import pipeline
        if not self.undo_stack:
            return
        entry = self.undo_stack.pop()
        self.btn_undo.setEnabled(bool(self.undo_stack))
        if entry[0] == "snap":
            _kind, snap, label = entry
            pipeline.restore(self.result, snap)
            self._prerender(self.result)
            self._update_quality()
            self._show_level()
            self._fill_text()
            self.lbl_changes.setText(f"Undid: {label}.")
        else:
            _kind, old, seed, label = entry
            self._applying = True
            try:
                self.ed_seed.setText(seed)
            finally:
                self._applying = False
            self._generated(old, None, push=False)
            self.lbl_changes.setText(f"Undid: {label}.")

    def _reroll_level(self):
        from geomorph import pipeline
        import random
        lvl = self.level_index
        self._reroll("Re-roll level", lambda res: pipeline.reroll_level(res, lvl, self.locked, seed=random.random()))

    def _reroll_all(self):
        from geomorph import pipeline
        import random
        self._reroll("Re-roll all unlocked", lambda res: sum(
            pipeline.reroll_level(res, g.index, self.locked, seed=random.random()) for g in res.grids))

    def _best_of(self, n=6):
        """Make ``n`` candidates from the current settings, rank them, and let the user pick one."""
        from geomorph import pipeline, render
        self._apply_learning()
        opts = self.options()
        reg, arch, images = self.registry, self.archetypes, self.images

        def work():
            cands = pipeline.generate_many(reg, opts, n, dict(arch, _problems=[]))
            for res in cands:
                res._thumb = render.render_level(res, 0, images, pps=4, gm=True)
            return cands

        def done(cands, err):
            if err:
                self.lbl_status.setText("Best of 6 failed: " + err.splitlines()[0])
                return
            self.candidates = cands
            if self.sync:                                   # tests: take the top-ranked one
                self._take_candidate(0)
                return
            dlg = _BestOfDialog(self, cands)
            if dlg.exec() and dlg.chosen is not None:
                self._take_candidate(dlg.chosen)
        self._run(work, done)

    def _take_candidate(self, index):
        from geomorph import learning
        res = self.candidates[index]
        self._applying = True
        try:
            self.ed_seed.setText(str((res.options or {}).get("seed", "")))
        finally:
            self._applying = False
        self._prerender(res)
        self._generated(res, None)
        self.lbl_changes.setText(f"Picked candidate {index + 1} of {len(self.candidates)} "
                                 f"(seed {(res.options or {}).get('seed', '')}).")

    def _update_quality(self):
        from geomorph import quality
        res = self.result
        if res is None:
            return
        q = res.quality = quality.assess(res)
        color = "#5fd38d" if q["score"] >= 85 else "#f0b040" if q["score"] >= 65 else "#ff6b6b"
        self.lbl_score.setText(str(q["score"]))
        self.lbl_score.setStyleSheet(f"font-size: 26px; font-weight: bold; color: {color};")
        lines = [quality.summary(q)]
        if q["unreachable"]:
            lines.append("Unreachable: " + ", ".join(q["unreachable"][:5]) + (" …" if len(q["unreachable"]) > 5 else ""))
        if q["dead_ends"]:
            lines.append("Dead ends (corridors or hubs that lead nowhere): " + ", ".join(q["dead_ends"][:5])
                         + (" …" if len(q["dead_ends"]) > 5 else ""))
        if q["variety_pct"] < 70:
            lines.append(f"Only {q['variety_pct']}% of the tiles are different from each other.")
        self.lbl_quality.setText("\n".join(lines))
        self.lbl_quality.setToolTip("Score starts at 100. Unreachable rooms cost the most, then dead ends, known issues, "
                                    "zones with no tile, low furnishing and repeated tiles.")

    # ------------------------------------------------------------------
    def _browse_tiles(self):
        d = QFileDialog.getExistingDirectory(self, "Tile pack folder", self.ed_tiles.text() or "")
        if d:
            self._set_tiles_dir(d)

    def _set_tiles_dir(self, d):
        from geomorph import render
        d = (d or "").strip()
        self.ed_tiles.setText(d)
        ok = bool(d) and os.path.isdir(os.path.join(d, PACK_FOLDERS[0]))
        self.tiles_dir = d if ok else ""
        self.registry.tiles_dir = Path(d) if ok else None
        cache = self.user_dir / "thumbs"
        self.images = render.TileImages(self.tiles_dir or None, cache_dir=cache)
        from ui.geomorph_dialog import find_symbols_dir as _fsd
        self.images.symbols_dir = _fsd(self.tiles_dir, getattr(self.main.library.library, "root", ""))
        self.settings.setValue("geomorph/tiles_dir", self.tiles_dir)
        flagged = sum(1 for t in self.registry.tiles.values() if t.review)
        self.lbl_pack.setText(
            f"{len(self.registry.tiles)} tiles in the manifest ({flagged} with edge data worth a look). " +
            ("Preview uses the images in that folder." if ok else
             "No tile folder: the preview shows boxes. Placing on the canvas still uses the tiles in your library."))

    def _reload_archetypes(self):
        extra = [self.user_dir / "archetypes"]
        self.archetypes = self.archmod.load_all(extra)
        self.arch_problems = self.archetypes.pop("_problems", [])
        cur = self.cb_arch.currentData()
        self.cb_arch.blockSignals(True)
        self.cb_arch.clear()
        for name in sorted(self.archetypes, key=lambda n: (self.archetypes[n]["group"], n)):
            a = self.archetypes[name]
            self.cb_arch.addItem(f"{a['group']} — {name}", name)
        if cur:
            i = self.cb_arch.findData(cur)
            if i >= 0:
                self.cb_arch.setCurrentIndex(i)
        self.cb_arch.blockSignals(False)
        self._arch_changed()

    def _arch_changed(self):
        a = self.archetypes.get(self.cb_arch.currentData() or "")
        self.cb_env.clear()
        if not a:
            return
        for e in a.get("environments") or ["breathable"]:
            self.cb_env.addItem(e, e)
        self.lbl_arch.setText(f"Topology: {a['topology']}. Zones: " + ", ".join(z["name"] for z in a["zones"][:8]) +
                              (" …" if len(a["zones"]) > 8 else ""))

    # ------------------------------------------------------------------
    def options(self) -> dict:
        kind = "ship" if self.tabs.currentIndex() == 0 else "site"
        seed = clean_seed(self.ed_seed.text())
        self.ed_seed.setText(seed)
        o = {"kind": kind, "seed": seed, "theme": self.cb_theme.currentData(), "name": self.ed_name.text().strip(),
             "condition": self.cb_cond.currentData() or None, "mixed_conditions": self.ck_mixed.isChecked(),
             "learn": self.ck_learn.isChecked(),
             "atmosphere": {"light": self.light_color, "fixture": self.fixture_color},
             "peculiarities": self.sp_pec.value(), "grouping": self.sl_group.value() / 100.0, "intensity": self.sl_int.value() / 100.0,
             "overlays": [k for k, c in self.overlay_checks.items() if c.isChecked()]}
        if self.ck_decor.isChecked() or self.ck_outdoor.isChecked():
            o["decor"] = {"enabled": self.ck_decor.isChecked(), "density": self.sl_decor.value() / 100.0,
                          "incident": self.cb_incident.currentData(), "where": self.cb_where.currentData(),
                          "exterior": self.ck_outdoor.isChecked(),
                          "origin": self.cb_origin.currentData(), "reach": self.cb_reach.currentData()}
        if kind == "ship":
            counts = {t: sp.value() for t, sp in self.count_spins.items() if sp.value() >= 0}
            parts = {"wing": self.cb_wing.currentData(), "nose": self.cb_nose.currentData(),
                     "tail": self.cb_tail.currentData(), "transition": self.cb_trans.currentData(), "counts": counts,
                     "square_shoulders": self.ck_square.isChecked()}
            o.update(ship_type=self.cb_ship_type.currentText(), tonnage=self.sp_tonnage.value(),
                     mode=self.cb_ship_mode.currentData(), orientation=self.cb_orient.currentData(),
                     symmetric=self.ck_sym.isChecked(), fins=self.ck_fins.isChecked(), parts=parts,
                     craft=[k for k, c in self.craft_checks.items() if c.isChecked()] or None)
        else:
            o.update(archetype=self.cb_arch.currentData(), scale=self.cb_scale.currentData(),
                     environment=self.cb_env.currentData(), mode=self.cb_site_mode.currentData())
        return o

    def _run(self, fn, done):
        self.btn_gen.setEnabled(False)
        self.btn_regen.setEnabled(False)
        self.lbl_status.setText("Working…")
        if self.sync:
            try:
                value, err = fn(), None
            except Exception as exc:
                value, err = None, str(exc)
            self.btn_gen.setEnabled(True)
            self.btn_regen.setEnabled(True)
            done(value, err)
            return

        def finish(value, err):
            self.btn_gen.setEnabled(True)
            self.btn_regen.setEnabled(True)
            done(value, err)
            if self._live_pending:
                self._live_pending = False
                self._live_timer.start()
        self.job = _Job(fn, self)
        self.job.done.connect(finish)
        self.job.start()

    def _apply_learning(self):
        """Point the generator at this user's preference file (or switch learning off)."""
        from geomorph import learning
        learning.use(self.user_dir / "preferences.json" if self.ck_learn.isChecked() else None)
        return learning

    def _update_learn_label(self):
        learning = self._apply_learning()
        if not self.ck_learn.isChecked():
            self.lbl_learn.setText("Learning is off.")
            return
        sm = learning.summary()
        self.lbl_learn.setText("Nothing learned yet: rate maps with the thumbs." if not sm["maps"] else
                               f"Learned from {sm['maps']} vote(s): {sm['up']} liked, {sm['down']} not; "
                               f"{sm['tiles']} tile(s) now favoured or avoided.")

    def _rate(self, vote):
        from geomorph import learning
        if self.result is None:
            return
        self._apply_learning()
        if learning.record(self.result, rating=vote):
            self.lbl_status.setText("Thanks — the tiles in this map will be used " + ("more" if vote > 0 else "less")
                                    + " in future maps.")
        self._update_learn_label()

    def _forget(self):
        learning = self._apply_learning()
        learning.reset()
        self._update_learn_label()
        self.lbl_status.setText("Forgot everything learned.")

    def _soft_vote(self):
        """A map you keep (place or export) is a gentle vote by its score."""
        if self.result is not None and self.ck_learn.isChecked():
            self._apply_learning().record(self.result, soft=True)
            self._update_learn_label()

    def generate(self):
        from geomorph import pipeline
        self._apply_learning()
        opts = self.options()
        reg, arch = self.registry, self.archetypes

        def work():
            res = pipeline.generate(reg, opts, dict(arch, _problems=[]))
            self._prerender(res)
            return res
        self._run(work, self._generated)

    def _prerender(self, res):
        """Render every level (and fill the thumbnail cache) off the UI thread."""
        from geomorph import render
        res._previews = {}
        for g in res.grids:
            res._previews[(g.index, True, True, True)] = render.render_level(res, g.index, self.images, pps=8, gm=True)
            res._previews[(g.index, False, True, True)] = render.render_level(res, g.index, self.images, pps=8, gm=False)

    def _generated(self, res, err, push=True):
        if err:
            self.lbl_status.setText("Generation failed: " + err.splitlines()[0])
            QMessageBox.warning(self, "Geomorph generator", err)
            return
        if self._scenario_hooks:
            hooks = res.text.setdefault("hooks", [])
            hooks.extend({"type": "scenario", "text": h} for h in self._scenario_hooks)
        self.locked = {z for z in self.locked if z in res.zones}      # layouts differ: keep only ids that still exist
        prev = self.result
        if push and prev is not None and prev is not res:
            self._push_undo(("result", prev, getattr(self, "_last_seed", ""), "New map"))
            from geomorph import quality
            self.lbl_changes.setText("New map: " + quality.compare(getattr(prev, "quality", None), res.quality) + "."
                                     if getattr(res, "quality", None) else "")
        elif push:
            self.lbl_changes.setText("")
        self._last_seed = (res.options or {}).get("seed", "")
        self.result = res
        self.cb_level.blockSignals(True)
        self.cb_level.clear()
        for g in res.grids:
            self.cb_level.addItem(g.name, g.index)
        self.cb_level.blockSignals(False)
        self.level_index = 0
        self.cb_zone.clear()
        for zid, z in res.zones.items():
            self.cb_zone.addItem(f"{z.name} ({zid})", zid)
        self._zone_changed()
        self._fill_text()
        self._update_quality()
        self._update_learn_label()
        self._enable(True)
        if res.gaps:
            names = ", ".join(list(res.gaps)[:6]) + (" …" if len(res.gaps) > 6 else "")
            self.lbl_gaps.setText(f"⚠ {len(res.gaps)} zone(s) had no suitable tile and were drawn procedurally "
                                  f"or substituted: {names}. Details in 'Checks & gaps'.")
            self.lbl_gaps.show()
        else:
            self.lbl_gaps.hide()
        n = sum(len(g.placed) for g in res.grids)
        self.lbl_status.setText(f"{res.meta['name']}: {n} tiles on {len(res.grids)} level(s). "
                                + (f"{len(res.issues)} issue(s) — see Checks." if res.issues else "All checks passed."))

    def _regenerate(self):
        self.ed_seed.setText(new_seed())
        self.generate()

    def _level_changed(self, i):
        self.level_index = max(0, i)
        self._show_level()

    def _show_level(self):
        res = self.result
        if res is None:
            return
        key = (self.level_index, self.ck_gm.isChecked(), self.ck_show_decor.isChecked(), self.ck_atmo.isChecked())
        previews = getattr(res, "_previews", None)
        if previews is None:
            previews = res._previews = {}
        pv = previews.get(key)
        if pv is None:
            from geomorph import render
            pv = previews[key] = render.render_level(res, self.level_index, self.images, pps=8,
                                                     gm=key[1], decor=key[2], atmosphere=key[3])
        pv = self._mark_tiles(pv)
        self.preview.setPixmap(pil_to_pixmap(pv))
        self.preview.resize(pv.size[0], pv.size[1])

    def _mark_tiles(self, im):
        """Outline the selected tile (yellow) and locked tiles (cyan) on a copy."""
        if not (self.locked or self.selected_zone):
            return im
        from PIL import ImageDraw
        from geomorph import render
        res = self.result
        x0, y0, _x1, _y1 = render.shared_bounds(res)
        pps, head = 8, 3 * 8
        out = im.copy()
        d = ImageDraw.Draw(out)
        for p in res.grids[self.level_index].placed:
            if p.zone in self.locked or p.zone == self.selected_zone:
                box = ((p.x - x0) * pps, head + (p.y - y0) * pps, (p.x + p.w - x0) * pps - 1, head + (p.y + p.h - y0) * pps - 1)
                if p.zone == self.selected_zone:
                    d.rectangle(box, outline=(255, 214, 64, 255), width=3)
                else:
                    d.rectangle(box, outline=(80, 220, 255, 255), width=2)
                if p.zone in self.locked:
                    d.rectangle((box[0] + 3, box[1] + 3, box[0] + 12, box[1] + 12), fill=(80, 220, 255, 255))
        return out

    def _fill_text(self):
        res = self.result
        t = res.text
        lines = [t.get("title", ""), "", t.get("description", ""), "", "NOTES"]
        lines += [f"  • {n}" for n in t.get("notes", [])]
        lines += ["", "ADVENTURE HOOKS"]
        lines += [f"  [{h['type']}] {h['text']}" for h in t.get("hooks", [])]
        self.txt_main.setPlainText("\n".join(lines))
        self.txt_key.setPlainText("\n".join(f"{e['n']}. {e['title']} (level {e['level'] + 1}) — {e.get('text', '')}"
                                            for e in res.key))
        self._fill_notes()
        from geomorph import reports
        rep = ["Issues:"] + ([f"  - {i}" for i in res.issues] or ["  none"])
        rep += ["", "Zones with no suitable tile (drawn procedurally or substituted):"]
        rep += [f"  - {k}: {v}" for k, v in res.gaps.items()] or ["  none"]
        if self.arch_problems:
            rep += ["", "Invalid archetype files:"] + [f"  - {p}" for p in self.arch_problems]
        self.txt_report.setPlainText("\n".join(rep))

    # ------------------------------------------------------------------
    def _reroll_zone(self):
        from geomorph import pipeline
        import random
        zid = self.cb_zone.currentData()
        if self.result is None or not zid:
            return
        name = self.result.zones[zid].name if zid in self.result.zones else zid
        self._reroll(f"Re-roll {name}", lambda res: 1 if pipeline.reroll_zone(res, zid, seed=random.random()) else 0)

    def place_on_canvas(self):
        res = self.result
        if res is None:
            return
        self.main._place_geomorph(res, self.registry, self.images)
        self.lbl_status.setText("Placed on the canvas as new levels.")
        self._soft_vote()

    def export(self):
        from geomorph import exporter
        res = self.result
        if res is None:
            return
        d = QFileDialog.getExistingDirectory(self, "Export folder (PNG per level, PDF and JSON, GM and player)")
        if not d:
            return
        files = exporter.export_all(res, d, self.images, pps=16)
        self.lbl_status.setText(f"Exported {len(files)} files to {d}.")
        self._soft_vote()

    def _save_layout(self):
        from geomorph import exporter
        if self.result is None:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Save layout", "layout.json", "Layout JSON (*.json)")
        if p:
            exporter.save_layout(self.result, p)

    def _load_layout(self):
        from geomorph import exporter
        p, _ = QFileDialog.getOpenFileName(self, "Load layout", "", "Layout JSON (*.json)")
        if not p:
            return
        try:
            res = exporter.load_layout(p, self.registry)
            self._prerender(res)
        except Exception as exc:
            QMessageBox.warning(self, "Load layout", f"Could not load that layout:\n{exc}")
            return
        self._generated(res, None)

    def _edit_archetypes(self):
        dlg = ArchetypeEditor(self, self.archmod, self.archetypes, self.user_dir / "archetypes", self.colors)
        dlg.exec()
        self._reload_archetypes()

    def _edge_editor(self):
        from ui.geomorph_edge_editor import EdgeEditor
        EdgeEditor(self, self.registry, self.images, self.user_dir / "edge_overrides.json").exec()


class ArchetypeEditor(QDialog):
    """'Other / custom' archetype editor: edit the definition, validate, save as a new file."""

    def __init__(self, parent, archmod, archetypes, directory, colors):
        super().__init__(parent)
        self.archmod, self.archetypes, self.directory = archmod, archetypes, Path(directory)
        self.setWindowTitle("Archetype editor")
        self.resize(760, 700)
        v = QVBoxLayout(self)
        v.addWidget(_help("An archetype is a JSON definition: zones with tags, counts, levels, access zones and "
                          "adjacency, plus text tables. Saving creates a new file in your archetype folder — "
                          "no code changes. Start from an existing archetype or a blank one.", colors))
        row = QHBoxLayout()
        self.cb = QComboBox()
        self.cb.addItem("Blank archetype", "")
        for n in sorted(archetypes):
            self.cb.addItem(n, n)
        self.cb.currentIndexChanged.connect(self._load)
        row.addWidget(QLabel("Start from"))
        row.addWidget(self.cb, 1)
        v.addLayout(row)
        self.edit = QPlainTextEdit()
        self.edit.setStyleSheet("font-family: monospace;")
        v.addWidget(self.edit, 1)
        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        row = QHBoxLayout()
        b1 = QPushButton("Validate")
        b1.clicked.connect(self.validate)
        b2 = QPushButton("Save as new archetype")
        b2.clicked.connect(self.save)
        b3 = QPushButton("Close")
        b3.clicked.connect(self.accept)
        for b in (b1, b2, b3):
            row.addWidget(b)
        v.addLayout(row)
        self._load()

    def _load(self):
        name = self.cb.currentData()
        a = self.archmod.blank_archetype() if not name else {k: v for k, v in self.archetypes[name].items() if not k.startswith("_")}
        if name:
            a = dict(a, name=f"{name} (custom)")
        self.edit.setPlainText(json.dumps(a, indent=1))

    def _parse(self):
        try:
            return json.loads(self.edit.toPlainText()), None
        except json.JSONDecodeError as exc:
            return None, f"Not valid JSON: {exc}"

    def validate(self):
        a, err = self._parse()
        if a is None:
            self.lbl.setText(err)
            return False
        errs = self.archmod.validate(a, a.get("name", "archetype"))
        self.lbl.setText("Valid." if not errs else "\n".join(errs))
        return not errs

    def save(self):
        if not self.validate():
            return
        a, _ = self._parse()
        path = self.archmod.save_custom(a, self.directory)
        self.lbl.setText(f"Saved {path}")
