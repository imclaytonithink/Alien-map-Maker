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

from PyQt6.QtCore import QObject, QStandardPaths, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
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
        self._build()
        self._reload_archetypes()
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
                           ("random", "In about a third of the rooms")):
            self.cb_where.addItem(label, key)
        f2 = QFormLayout()
        f2.addRow("Something bad happened", self.cb_incident)
        f2.addRow("Where", self.cb_where)
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
        for wdg in (self.btn_gen, self.btn_regen, QLabel("Level"), self.cb_level, self.ck_gm):
            top.addWidget(wdg)
        top.addStretch(1)
        right.addLayout(top)
        self.preview = QLabel("Press Generate.")
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
        for wdg in (self.cb_zone, self.btn_reroll, self.btn_place, self.btn_export, self.btn_save, self.btn_load, b_edge, b_close):
            row.addWidget(wdg)
        right.addLayout(row)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        right.addWidget(self.lbl_status)
        credit = _help("Tiles: Starship Geomorphs 2.0 by Robert Pearce (Pearce Design Studio, LLC), CC BY-NC 4.0; "
                       "PNG renderings by Eric Smith / RPG Mobius. Non-commercial fan tool; Traveller is a trademark of "
                       "Far Future Enterprises.", self.colors)
        right.addWidget(credit)
        self._enable(False)

    def _enable(self, has):
        for b in (self.btn_place, self.btn_export, self.btn_save, self.btn_reroll):
            b.setEnabled(has)

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
             "peculiarities": self.sp_pec.value(), "grouping": self.sl_group.value() / 100.0, "intensity": self.sl_int.value() / 100.0,
             "overlays": [k for k, c in self.overlay_checks.items() if c.isChecked()]}
        if self.ck_decor.isChecked():
            o["decor"] = {"enabled": True, "density": self.sl_decor.value() / 100.0,
                          "incident": self.cb_incident.currentData(), "where": self.cb_where.currentData()}
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
        self.job = _Job(fn, self)
        self.job.done.connect(finish)
        self.job.start()

    def generate(self):
        from geomorph import pipeline
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
        gm = True
        for g in res.grids:
            res._previews[(g.index, True)] = render.render_level(res, g.index, self.images, pps=8, gm=True)
            res._previews[(g.index, False)] = render.render_level(res, g.index, self.images, pps=8, gm=False)

    def _generated(self, res, err):
        if err:
            self.lbl_status.setText("Generation failed: " + err.splitlines()[0])
            QMessageBox.warning(self, "Geomorph generator", err)
            return
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
        self._show_level()
        self._fill_text()
        self._enable(True)
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
        pv = getattr(res, "_previews", {}).get((self.level_index, self.ck_gm.isChecked()))
        if pv is None:
            from geomorph import render
            pv = render.render_level(res, self.level_index, self.images, pps=8, gm=self.ck_gm.isChecked())
        self.preview.setPixmap(pil_to_pixmap(pv))
        self.preview.resize(pv.size[0], pv.size[1])

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
        res = self.result
        zid = self.cb_zone.currentData()
        if res is None or not zid:
            return
        import random
        ok = pipeline.reroll_zone(res, zid, seed=random.random())
        self._prerender(res)
        self._show_level()
        self.lbl_status.setText("Zone re-rolled." if ok else "No other tile fits that zone here.")

    def place_on_canvas(self):
        res = self.result
        if res is None:
            return
        self.main._place_geomorph(res, self.registry, self.images)
        self.lbl_status.setText("Placed on the canvas as new levels.")

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
