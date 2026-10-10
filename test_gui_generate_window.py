"""The Generate window: Geomorph first, your own tiles on a second tab with the folder browser."""
import os
import sys
import tempfile

from PyQt6.QtCore import QStandardPaths, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)

import ui.launch_screen as ls

ls.LaunchScreen.exec = lambda self: (setattr(self, "result_action", ("new", None)) or 1)

from ui.generate_window import GEOMORPH, OWN, GenerateWindow
from ui.main_window import MainWindow

MainWindow._offer_recovery = lambda self: None
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.information = staticmethod(lambda *a, **k: None)

store = tempfile.mkdtemp(prefix="sceneboard-genwin-")
for rel in ("Pack/Rooms/room_a.png", "Pack/Rooms/room_b.png", "Pack/Rooms/Big/hall.png",
            "Pack/Props/crate.png", "loose.png"):
    path = os.path.join(store, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = QImage(60, 60, QImage.Format.Format_ARGB32)
    img.fill(QColor("#336677"))
    assert img.save(path)
win = MainWindow()
win.settings.clear()
win.project.asset_store = store
lib = win.library.library
lib.scan(store)
win.library.set_project(win.project, lib, win._add_at_center)
win.show()
app.processEvents()
win.overlay.hide()

# every way in opens the one window; Geomorph is the default tab
opened = []
real_exec = GenerateWindow.exec
GenerateWindow.exec = lambda self: (opened.append(self), 1)[1]
try:
    win._open_generator()                      # toolbar Generate / Tools > Generate Map
    win._open_geomorph()
    win._open_own_tiles_generator()
finally:
    GenerateWindow.exec = real_exec
assert [w.tabs.currentIndex() for w in opened] == [GEOMORPH, GEOMORPH, OWN]
w = opened[0]
assert w.tabs.count() == 2 and w.tabs.tabText(0).startswith("Ships") and w.tabs.tabText(1) == "Your own tiles"
assert w.geomorph.cb_preset.count() > 5, "presets are on the default tab"
assert not w.geomorph.isWindow() and not w.own.isWindow(), "pages live inside the window"
# it fits small screens: the pages scroll instead of pushing the window off the screen
assert w.minimumSizeHint().height() <= 720 and w.geomorph.minimumSizeHint().height() <= 720,     (w.minimumSizeHint(), w.geomorph.minimumSizeHint())
screen = w.screen().availableGeometry()
assert w.height() <= screen.height() and w.width() <= screen.width()
assert w.geomorph.left_scroll.widget() is not None and w.geomorph.right_scroll.widget() is not None
w.show()
app.processEvents()
assert w.isVisible()

# the browser: the library's folder tree, nothing listed until a folder is opened
picker = w.own.picker
assert picker is not None
tokens = set(picker._items)
assert {"folder-root", "folder:.", "folder:Pack", "folder:Pack/Rooms", "folder:Pack/Rooms/Big",
        "folder:Pack/Props"} <= tokens, tokens
assert picker.list.count() == 0 and "Open a folder" in picker.lbl.text()
assert not picker._items["folder:Pack/Rooms"].isExpanded()
picker.tree.setCurrentItem(picker._items["folder:Pack/Rooms"])
assert picker.list.count() == 2, "only the folder's own pictures"
picker.tree.setCurrentItem(picker._items["folder:Pack"])
assert picker.list.count() == 0 and "subfolders" in picker.lbl.text()
picker.search.setText("crate")
assert picker.list.count() == 1
picker.search.setText("")

# ticking in the browser picks for the generator (and the library), and back
picker.tree.setCurrentItem(picker._items["folder:Pack/Rooms"])
model = picker.list.asset_model
model.setData(model.index(0, 0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
assert len(w.own.assets) == 1 and w.own.selection_list.count() == 1
assert picker._items["folder:Pack/Rooms"].checkState(0) == Qt.CheckState.PartiallyChecked
picker._items["folder:Pack/Rooms"].setCheckState(0, Qt.CheckState.Checked)
assert len(w.own.assets) == 3, "a ticked folder takes its subfolders too"
assert set(win.library._picked) == {a.path for a in w.own.assets}
win.library.clear_picks()
assert w.own.assets == [] and picker._items["folder:Pack/Rooms"].checkState(0) == Qt.CheckState.Unchecked

# generating from the own-tiles tab still works
picker._items["folder:Pack/Props"].setCheckState(0, Qt.CheckState.Checked)
levels = len(win.project.levels)
assert w.own._generate() is True
assert len(win.project.levels) == levels + 1

# a page's Close closes the window
w.own.btn_close.click()
app.processEvents()
assert not w.isVisible()
print("GENERATE WINDOW OK")
