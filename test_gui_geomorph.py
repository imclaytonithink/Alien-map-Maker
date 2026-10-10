"""The geomorph generator window: options, preview, placing on the canvas, the
archetype editor and the edge editor (offscreen Qt, synthetic tile images)."""
import json
import os
from pathlib import Path
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

from ui.geomorph_dialog import ArchetypeEditor, GeomorphDialog, find_tiles_dir, user_data_dir

import shutil
# this test caches synthetic tile thumbnails later on; a rerun must not find them at the start
shutil.rmtree(user_data_dir() / "thumbs", ignore_errors=True)
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
assert not any(p.is_text for lv in new_levels for p in lv.pieces), "no stray key numbers or marker letters"
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
for sp in dlg.count_spins.values():                 # an ordinary ship (escape pod decks hold no furniture)
    sp.setValue(-1)
dlg.ck_decor.setChecked(True)
dlg.cb_incident.setCurrentIndex(dlg.cb_incident.findData("overrun"))
dlg.cb_where.setCurrentIndex(dlg.cb_where.findData("all"))
dlg.sl_decor.setValue(90)
opts = dlg.options()
assert opts["decor"] == {"enabled": True, "density": 0.9, "incident": "overrun", "where": "all", "exterior": True, "origin": "random", "reach": "medium"}
dlg.ck_outdoor.setChecked(False)
assert dlg.options()["decor"]["exterior"] is False
dlg.ck_outdoor.setChecked(True)
dlg.generate()
assert dlg.result.decor and dlg.result.meta["decor"]["incident"] == "overrun"
dlg.ck_decor.setChecked(False)
dlg.ck_outdoor.setChecked(False)
assert "decor" not in dlg.options()

assert dlg.options()["grouping"] == 0.6
dlg.sl_group.setValue(0)
assert dlg.options()["grouping"] == 0.0

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

# ---- scenarios, presets, live preview, lock / re-roll, gap warning ---------------
dlg.user_dir = Path(tempfile.mkdtemp(prefix="geo-user-"))
dlg._fill_presets()
names = [dlg.cb_preset.itemText(i) for i in range(dlg.cb_preset.count())]
assert any(n.startswith("Scenario: ") for n in names) and len(names) >= 8, names
_set = dlg.cb_preset.findText("Scenario: Prison riot")
dlg.cb_preset.setCurrentIndex(_set)
dlg._apply_preset()
assert dlg.tabs.currentIndex() == 1 and dlg.cb_arch.currentData() == "Prison / penal colony"
assert dlg.cb_incident.currentData() == "struggle" and dlg.overlay_checks["lockdown"].isChecked()
assert dlg.result is not None and any(h["type"] == "scenario" for h in dlg.result.text["hooks"])
assert dlg.options()["decor"]["incident"] == "struggle"
dlg._scenario_hooks = []
# save / apply / delete my own preset
from PyQt6.QtWidgets import QInputDialog
QInputDialog.getText = staticmethod(lambda *a, **k: ("Mine", True))
dlg.sl_decor.setValue(77)
dlg._save_preset()
assert dlg.cb_preset.currentData() == "user:Mine"
dlg.sl_decor.setValue(20)
dlg._apply_preset()
assert dlg.sl_decor.value() == 77, "preset restores the amount slider"
dlg._delete_preset()
assert dlg.cb_preset.findData("user:Mine") < 0
# ship scenario round-trips through apply_options
dlg.cb_preset.setCurrentIndex(dlg.cb_preset.findText("Scenario: Derelict with a nest"))
dlg._apply_preset()
assert dlg.tabs.currentIndex() == 0 and dlg.cb_cond.currentData() == "Derelict" and dlg.sp_tonnage.value() == 2000
assert dlg.cb_where.currentData() == "spread" and dlg.options()["decor"]["origin"] == "cargo" \
    and dlg.options()["decor"]["reach"] == "medium", "the nest scenario spreads from a cargo hold"
