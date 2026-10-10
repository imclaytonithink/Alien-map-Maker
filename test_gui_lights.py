"""The canvas Lights tool: lamps on placed Geomorph tiles can be moved, added, re-coloured, deleted and placed
automatically, and every change re-draws the room's lighting (offscreen Qt, synthetic tile art)."""
import sys

from PyQt6.QtCore import QPoint, QPointF, QStandardPaths, Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)

import ui.launch_screen as ls

ls.LaunchScreen.exec = lambda self: (setattr(self, "result_action", ("new", None)), 1)[1]

from PIL import Image

from core.project import Piece, Project
from geomorph import canvas_export, canvas_lights as CL, pipeline, render
from geomorph.registry import Registry
from ui.main_window import MainWindow

MainWindow._offer_recovery = lambda self: self.overlay.open_menu()
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.information = staticmethod(lambda *a, **k: None)

win = MainWindow()
win.settings.clear()
win.show()
app.processEvents()
win.overlay.hide()

reg = Registry.load()
images = render.TileImages(None, loader=lambda t: Image.new(
    "RGBA", ((t.w + 4) * render.THUMB_PPS, (t.h + 4) * render.THUMB_PPS), (90, 120, 130, 255)))
res = pipeline.generate(reg, {"kind": "ship", "seed": "lights-gui", "overlays": ["power_failure"], "intensity": 1.0})
assert res.overlays["power_failure"]
assert win._place_geomorph(res, reg, images) is not None
canvas = win.canvas
canvas.light_context = lambda: (reg, images)
level = canvas.level


class Ev:
    def __init__(self, button=Qt.MouseButton.LeftButton, mods=Qt.KeyboardModifier.NoModifier):
        self._b, self._m = button, mods

    def button(self): return self._b
    def modifiers(self): return self._m
    def accept(self): pass
    def globalPosition(self): return QPointF(0, 0)


def screen(wx, wy):
    return canvas.world_to_screen(wx, wy)


# ---- placed maps keep their lamps as editable records ---------------------------------------------------
lit = [p for p in level.pieces if p.lighting]
assert lit, "dark rooms come with a lighting node"
assert all(p.lighting.get("tile") and "power_failure" in p.lighting["states"] for p in lit)
with_lamps = [p for p in lit if p.lighting["lights"]]
assert with_lamps, "the automatic emergency lamps are on the node, so they can be moved"
assert all(L.get("auto") for p in with_lamps for L in p.lighting["lights"])
tiles = [p for p in level.pieces if p.room]
assert tiles and all(p.room.get("tile") for p in tiles), "tile nodes remember their tile"
assert not [p for p in level.pieces if p.is_text], "no key numbers or marker letters on the canvas"

# ---- the tool and its bar -------------------------------------------------------------------------------
win._start_lights_tool()
app.processEvents()
assert canvas.lights_tool and win.lights_bar.isVisible()
assert win.lights_bar.kind_buttons["wall"].isChecked()

# ---- drag a lamp along its corridor ---------------------------------------------------------------------
node = with_lamps[0]
host = canvas._light_host_of(node)
assert host is not None, "a lighting node finds its tile by position"
_n, placed, x0, y0, cell = host
assert abs(node.x - x0) < 1 and abs(node.y - y0) < 1, "the node lies right over the tile"
L0 = dict(node.lighting["lights"][0])
before_img = node.embedded
sx, sy = screen(x0 + L0["x"] * cell, y0 + L0["y"] * cell)
assert canvas._lamp_at(*canvas.screen_to_world(sx, sy))[0] is node
assert canvas._lights_press(sx, sy, Ev())
target = None                                        # another wall spot in the same tile
from geomorph import atmosphere as A
rows = A._floor(placed)
for cy in range(len(rows)):
    for cx in range(len(rows[0])):
        if rows[cy][cx] in "cr":
            wx, wy = x0 + (cx / 2 + 0.25) * cell, y0 + (cy / 2 + 0.25) * cell
            spot = canvas._snap_spot(host, wx, wy, "wall")
            if spot and ((spot[0] - L0["x"]) ** 2 + (spot[1] - L0["y"]) ** 2) ** 0.5 > 2:
                target = (wx, wy, spot)
                break
    if target:
        break
assert target, "a second wall to move the lamp to"
canvas._lights_motion(*screen(target[0], target[1]), Ev())
assert canvas._light_drag["moved"] and canvas._light_drag["ghost"] is not None
assert canvas._lights_release(*screen(target[0], target[1]), Ev())
moved = node.lighting["lights"][0]
assert (moved["x"], moved["y"]) == (target[2][0], target[2][1]), (moved, target)
assert not moved.get("auto"), "moved by hand: the user's lamp now"
assert node.embedded != before_img, "the room was re-drawn with the lamp in its new place"
assert node in level.pieces

# ---- undo puts it back ----------------------------------------------------------------------------------
win.undo()
node = next(p for p in canvas.level.pieces if p.id == node.id)
assert (node.lighting["lights"][0]["x"], node.lighting["lights"][0]["y"]) == (L0["x"], L0["y"])

