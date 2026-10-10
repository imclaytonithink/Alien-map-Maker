"""MU/TH/UR terminal export in the app: markers, Geomorph rooms, hand-drawn zones, the export dialog."""
import os
import sys
import tempfile

from PyQt6.QtCore import QStandardPaths
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)

import ui.launch_screen as ls

ls.LaunchScreen.exec = lambda self: (setattr(self, "result_action", ("new", None)) or 1)

from core import exporter, muthur as M
from core.project import ZoneRegion
from ui.context_menu import build_canvas_menu
from ui.geomorph_dialog import GeomorphDialog
from ui.main_window import MainWindow
from ui.muthur_dialog import MuthurExportDialog

MainWindow._offer_recovery = lambda self: None
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.information = staticmethod(lambda *a, **k: None)

store = tempfile.mkdtemp(prefix="sceneboard-muthur-")
win = MainWindow()
win.settings.clear()
win.project.asset_store = store
lib = win.library.library
lib.scan(store)
win.library.set_project(win.project, lib, win._add_at_center)
win.canvas.library = lib
win.show()
app.processEvents()
win.overlay.hide()
cell = win.project.cell_size

# ---- the right-click menu offers the marker ---------------------------------------
menu = build_canvas_menu(win, None, (3 * cell, 3 * cell))
labels = [a.text() for a in menu.actions()]
assert "Place MU/TH/UR terminal here" in labels, labels

# ---- a Geomorph site on the canvas: tiles remember their rooms ----------------------
dlg = GeomorphDialog(win, win, sync=True)
dlg.tabs.setCurrentIndex(1)
dlg.cb_arch.setCurrentIndex(dlg.cb_arch.findData("Frontier colony outpost"))
dlg.ed_seed.setText("muthur-test")
dlg.overlay_checks["power_failure"].setChecked(True)
dlg.generate()
res = dlg.result
for t in {p.tile.id: p.tile for g in res.grids for p in g.placed}.values():
    path = os.path.join(store, "Pack", *t.image.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = QImage((t.w + 4) * 6, (t.h + 4) * 6, QImage.Format.Format_ARGB32)
    img.fill(QColor("#336677"))
    assert img.save(path)
lib.scan(store)
win.library.set_project(win.project, lib, win._add_at_center)
first = len(win.project.levels)
assert win._place_geomorph(res, dlg.registry, dlg.images) is not None
levels = win.project.levels[first:]
rooms = [p for lv in levels for p in lv.pieces if p.room]
assert rooms and all(p.room["arch"] == "Frontier colony outpost" for p in rooms)
assert all(p.room["zone"] for p in rooms)

# one marker in the middle of each of three different allowed rooms
picked, seen = [], set()
for li, lv in enumerate(win.project.levels):
    for p in lv.pieces:
        if not p.room or p.room["zone"] in seen:
            continue
        hit = M.resolve_room(p.room)
        if hit and M.catalog()["archetypes"][hit[0]]["rooms"][hit[1]]["allowed"]:
            seen.add(p.room["zone"])
            picked.append((li, p))
    if len(picked) >= 3:
        break
picked = picked[:3]
assert len(picked) == 3, picked
for li, tile in picked:
    win.level_bar.tabs.setCurrentIndex(li)
    win.canvas.set_level(li)
    x, y = tile.center
    marker = win._place_terminal_at(x + tile.room.get("ox", 0), y + tile.room.get("oy", 0))
    assert marker.is_terminal and marker.embedded
    assert any(l.name == "MU/TH/UR terminals" for l in win.project.levels[li].layers)

# ---- a hand-drawn zone on a level of its own ------------------------------------------
own = win.project.add_level("Annex")
own.zones.append(ZoneRegion(name="Zone", label="Medical clinic",
                            points=[(0, 0), (8 * cell, 0), (8 * cell, 8 * cell), (0, 8 * cell)]))
win.level_bar.refresh()
win.canvas.set_level(len(win.project.levels) - 1)
win._place_terminal_at(4 * cell, 4 * cell)

# ---- the export dialog -----------------------------------------------------------------
d = MuthurExportDialog(win)
assert d.table.rowCount() == 4
d.sp_count.setValue(3)
d._get_code()
assert not d.ed_code.text() and "You said" in d.lbl_msg.text(), "wrong count: no code"
d.sp_count.setValue(4)
d._get_code()
code = d.ed_code.text()
assert code and d.btn_copy.isEnabled(), d.lbl_msg.text()
spec = M.decode(code)
names = sorted(t.name for t in spec.terminals)
expected = sorted(f.terminal.name for f in d.scan.found)
assert names == expected, (names, expected)
assert spec.arch == M.arch_index("Frontier colony outpost")
assert max(t.floor for t in spec.terminals) == spec.floors
assert "Medical clinic" in names

# the GM's starting state is kept with the marker and lands in the code
row = next(i for i, f in enumerate(d.scan.found) if f.source == "zone")
d.table.cellWidget(row, 3).setCurrentIndex(M.LOCKDOWN)
d._get_code()
spec = M.decode(d.ed_code.text())
assert any(t.name == "Medical clinic" and t.state == M.LOCKDOWN for t in spec.terminals)
assert own.pieces[-1].room.get("state") == M.LOCKDOWN

# a marker outside every room blocks the code and says why
win._place_terminal_at(30 * cell, 30 * cell)
d._get_code()
d.sp_count.setValue(5)
d._get_code()
assert not d.ed_code.text() and "not inside a room" in d.lbl_msg.text()

# markers draw in exports (a visible, non-empty marker area)
img = exporter.render_level(win.project, own, True, 1.0)
px = img.pixelColor(4 * cell, int(4.3 * cell))
assert px.green() > px.red() + 10, px.name()          # the marker's green screen

print("MUTHUR GUI OK —", code)
