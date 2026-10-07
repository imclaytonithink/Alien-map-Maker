"""One-window generator: sorting, seeds, strategies and applying results."""
import os
import sys
import tempfile
import time

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

from core.asset_roles import ROLE_IDS
from ui.generator_dialog import GeneratorDialog
from ui.main_window import MainWindow
from ui.sort_dialog import NEEDS_REVIEW, SortDialog

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
png("Pack/100x100 Core/B001 [100x100] Blank.png", 24 * PPS, 24 * PPS)
png("Pack/Symbols/Storage/Locker 1.png", 90, 60)
png("Pack/Symbols/Storage/Locker 2.png", 60, 90)
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

roles = lib.roles()
by_name = {a.name: roles[a.path].role for a in lib.assets}
assert by_name["E100 [100x100] Deck 0.png"] == "deck_plan"
assert by_name["E200 [50x50] Station 0.png"] == "room"
assert by_name["B001 [100x100] Blank.png"] == "empty_room"
assert by_name["Locker 1.png"] == "interior_part"
assert by_name["room_100x100.png"] == "floor_tile"
assert by_name["wall_25x50.png"] == "wall_tile"
print("roles ok")

# ---- the library shows roles and lets you change them
panel = win.library
assert "role:deck_plan" in panel._tree_items and "role:empty_room" in panel._tree_items
panel.group_tree.setCurrentItem(panel._tree_items["role:empty_room"])
assert panel._view == ("role", "empty_room") and panel.list.count() == 1
locker = next(a for a in lib.assets if a.name == "Locker 1.png")
panel.set_roles([locker.path], "symbol")
assert lib.roles()[locker.path].role == "symbol" and lib.roles()[locker.path].overridden
assert "(1)" in panel._tree_items["role:symbol"].text(0)
assert os.path.isfile(os.path.join(store, ".asset_roles.json"))
panel.set_roles([locker.path], None)                       # back to automatic
assert lib.roles()[locker.path].role == "interior_part"
assert not os.path.exists(os.path.join(store, ".asset_roles.json"))
print("library roles ok")

# ---- the sorter: filter, select, reassign
sorter = SortDialog(lib, on_changed=panel.refresh_roles)
sorter.show(); app.processEvents()
assert sorter.proxy.rowCount() == len(lib.assets)
sorter._select_role("empty_room")
assert sorter.proxy.rowCount() == 1
sorter._select_role("__all__")
sorter.search.setText("station")
assert sorter.proxy.rowCount() == 2
sorter.table.selectAll()
sorter.cmb_role.setCurrentIndex(ROLE_IDS.index("empty_room"))
sorter._apply_role()
assert sum(info.role == "empty_room" for info in lib.roles().values()) == 3
assert all(lib.roles()[a.path].overridden for a in lib.assets if "Station" in a.name)
sorter.table.selectAll()
sorter._reset_role()
assert sum(info.role == "empty_room" for info in lib.roles().values()) == 1
sorter._select_role(NEEDS_REVIEW)
sorter.close()
print("sorter ok")

# ---- the dialog: one window, seeds, readiness
opened = []
dlg = GeneratorDialog(win.project, panel, win.canvas, win._run_generator, win)
dlg.show(); app.processEvents()
assert "Ready" in dlg.strategy_status["assembly"].text()
assert "Ready" in dlg.strategy_status["tiles"].text()
assert "Ready" in dlg.strategy_status["furnish"].text()
first = dlg.edit_seed.text()
assert len(first) == 12 and first.isdigit()
dlg._reroll()
assert dlg.edit_seed.text() != first and len(dlg.edit_seed.text()) == 12
dlg.edit_seed.setText("hangar-7")
assert dlg._opts()["seed"] == "hangar-7"
dlg.edit_seed.setText("")
assert len(dlg._opts()["seed"]) == 12                       # blank becomes a random seed
dlg.chk_keep_seed.setChecked(True)
kept = dlg.edit_seed.text()
dlg._reroll_for_regenerate()
assert dlg.edit_seed.text() == kept                         # kept on request
dlg.chk_keep_seed.setChecked(False)
dlg._reroll_for_regenerate()
assert dlg.edit_seed.text() != kept
dlg.cmb_preset.setCurrentIndex(0)
assert (dlg.spin_cols.value(), dlg.spin_rows.value()) == (30, 30)
dlg.spin_cols.setValue(77)
assert dlg.cmb_preset.currentData() is None                  # "Custom"
print("dialog basics ok")

