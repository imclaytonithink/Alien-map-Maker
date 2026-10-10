"""The map generator window: selection, layouts, seeds and applying results.

The generator has no automatic sorting to review any more - it builds from
exactly the assets selected in the library, so these checks follow that path:
selecting, reading the selection into the dialog, building a level, and
replacing the previous result on Regenerate.
"""
import os
import sys
import tempfile

from PyQt6.QtCore import QStandardPaths, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)

import ui.launch_screen as ls


def fake_exec(self):
    self.result_action = ("new", None)
    return 1


ls.LaunchScreen.exec = fake_exec

from ui.generator_dialog import GeneratorDialog
from ui.main_window import MainWindow

# A recovery copy left behind by an interrupted run must not block the suite
# with its modal "restore it?" prompt.
MainWindow._offer_recovery = lambda self: self.overlay.open_menu()

store = tempfile.mkdtemp(prefix="sceneboard-gen-")


def png(rel, w, h, color="#446688"):
    path = os.path.join(store, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image = QImage(w, h, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    assert image.save(path)


PPS = 60                           # source pixels per 5-ft square in these fixtures
for i in range(3):
    png(f"Pack/100x100 Core/E10{i} [100x100] Deck {i}.png", 24 * PPS, 24 * PPS)
for i in range(2):
    png(f"Pack/50x50 Core/E20{i} [50x50] Station {i}.png", 14 * PPS, 14 * PPS)
png("Pack/Symbols/Storage/Locker 1.png", 90, 60)
png("tiles/room_100x100.png", 100, 100)
png("tiles/wall_25x50.png", 25, 50)
png("tiles/door_40x40.png", 40, 40)

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

panel = win.library
decks = [a for a in lib.assets if "Deck" in a.name]
stations = [a for a in lib.assets if "Station" in a.name]
tiles = [a for a in lib.assets if a.folder == "tiles"]
assert len(decks) == 3 and len(stations) == 2 and len(tiles) == 3

# ---- the library's only organisation is the folders the pack arrived in ----
assert panel.group_tree.topLevelItemCount() == 2          # All assets + Folders
for token in panel._tree_items:
    assert token == "all" or token.startswith("folder"), token
assert "folder:Pack/100x100 Core" in panel._tree_items
assert "folder:Pack/Symbols/Storage" in panel._tree_items
assert "folder:tiles" in panel._tree_items
assert not os.path.exists(os.path.join(store, ".asset_roles.json"))
assert not hasattr(lib, "roles") and not hasattr(lib, "set_role")
panel.group_tree.setCurrentItem(panel._tree_items["folder:tiles"])
assert panel._view == ("folder", "tiles")
assert panel.list.count() == 3
print("library folders ok")

# ---- selecting assets is what feeds the generator -------------------------
panel._show_all_assets()
panel.refresh()
assert panel.list.count() == len(lib.assets)
assert panel.select_paths([a.path for a in decks]) == 3
assert panel.selected_paths() == [a.path for a in decks]
assert [a.name for a in panel.selected_assets()] == [a.name for a in decks]

# ---- the dialog reads that selection -------------------------------------
dlg = GeneratorDialog(win.project, panel, win.canvas, win._run_generator, win)
dlg.show(); app.processEvents()
assert [a.path for a in dlg.assets] == [a.path for a in decks]
assert dlg.selection_list.count() == 3
assert "3 asset(s) selected" in dlg.lbl_selection.text()
# a 100 ft deck is 20 squares at 5 ft per square
assert "20 × 20 squares" in dlg.selection_list.item(0).text(), \
    dlg.selection_list.item(0).text()

# the buttons re-read the library
panel.group_tree.setCurrentItem(panel._tree_items["folder:tiles"])
panel.select_paths([a.path for a in tiles])
dlg.btn_refresh.click()
assert [a.path for a in dlg.assets] == [a.path for a in tiles]
panel.select_paths([decks[0].path])
dlg.btn_all.click()                                   # everything the list shows
assert len(dlg.assets) == 3, "Use everything shown takes the whole folder"

# nothing selected -> a clear refusal, not a invented map
dlg.set_selection([])
assert dlg._generate() is False
assert "No assets are ticked" in dlg.lbl_status.text()
print("dialog selection ok")

# ---- build a new level from the selection --------------------------------
dlg.set_selection(decks + stations)
dlg.cmb_layout.setCurrentIndex(0)                     # tidy rows
dlg.spin_copies.setValue(1)
dlg.spin_cols.setValue(80)
dlg.spin_rows.setValue(80)
dlg.cmb_mode.setCurrentIndex(0)
seed_before = dlg.edit_seed.text()
levels_before = len(win.project.levels)
assert dlg._generate(), dlg.lbl_status.text()
level = win.project.levels[-1]
assert len(win.project.levels) == levels_before + 1
assert len(level.pieces) == 5, level.pieces
assert {p.asset_path for p in level.pieces} == {a.path for a in decks + stations}
assert any(layer.name == "Generated" for layer in level.layers)
assert "5 nodes from 5 asset(s)" in dlg.lbl_status.text(), dlg.lbl_status.text()
assert seed_before in dlg.lbl_status.text()
# undo takes the whole build back in one step
assert len(win.project.levels) == levels_before + 1
win.undo()
assert len(win.project.levels) == levels_before, "one Generate is one undo step"
win.redo()
assert len(win.project.levels) == levels_before + 1
assert len(win.project.levels[-1].pieces) == 5
win._gen_output = None                  # start the rebuild checks from scratch
print("build a level ok")

# ---- the same seed rebuilds the same map --------------------------------
snapshot = sorted((p.asset_path, round(p.x, 3), round(p.y, 3), p.rotation)
                  for p in win.project.levels[-1].pieces)
dlg.chk_keep_seed.setChecked(True)
assert dlg._regenerate()
rebuilt = sorted((p.asset_path, round(p.x, 3), round(p.y, 3), p.rotation)
                 for p in win.project.levels[-1].pieces)
assert rebuilt == snapshot, "the same seed and selection must rebuild the same map"
print("seeded rebuild ok")

# ---- Regenerate replaces the previous output -----------------------------
dlg.chk_keep_seed.setChecked(False)
previous_ids = set(win._gen_output["new"]["ids"])
levels_now = len(win.project.levels)
assert dlg._regenerate()
assert len(win.project.levels) == levels_now, "Regenerate replaces, it does not add"
assert not previous_ids.intersection(
    p.id for lv in win.project.levels for p in lv.pieces), \
    "Regenerate should replace the previous build"

# a failed build leaves the map that is already there
from core import mapbuilder
original = mapbuilder.build_map


def boom(_opts):
    raise RuntimeError("boom")


mapbuilder.build_map = boom
try:
    assert dlg._generate() is False
    assert "Generation failed" in dlg.lbl_status.text()
finally:
    mapbuilder.build_map = original
print("regenerate ok")

# ---- filling the selected nodes' area -----------------------------------
dlg.set_selection(tiles)
dlg.cmb_layout.setCurrentIndex(2)                     # fill the area
level = win.canvas.level
win.canvas.select(level.pieces[:1])
before = len(level.pieces)
dlg.cmb_mode.setCurrentIndex(1)
assert dlg._generate(), dlg.lbl_status.text()
assert len(win.canvas.level.pieces) > before, "fill should add nodes in the selection"
# with nothing selected on the canvas the dialog says so instead
win.canvas.clear_selection()
assert dlg._generate() is False and "Select the nodes" in dlg.lbl_status.text()
print("area fill ok")

# ---- the library's right-click entry point ------------------------------
# it passes the paths straight through, so it works from a filtered folder too
panel.group_tree.setCurrentItem(panel._tree_items["folder:Pack/50x50 Core"])
assert panel.list.count() == 2
opened = []
from ui.generate_window import OWN, GenerateWindow
real_exec = GenerateWindow.exec
GenerateWindow.exec = lambda self: (opened.append(self), 1)[1]
try:
    win._generate_from_paths([a.path for a in stations])
finally:
    GenerateWindow.exec = real_exec
assert len(opened) == 1
assert opened[0].tabs.currentIndex() == OWN, "right-click opens the 'Your own tiles' tab"
assert [a.path for a in opened[0].own.assets] == [a.path for a in stations]
print("right-click generate ok")

dlg.close()
print("\nGENERATOR DIALOG TESTS PASSED")
