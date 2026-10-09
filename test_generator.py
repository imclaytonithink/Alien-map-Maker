"""Headless test of the selection-driven map generator + placement."""
import os
import sys
import tempfile

from sample_fixtures import sample_assets_dir
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox
QMessageBox.information = lambda *a, **k: None  # avoid blocking in headless
QMessageBox.warning = lambda *a, **k: None
app = QApplication.instance() or QApplication(sys.argv)

import ui.launch_screen as ls
ls.LaunchScreen.exec = lambda self: (setattr(self, "result_action", ("new", None)) or 1)

from ui.generator_dialog import GeneratorDialog, ensure_sizes
from ui.main_window import MainWindow
from core import mapbuilder

win = MainWindow()

# import sample assets
sample = sample_assets_dir()
win.project.asset_store = tempfile.mkdtemp(prefix="sceneboard-store-")  # not the default store
win._ensure_store()
win.library.library.root = win.project.asset_store
win.library.library.scan(win.project.asset_store)
n = win.library.library.import_folder(sample)
win.library.set_project(win.project, win.library.library, win._add_at_center)
print("imported:", n)

assets = win.library.library.assets
ensure_sizes(assets, win.library.library)
assert assets, "the sample pack should have imported"

# ---- the library only offers the store's own folder structure -------------
tokens = set()
for row in range(win.library.group_tree.topLevelItemCount()):
    item = win.library.group_tree.topLevelItem(row)
    tokens.add(item.data(0, Qt.ItemDataRole.UserRole))
assert tokens == {"all", "folder-root"}, tokens
tree_tokens = set(win.library._tree_items)
assert not any(t.startswith(("category", "role", "smart")) for t in tree_tokens), \
    "smart categories and generator roles are gone"
assert "folder:floors" in tree_tokens and "folder:walls" in tree_tokens, \
    "the folders the assets arrived in are the whole organisation"

# ---- pure builder: only the assets you hand it are used -------------------
picked = [a for a in assets if a.folder.replace("\\", "/").endswith("floors")]
assert picked, "the sample pack has a floors folder"
base = {"selection": picked, "cell_size": 70, "feet_per_square": 5,
        "region": (0, 0, 39, 39), "seed": 7}
for layout in mapbuilder.LAYOUTS:
    res = mapbuilder.build_map(dict(base, layout=layout, copies=3, gap=1,
                                    rotate=True))
    used = {p["asset_path"] for p in res["pieces"]}
    assert res["pieces"], f"{layout} produced nothing"
    assert used <= {a.path for a in picked}, \
        f"{layout} used an asset that was not selected"
    print(f"{layout}: {res['counts']['pieces']} pieces from "
          f"{res['counts']['assets']} assets, covers {res['used_cells']}")

# an empty selection is refused rather than quietly inventing a map
empty = mapbuilder.build_map(dict(base, selection=[]))
assert not empty["pieces"] and empty["warnings"], empty

# the same seed + selection + settings always builds the same map
again = mapbuilder.build_map(dict(base, layout="scatter", rotate=True))
first = mapbuilder.build_map(dict(base, layout="scatter", rotate=True))
key = lambda r: [(p["asset_path"], round(p["x"], 4), round(p["y"], 4), p["rotation"])
                 for p in r["pieces"]]
assert key(again) == key(first), "the seed should make a build reproducible"
other = mapbuilder.build_map(dict(base, layout="scatter", rotate=True, seed=8))
assert key(other) != key(first), "a different seed should build differently"

# every placement's visible box starts exactly on a grid line, and no two
# pieces overlap
cell = 70
boxes = []
for p in first["pieces"] + mapbuilder.build_map(dict(base, layout="grid",
                                                    copies=3))["pieces"]:
    box = p["_box"]
    assert abs(box[0] / cell - round(box[0] / cell)) < 1e-6, p
    assert abs(box[1] / cell - round(box[1] / cell)) < 1e-6, p
    boxes.append(box)
for layout in ("grid", "scatter"):
    placed = mapbuilder.build_map(dict(base, layout=layout, copies=4,
                                       gap=0))["pieces"]
    rects = [p["_box"] for p in placed]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            assert (a[0] + a[2] <= b[0] + 1e-6 or b[0] + b[2] <= a[0] + 1e-6
                    or a[1] + a[3] <= b[1] + 1e-6 or b[1] + b[3] <= a[1] + 1e-6), \
                f"{layout} overlapped two pieces"

