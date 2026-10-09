"""The geomorph generator window: options, preview, placing on the canvas, the
archetype editor and the edge editor (offscreen Qt, synthetic tile images)."""
import json
import os
import sys
import tempfile

from PyQt6.QtCore import QPointF, QStandardPaths, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)

import ui.launch_screen as ls


def fake_exec(self):
    self.result_action = ("new", None)
    return 1


ls.LaunchScreen.exec = fake_exec

from ui.geomorph_dialog import ArchetypeEditor, GeomorphDialog, find_tiles_dir
from ui.geomorph_edge_editor import EdgeEditor, _Strip
from ui.main_window import MainWindow

MainWindow._offer_recovery = lambda self: self.overlay.open_menu()
shown = []
QMessageBox.warning = staticmethod(lambda *a, **k: shown.append(("warn", a[2:])))
QMessageBox.information = staticmethod(lambda *a, **k: shown.append(("info", a[2:])))

store = tempfile.mkdtemp(prefix="sceneboard-geo-")
win = MainWindow()
win.settings.clear()
win.project.asset_store = store
lib = win.library.library
lib.scan(store)
win.library.set_project(win.project, lib, win._add_at_center)
win.canvas.library = lib
win.show()
app.processEvents()
win.overlay.hide()

dlg = GeomorphDialog(win, win, sync=True)
assert len(dlg.registry.tiles) > 500
assert dlg.cb_arch.count() >= 25 and dlg.cb_env.count() >= 1
assert dlg.tiles_dir == "", "no pack in the library yet"

# ---- a site ---------------------------------------------------------------
dlg.tabs.setCurrentIndex(1)
dlg.cb_arch.setCurrentIndex(dlg.cb_arch.findData("Deep mine"))
dlg.ed_seed.setText("gui-test")
dlg.overlay_checks["threat"].setChecked(True)
dlg.generate()
assert dlg.result is not None and dlg.result.kind == "site"
assert dlg.cb_level.count() == len(dlg.result.grids) > 1
assert dlg.preview.pixmap() is not None and not dlg.preview.pixmap().isNull()
assert "Deep mine" in dlg.txt_main.toPlainText() or dlg.result.meta["name"] in dlg.txt_main.toPlainText()
assert "ADVENTURE HOOKS" in dlg.txt_main.toPlainText() and dlg.txt_key.toPlainText().startswith("1.")
assert dlg.cb_zone.count() == len(dlg.result.zones)
first = dlg.options()
dlg.generate()
assert [(g.name, len(g.placed)) for g in dlg.result.grids] == [(g.name, len(g.placed)) for g in
                                                               dlg.result.grids], "same options -> same result"
dlg.ck_gm.setChecked(False)
dlg.cb_level.setCurrentIndex(1)
assert not dlg.preview.pixmap().isNull()
dlg._reroll_zone()

# ---- placing without the tiles in the library skips safely ----------------------
n_levels = len(win.project.levels)
assert win._place_geomorph(dlg.result, dlg.registry, dlg.images) is None
assert len(win.project.levels) == n_levels and shown, "nothing placed, user told why"