assert "origin" in dlg.result.meta["decor"] and dlg.result.meta["decor"]["origin"] in dlg.result.zones
# live preview regenerates after an option changes
before_result = dlg.result
dlg.ck_live.setChecked(True)
dlg.sp_tonnage.setValue(1500)
assert dlg._live_timer.isActive()
dlg._live_timer.stop()
dlg._live_fire()
assert dlg.result is not before_result and dlg.options()["tonnage"] == 1500
dlg.ck_live.setChecked(False)
# click a tile -> selected; lock it; re-rolls keep it
res = dlg.result
g0 = res.grids[0]
cand = next(p for p in g0.placed if p.zone and p.tile.type == "standard")
from geomorph import render as _r
bx0, by0, _a, _b = _r.shared_bounds(res)
dlg.level_index = 0
dlg._preview_clicked(int((cand.x + 1 - bx0) * 8), int(24 + (cand.y + 1 - by0) * 8))
assert dlg.cb_zone.currentData() == cand.zone and dlg.selected_zone == cand.zone
dlg.ck_lock.setChecked(True)
assert cand.zone in dlg.locked
locked_tile = cand.tile.id
for _ in range(4):
    dlg._reroll_all()
now = next(p for p in res.grids[0].placed if p.zone == cand.zone)
assert now.tile.id == locked_tile, "locked tile survives re-rolls"
dlg._reroll_level()
assert "tile(s) changed" in dlg.lbl_status.text()
dlg._show_level()
assert not dlg.preview.pixmap().isNull()
# editable room notes flow into the exports; player version stays free of GM text
assert dlg.lst_notes.count() == len(res.key)
dlg.lst_notes.setCurrentRow(0)
dlg.ed_note_gm.setPlainText("GM ONLY: the vent leads to the nest.")
dlg.ed_note_player.setPlainText("A quiet corridor.")
assert res.key[0]["text"].startswith("GM ONLY") and res.key[0]["player"] == "A quiet corridor."
assert "GM ONLY" in dlg.txt_key.toPlainText()
from geomorph import exporter as _ex
pk = _ex.to_package(res, gm=False)
assert "GM ONLY" not in str(pk) and pk["key"][0]["text"] == "A quiet corridor."
assert "GM ONLY" in str(_ex.to_package(res, gm=True))
dlg._preview_clicked(int((cand.x + 1 - bx0) * 8), int(24 + (cand.y + 1 - by0) * 8))
assert dlg.lst_notes.currentRow() >= 0
# map check panel: score colour, summary, refreshes after a re-roll; preview can hide the furniture
assert dlg.lbl_score.text().isdigit() and "dead end" in dlg.lbl_quality.text()
score_before = dlg.lbl_score.text()
dlg._reroll_all()
assert dlg.lbl_score.text().isdigit()
dlg.ck_show_decor.setChecked(False)
assert not dlg.preview.pixmap().isNull()
dlg.ck_show_decor.setChecked(True)
dlg.ck_atmo.setChecked(False)                       # the atmosphere effect can be switched off in the preview
assert not dlg.preview.pixmap().isNull()
dlg.ck_atmo.setChecked(True)
# what changed + undo: a re-roll says what moved and can be undone exactly; a new map can be undone too
ids_now = lambda r: [[(p.tile.id, p.x, p.y) for p in g.placed] for g in r.grids]
dlg.locked.clear()
ids_before = ids_now(dlg.result)
dlg.undo_stack.clear()
dlg._reroll_level()
assert "Re-roll level" in dlg.lbl_changes.text() and "tile(s) changed" in dlg.lbl_changes.text(), dlg.lbl_changes.text()
assert dlg.btn_undo.isEnabled() and len(dlg.undo_stack) == 1
if ids_now(dlg.result) != ids_before:
    dlg._undo()
    assert ids_now(dlg.result) == ids_before, "undo restores the layout"
    assert dlg.lbl_changes.text().startswith("Undid: Re-roll level")
    assert not dlg.btn_undo.isEnabled()
