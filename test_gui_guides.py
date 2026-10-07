"""Offscreen checks: guide rails, placed guides (create / move / remove / undo),
snapping to guides, guide menus and dialogs, grid coordinates, and exports."""
import os
import sys
import tempfile

from PyQt6.QtCore import QEvent, QPointF, QStandardPaths, Qt
from PyQt6.QtGui import QImage, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)   # keep settings out of the real profile

import ui.launch_screen as ls


def fake_exec(self):
    self.result_action = ("new", None)
    return 1


ls.LaunchScreen.exec = fake_exec

import ui.guide_dialogs as guide_dialogs
from core.project import Piece
from ui.context_menu import build_canvas_menu, build_guide_menu
from ui.export_dialog import ExportDialog
from ui.main_window import MainWindow

# Dialogs must never block: the position dialog "types" a preset value.
typed_squares = {"value": 7.0}


def fake_position_exec(self):
    self.spin.setValue(typed_squares["value"])
    return QDialog.DialogCode.Accepted


guide_dialogs.GuidePositionDialog.exec = fake_position_exec
for name in ("information", "warning", "critical"):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))

win = MainWindow()
win.settings.clear()
win._reset_layout()
win.show()
app.processEvents()
canvas = win.canvas
project = win.project
cell = project.cell_size                       # 70
canvas.zoom, canvas.pan_x, canvas.pan_y = 1.0, 0.0, 0.0   # screen == world
W, H = canvas.width(), canvas.height()
LEFT, RIGHT = Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton
NONE = Qt.MouseButton.NoButton
ALT = Qt.KeyboardModifier.AltModifier


def mouse(kind, x, y, button=LEFT, mods=Qt.KeyboardModifier.NoModifier):
    pos = QPointF(x, y)
    buttons = NONE if kind == QEvent.Type.MouseButtonRelease else button
    return QMouseEvent(kind, pos, canvas.mapToGlobal(pos), button, buttons, mods)


def press(x, y, **kw):
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, x, y, **kw))


def move(x, y, **kw):
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, x, y, **kw))


def release(x, y, **kw):
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, x, y, **kw))


def guides():
    return sorted((g.axis, g.pos) for g in canvas.level.guides)


def labels():
    return [label for label, _ in win.history.undos]


def guide_at(axis, pos):
    return next(g for g in canvas.level.guides if g.axis == axis and g.pos == pos)


# ---- rails: geometry, corners, coordinates widen them, can be hidden
t = canvas.rail_thickness()
assert project.show_coordinates and t == 20, t
assert canvas._rail_at(5, 300) == "left" and canvas._rail_at(W - 5, 300) == "right"
assert canvas._rail_at(300, 5) == "top" and canvas._rail_at(300, H - 5) == "bottom"
assert canvas._rail_at(5, 5) is None and canvas._rail_at(W - 3, H - 3) is None, "corners are dead"
assert canvas._rail_at(300, 300) is None
canvas.set_show_coordinates(False)
assert canvas.rail_thickness() == 8
canvas.set_show_coordinates(True)
win._view_set("rails", False)
assert canvas.rail_thickness() == 0 and canvas._rail_at(5, 300) is None
assert not win.view_actions["rails"].isChecked()
win._view_set("rails", True)
assert win.view_actions["rails"].isChecked() and canvas.rail_thickness() == 20
assert win.minimap.x() >= 10 + canvas.rail_thickness(), "the minimap keeps the rails clear"
canvas.fit_to_view()
r = canvas._canvas_rect_screen()
assert r.left() >= t and r.top() >= t and r.right() <= W - t and r.bottom() <= H - t
canvas.zoom, canvas.pan_x, canvas.pan_y = 1.0, 0.0, 0.0
print("rails ok")

# ---- drag a vertical guide out of the left rail; it snaps to the grid line
base = len(win.history.undos)
press(10, 300)
assert canvas._guide_drag and canvas._guide_drag["axis"] == "v"
move(14, 300)                                  # still on the rail: no guide yet
assert guides() == []
move(60, 300)
move(213, 300)                                 # 3 px from the 210 grid line
assert canvas._guide_drag["pos"] == 210.0
release(213, 300)
assert guides() == [("v", 210.0)], guides()
assert labels()[-1] == "Add guide" and len(win.history.undos) == base + 1
# Alt places a guide freely
press(10, 400)
move(120, 400)
move(253, 400, mods=ALT)
release(253, 400)
assert guides() == [("v", 210.0), ("v", 253.0)], guides()
# a horizontal guide from the top rail
press(300, 10)
move(300, 120)
move(300, 352)
release(300, 352)
assert ("h", 350.0) in guides()
win.undo()
assert guides() == [("v", 210.0), ("v", 253.0)]
win.redo()
assert ("h", 350.0) in guides()
print("create guides ok")

