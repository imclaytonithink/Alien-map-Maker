"""Render a composed demo map from the checked-in sample assets.

Run with ``QT_QPA_PLATFORM=offscreen python demo_render.py``. The PNG defaults
outside the repository; pass ``--output`` to choose another destination.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

from sample_fixtures import sample_assets_dir
from PyQt6.QtWidgets import QApplication

from core.asset_manager import AssetLibrary
from core.project import Piece, Project
from core import exporter


def render_demo(output_path: str | None = None) -> str:
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app  # retain the wrapper until QImage/QPixmap rendering is complete
    root = sample_assets_dir()
    output_path = output_path or os.path.join(
        tempfile.gettempdir(), "sceneboard-demo.png")

    library = AssetLibrary()
    library.scan(root)
    by_name = {os.path.splitext(asset.name)[0]: asset
               for asset in library.assets}
    project = Project(name="SceneBoard demo", asset_store=root,
                      cell_size=10, map_cols=32, map_rows=26,
                      show_grid=True, export_grid=True,
                      export_grid_color="#2e6fdf")

    def add(name, x, y, rotation=0, opacity=1.0, snap=True, overlay=False):
        asset = by_name[name]
        piece = Piece(
            asset_path=asset.path, name=name,
            w=asset.width or 64, h=asset.height or 64,
            x=x, y=y, rotation=rotation, opacity=opacity,
            snap=snap, is_overlay=overlay)
        project.levels[0].add(piece)
        return piece

    add("room_200x100", 40, 40)
    add("corridor_40x120", 250, 80, rotation=90)
    add("wall_10x50", 45, 45)
    add("wall_10x50", 45, 100)
    add("wall_25x50", 45, 155)
    add("terminal_25x50", 90, 70)
    add("keyboard_40x15", 90, 125)
    add("computer_10x50", 160, 70)
    add("overlay_glow_100x100", 110, 90, rotation=30,
        opacity=0.65, snap=False, overlay=True)

    image = exporter.render_level(project, project.levels[0],
                                  include_grid=True, scale=2.0)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    if not image.save(output_path, "PNG"):
        raise OSError(f"Could not write demo render: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="PNG destination (defaults to system temp)")
    args = parser.parse_args()
    path = render_demo(args.output)
    print(f"Demo map written to {path}")


if __name__ == "__main__":
    main()
