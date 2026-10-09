"""Offscreen checks: selection basics and shortcuts, arrange, send to level,
swapping pictures, door-mode stamps, the cut-out tool (rectangle, ellipse,
lasso, polygon; delete / cut / copy / paste / new node / keep only), clone
patches, level backdrops (color, floor texture, none; textures uploaded from
a file) and sharp crops."""
import atexit
import faulthandler
import os
import sys
import tempfile

faulthandler.dump_traceback_later(300, exit=True)   # a stuck dialog fails loudly

from PyQt6.QtCore import QEvent, QPointF, QStandardPaths, Qt
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)

import ui.launch_screen as ls

ls.LaunchScreen.exec = lambda self: (setattr(self, "result_action", ("new", None)) or 1)
for name in ("information", "warning", "critical"):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
QMenu.exec = lambda self, *args, **kwargs: None      # menus must never block

from core import cutouts, exporter
from core.project import Piece
from ui.context_menu import build_canvas_menu
from ui.export_dialog import ExportDialog
from ui.main_window import MainWindow

LEFT, RIGHT = Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton
NOMOD = Qt.KeyboardModifier.NoModifier
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
temp_root = tempfile.mkdtemp(prefix="sceneboard-edit-")

win = MainWindow()
win._autosave_timer.stop()
win.settings.clear()
win._clear_all_stamps()


def _leave_no_stamps():
    win.settings.setValue("stamps/slots", "[]")
    win.settings.sync()


atexit.register(_leave_no_stamps)
win._reset_layout()
win.show()
app.processEvents()
canvas = win.canvas
project = win.project
cell = project.cell_size                      # 70 px squares
canvas.zoom, canvas.pan_x, canvas.pan_y = 1.0, 0.0, 0.0      # screen == world


# ---- a small asset library ---------------------------------------------------
store = os.path.join(temp_root, "store")


def save_image(rel, width, height, painter_fn):
    path = os.path.join(store, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter_fn(image)
    image.save(path)
    return rel


def quadrants(image):
    w, h = image.width(), image.height()
    for x in range(w):
        for y in range(h):
            color = (("#ff0000" if y < h // 2 else "#0000ff") if x < w // 2
                     else ("#00ff00" if y < h // 2 else "#ffff00"))
            image.setPixelColor(x, y, QColor(color))


def solid(color):
    return lambda image: image.fill(QColor(color))


def checker(image):
    for x in range(image.width()):
        for y in range(image.height()):
            image.setPixelColor(x, y, QColor("#ffffff" if (x // 10 + y // 10) % 2 else "#000000"))


def labeled_floor(image):
    # grey floor plates with a dark seam every square, plus a white "label"
    for x in range(image.width()):
        for y in range(image.height()):
            seam = x % 70 < 3 or y % 70 < 3
            image.setPixelColor(x, y, QColor("#202020" if seam else "#707070"))
    for x in range(85, 125):
        for y in range(90, 120):
            image.setPixelColor(x, y, QColor("#ffffff"))


TILE_A = save_image("tiles/tile_a.png", 140, 140, quadrants)
TILE_B = save_image("tiles/tile_b.png", 140, 140, solid("#800080"))
TILE_WIDE = save_image("tiles/tile_c.png", 280, 140, solid("#008080"))
DOOR = save_image("doors/door_1sq.png", 70, 14, solid("#cc8800"))
GRATE = save_image("floors/grate.png", 20, 20, checker)
LABELED = save_image("decks/labeled.png", 210, 210, labeled_floor)
project.asset_store = store
win.library.set_project(project, win.library.library, win._add_at_center)
assert win.library.library.get(TILE_A) is not None


def labels():
    return [label for label, _ in win.history.undos]


def mouse(kind, x, y, button=LEFT, mods=NOMOD):
    pos = QPointF(x, y)
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
    return QMouseEvent(kind, pos, canvas.mapToGlobal(pos), button, buttons, mods)


def press(x, y, button=LEFT, mods=NOMOD):
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, x, y, button, mods))


def move(x, y, button=LEFT, mods=NOMOD):
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, x, y, button, mods))


def release(x, y, button=LEFT, mods=NOMOD):
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, x, y, button, mods))


def drag(points, mods=NOMOD):
    (x0, y0), rest = points[0], points[1:]
    press(x0, y0, mods=mods)
    for x, y in rest:
        move(x, y, mods=mods)
    release(*points[-1], mods=mods)


def key(code, mods=NOMOD, text=""):
    win.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, code, mods, text))


