"""Render a small composed demo map from the sample assets (no GUI needed)."""
from __future__ import annotations

import os
import sys

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from core.asset_manager import AssetLibrary
from core.project import Project, Piece
from core import exporter

ROOT = os.path.join(os.path.dirname(__file__), "sample_assets")
OUT = os.path.join(os.path.dirname(__file__), "demo_map.png")

lib = AssetLibrary()
lib.scan(ROOT)
import os as _os
by_name = {_os.path.splitext(a.name)[0]: a for a in lib.assets}

proj = Project(name="Demo", asset_root=ROOT, cell_size=10,
                show_grid=True, grid_color="#2e6fdf", canvas_w=320, canvas_h=260)

def add(name, x, y, rot=0, op=1.0, scale=1.0, snap=True, overlay=False):
    a = by_name[name]
    p = Piece(asset_path=a.path, name=name, w=a.width or 64, h=a.height or 64,
              x=x, y=y, rotation=rot, opacity=op, scale=scale, snap=snap, is_overlay=overlay)
    proj.levels[0].add(p)
    return p

# floor room
add("room_200x100", 40, 40, snap=True)
# corridor attached
add("corridor_40x120", 250, 80, rot=90, snap=True)
# walls along the room
add("wall_10x50", 45, 45)
add("wall_10x50", 45, 100)
add("wall_25x50", 45, 155)
# props
add("terminal_25x50", 90, 70, rot=0)
add("keyboard_40x15", 90, 125)
add("computer_10x50", 160, 70)
# overlay glow on top, free placement + rotation + opacity
add("overlay_glow_100x100", 110, 90, rot=30, op=0.65, overlay=True, snap=False)

img = exporter.render_level(proj, proj.levels[0], include_grid=True, scale=2.0)
img.save(OUT, "PNG")
print("Demo map written to", OUT, f"({img.width()}x{img.height()})")