# ---- assembly: new level, replace, canvas grows
dlg.cmb_preset.setCurrentIndex(1)                            # 60 x 60
dlg.sl_symbols.setValue(0)
levels = len(win.project.levels)
dlg.edit_seed.setText("111111111111")
assert dlg._generate()
assert len(win.project.levels) == levels + 1
level = win.project.levels[-1]
assert len(level.pieces) >= 4 and level.name.startswith("Assembly")
first_paths = [(p.asset_path, round(p.x), round(p.y)) for p in level.pieces]
assert dlg._generate()                                       # same seed: identical, kept
assert len(win.project.levels) == levels + 2
assert [(p.asset_path, round(p.x), round(p.y))
        for p in win.project.levels[-1].pieces] == first_paths
dlg.chk_keep_seed.setChecked(False)
before_levels = len(win.project.levels)
assert dlg._regenerate(), dlg.lbl_status.text()
assert len(win.project.levels) == before_levels - 1     # both earlier results replaced by one new level
assert win.history.can_undo()
print("assembly ok")

# ---- big maps grow the canvas
dlg.cmb_preset.setCurrentIndex(3)                            # 160 x 160
before_cols = win.project.map_cols
assert dlg._generate()
assert win.project.map_cols >= 160 and win.project.map_cols >= before_cols
print("canvas growth ok")

# ---- role checkboxes feed the pool
dlg.cmb_preset.setCurrentIndex(1)
dlg.role_checks["deck_plan"].setChecked(False)
dlg.role_checks["empty_room"].setChecked(False)
dlg.edit_seed.setText("222222222222")
assert dlg._generate()
placed = [p.name for p in win.canvas.level.pieces]
assert placed and all("Station" in name for name in placed), placed[:3]
dlg.role_checks["room"].setChecked(False)
assert dlg._generate() is False and "Tick at least one" in dlg.lbl_status.text()
dlg.role_checks["deck_plan"].setChecked(True)
dlg.role_checks["room"].setChecked(True)
print("ingredient toggles ok")

# ---- tiles strategy
dlg.strategy_radios["tiles"].setChecked(True); app.processEvents()
dlg.spin_cols.setValue(30); dlg.spin_rows.setValue(30)
dlg.edit_seed.setText("333333333333")
levels = len(win.project.levels)
assert dlg._generate()
assert len(win.project.levels) == levels + 1 and win.canvas.level.pieces
print("tiles ok")

# ---- furnish: needs rooms, then props go inside them
dlg.strategy_radios["furnish"].setChecked(True); app.processEvents()
win.canvas.clear_selection()
assert dlg._generate() is False and "Select" in dlg.lbl_status.text()
dlg.cmb_scope.setCurrentIndex(1)                             # every empty room on this level
win.canvas.set_level(len(win.project.levels) - 1)
assert dlg._generate() is False                              # tiles level has no empty room nodes
blank = next(a for a in lib.assets if "Blank" in a.name)
win.canvas.set_level(0)
level0 = win.canvas.level
level0.pieces.clear()
win.canvas.add_asset(blank.path, 1200, 1200)
win.canvas.tighten_selected  # (placement trims asynchronously; furnish uses the node bounds)
dlg.edit_seed.setText("444444444444")
dlg.sl_furnish.setValue(80)
n_before = len(level0.pieces)
assert dlg._generate()
props = [p for p in level0.pieces if p is not level0.pieces[0] and "Locker" in p.name]
assert props, "furniture should be placed in the empty room"
room_box = win.canvas._aabb(level0.pieces[0])
for p in props:
    cx, cy = p.center
    assert room_box[0] <= cx <= room_box[2] and room_box[1] <= cy <= room_box[3]
count_one = len(props)
assert dlg._regenerate()                                     # replaces the previous furniture
props2 = [p for p in level0.pieces if "Locker" in p.name]
assert 0 < len(props2) <= count_one * 3 and len(level0.pieces) - 1 == len(props2)
print("furnish ok")

# ---- unsorted advice and the sorter button
assert dlg.lbl_unsorted.text()
dlg.close()
print("ALL GENERATOR TESTS PASSED")