def fresh_level():
    level = canvas.level
    level.pieces.clear()
    level.guides.clear()
    canvas.select([])
    canvas.cancel_extra_tool()
    return level


def tile(path=TILE_A, x=0, y=0, w=140, h=140, **extra):
    piece = Piece(asset_path=path, name=os.path.basename(path), x=x, y=y, w=w, h=h,
                  snap=False, **extra)
    canvas.level.add(piece)
    return piece


def render(level=None, **kwargs):
    return exporter.render_level(project, level or canvas.level, include_grid=False,
                                 include_zones=False, **kwargs)


def pixel(image, x, y):
    return image.pixelColor(int(x), int(y))


def menu_texts(menu):
    out = []
    for action in menu.actions():
        out.append(action.text())
        if action.menu() is not None:
            out.extend("  " + text for text in menu_texts(action.menu()))
    return out


# ---- selection basics & shortcuts ------------------------------------------------
level = fresh_level()
floor, walls = level.layers[0], level.layers[1]
a = tile(x=0, y=0, layer=floor.id)
b = tile(x=210, y=0, layer=floor.id)
c = tile(x=420, y=0, layer=walls.id)
locked = tile(x=0, y=210, locked=True)
key(Qt.Key.Key_A, CTRL)
assert set(canvas.selection) == {a.id, b.id, c.id}, "Ctrl+A skips locked nodes"
walls.visible = False
assert {p.id for p in canvas.select_all()} == {a.id, b.id}, "hidden layers are skipped"
walls.visible = True
canvas.select([a])
key(Qt.Key.Key_I, CTRL | SHIFT)
assert set(canvas.selection) == {b.id, c.id}, "invert"
key(Qt.Key.Key_A, CTRL | SHIFT)
assert not canvas.selection, "Ctrl+Shift+A deselects"
assert {p.id for p in canvas.select_layer(walls.id)} == {c.id}
walls.locked = True
assert canvas.select_layer(walls.id) == [], "a locked layer can't be selected"
walls.locked = False
canvas.select([a])
key(Qt.Key.Key_C, CTRL)
key(Qt.Key.Key_V, CTRL)
pasted = canvas.selected_pieces()
assert len(pasted) == 1 and pasted[0].id != a.id and pasted[0].x == a.x + cell
key(Qt.Key.Key_X, CTRL)
assert pasted[0] not in level.pieces and labels()[-1] == "Cut"
key(Qt.Key.Key_V, CTRL)
assert len(level.pieces) == 5, "cut nodes paste back"
canvas.select([b])
key(Qt.Key.Key_D, CTRL)
assert len(level.pieces) == 6 and labels()[-1] == "Duplicate"
here = canvas.paste_at(1000, 600)
assert len(here) == 1 and abs(here[0].center[0] - 1015) <= cell and labels()[-1] == "Paste here"
win.undo()
assert here[0] not in canvas.level.pieces
print("selection basics ok")

# ---- arrange: front/back, forward/backward pass the overlapping neighbor ----------
level = fresh_level()
low = tile(x=0, y=0)
mid = tile(x=40, y=40)
top = tile(x=80, y=80)
far = tile(x=600, y=600)


def order():
    return [p.id for p in canvas._paint_sequence() if p.id != far.id]