# ---- a click on a rail, or out-and-straight-back, changes nothing
count, steps = len(guides()), len(win.history.undos)
press(10, 500)
release(10, 500)
assert len(guides()) == count and len(win.history.undos) == steps
press(10, 500)
move(150, 500)
assert len(guides()) == count + 1               # visible while dragging
move(W - 5, 500)                                # onto the right rail
assert canvas._guide_drag["remove"]
canvas.grab()                                   # paints the "Release to remove" state
release(W - 5, 500)
assert len(guides()) == count and len(win.history.undos) == steps
print("rail clicks ok")

# ---- move a guide by grabbing it anywhere along its length
g = guide_at("v", 210.0)
assert canvas._guide_at(213, 300) is g and canvas._guide_at(220, 300) is None
press(213, 300)
move(214, 300)                                  # tiny wobble is not a move
assert len(win.history.undos) == steps
move(260, 300)
move(282, 300)
release(282, 300)
assert guide_at("v", 280.0).id == g.id and labels()[-1] == "Move guide"
# drag it onto a rail to remove it; undo puts it back where it was
press(280, 300)
move(150, 300)
move(5, 300)
release(5, 300)
assert not canvas.level.has_guide("v", 280.0) and labels()[-1] == "Remove guide"
win.undo()
assert canvas.level.has_guide("v", 280.0), guides()
# Esc cancels a drag in progress
steps = len(win.history.undos)
press(10, 600)
move(400, 600)
assert canvas.level.has_guide("v", 400.0)
canvas.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                               Qt.KeyboardModifier.NoModifier))
assert not canvas.level.has_guide("v", 400.0) and canvas._guide_drag is None
assert len(win.history.undos) == steps
release(400, 600)                               # the stray release is harmless
print("move / remove / cancel ok")

# ---- hover feedback and double-click for an exact position
g = guide_at("v", 280.0)
move(281, 300, button=NONE)
assert canvas._guide_hover == g.id
assert canvas.cursor().shape() == Qt.CursorShape.SplitHCursor
move(400, 450, button=NONE)
assert canvas._guide_hover is None and canvas.cursor().shape() == Qt.CursorShape.ArrowCursor
move(5, 450, button=NONE)
assert canvas._rail_hover == "left"
move(400, 450, button=NONE)
assert canvas._rail_hover is None
edits = []
canvas.guideEditRequested.connect(edits.append)
typed_squares["value"] = 7.0
canvas.mouseDoubleClickEvent(mouse(QEvent.Type.MouseButtonDblClick, 280, 300))
app.processEvents()                             # the deferred dialog "accepts" 7 squares
assert edits == [g.id] and guide_at("v", 490.0).id == g.id
dialog = guide_dialogs.GuidePositionDialog("h", 350.0, cell, 5, project.canvas_h, win)
assert abs(dialog.spin.value() - 5.0) < 1e-9 and "25 ft" in dialog.feet_label.text()
dialog.spin.setValue(6.5)
assert dialog.position() == 455.0
print("hover + exact position ok")

# ---- nodes snap to guides (guides beat the grid), Alt skips all snapping
canvas.clear_guides()
canvas.add_guide("v", 203.0)
level = canvas.level
level.pieces.clear()
box = Piece(x=300, y=300, w=100, h=100, snap=True)
level.add(box)
canvas.select([box])
press(350, 350)
move(300, 350)
move(255, 350)                                  # left edge 205: grid 210 vs guide 203
assert box.x == 203.0, box.x
assert canvas._smart_guides == [], "the guide lights up instead of a red smart line"
assert guide_at("v", 203.0).id in canvas._guide_flash
release(255, 350)
assert canvas._guide_flash == set()
press(253, 350)
move(270, 350, mods=ALT)
move(287, 350, mods=ALT)
release(287, 350)
assert box.x == 237.0, box.x                    # exactly where it was dragged
# snapping off: guides stay visible but stop pulling
canvas.set_guide_flag("snap_to_guides", False)
assert canvas._guide_positions("v") == [] and canvas._guide_at(203, 500) is not None
canvas.set_guide_flag("snap_to_guides", True)
# resizing snaps the dragged edge to a guide
level.pieces.clear()
wide = Piece(x=70, y=70, w=100, h=100, snap=True)
level.add(wide)
canvas.select([wide])
east = canvas._resize_handle_points(wide)["e"]
press(east.x(), east.y())
move(east.x() + 20, east.y())
move(east.x() + 35, east.y())                   # 205: grid 210 vs guide 203
release(east.x() + 35, east.y())
assert abs(wide.x + wide.vis_w - 203.0) < 1e-6, wide.x + wide.vis_w
# library drops: grid as before unless a guide is close
assert canvas._drop_origin(260, 500, 100, 100, True) == (203.0, 420.0)
assert canvas._drop_origin(600, 500, 100, 100, True) == (560.0, 420.0)
print("node snapping ok")

