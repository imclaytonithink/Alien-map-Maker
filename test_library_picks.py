"""Library picking, de-duplication, unreadable/empty omission and removal.

The generator is fed by what you tick in the library - individual pictures or
whole folders. These checks cover the rules the library promises on top of the
pack's own folder layout:

* only one copy of a name is shown (duplicates are folded away, not re-filed);
* files that are not readable images are omitted during import/install;
* empty folders never appear in the tree;
* ticking a folder picks everything inside it;
* tiles (and whole folders) can be removed from the pool again;
* the automatic map size always fits large rooms.
"""
import os
import sys
import tempfile

from PyQt6.QtCore import QStandardPaths, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)

from core.asset_manager import AssetLibrary
from core.mapbuilder import build_map, make_item, suggest_region
from core.project import Project
from ui.library import LibraryPanel

store = tempfile.mkdtemp(prefix="sceneboard-picks-")


def png(rel, w, h, color="#446688"):
    path = os.path.join(store, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image = QImage(w, h, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    assert image.save(path)


png("A/room.png", 100, 100)
png("A/prop.png", 40, 40)
png("B/room.png", 100, 100)                 # same name and size as A/room.png: a duplicate
png("D/prop.png", 80, 80)                   # same name as A/prop.png, other size: kept
os.makedirs(os.path.join(store, "empty"), exist_ok=True)   # empty folder
with open(os.path.join(store, "bad.png"), "wb") as fh:
    fh.write(b"not an image at all")        # unreadable, must be omitted
png("C/big [100x100].png", 480, 480)        # a 100 ft room at 20 squares

library = AssetLibrary()
library.scan(store)

# ---- one copy of a name, unreadable and empty folders omitted -------------
assert library.get("A/room.png") is not None
assert library.get("B/room.png") is None, "a verified duplicate shows one copy"
assert library.get("D/prop.png") is not None, \
    "same name but a different file size is different art and stays visible"
assert len(library.hidden_duplicates) == 1, library.hidden_duplicates
# the duplicates remain reachable for checking
assert library.set_show_duplicates(True)
shown = library.get("B/room.png")
assert shown is not None and shown.duplicate_of == "A/room.png" \
    and "duplicate" in shown.tags, shown
assert "B" in library.groups()
assert library.set_show_duplicates(False)
assert library.get("B/room.png") is None and "B" not in library.groups()
assert library.skipped_unreadable == 1, library.skipped_unreadable
assert library.get("bad.png") is None, "unreadable images are left out"
assert "B" not in library.groups(), library.groups()
assert "empty" not in library.groups(), library.groups()
assert "C" in library.groups()
print("dedup / omission ok")

project = Project(name="picks", asset_store=store, cell_size=10,
                  map_cols=30, map_rows=30)
panel = LibraryPanel()
panel.set_project(project, library, None)
app.processEvents()

# the tree never lists the empty folder nor the duplicate-only folder
assert "folder:B" not in panel._tree_items
assert "folder:empty" not in panel._tree_items
assert "folder:A" in panel._tree_items and "folder:C" in panel._tree_items
panel.act_dupes.setChecked(True)
assert "folder:B" in panel._tree_items, "duplicates can be browsed on request"
panel.act_dupes.setChecked(False)
assert "folder:B" not in panel._tree_items

# ---- ticking individual assets --------------------------------------------
model = panel.list.asset_model
row = next(i for i, a in enumerate(model.assets) if a.path == "A/prop.png")
model.setData(model.index(row, 0), Qt.CheckState.Checked,
              Qt.ItemDataRole.CheckStateRole)
assert panel.selected_paths() == ["A/prop.png"], panel.selected_paths()

# ---- ticking a folder picks everything in it ------------------------------
panel.clear_picks()
assert panel.selected_paths() == []
folder_item = panel._tree_items["folder:A"]
folder_item.setCheckState(0, Qt.CheckState.Checked)
assert panel.selected_paths() == ["A/prop.png", "A/room.png"], \
    panel.selected_paths()
assert folder_item.checkState(0) == Qt.CheckState.Checked
# a partially-ticked folder shows the partial state
panel._on_pick_toggled("A/prop.png", False)
assert panel._tree_items["folder:A"].checkState(0) == \
    Qt.CheckState.PartiallyChecked
panel.clear_picks()
assert panel._tree_items["folder:A"].checkState(0) == Qt.CheckState.Unchecked
print("picking ok")

# ---- removing tiles from the pool -----------------------------------------
assert library.remove_paths(["A/prop.png"]) == 1
assert library.get("A/prop.png") is None
assert not os.path.exists(os.path.join(store, "A", "prop.png"))
# removing the shown copy of a name reveals the folded-away copy
assert library.remove_paths(["A/room.png"]) == 1
assert library.get("B/room.png") is not None, \
    "the hidden duplicate becomes visible once the shown copy is removed"
print("removal ok")

# ---- automatic size always fits large rooms -------------------------------
big = library.get("C/big [100x100].png")
assert big is not None
item = make_item(big, 10, 5)
assert item["cells_w"] == 20 and item["cells_h"] == 20, item
# a fixed 10x10 area is too small for a 20x20 room -> skipped
small = build_map({"selection": [big], "cell_size": 10, "feet_per_square": 5,
                   "region": (0, 0, 9, 9), "layout": "grid", "seed": 1})
assert small["counts"]["pieces"] == 0, small["counts"]
# the suggested region is big enough, so the room always lands
region = suggest_region({"selection": [big], "cell_size": 10,
                         "feet_per_square": 5, "layout": "grid"})
auto = build_map({"selection": [big], "cell_size": 10, "feet_per_square": 5,
                  "region": region, "layout": "grid", "seed": 1})
assert auto["counts"]["pieces"] == 1, auto["counts"]
print("auto-size ok")

print("\nLIBRARY PICK TESTS PASSED")