canvas.select([low])
canvas._quick("up")
assert order() == [mid.id, low.id, top.id], "one step forward really passes its neighbor"
canvas._quick("down")
assert order() == [low.id, mid.id, top.id]
canvas.bring_to_front()
assert order()[-1] == low.id and labels()[-1] == "Bring to front"
canvas.select([top])
canvas.send_to_back()
assert order()[0] == top.id
canvas.select([low, mid])
canvas.group()
assert labels()[-1] == "Group" and low.group_id and low.group_id == mid.group_id
canvas.ungroup()
assert labels()[-1] == "Ungroup" and not low.group_id
win.undo()
level = canvas.level
assert {p.group_id for p in level.pieces if p.id in (low.id, mid.id)} != {""}, "ungroup undone"
print("arrange ok")

# ---- send to level ----------------------------------------------------------------
level = fresh_level()
if len(project.levels) < 3:
    while len(project.levels) < 3:
        project.add_level()
    win.level_bar.refresh()
deck2, deck3 = project.levels[1], project.levels[2]
deck2.layers[1].name = deck2.layers[1].name      # "Walls" exists on every level
grouped = [tile(x=70, y=70, layer=level.layers[1].id), tile(x=210, y=70,
                                                            layer=level.layers[1].id)]
canvas.select(grouped)
canvas.group()
before2 = len(deck2.pieces)
assert canvas.send_to_levels([1], copy_nodes=True) == 2
copies = deck2.pieces[before2:]
assert [(p.x, p.y) for p in copies] == [(70, 70), (210, 70)], "same spot"
assert {p.layer for p in copies} == {deck2.layers[1].id}, "same-named layer"
assert copies[0].group_id == copies[1].group_id != grouped[0].group_id, "own group"
assert all(p in canvas.level.pieces for p in grouped), "copy keeps the originals"
canvas.select(grouped)
assert canvas.send_to_levels([2], copy_nodes=False) == 2
assert not any(p in canvas.level.pieces for p in grouped) and labels()[-1] == "Move to level"
assert len(deck3.pieces) >= 2
canvas.select([tile(x=500, y=500)])
win._send_to_levels([1, 2], True)
assert labels()[-1] == "Copy to level"
menu = QMenu()
win._fill_send_level_menu(menu)
assert "Move to" in menu_texts(menu) and "Copy to" in menu_texts(menu)
print("send to level ok")

# ---- swapping pictures ------------------------------------------------------------
level = fresh_level()
first = tile(TILE_A, x=140, y=140, rotation=90, flip_h=True)
first.cutouts = [[[0.0, 0.0], [0.2, 0.0], [0.2, 0.2]]]
center = first.center
canvas.select([first])
assert win._swap_selected_to(TILE_WIDE) == 1
assert first.asset_path == TILE_WIDE and first.rotation == 90 and first.flip_h
assert abs(first.center[0] - center[0]) < 1e-6 and abs(first.center[1] - center[1]) < 1e-6
assert (first.w, first.h) == (280.0, 140.0), "size follows the new picture"
assert first.cutouts, "cut-outs stay"
first.asset_path, first.w, first.h = TILE_A, 140.0, 140.0
assert canvas.folder_variants(TILE_A) == [TILE_A, TILE_B, TILE_WIDE]
key(Qt.Key.Key_BracketRight)
assert first.asset_path == TILE_B and "Swap to next image" in labels()[-1]
key(Qt.Key.Key_BracketRight)
key(Qt.Key.Key_BracketLeft)
assert first.asset_path == TILE_B
undo_depth = len(labels())
key(Qt.Key.Key_BracketLeft)
assert first.asset_path == TILE_A and len(labels()) == undo_depth, "held keys share one step"
other_level_copy = Piece(asset_path=TILE_A, x=0, y=0, w=140, h=140)
deck2.add(other_level_copy)
tile(TILE_A, x=500, y=0)
canvas.select([first])
changed = win._swap_every_copy_to(TILE_B)
assert changed >= 3 and other_level_copy.asset_path == TILE_B, "every level"
win.library.list.setCurrentIndex(win.library.list.model().index(0, 0))
swap_menu = QMenu()
win._fill_swap_menu(swap_menu)
assert any("Next image in its folder" in t for t in menu_texts(swap_menu))
print("swap images ok")

