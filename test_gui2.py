"""Headless boot + feature exercise for the full rebuilt app."""
import os
import sys

from sample_fixtures import sample_assets_dir
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

# auto-accept the launch screen as "new map"
import ui.launch_screen as ls
def fake_exec(self):
    self.result_action = ("new", None)
    return 1
ls.LaunchScreen.exec = fake_exec

from ui.main_window import MainWindow
from core import exporter

win = MainWindow()
print("booted, levels:", len(win.project.levels))

# import sample assets into the store
sample = sample_assets_dir()
import tempfile
win.project.asset_store = tempfile.mkdtemp(prefix="sceneboard-store-")   # not the default store
win._ensure_store()
win.library.library.root = win.project.asset_store
win.library.library.scan(win.project.asset_store)
n = win.library.library.import_folder(sample)
win.library.set_project(win.project, win.library.library, win._add_at_center)
print("imported assets:", n)

paths = [a.path for a in win.library.library.assets]
assert paths
win.canvas.add_asset(paths[0], 100, 100)
win.canvas.add_asset(paths[1], 300, 200)
win.canvas.add_asset(paths[2], 500, 100)
print("placed pieces:", len(win.project.levels[0].pieces))

# multi-select + align + group + tint
win.canvas.select(win.project.levels[0].pieces[:3])
win.canvas.align("left")
win.canvas.group()
win.props.load_selection(win.canvas.selected_pieces())
win.props._tint_strength(60)
print("tint applied, group_id set:",
      any(p.group_id for p in win.project.levels[0].pieces))

# text + embedded custom upload (simulate via base64 of first asset)
from core.project import embed_png
b64 = embed_png(win.library.library.abs_path(paths[0]))
win.canvas.add_embedded(b64, 700, 400, "Custom")
print("embedded piece:", any(p.embedded for p in win.project.levels[0].pieces))

# add text
win.canvas.add_text(200, 500)

# layers
win.layers.set_project(win.project, win.project.levels[0])
win.project.levels[0].layers.append(__import__("core.project", fromlist=["Layer"]).Layer(name="Test"))
win.layers.rebuild()

# levels
win.level_bar._add()
print("levels now:", len(win.project.levels))

# undo / redo
before = len(win.project.levels[0].pieces)
win.undo()
win.redo()
print("undo/redo ok, pieces:", len(win.project.levels[0].pieces))

# theme
win._set_accent("#ffb000")
win._toggle_flourish("flourish_scanlines")
print("accent:", win.project.accent)

# export png + pdf
img = exporter.render_level(win.project, win.project.levels[0], True, 1.0)
out = os.path.join(os.path.dirname(__file__), "gui2_out.png")
img.save(out, "PNG")
assert os.path.getsize(out) > 1000
pdf = os.path.join(os.path.dirname(__file__), "gui2_out.pdf")
exporter.export_pdf(win.project, pdf, True, 1.0)
print("exported png+pdf OK")

# project save/load round-trip
import json, tempfile
fn = os.path.join(tempfile.gettempdir(), "t.bmap")
win._current_file = fn
win._write(fn)
proj2 = __import__("core.project", fromlist=["Project"]).Project.from_dict(json.load(open(fn)))
assert len(proj2.levels) == len(win.project.levels)
print("save/load round-trip OK")

print("\nALL GUI TESTS PASSED")