# ---- drawing tools snap to guides (Shift still means grid)
canvas.set_ruler_tool(True)
press(206, 500)
move(330, 500)
release(330, 500)
assert canvas._ruler_result[0] == (203.0, 500.0), canvas._ruler_result
canvas.cancel_extra_tool()
canvas.set_zone_tool("rectangle")
press(207, 250)
move(300, 300)
release(400, 330)
zone = canvas.level.zones[-1]
assert min(x for x, _ in zone.points) == 203.0, zone.points
canvas.cancel_zone_tool()
canvas.set_ruler_tool(True)
press(206, 500, mods=Qt.KeyboardModifier.ShiftModifier)
release(330, 500, mods=Qt.KeyboardModifier.ShiftModifier)
assert canvas._ruler_result[0] == (210.0, 490.0), "Shift snaps to the grid as before"
canvas.cancel_extra_tool()
print("tool snapping ok")

# ---- lock and hide
canvas.set_guide_flag("lock_guides", True)
assert canvas._guide_at(203, 300) is None
assert canvas._guide_at(203, 300, include_locked=True) is not None
canvas.clear_selection()
press(203, 600)
assert canvas._guide_drag is None, "locked guides cannot be grabbed"
release(203, 600)
canvas.grab()
canvas.set_guide_flag("lock_guides", False)
win.guide_actions["show_guides"].setChecked(False)
assert not project.show_guides and not win.props.chk_guides.isChecked()
assert canvas._guide_positions("v") == [] and canvas._guide_at(203, 300) is None
press(10, 300)                                  # dragging a new guide shows guides again
move(500, 300)
release(500, 300)
assert project.show_guides and win.guide_actions["show_guides"].isChecked()
assert win.props.chk_guides.isChecked()
win.guide_actions["show_coordinates"].setChecked(False)
assert canvas.rail_thickness() == 8 and not win.props.chk_coordinates.isChecked()
win.props.chk_coordinates.setChecked(True)
assert project.show_coordinates and win.guide_actions["show_coordinates"].isChecked()
assert canvas.rail_thickness() == 20
print("lock / hide / sync ok")

# ---- right-click menus for guides, rails and nodes
targets = []
canvas.guideMenuRequested.disconnect(win._show_guide_menu)   # don't open real menus
canvas.guideMenuRequested.connect(lambda pos, target: targets.append(target))
gid = guide_at("v", 203.0).id
press(204, 300, button=RIGHT)
release(204, 300, button=RIGHT)
assert targets[-1] == {"guide": gid}, targets
press(5, 300, button=RIGHT)
release(5, 300, button=RIGHT)
assert targets[-1] == {"rail": "left"}
canvas.guideMenuRequested.connect(win._show_guide_menu)
texts = [a.text().split("\t")[0] for a in build_guide_menu(win, {"guide": gid}).actions()]
assert {"Set position…", "Delete guide", "Lock guides", "Clear guides on this level"} <= set(texts)
texts = [a.text().split("\t")[0] for a in build_guide_menu(win, {"rail": "top"}).actions()]
assert {"Show guides", "Grid coordinates", "Guide layout…", "Hide guide rails"} <= set(texts)
level.pieces.clear()
piece = Piece(x=140, y=210, w=140, h=70, snap=False)
level.add(piece)
canvas.select([piece])
menu = build_canvas_menu(win, piece)
assert "Add guides" in [a.text() for a in menu.actions()]
print("menus ok")

# ---- guides around a selection, copy to all levels, layouts, clear
canvas.clear_guides()
assert canvas.add_guides_around_selection("both") == 6
assert guides() == [("h", 210.0), ("h", 245.0), ("h", 280.0),
                    ("v", 140.0), ("v", 210.0), ("v", 280.0)], guides()
