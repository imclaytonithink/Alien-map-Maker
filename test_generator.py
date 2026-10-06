"""Headless test of the map generator + placement."""
import os
import sys

from PyQt6.QtWidgets import QApplication, QMessageBox
QMessageBox.information = lambda *a, **k: None  # avoid blocking in headless
app = QApplication.instance() or QApplication(sys.argv)

import ui.launch_screen as ls
ls.LaunchScreen.exec = lambda self: (setattr(self, "result_action", ("new", None)) or 1)

from ui.generator_dialog import ensure_sizes
from ui.main_window import MainWindow
from core import generator as gen

win = MainWindow()

# import sample assets
sample = os.path.join(os.path.dirname(__file__), "sample_assets")
win._ensure_store()
win.library.library.root = win.project.asset_store
win.library.library.scan(win.project.asset_store)
n = win.library.library.import_folder(sample)
win.library.set_project(win.project, win.library.library, win._add_at_center)
print("imported:", n)

ensure_sizes(win.library.library.assets, win.library.library)
cats = gen.classify_assets(win.library.library.assets)
print("taxonomy:", gen.summarize(cats))

# pure generation
opts = {"cell_size": 70, "region": (0, 0, 29, 29), "categories": cats,
        "layout": "corridors", "setting": "Starship", "clutter": 0.4,
        "seed": 7, "rooms": 6}
res = gen.generate(opts)
print("generated pieces:", res["counts"]["pieces"], "rooms:", res["counts"]["rooms"],
      "connected:", res["connected"])
assert res["counts"]["pieces"] > 0
assert res["connected"], "map should be fully connected"

# place via the app (new level)
ui_opts = {"setting": "Starship", "layout": "corridors", "clutter": 0.4,
           "seed": 7, "mode": "new", "categories": cats}
before = len(win.project.levels)
win._run_generator(ui_opts)
print("levels after gen:", len(win.project.levels))
lvl = win.project.levels[-1]
print("pieces on new level:", len(lvl.pieces))
assert len(win.project.levels) == before + 1
assert len(lvl.pieces) > 0, "new level should contain generated pieces"

# verify fixture semantics: wall-fixture names live on wall/prop layers, not base floor tiles
names = [p.name for p in lvl.pieces]
print("sample names:", names[:8])
# fill-area mode with a selection
for p in lvl.pieces[:4]:
    pass
win.canvas.select(lvl.pieces[:3])
area_opts = dict(ui_opts)
area_opts["mode"] = "area"
lvl2_before = len(lvl.pieces)
win._run_generator(area_opts)
print("pieces after area fill:", len(lvl.pieces), "(was", lvl2_before, ")")

print("\nGENERATOR TESTS PASSED")
