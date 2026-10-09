"""Library search by function: tiles are findable by what they are for, not just by file name."""
import os
import sys
import tempfile

from PyQt6.QtCore import QStandardPaths
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)

from core.asset_manager import AssetLibrary
from core.project import Project
from geomorph.registry import Registry
from ui.library import LibraryPanel

store = tempfile.mkdtemp(prefix="sceneboard-funcs-")
reg = Registry.load()
by_tag = {}
for t in reg.tiles.values():
    if t.type == "standard":
        for tag, w in t.tags.items():
            if w >= 0.9:
                by_tag.setdefault(tag, []).append(t)
medical = by_tag["medical"][0]
hangar = by_tag["hangar"][0]
cargo = by_tag["cargo"][0]


def png(rel, w=40, h=40):
    path = os.path.join(store, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor("#446688"))
    assert img.save(path)


for t in (medical, hangar, cargo):
    png("Pack/" + t.image)
png("Mine/dungeon corridor.png")             # not a geomorph tile: no function, still searchable by name

lib = AssetLibrary()
lib.scan(store)
counts = dict(lib.function_counts())
assert counts.get("medical", 0) >= 1 and counts.get("hangar", 0) >= 1 and counts.get("cargo", 0) >= 1, counts
mine = lib.get("Mine/dungeon corridor.png")
assert mine is not None and mine.functions == ()

# search finds tiles by function, by friendly alias, with several words, and still by name
assert any(a.name == os.path.basename(medical.image) for a in lib.search("medical"))
assert {a.path for a in lib.search("sick bay")} >= {a.path for a in lib.search("", function="medical")}, "alias"
assert lib.search("dungeon") == [mine]
assert mine not in lib.search("medical")
both = lib.search("medical cargo")                      # every word must match
assert all("medical" in a.functions and "cargo" in a.functions for a in both)
assert lib.search("", function="hangar") and all("hangar" in a.functions for a in lib.search("", function="hangar"))
assert lib.search("zzzz-nothing") == []

# the panel: function dropdown lists what is in the library and filters the grid
panel = LibraryPanel()
project = Project()
project.asset_store = store
panel.set_project(project, lib, None)
panel.refresh()
labels = [panel.function_filter.itemText(i) for i in range(panel.function_filter.count())]
assert labels[0] == "Any function" and any(l.startswith("Medical (") for l in labels), labels
total = len(panel.visible_assets())
panel.function_filter.setCurrentIndex(panel.function_filter.findData("medical"))
panel.refresh()
shown = panel.visible_assets()
assert shown and all("medical" in a.functions for a in shown) and len(shown) < total
panel.search.setText("hangar")                           # combined with the dropdown: nothing is both
panel.refresh()
assert all("medical" in a.functions for a in panel.visible_assets()), "the dropdown still applies"
panel.search.setText("zzzz-nothing")
panel.refresh()
assert panel.visible_assets() == [], "a filter that matches nothing shows nothing, not everything"
panel.search.setText("hangar")
assert all(("hangar" in a.name.casefold()) or "hangar" in a.functions for a in panel.visible_assets())
panel.function_filter.setCurrentIndex(0)
panel.search.setText("")
panel.refresh()
assert len(panel.visible_assets()) == total
panel._show_all_assets()
assert panel.function_filter.currentData() == ""
panel.drain_background_work()
print("ALL LIBRARY FUNCTION CHECKS PASSED")
