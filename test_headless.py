"""Headless smoke test: scan assets, place pieces, export a PNG.

Run with:  QT_QPA_PLATFORM=offscreen python3 test_headless.py
"""
from __future__ import annotations

import os
import sys

from sample_fixtures import sample_assets_dir
from PyQt6.QtWidgets import QApplication

# ensure a QApplication exists before any QPixmap use
app = QApplication.instance() or QApplication(sys.argv)

from core.asset_manager import AssetLibrary
from core.project import Project, Piece
from ui.canvas import CanvasView
from core import exporter

ROOT = sample_assets_dir()
OUT = os.path.join(os.path.dirname(__file__), "test_output.png")

# 1) scan + size parsing
lib = AssetLibrary()
lib.scan(ROOT)
print(f"[scan] {len(lib.assets)} assets, {len(lib.groups())} groups")
assert len(lib.assets) >= 8, "expected several sample assets"
sized = [a for a in lib.assets if a.size]
print(f"[scan] {len(sized)} assets had parseable WxH in name")
assert any(a.size == (40, 120) for a in lib.assets), "size parse failed"

# 2) project + canvas place
proj = Project(name="Test Map", asset_store=ROOT, cell_size=10,
               show_grid=True, grid_color="#3a7bd5", canvas_w=400, canvas_h=400)
canvas = CanvasView()
canvas.set_project(proj, lib)
canvas.resize(600, 600)

# add a few assets at world coords
paths = [a.path for a in lib.assets]
p1 = canvas.add_asset(paths[0], 50, 50)      # snaps
p2 = canvas.add_asset(paths[1], 120, 60)
p3 = canvas.add_asset(paths[-2], 80, 200)    # overlay (free, no snap)
p3.rotation = 45
p3.opacity = 0.7
print(f"[place] pieces on level: {len(proj.levels[0].pieces)}")

# add a second level and export all
proj.add_level("Level 2")
lv2 = proj.levels[1]
lv2.add(Piece(asset_path=paths[2], name="wall", w=10, h=50,
              x=30, y=30, is_overlay=False))

# 3) export
img = exporter.render_level(proj, proj.levels[0], include_grid=True, scale=1.0)
img.save(OUT, "PNG")
print(f"[export] wrote {OUT} ({img.width()}x{img.height()})")
assert os.path.getsize(OUT) > 1000, "exported PNG looks empty"

files = exporter.export_all_levels(proj, os.path.dirname(OUT), include_grid=False, scale=2.0, name_prefix="testmap")
print(f"[export] all-levels: {files}")

# 4) serialize round-trip
import json
d = json.loads(json.dumps(proj.to_dict()))
proj2 = Project.from_dict(d)
assert len(proj2.levels) == 2
print("[serialize] project round-trip OK")

# 5) scaled geometry: Piece.x/y must be the visual top-left
sp = Piece(name="scaled", x=100, y=100, w=200, h=100, scale=0.5)
assert sp.center == (150, 125), sp.center
assert sp.vis_w == 100 and sp.vis_h == 50
print("[geometry] scaled center/vis size OK")

print("\nALL HEADLESS TESTS PASSED")