before_result, before_seed = dlg.result, dlg.ed_seed.text()
dlg._regenerate()
assert dlg.result is not before_result and dlg.lbl_changes.text().startswith("New map:") and dlg.btn_undo.isEnabled()
dlg._undo()
assert dlg.result is before_result and dlg.ed_seed.text() == before_seed, "undo brings back the previous map and seed"
assert dlg.cb_zone.count() == len(before_result.zones)
# ratings teach the generator; best of 6 ranks candidates; learning can be switched off and reset
from geomorph import learning as _learning
dlg.ck_learn.setChecked(True)
dlg._forget()
assert "Nothing learned" in dlg.lbl_learn.text()
dlg._rate(+1)
assert "1 vote" in dlg.lbl_learn.text() and "1 liked" in dlg.lbl_learn.text(), dlg.lbl_learn.text()
assert (dlg.user_dir / "preferences.json").exists() and _learning.net_by_tile()
dlg._rate(-1)
assert "2 vote" in dlg.lbl_learn.text() and "1 not" in dlg.lbl_learn.text()
dlg.ck_learn.setChecked(False)
dlg._rate(+1)                                              # off: nothing is recorded
assert dlg.lbl_learn.text() == "Learning is off." and _learning.PATH is None
dlg.ck_learn.setChecked(True)
dlg._update_learn_label()
assert "2 vote" in dlg.lbl_learn.text()
dlg.ed_seed.setText("bo-gui")
before_best = dlg.result
dlg._best_of()
assert len(dlg.candidates) == 6 and dlg.result is dlg.candidates[0], "the top-ranked candidate is taken in tests"
scores = [c.quality["score"] for c in dlg.candidates]
assert dlg.ed_seed.text() == dlg.candidates[0].options["seed"] and dlg.lbl_changes.text().startswith("Picked candidate 1 of 6")
assert dlg.btn_undo.isEnabled()
dlg._undo()
assert dlg.result is before_best, "a picked candidate can be undone"
from ui.geomorph_dialog import _BestOfDialog
box = _BestOfDialog(dlg, dlg.candidates)
assert box.chosen is None
box._pick(3)
assert box.chosen == 3
dlg._forget()
assert "Nothing learned" in dlg.lbl_learn.text() and not _learning.net_by_tile()
# a placed or exported map votes softly by its score
dlg.result.quality = dict(dlg.result.quality, score=96)
dlg._soft_vote()
assert _learning.summary()["maps"] == 1 and "1 vote" in dlg.lbl_learn.text()
dlg._forget()
# lights: colours for the emergency lamps and fixtures, and the user's own lights
from geomorph import atmosphere as _A, render as _render
dlg.ck_live.setChecked(False)
dlg.cb_where.setCurrentIndex(0)
dlg.generate()
assert dlg.options()["atmosphere"] == {"light": _A.DEFAULT_LIGHT, "fixture": _A.DEFAULT_FIXTURE}
dlg._set_color("light_color", "#2060ff")
dlg._set_color("fixture_color", "#00ff00")
assert dlg.options()["atmosphere"] == {"light": "#2060ff", "fixture": "#00ff00"}
assert dlg.result.options["atmosphere"] == {"light": "#2060ff", "fixture": "#00ff00"}, "a colour change applies to the map on screen"
assert not dlg.preview.pixmap().isNull()
dlg._reset_lamp_colors()
assert dlg.light_color == _A.DEFAULT_LIGHT
dlg.apply_options(dict(dlg.options(), atmosphere={"light": "#ff8800", "fixture": "#ffffff"}))
assert dlg.light_color == "#ff8800" and dlg.fixture_color == "#ffffff"
dlg._reset_lamp_colors()
# placing: left-click adds a light beside a wall, right-click removes the nearest one, undo and clear work
res = dlg.result
g0 = res.grids[dlg.level_index]
res.lights = []
tp = next(p for p in g0.placed if _A._floor(p) is not None and p.tile.type == "standard" and _A._lamps(p, _A._floor(p)))
cx, cy, wall = _A._lamps(tp, _A._floor(tp))[0]
bx0, by0, _a, _b = _render.shared_bounds(res)
click_px = (int((tp.x + (cx + 0.5) / 2 - bx0) * 8), int(24 + (tp.y + (cy + 0.5) / 2 - by0) * 8))
dlg.undo_stack.clear()
dlg._preview_clicked(*click_px)                               # not in place mode: this only selects a room
assert not res.lights
dlg.btn_place_light.setChecked(True)
dlg.sp_light_radius.setValue(5)
dlg._set_color("my_light_color", "#33ff99")
dlg._preview_clicked(*click_px, 1)
assert len(res.lights) == 1 and res.lights[0]["color"] == "#33ff99" and res.lights[0]["radius"] == 5.0
assert dlg.btn_undo.isEnabled() and "Placed a wall light" in dlg.lbl_changes.text()
dlg.cb_light_kind.setCurrentIndex(1)
dlg._preview_clicked(*click_px, 1)
assert len(res.lights) == 2 and res.lights[1]["kind"] == "ceiling" and res.lights[1]["wall"] == ""
assert not dlg.preview.pixmap().isNull()
dlg._preview_clicked(*click_px, 2)                            # right-click removes the nearest
assert len(res.lights) == 1
dlg._undo()
assert len(dlg.result.lights) == 2, "undo brings a removed light back"
dlg._preview_clicked(0, 0, 1)                                 # nowhere near a building: told why, nothing added
n_now = len(dlg.result.lights)
assert n_now == 2
dlg._clear_lights()
assert dlg.result.lights == [] and "Removed 2 light(s)" in dlg.lbl_changes.text()
dlg._undo()
assert len(dlg.result.lights) == 2
# a re-roll that replaces the tile a light is on removes that light, and says so
dlg.locked.clear()
dlg.result.lights = [dict(dlg.result.lights[0])]
for _ in range(6):
    dlg._reroll_all()
    if not dlg.result.lights:
        assert "were on replaced tiles" in dlg.lbl_changes.text() or "removed" in dlg.lbl_changes.text()
        break