# ---- door mode stamps -----------------------------------------------------------------
level = fresh_level()
win._pin_asset_stamp(0, DOOR)
assert win.stamp_slots[0]["edge"], "doors start out in door mode"
assert win.stamp_bar.buttons[0].edge
win._arm_stamp(0)
assert canvas.stamp_tool and canvas._stamp_edge
press(100, 144)
release(100, 144)
door = canvas.level.pieces[-1]
assert door.asset_path == DOOR and abs(door.center[0] - 105) < 1e-6 and \
    abs(door.center[1] - 140) < 1e-6 and door.rotation == 0, (door.center, door.rotation)
press(100, 144)
release(100, 144)
assert len(canvas.level.pieces) == 1, "no second door on the same spot"
press(30, 211)
move(100, 211)
move(170, 211)
move(240, 211)
release(240, 211)
row = [p for p in canvas.level.pieces if abs(p.center[1] - 210) < 1e-6]
assert sorted(round(p.center[0]) for p in row) == [35, 105, 175, 245], \
    [p.center for p in row]
press(279, 100)
release(279, 100)
assert canvas.level.pieces[-1].rotation in (90.0, 270.0), "turns along a vertical line"
canvas.cancel_extra_tool()
win._set_stamp_door_mode(0, False)
assert not win.stamp_slots[0]["edge"]
win._clear_all_stamps()
print("door mode ok")

# ---- crops render sharp ------------------------------------------------------------------
level = fresh_level()
sharp = tile(GRATE, x=0, y=0, w=10, h=10, crop_rect=[0.0, 0.0, 0.5, 0.5], scale=10.0)
image = render(scale=1.0)
values = {pixel(image, x, 50).lightness() for x in range(5, 95, 3)}
assert values <= {0, 255} or min(values) < 30, "a heavily cropped node isn't blurred"
print("sharp crops ok")

# ---- cut-out tool: rectangle, delete, undo ------------------------------------------------
level = fresh_level()
base = tile(TILE_A, x=140, y=140)              # red / green / blue / yellow quadrants
canvas.select([base])
win._start_cutout_tool("rect")
assert canvas.cutout_tool and win.cutout_bar.isVisible()
assert win.cutout_bar.title.text() == "Cut out · 1 image"
drag([(150, 150), (180, 170), (200, 205)])          # snaps to 140..210
assert canvas.has_cutout_area()
assert canvas._cutout_area == [(140, 140), (210, 140), (210, 210), (140, 210)], \
    canvas._cutout_area
assert all(b.isEnabled() for b in win.cutout_bar.action_buttons.values())
canvas.grab()                                         # marching ants draw
key(Qt.Key.Key_Delete)
assert len(base.cutouts) == 1 and labels()[-1] == "Delete image area"
assert not canvas.has_cutout_area()
image = render(transparent=True)
assert pixel(image, 175, 175).alpha() == 0, "the red quadrant is gone"
assert pixel(image, 245, 175).alpha() == 255 and pixel(image, 245, 175).green() == 255
assert not base.hit_test(175, 175) and base.hit_test(245, 175)
win.undo()
base = canvas.level.pieces[0]
assert base.cutouts == [] and canvas.cutout_tool is False or base.cutouts == []
print("cut-out delete ok")