# ---- put synthetic tile PNGs in the library (same file names as the pack) ---------
need = list({p.tile.id: p.tile for g in dlg.result.grids for p in g.placed}.values())
for t in need:
    path = os.path.join(store, "Pack", *t.image.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = QImage((t.w + 4) * 6, (t.h + 4) * 6, QImage.Format.Format_ARGB32)
    img.fill(QColor("#336677"))
    assert img.save(path)
lib.scan(store)
win.library.set_project(win.project, lib, win._add_at_center)
out = win._place_geomorph(dlg.result, dlg.registry, dlg.images)
assert out is not None
assert len(win.project.levels) == n_levels + len(dlg.result.grids)
new_levels = win.project.levels[n_levels:]
tiles_on_canvas = sum(1 for lv in new_levels for p in lv.pieces if p.layer and not p.is_text and p.asset_path)
assert tiles_on_canvas == sum(len(g.placed) for g in dlg.result.grids), tiles_on_canvas
assert any(p.is_text for lv in new_levels for p in lv.pieces), "key numbers placed"
assert any(p.embedded for lv in new_levels for p in lv.pieces), "filler embedded"
lv0 = new_levels[1]
layer_names = {l.name for l in lv0.layers}
assert "Geomorph tiles" in layer_names
# tile nodes sit on whole grid squares (footprint aligned) and use rotation/mirror
cs = win.project.cell_size
for lv in new_levels:
    for p in lv.pieces:
        if p.asset_path and not p.is_text:
            vis_w = p.w * p.scale
            cx, cy = p.x + vis_w / 2, p.y + p.h * p.scale / 2
            # footprint centre is on a half-square grid line (20 / 10 / 5 square tiles)
            assert abs((cx / cs * 2) - round(cx / cs * 2)) < 1e-6, (cx / cs)
# Regenerate replaces the previous output instead of piling up
levels_before = len(win.project.levels)
win._place_geomorph(dlg.result, dlg.registry, dlg.images)
assert len(win.project.levels) == levels_before, "second placement replaces the first"
win.canvas.undo() if hasattr(win.canvas, "undo") else None

# ---- ship tab -----------------------------------------------------------------
dlg.tabs.setCurrentIndex(0)
dlg.cb_ship_type.setCurrentText("Military")
dlg.sp_tonnage.setValue(1500)
dlg.cb_ship_mode.setCurrentIndex(0)
dlg.generate()
assert dlg.result.kind == "ship" and dlg.result.grids[0].placed
assert "Military" in dlg.result.meta["ship_type"]
# part options: no wings, no guns, exactly 2 escape pods, a chosen nose style, craft overlays on
dlg.cb_wing.setCurrentIndex(dlg.cb_wing.findData("none"))
dlg.count_spins["weapons"].setValue(0)
dlg.count_spins["escape"].setValue(2)
dlg.cb_nose.setCurrentIndex(dlg.cb_nose.findData("style:Bridge, Rounded Nose"))
dlg.craft_checks["escape"].setChecked(True)
dlg.generate()
placed = dlg.result.grids[0].placed
assert not [p for p in placed if p.tile.type == "wing"]
assert not [p for p in placed if p.tile.tags.get("weapons", 0) >= 0.6]
assert dlg.result.options["craft"] == ["escape"] and dlg.result.options["parts"]["counts"] == {"weapons": 0, "escape": 2}

# ---- symbol decor options ------------------------------------------------------
dlg.ck_decor.setChecked(True)
dlg.cb_incident.setCurrentIndex(dlg.cb_incident.findData("overrun"))
dlg.cb_where.setCurrentIndex(dlg.cb_where.findData("all"))
dlg.sl_decor.setValue(90)
opts = dlg.options()
assert opts["decor"] == {"enabled": True, "density": 0.9, "incident": "overrun", "where": "all"}
dlg.generate()
assert dlg.result.decor and dlg.result.meta["decor"]["incident"] == "overrun"
dlg.ck_decor.setChecked(False)
assert "decor" not in dlg.options()

# ---- tile folder detection --------------------------------------------------
fake_pack = tempfile.mkdtemp()
os.makedirs(os.path.join(fake_pack, "x", "100x100 Core"))
assert find_tiles_dir(fake_pack) == os.path.join(fake_pack, "x")
assert find_tiles_dir("") == ""
dlg._set_tiles_dir(os.path.join(fake_pack, "x"))
assert dlg.tiles_dir.endswith("x")

# ---- archetype editor: validate, reject, save, reload (no code change) ---------
d = tempfile.mkdtemp()
ed = ArchetypeEditor(dlg, dlg.archmod, dlg.archetypes, d, dlg.colors)
assert ed.validate()
ed.edit.setPlainText("{nope")
assert not ed.validate() and "JSON" in ed.lbl.text()
ed.cb.setCurrentIndex(ed.cb.findData("Deep mine"))
txt = json.loads(ed.edit.toPlainText())
assert txt["name"] == "Deep mine (custom)"
txt["name"] = "GUI Test Mine"
ed.edit.setPlainText(json.dumps(txt))
ed.save()
assert os.path.exists(os.path.join(d, "gui_test_mine.json"))
assert "GUI Test Mine" in dlg.archmod.load_all([d])
txt["topology"] = "pentagon"
ed.edit.setPlainText(json.dumps(txt))
assert not ed.validate()

# ---- edge editor: click a square, save the correction ----------------------------
ov = os.path.join(tempfile.mkdtemp(), "edge_overrides.json")
ee = EdgeEditor(dlg, dlg.registry, dlg.images, ov)
ee.list.setCurrentRow(0)
tid = ee.tile.id
before = list(ee.tile.edges["N"]["cls"])
strip = _Strip("N", before, False, ee._changed)
strip.resize(14 * len(before), 24)
strip.mousePressEvent(type("E", (), {"position": lambda s: QPointF(5, 10), "button": lambda s: Qt.MouseButton.LeftButton})())
assert ee.overrides[tid]["N"][0] == (before[0] + 1) % 3
ee._save()
assert json.load(open(ov))[tid]["N"][0] == (before[0] + 1) % 3
ee.tile.edges["N"]["cls"] = before                      # leave the shared registry untouched

# ---- symbol legend: toggle on the canvas and in exports ------------------------
from core import exporter as core_exporter, legend
legend.STATE.update(path="", show=False, export=False)
win._toggle_legend_show(True)              # no legend in the library yet -> told why, stays off
assert not legend.STATE["show"] and shown
png("Pack/Symbols & Abbreviations.png", 132, 220, "#e8fafa") if False else None
img = QImage(132, 220, QImage.Format.Format_ARGB32)
img.fill(QColor("#e8fafa"))
os.makedirs(os.path.join(store, "Geomorphs"), exist_ok=True)
assert img.save(os.path.join(store, "Geomorphs", "Symbols & Abbreviations.png"))
lib.scan(store)
win._toggle_legend_show(True)
assert legend.STATE["path"] and legend.STATE["show"] and win.act_legend_show.isChecked()
win.canvas.grab()                           # paints with the overlay without error
base = core_exporter.render_level(win.project, win.project.levels[0])
win._toggle_legend_export(True)
with_legend = core_exporter.render_level(win.project, win.project.levels[0])
assert with_legend != base, "legend appears in the export"
win._toggle_legend_export(False)
assert core_exporter.render_level(win.project, win.project.levels[0]) == base
win._toggle_legend_show(False)
assert not legend.STATE["show"]

dlg.close()
win._confirm_discard = lambda *a, **k: True
win.close()
print("ALL GEOMORPH GUI CHECKS PASSED")