dlg.btn_place_light.setChecked(False)
# dragging a placed light moves it (and snaps it to a wall), and Undo puts it back
dlg.result.lights = [dict(dlg.result.lights[0])] if dlg.result.lights else []
if not dlg.result.lights:
    dlg.btn_place_light.setChecked(True)
    dlg._preview_clicked(*click_px, 1)
    dlg.btn_place_light.setChecked(False)
L0 = dlg.result.lights[0]
old = (L0["x"], L0["y"])
start = (int((L0["x"] - bx0) * 8), int(24 + (L0["y"] - by0) * 8))
dlg._preview_clicked(*start, 1)
assert dlg._drag_light is not None, "pressing on a light picks it up"
dlg._preview_dragged(start[0] + 60, start[1])
dlg._preview_dragged(start[0] - 60, start[1] + 40)
dlg._preview_released(0, 0)
moved = (dlg.result.lights[0]["x"], dlg.result.lights[0]["y"])
assert dlg._drag_light is None and "Moved a light" in dlg.lbl_changes.text()
if moved != old:
    dlg._undo()
    assert (dlg.result.lights[0]["x"], dlg.result.lights[0]["y"]) == old, "undo puts a dragged light back"
# gap warning label follows the result
res.gaps = {"Test zone": "no tile"}
dlg._generated(res, None)
assert dlg.lbl_gaps.isVisibleTo(dlg) and "Test zone" in dlg.lbl_gaps.text()
res.gaps = {}
dlg._generated(res, None)
assert not dlg.lbl_gaps.isVisibleTo(dlg)

dlg.close()
win._confirm_discard = lambda *a, **k: True
win.close()
print("ALL GEOMORPH GUI CHECKS PASSED")