# ---- ellipse (Shift = circle), lasso, polygon -------------------------------------------
level = fresh_level()
base = tile(TILE_A, x=140, y=140)
canvas.select([base])
win._start_cutout_tool("ellipse")
canvas.set_cutout_snap(False)
drag([(150, 150), (190, 170)], mods=SHIFT)
xs = [x for x, _ in canvas._cutout_area]
ys = [y for _, y in canvas._cutout_area]
assert abs((max(xs) - min(xs)) - (max(ys) - min(ys))) < 1e-6, "Shift makes a circle"
canvas.cutout_delete()
image = render(transparent=True)
assert pixel(image, 170, 170).alpha() == 0 and pixel(image, 141, 205).alpha() == 255
win._set_cutout_shape("lasso")
drag([(230, 230), (270, 228), (272, 270), (232, 272), (229, 240)])
assert canvas.has_cutout_area() and len(canvas._cutout_area) >= 4
canvas.cutout_delete()
assert len(base.cutouts) == 2
win._set_cutout_shape("polygon")
canvas.set_cutout_snap(True)
for x, y in ((211, 141), (279, 141), (279, 209)):
    press(x, y)
    release(x, y)
canvas.mouseDoubleClickEvent(mouse(QEvent.Type.MouseButtonDblClick, 279, 209))
assert canvas._cutout_area == [(210, 140), (280, 140), (280, 210)], canvas._cutout_area
key(Qt.Key.Key_Backspace)                         # Backspace = delete the area too
assert len(base.cutouts) == 3
for x, y in ((150, 230), (200, 230)):
    press(x, y)
    release(x, y)
key(Qt.Key.Key_Backspace)
assert canvas._cutout_poly == [(140, 210)], "Backspace drops the last corner"
key(Qt.Key.Key_Escape)
assert not canvas._cutout_poly and canvas.cutout_tool
key(Qt.Key.Key_Escape)
assert not canvas.cutout_tool and not win.cutout_bar.isVisible(), "second Esc: tool away"
assert win.settings.value("tools/cutout_shape") == "polygon"
win._start_cutout_tool()
assert canvas.cutout_shape == "polygon", "the shape is remembered"
canvas.set_cutout_tool(False)
canvas.restore_cutouts([base], last_only=True)
assert len(base.cutouts) == 2 and labels()[-1] == "Restore last cut-out"
canvas.restore_cutouts([base])
assert base.cutouts == []
print("cut-out shapes ok")

# ---- cut / copy / paste, to new node, keep only ------------------------------------------------
level = fresh_level()
base = tile(TILE_A, x=140, y=140)
canvas.select([base])
before = render(transparent=True)
win._start_cutout_tool("rect")
canvas.set_cutout_snap(True)
drag([(141, 141), (209, 209)])
assert canvas.cutout_copy() == 1 and not base.cutouts, "copy leaves the image alone"
assert canvas._clipboard[0]["clip_shapes"] and canvas._clipboard[0]["asset_path"] == TILE_A
key(Qt.Key.Key_X, CTRL)
assert len(base.cutouts) == 1 and labels()[-1] == "Cut image area"
key(Qt.Key.Key_V, CTRL)                            # leaves the tool and pastes
assert not canvas.cutout_tool
part = canvas.selected_pieces()[0]
assert part.clip_shapes and abs(part.x - (140 + cell)) < 1e-6 and abs(part.w - 70) < 1e-6
image = render(transparent=True)
assert pixel(image, 245, 245).red() == 255 and pixel(image, 245, 245).green() == 0, \
    "the pasted part shows the red corner"
from ui.image_utils import all_bounds_for_image
table = all_bounds_for_image(QImage(os.path.join(store, TILE_A)))
canvas._apply_tighten(part, table, dict(canvas.tighten_options))
assert abs(part.w - 70) < 1e-6 and abs(part.h - 70) < 1e-6, "tightening never grows a part"
win.undo()
win.undo()
base = canvas.level.pieces[0]
canvas.select([base])
win._start_cutout_tool("rect")
drag([(141, 141), (209, 209)])
made = canvas.cutout_to_new_node(cut=True)
assert len(made) == 1 and not canvas.cutout_tool and canvas.selected_pieces() == made
after = render(transparent=True)
for x, y in ((150, 150), (200, 200), (250, 150), (150, 250), (250, 250)):
    assert pixel(before, x, y) == pixel(after, x, y), ("a part cut out in place looks the same",
                                                      x, y)
