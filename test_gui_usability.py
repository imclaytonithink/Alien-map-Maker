"""Offscreen checks for splitter layout, resize handles, context menu,
command palette and the upgraded Layers panel."""
import math
import os
import sys

from PyQt6.QtCore import Qt, QPointF, QEvent, QSettings
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

import ui.launch_screen as ls
def fake_exec(self):
    self.result_action = ("new", None)
    return 1
ls.LaunchScreen.exec = fake_exec

from ui.main_window import MainWindow
from ui.command_palette import collect_commands, match_score
from ui.context_menu import build_canvas_menu
from core.project import Piece

win = MainWindow()
win.settings.clear()
win._reset_layout()
win.show()
app.processEvents()
canvas = win.canvas
level = win.project.levels[0]


def mouse(kind, x, y, button=Qt.MouseButton.LeftButton, mods=Qt.KeyboardModifier.NoModifier):
    pos = QPointF(x, y)
    buttons = button if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
    return QMouseEvent(kind, pos, canvas.mapToGlobal(pos), button, buttons, mods)


def drag(x0, y0, x1, y1, mods=Qt.KeyboardModifier.NoModifier):
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, x0, y0))
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, (x0 + x1) / 2, (y0 + y1) / 2,
                                mods=mods))
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, x1, y1, mods=mods))
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, x1, y1))


# ---- splitter: collapse / restore / persist
assert win.splitter.count() == 3
win._toggle_panel(0)
assert win.splitter.sizes()[0] == 0
win._toggle_panel(0)
assert win.splitter.sizes()[0] > 0
win._toggle_focus_canvas()
assert win.splitter.sizes()[0] == 0 and win.splitter.sizes()[2] == 0
win._toggle_focus_canvas()
assert win.splitter.sizes()[0] > 0 and win.splitter.sizes()[2] > 0
win.splitter.setSizes([210, 700, 240])
win._save_layout()
win.splitter.setSizes([300, 600, 300])
win._restore_layout()
assert abs(win.splitter.sizes()[0] - 210) < 40
print("splitter ok")

# ---- direct resize handles
canvas.zoom, canvas.pan_x, canvas.pan_y = 1.0, 0.0, 0.0
piece = Piece(name="box", x=200, y=200, w=100, h=60)
level.add(piece)
canvas.select([piece])
pts = canvas._resize_handle_points(piece)
assert abs(pts["se"].x() - 300) < 1e-6 and abs(pts["se"].y() - 260) < 1e-6

# east edge: stretch width only
drag(300, 230, 350, 230)
assert abs(piece.vis_w - 150) < 1e-6 and abs(piece.vis_h - 60) < 1e-6, (piece.vis_w, piece.vis_h)
assert abs(piece.x - 200) < 1e-6          # opposite edge is the anchor

# corner: free resize, then proportional with Shift
canvas.select([piece]); pts = canvas._resize_handle_points(piece)
drag(pts["se"].x(), pts["se"].y(), pts["se"].x() + 50, pts["se"].y() + 20)
assert abs(piece.vis_w - 200) < 1e-6 and abs(piece.vis_h - 80) < 1e-6
before_ratio = piece.vis_w / piece.vis_h
base_w = piece.w
canvas.select([piece]); pts = canvas._resize_handle_points(piece)
drag(pts["se"].x(), pts["se"].y(), pts["se"].x() + 100, pts["se"].y() + 5,
     mods=Qt.KeyboardModifier.ShiftModifier)
assert abs(piece.vis_w / piece.vis_h - before_ratio) < 1e-6
assert piece.scale > 1.0 and abs(piece.w - base_w) < 1e-6   # uniform scale, base size kept

# Alt: from center keeps the center fixed
cx0, cy0 = piece.center
canvas.select([piece]); pts = canvas._resize_handle_points(piece)
drag(pts["e"].x(), pts["e"].y(), pts["e"].x() + 30, pts["e"].y(),
     mods=Qt.KeyboardModifier.AltModifier)
assert abs(piece.center[0] - cx0) < 1e-6 and abs(piece.center[1] - cy0) < 1e-6

# rotated node: opposite corner stays put
piece2 = Piece(name="rot", x=500, y=300, w=100, h=100, rotation=30)
level.add(piece2)
canvas.select([piece2])
pts = canvas._resize_handle_points(piece2)
anchor = pts["nw"]
drag(pts["se"].x(), pts["se"].y(), pts["se"].x() + 40, pts["se"].y() + 10)
after = canvas._resize_handle_points(piece2)["nw"]
assert math.hypot(after.x() - anchor.x(), after.y() - anchor.y()) < 1e-6
print("resize handles ok")

# numeric size fields
canvas.select([piece2]); win.props.load_selection([piece2])
win.props.spin_w.setValue(120)
assert abs(piece2.vis_w - 120) < 1e-6
win.undo(); win.undo()  # coalesced edits undo as one
print("numeric fields ok")

# ---- history coalescing through the real UI path
level.pieces[:] = [p for p in level.pieces if p.id == piece.id]
canvas.select([piece]); win.props.load_selection([piece])
start = len(win.history.undos)
for v in range(10, 60, 5):
    win.props.spin_x.setValue(v)
assert len(win.history.undos) == start + 1
print("history coalescing ok")

# ---- context menu + command palette
menu = build_canvas_menu(win, piece)
labels = [a.text() for a in menu.actions()]
assert any(l.startswith("Duplicate") for l in labels)
canvas.clear_selection()
labels = [a.text() for a in build_canvas_menu(win, None).actions()]
assert any(l.startswith("Fit") for l in labels)

# right-click without dragging requests the menu; dragging pans instead
got = []
canvas.contextMenuRequested.disconnect()   # avoid a blocking menu.exec()
canvas.contextMenuRequested.connect(lambda pos, hit: got.append(hit))
canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 250, 220, Qt.MouseButton.RightButton))
canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, 250, 220, Qt.MouseButton.RightButton))
assert len(got) == 1
canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 250, 220, Qt.MouseButton.RightButton))
canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, 300, 260, Qt.MouseButton.RightButton))
canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, 300, 260, Qt.MouseButton.RightButton))
assert len(got) == 1
commands = collect_commands(win.menuBar())
names = [c[0] for c in commands]
assert any("Fit" in n for n in names) and any("Command palette" in n for n in names)
assert match_score("fit", "View › Fit") is not None
assert match_score("zzzz", "View › Fit") is None
print("context menu + palette ok")

# ---- layers: rename, reorder, color, solo, filter
win.layers.set_project(win.project, level)
first = level.layers[0]
win.layers._rename(first.id, "Floors!")
assert level.layers[0].name == "Floors!"
ids = [l.id for l in level.layers]
win.layers._reordered(list(reversed(ids)))
assert [l.id for l in level.layers] == list(reversed(ids))
win.undo()
level = win.project.levels[0]   # undo swaps in restored level objects
assert [l.id for l in level.layers] == ids
win.layers.set_project(win.project, level)
win.layers._set_color(ids[0], "#4a90e2")
assert level.layers[0].color == "#4a90e2"
win.layers._toggle_solo(ids[1])
assert canvas.solo_layer_id == ids[1]
win.layers._toggle_solo(ids[1])
assert canvas.solo_layer_id is None
win.layers.filter.setText("walls")
hidden = [win.layers.list.item(i).isHidden() for i in range(win.layers.list.count())]
assert hidden.count(False) == 1
win.layers.filter.setText("")
win.layers._duplicate(ids[1])
assert len(level.layers) == 5
canvas.grab()   # full paint pass with culling + handles
print("layers ok")
print("ALL USABILITY TESTS PASSED")