assert canvas.add_guides_around_selection("edges") == 0, "no duplicates"
project.add_level("Deck 2")
win.level_bar.refresh()
assert canvas.copy_guides_to_all_levels() == 1
deck2 = project.levels[1]
assert sorted((g.axis, g.pos) for g in deck2.guides) == guides()
assert not {g.id for g in deck2.guides} & {g.id for g in canvas.level.guides}
layout = guide_dialogs.GuideLayoutDialog(project, len(project.levels), win)
vertical, horizontal = layout.positions()
assert vertical == [350.0, 700.0, 1050.0, 1400.0, 1750.0] and horizontal == vertical
layout.chk_rows.setChecked(False)
layout.chk_margin.setChecked(True)
layout.chk_center.setChecked(True)
vertical, horizontal = layout.positions()
assert horizontal == [140.0, 1050.0, 1960.0] and 140.0 in vertical and 1960.0 in vertical
assert layout.rb_all.isEnabled()
assert canvas.apply_guide_layout([350.0, 700.0], [350.0]) == 3
assert guides() == [("h", 350.0), ("v", 350.0), ("v", 700.0)]
steps = len(win.history.undos)
assert canvas.apply_guide_layout([350.0, 700.0], [350.0]) == 0
assert len(win.history.undos) == steps, "an unchanged layout adds no undo step"
assert canvas.apply_guide_layout([1050.0], [], replace=False, all_levels=True) == 2
assert canvas.level.has_guide("v", 1050.0) and deck2.has_guide("v", 1050.0)
assert canvas.clear_guides() == 4 and guides() == []
win.undo()
assert len(guides()) == 4
print("guide commands ok")

# ---- hover follows press priority (handles beat guides); helpful tooltips
saved_guides = list(canvas.level.guides)
canvas.level.guides = []
level = canvas.level
level.pieces.clear()
level.zones.clear()            # zone edges also beat guides; keep this block clean
node = Piece(x=140, y=140, w=140, h=140, snap=False)
level.add(node)
canvas.select([node])
east = canvas._resize_handle_points(node)["e"]
canvas.add_guide("v", east.x())                 # a guide straight through the handle
move(east.x(), east.y(), button=NONE)
assert canvas._guide_hover is None, "the resize handle wins over the guide"
move(east.x(), east.y() + 45, button=NONE)      # further along the same guide
assert canvas._guide_hover is not None
press(east.x(), east.y())
assert canvas._guide_drag is None and canvas._drag["mode"] == "resize"
release(east.x(), east.y())
assert "Drag onto the map to add a vertical guide" in canvas._guide_tooltip(5, 300)
assert "horizontal guide" in canvas._guide_tooltip(300, 5)
assert canvas._guide_tooltip(east.x(), east.y() + 45).startswith("Guide at x 4 sq")
assert canvas._guide_tooltip(600, 600) == ""
canvas.level.guides = saved_guides
level.pieces.clear()
canvas.clear_selection()
print("hover priority + tooltips ok")

# ---- painting: guides in the guide color, rails, coordinates, readout
canvas.zoom, canvas.pan_x, canvas.pan_y = 1.0, 0.0, 0.0
image = canvas.grab().toImage()
dpr = image.devicePixelRatio() or 1.0
pixel = image.pixelColor(int(350 * dpr), int(400 * dpr))
assert pixel.red() > 150 and pixel.blue() > 120 and pixel.green() < 110, pixel.name()
press(10, 640)
move(333, 640)
image = canvas.grab()                           # readout bubble while dragging
release(333, 640)
win._view_set("rails", False)
canvas.grab()
win._view_set("rails", True)
canvas.set_show_coordinates(False)
canvas.grab()
canvas.set_show_coordinates(True)
print("painting ok")

# ---- export dialog: editor aids are opt-in and remembered; PNG gets them
out_dir = tempfile.mkdtemp(prefix="sceneboard-guides-")
dialog = ExportDialog(project, canvas, win)
assert not (dialog.chk_guides.isChecked() or dialog.chk_centerlines.isChecked()
            or dialog.chk_coordinates.isChecked())
dialog.chk_guides.setChecked(True)
dialog.chk_coordinates.setChecked(True)
dialog.output_path = os.path.join(out_dir, "map.png")
dialog._export()
png = QImage(dialog.output_path)
assert (png.width(), png.height()) == (project.canvas_w + 2 * cell, project.canvas_h + 2 * cell)
mark = png.pixelColor(cell + 350, cell + 1000)
assert mark.red() > 150 and mark.blue() > 120 and mark.green() < 110, mark.name()
assert project.export_guides and project.export_coordinates and not project.export_centerlines
again = ExportDialog(project, canvas, win)
assert again.chk_guides.isChecked() and again.chk_coordinates.isChecked()
assert not again.chk_centerlines.isChecked()
pdf_dialog = ExportDialog(project, canvas, win, file_format="pdf")
pdf_dialog.output_path = os.path.join(out_dir, "map.pdf")
pdf_dialog._export()
assert os.path.getsize(pdf_dialog.output_path) > 1000
print("export ok")

print("ALL GUIDE TESTS PASSED")