order_ids = [p.id for p in canvas._paint_sequence()]
assert order_ids.index(made[0].id) == order_ids.index(base.id) + 1, "right above its image"
win.undo()
base = canvas.level.pieces[0]
canvas.select([base])
win._start_cutout_tool("rect")
drag([(141, 141), (209, 279)])
assert canvas.cutout_keep_only() == 1
assert abs(base.w - 70) < 1e-6 and abs(base.h - 140) < 1e-6 and base.clip_shapes
image = render(transparent=True)
assert pixel(image, 175, 175).red() == 255 and pixel(image, 175, 245).blue() == 255
assert pixel(image, 245, 175).alpha() == 0, "the rest is hidden"
assert canvas.reset_selected_crop() and not base.clip_shapes and abs(base.w - 140) < 1e-6
win.undo()
base = canvas.level.pieces[0]
canvas.select([base])
assert canvas.replace_selected_image(os.path.join(store, TILE_B))
assert base.clip_shapes == [] and base.embedded, "a replaced picture drops the old shape"
canvas.set_cutout_tool(False)
print("cut / copy / paste / new node / keep ok")

# ---- several images, rotated images, Ctrl+click targets, whole-image cuts ---------------------
level = fresh_level()
left = tile(TILE_A, x=140, y=140)
right = tile(TILE_A, x=280, y=140, rotation=90, flip_h=True)
canvas.select([left])
win._start_cutout_tool("rect")
canvas.set_cutout_snap(True)
press(340, 190, mods=CTRL)
release(340, 190, mods=CTRL)
assert set(canvas._cutout_targets) == {left.id, right.id}, "Ctrl+click adds an image"
drag([(211, 141), (349, 209)])                      # a doorway across the seam
assert canvas._cutout_area == [(210, 140), (350, 140), (350, 210), (210, 210)]
assert win.cutout_bar.title.text() == "Cut out · 2 images"
copied = canvas.cutout_copy()
assert copied == 2 and canvas._clipboard[0]["group_id"] == canvas._clipboard[1]["group_id"] != ""
canvas.cutout_delete()
image = render(transparent=True)
for x in (240, 320):
    assert pixel(image, x, 175).alpha() == 0, ("hole in both images", x)
assert pixel(image, 175, 175).alpha() == 255 and pixel(image, 385, 175).alpha() == 255
u, v = cutouts.world_to_source(right, 315, 175)
assert not right.hit_test(315, 175) and cutouts.visible_at_source(right, u, v) is False
drag([(141, 211), (279, 279)])
canvas.cutout_delete()
assert any(p.id == left.id for p in canvas.level.pieces)
canvas._cutout_targets = [left.id]
drag([(141, 141), (279, 279)])                      # exactly the whole image
canvas.cutout_delete()
assert all(p.id != left.id for p in canvas.level.pieces), "covered completely: removed"
canvas.set_cutout_tool(False)
print("multi-image and rotated cut-outs ok")

# ---- right-click menus ------------------------------------------------------------------
level = fresh_level()
img = tile(TILE_A, x=0, y=0)
img.cutouts = [[[0.1, 0.1], [0.3, 0.1], [0.3, 0.3]]]
canvas.select([img])
texts = menu_texts(build_canvas_menu(win, img, (500.0, 500.0)))
for wanted in ("Cut out part of the image", "  Ellipse / circle", "Restore cut-out areas",
               "Clone patch over a label…", "Swap image", "Send to level", "Paste here",
               "  Bring to front", "  Send to back", "  Invert selection\tCtrl+Shift+I",
               "  Everything on this layer", "Cut\tCtrl+X"):
    assert wanted in texts, (wanted, texts)