# ---- add a lamp to a room that has none -----------------------------------------------------------------
level = canvas.level
hosts = canvas._light_hosts()
plain = [h for h in hosts if canvas._lighting_node(h) is None]
assert plain, "some rooms are not dark"
added = False
for h in plain:
    _n, pl, hx, hy, hc = h
    rws = A._floor(pl)
    spots = [(cx, cy) for cy in range(len(rws)) for cx in range(len(rws[0])) if rws[cy][cx] in "c.r"]
    for cx, cy in spots[::7]:
        wx, wy = hx + (cx / 2 + 0.25) * hc, hy + (cy / 2 + 0.25) * hc
        if canvas._snap_spot(h, wx, wy, "wall") is not None:
            n_before = len(level.pieces)
            assert canvas.add_light_at(wx, wy)
            new = canvas._lighting_node(h)
            assert new is not None and len(new.lighting["lights"]) == 1 and new.embedded
            assert len(level.pieces) == n_before + 1
            assert new.lighting["lights"][0]["color"] == "#fff0c8", "working light by default"
            added = True
            break
    if added:
        break
assert added
# a ceiling lamp with Ctrl goes exactly where it is clicked
cx, cy = spots[len(spots) // 2]
wx, wy = hx + (cx / 2 + 0.25) * hc, hy + (cy / 2 + 0.25) * hc
assert canvas._lights_press(*screen(wx, wy), Ev(mods=Qt.KeyboardModifier.ControlModifier))
ceil = new.lighting["lights"][-1]
assert ceil["kind"] == "ceiling" and ceil["wall"] == "" and abs(ceil["x"] - (wx - hx) / hc) < 0.01

# ---- change and delete through the lamp's own options ---------------------------------------------------
h = canvas._light_host_of(new)
canvas.change_light(new, 0, h, color="#7cc8ff", fixture="#cfeaff")
assert new.lighting["lights"][0]["color"] == "#7cc8ff"
canvas.change_light(new, 0, h, radius=5.5)
assert new.lighting["lights"][0]["radius"] == 5.5
n = len(new.lighting["lights"])
canvas.remove_light(new, n - 1, h)
assert len(new.lighting["lights"]) == n - 1

# ---- auto-light the whole level: every big open space gets a lamp, small rooms none ----------------------
canvas.select([])
placed_n = canvas.auto_light()
assert placed_n > 0
auto_nodes = [p for p in canvas.level.pieces if p.lighting and any(L.get("auto") for L in p.lighting["lights"])]
assert auto_nodes
for p in auto_nodes:
    if "power_failure" in p.lighting["states"]:
        assert all(L["color"] == A.DEFAULT_LIGHT for L in p.lighting["lights"] if L.get("auto")), "red in the dark"
    elif not p.lighting["states"]:
        assert all(L["color"] == A.WORK_LIGHT for L in p.lighting["lights"] if L.get("auto")), "warm white otherwise"
assert any(not L.get("auto") for L in new.lighting["lights"]), "hand-placed lamps are kept"
assert canvas.clear_lights() > 0
assert not any(p.lighting.get("lights") for p in canvas.level.pieces if p.lighting)
assert any(p.lighting for p in canvas.level.pieces), "dark rooms stay dark without their lamps"

# ---- saved with the map -----------------------------------------------------------------------------------
canvas.auto_light()
data = win.project.to_dict()
back = Project.from_dict(data)
lv = next(l for l in back.levels if l.name == canvas.level.name)
assert any(p.lighting and p.lighting["lights"] for p in lv.pieces)

# ---- the export dialog can leave the room-state symbols and the legend out ----------------------------------
import os
import tempfile
from ui.export_dialog import ExportDialog
names = {l.name for l in canvas.level.layers}
assert canvas_export.LAYER_STATES in names and canvas_export.LAYER_LEGEND in names
ed = ExportDialog(win.project, canvas, None, file_format="png")
assert not ed.chk_states.isHidden() and ed.chk_states.isChecked()
ed.chk_states.setChecked(False)
out = tempfile.mkdtemp(prefix="gui-legend-")
ed.output_path = os.path.join(out, "Ship.png")
ed.accept = lambda: None
ed._export()
assert os.path.exists(os.path.join(out, "Ship.png"))
state_layers = [l for lv in win.project.levels for l in lv.layers
                if l.name in (canvas_export.LAYER_STATES, canvas_export.LAYER_LEGEND)]
assert state_layers and not any(l.export for l in state_layers), "left out, and remembered with the map"
ed2 = ExportDialog(win.project, canvas, None, file_format="png")
assert not ed2.chk_states.isChecked()
ed2.chk_states.setChecked(True)
ed2.output_path = os.path.join(out, "Ship2.png")
ed2.accept = lambda: None
ed2._export()
assert all(l.export for l in state_layers)

# ---- Esc puts the tool away ---------------------------------------------------------------------------
win._lights_action("done")
app.processEvents()
assert not canvas.lights_tool and not win.lights_bar.isVisible()
print("lights tool ok")