# ---- border, mirror flips and layer name are honoured ---------------------
margined = mapbuilder.build_map(dict(base, layout="grid", copies=2, margin=3))
assert margined["pieces"]
for p in margined["pieces"]:
    assert p["_box"][0] >= 3 * cell and p["_box"][1] >= 3 * cell, \
        ("empty border not kept", p)
assert not any(p["flip_h"] or p["flip_v"] for p in margined["pieces"])
flipped = mapbuilder.build_map(dict(base, layout="scatter", copies=3,
                                    flips=True, seed=11))
assert any(p["flip_h"] or p["flip_v"] for p in flipped["pieces"]), \
    "flips requested but none applied"
layered = mapbuilder.build_map(dict(base, layout="grid", layer_name="Rooms"))
assert all(p["layer_name"] == "Rooms" for p in layered["pieces"])

# ---- through the app: build a new level from the selection -----------------
win.library.select_paths([a.path for a in picked])
assert win.library.selected_paths() == [a.path for a in picked]

dlg = GeneratorDialog(win.project, win.library, win.canvas, win._run_generator, win)
assert len(dlg.assets) == len(picked), "the dialog reads the library selection"
dlg.cmb_layout.setCurrentIndex(0)
dlg.spin_copies.setValue(2)
dlg.spin_cols.setValue(40)
dlg.spin_rows.setValue(40)
assert dlg._generate(), dlg.lbl_status.text()

lvl = win.project.levels[-1]
print("levels after gen:", len(win.project.levels),
      "pieces on new level:", len(lvl.pieces))
assert len(lvl.pieces) == 2 * len(picked), lvl.pieces
assert {p.asset_path for p in lvl.pieces} <= {a.path for a in picked}
assert any(layer.name == "Generated" for layer in lvl.layers)

# ---- fill the selected nodes' area on the current level --------------------
win.canvas.select(lvl.pieces[:2])
area_opts = dict(dlg._opts(), mode="area",
                 selection=[picked[0]])
before = len(lvl.pieces)
win._run_generator(area_opts)
print("pieces after area build:", len(lvl.pieces), "(was", before, ")")
assert len(lvl.pieces) > before

# ---- Generate keeps earlier outputs; Regenerate replaces tracked output -----
tracked = dict(dlg._opts(), mode="new", replace_prev=False)
levels_before = len(win.project.levels)
kept_levels = len(win._gen_output["new"]["levels"])   # from the dialog build above
win._run_generator(tracked)
win._run_generator(tracked)
record = win._gen_output["new"]
assert len(record["levels"]) == kept_levels + 2, record["levels"]
assert len(win.project.levels) == levels_before + 2
previous_ids = set(record["ids"])

original_build = mapbuilder.build_map


def fail_generation(_opts):
    raise RuntimeError("simulated generator failure")


mapbuilder.build_map = fail_generation
try:
    try:
        win._run_generator(dict(tracked, replace_prev=True, seed=8))
    except RuntimeError:
        pass
    else:
        raise AssertionError("generator failure was not propagated")
finally:
    mapbuilder.build_map = original_build
assert all(level in win.project.levels for level in record["levels"])
remaining_ids = {piece.id for level in win.project.levels
                 for piece in level.pieces}
assert previous_ids <= remaining_ids, \
    "failed regeneration should preserve the previous output"

win._run_generator(dict(tracked, replace_prev=True, seed=8))
assert len(win.project.levels) == levels_before + 1 - kept_levels
assert len(win._gen_output["new"]["levels"]) == 1
assert not previous_ids.intersection(
    piece.id for level in win.project.levels for piece in level.pieces)

# the customisation controls reach the placed pieces
dlg.edit_layer.setText("Rooms A")
dlg.chk_flip.setChecked(True)
dlg.spin_margin.setValue(2)
assert dlg._generate(), dlg.lbl_status.text()
lvl2 = win.project.levels[-1]
assert any(layer.name == "Rooms A" for layer in lvl2.layers), \
    [layer.name for layer in lvl2.layers]
assert any(p.flip_h or p.flip_v for p in lvl2.pieces), \
    "mirror flips requested but none placed"
for p in lvl2.pieces:
    assert p.x >= 0, p

# an empty selection is refused by the window as well as by the builder
assert win._run_generator(dict(tracked, selection=[])) is None

print("\nGENERATOR TESTS PASSED")