canvas.clear_selection()
empty = menu_texts(build_canvas_menu(win, None, (10.0, 10.0)))
assert "Backdrop…" in empty and "Select all\tCtrl+A" in empty
win._show_cutout_menu(canvas.mapToGlobal(QPointF(10, 10).toPoint()))
print("menus ok")

# ---- clone patch -------------------------------------------------------------------------
level = fresh_level()
deck = tile(LABELED, x=0, y=0, w=210, h=210)
assert render().pixelColor(105, 105).lightness() > 240, "the label is there"
win._start_clone_tool()
drag([(85, 90), (125, 120)])
assert canvas.clone_phase() == "source"
move(85 + 70 + 20, 105)                             # one square right (snapped)
press(175, 105)
release(175, 105)
patch = next(p for p in canvas.level.pieces if p.clone_home)
assert labels()[-1] == "Clone patch" and canvas.clone_tool, "stays on for the next label"
du = patch.crop_rect[0] - patch.clone_home[0]
assert abs(du * 210 - 70) < 1e-6, ("copied from exactly one square away", du * 210)
image = render()
assert image.pixelColor(105, 105).lightness() < 200, "the label is covered"
assert image.pixelColor(105, 105) == image.pixelColor(175, 105), "with matching floor"
order_ids = [p.id for p in canvas._paint_sequence()]
assert order_ids.index(patch.id) == order_ids.index(deck.id) + 1
key(Qt.Key.Key_Escape)
assert not canvas.clone_tool
canvas.select([patch])
win._repick_clone_source()
assert canvas.clone_phase() == "source"
move(patch.center[0], patch.center[1] + 70)
press(patch.center[0], patch.center[1] + 70)
release(patch.center[0], patch.center[1] + 70)
assert labels()[-1] == "Clone patch source"
assert abs((patch.crop_rect[1] - patch.clone_home[1]) * 210 - 70) < 1e-6
assert not canvas.clone_tool
print("clone patch ok")

# ---- backdrops: color, floor texture, none ---------------------------------------------------
level = fresh_level()
level.background = "#123456"
level.backdrop = "color"
assert render().pixelColor(5, 5).name() == "#123456"
win._use_backdrop_texture(GRATE)
assert level.backdrop == "texture" and level.backdrop_texture == GRATE
assert labels()[-1] == "Backdrop texture"
image = render()
assert image.pixelColor(5, 5).name() == "#000000" and image.pixelColor(15, 5).name() == "#ffffff"
level.backdrop_tile = 1.0                         # one texture tile per 70 px square
image = render()
assert image.pixelColor(10, 10).lightness() < 40 and image.pixelColor(50, 10).lightness() > 215
level.backdrop_opacity = 0.0
assert render().pixelColor(10, 10).name() == "#123456", "strength 0 shows the color"
level.backdrop_opacity = 1.0
assert project.referenced_assets().get(GRATE) == 1
level.backdrop = "none"
image = render()
assert image.hasAlphaChannel() and image.pixelColor(5, 5).alpha() == 0
opaque = render(opaque=True)
assert not opaque.hasAlphaChannel() and opaque.pixelColor(5, 5).name() == "#123456"
shot = canvas.grab()
cx, cy = canvas.world_to_screen(35, 35)
assert shot.toImage().pixelColor(int(cx), int(cy)).name() != "#123456", "checkerboard"
win.props.refresh_backdrop()
assert win.props.cmb_backdrop.currentData() == "none"
win.props.cmb_backdrop.setCurrentIndex(win.props.cmb_backdrop.findData("color"))
assert level.backdrop == "color" and labels()[-1] == "Backdrop"
win.props.spin_backdrop_tile.setValue(2.0)
assert level.backdrop_tile == 2.0
win.props._backdrop_to_all_levels()
assert all(lv.backdrop == "color" and lv.background == "#123456" for lv in project.levels)
dialog = ExportDialog(project, canvas, win)
assert "solid color" in dialog.lbl_backdrop.text()
dialog.cmb_preset.setCurrentText("Tabletop Sim (2048px)")
assert not dialog.chk_trans.isEnabled()
dialog.deleteLater()
win._show_backdrop_settings()
assert win.inspector.currentWidget() is win.props
level.backdrop_texture = "gone/missing.png"
level.backdrop = "texture"
assert "gone/missing.png" in project.missing_assets()
level.backdrop = "color"
print("backdrops ok")

