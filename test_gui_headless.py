"""Construct the full MainWindow headlessly (offscreen) to catch init errors."""
from __future__ import annotations

import os
import sys

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from ui.main_window import MainWindow
from core import exporter

# don't block on dialogs in headless mode
import ui.main_window as mw
mw.QMessageBox.information = lambda *a, **k: None
mw.QMessageBox.question = lambda *a, **k: mw.QMessageBox.StandardButton.Yes
import ui.launch_screen as ls
ls.LaunchScreen.exec = lambda self: (setattr(self, "result_action", ("new", None)) or 1)

win = MainWindow()
win.show()

root = os.path.join(os.path.dirname(__file__), "sample_assets")
win._ensure_store()
win.library.set_project(win.project, win.library.library, win._add_at_center)
lib = win.library.library
lib.import_folder(root)
win.library.set_project(win.project, lib, win._add_at_center)
paths = [a.path for a in lib.assets]
assert paths, "no sample assets"

# The library's QTreeWidget exposes both the smart taxonomy and the preserved
# folder branch; selecting either view filters assets without changing paths.
panel = win.library
assert panel.group_tree.topLevelItemCount() == 3
assert "category:other" in panel._tree_items
assert "folder:floors" in panel._tree_items
panel.group_tree.setCurrentItem(panel._tree_items["category:other"])
assert panel._view == ("category", "other")
assert panel.list.count() == sum(
    "other" in categories for categories in panel._category_tags.values())
panel.group_tree.setCurrentItem(panel._tree_items["folder:."])
assert panel._view == ("folder", ".")
assert panel.list.count() == len(lib.assets)
panel._show_all_assets()
panel.refresh()

# place pieces
win.canvas.add_asset(paths[0], 50, 50)
win.canvas.add_asset(paths[1], 120, 60)
sel = win.canvas.selected_pieces()
if sel:
    sel[0].rotation = 45
    sel[0].opacity = 0.7

# collections
win.project.collections.setdefault("Favorites", []).append(paths[2])
win.library._rebuild_collections()

# reference overlay
win.props._apply_ref()

# levels
win.level_bar._add()
win.level_bar._add()
assert len(win.project.levels) == 3

# export current level
img = exporter.render_level(win.project, win.project.levels[0], True, 1.0)
out = os.path.join(os.path.dirname(__file__), "gui_test_out.png")
img.save(out, "PNG")
assert os.path.getsize(out) > 1000

# exercise property setters (current API: load_selection takes a list)
p = win.project.levels[0].pieces[0]
win.props.load_selection([p])
win.props._set("x", 35)
win.props._rotate(90)

# ESC toggles the in-window system menu
win.overlay.open_menu()
assert win.overlay.isVisible(), "overlay should open"
win.overlay.close_menu()
assert not win.overlay.isVisible(), "overlay should close"

print("GUI HEADLESS OK — pieces:", len(win.project.levels[0].pieces),
      "levels:", len(win.project.levels), "export:", out)