# ---- backdrop: upload a floor texture from a file ---------------------------------------------
from PyQt6.QtWidgets import QFileDialog
from core.project import BACKDROP_FOLDER

level = fresh_level()
level.backdrop, level.backdrop_texture = "color", ""
level.backdrop_tile, level.backdrop_opacity = 0.0, 1.0
outside = os.path.join(temp_root, "My Pictures")
os.makedirs(outside)
picked = os.path.join(outside, "steel floor.png")
picture = QImage(20, 20, QImage.Format.Format_ARGB32)
checker(picture)
assert picture.save(picked)
broken = os.path.join(outside, "broken.png")
with open(broken, "wb") as handle:
    handle.write(b"not a picture")
chosen = [picked]
real_get_open = QFileDialog.getOpenFileName
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (chosen[0], "Images"))
backdrops_dir = os.path.join(store, BACKDROP_FOLDER)
lib = win.library
lib.group_tree.setCurrentItem(lib._tree_items["folder:tiles"])
assert lib._view == ("folder", "tiles")

assert win.props.btn_backdrop_upload.isEnabled(), "works whatever the backdrop is set to"
win.props.btn_backdrop_upload.click()
uploaded = BACKDROP_FOLDER + "/steel floor.png"
assert level.backdrop == "texture" and level.backdrop_texture == uploaded, level.backdrop_texture
assert labels()[-1] == "Backdrop texture"
assert os.listdir(backdrops_dir) == ["steel floor.png"]
assert win.props.lbl_backdrop_texture.text() == "steel floor.png"
assert "Backdrops" in win.status.currentMessage(), win.status.currentMessage()
image = render()
assert image.pixelColor(5, 5).name() == "#000000" and image.pixelColor(15, 5).name() == "#ffffff"
# the library lists it right away, keeps the folder being browsed, and the
# generator leaves it alone
assert lib.library.get(uploaded) is not None
assert f"folder:{BACKDROP_FOLDER}" in lib._tree_items
assert lib._view == ("folder", "tiles") and lib.group_tree.currentItem() is lib._tree_items["folder:tiles"]
assert lib.library.get(uploaded).folder == BACKDROP_FOLDER
assert win.settings.value("files/last_import_dir") == outside

win.undo()
level = canvas.level
assert level.backdrop == "color" and level.backdrop_texture == ""
win.props.btn_backdrop_upload.click()                  # same picture: reuses the copy
level = canvas.level
assert level.backdrop_texture == uploaded and os.listdir(backdrops_dir) == ["steel floor.png"]
depth = len(labels())
win.props.btn_backdrop_upload.click()                  # already the texture: no new step
assert len(labels()) == depth and "already" in win.status.currentMessage()

level.backdrop = "color"
chosen[0] = ""                                         # Cancel
win.props.btn_backdrop_upload.click()
chosen[0] = broken                                     # not a picture: a warning, no change
win.props.btn_backdrop_upload.click()
assert level.backdrop == "color" and len(labels()) == depth
assert os.listdir(backdrops_dir) == ["steel floor.png"]
chosen[0] = os.path.join(store, *GRATE.split("/"))     # a library image is used in place
win.props.btn_backdrop_upload.click()
assert level.backdrop == "texture" and level.backdrop_texture == GRATE
assert os.listdir(backdrops_dir) == ["steel floor.png"]
QFileDialog.getOpenFileName = real_get_open
level.backdrop = "color"
print("backdrop upload ok")

print("ALL EDIT TOOL TESTS PASSED")
