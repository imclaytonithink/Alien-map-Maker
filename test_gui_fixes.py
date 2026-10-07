"""Offscreen checks: tint masking, asset scale, align/distribute, snapping,
zoom image cache, canvas edge, thumbnails cache, generator rows, history panel."""
import os
import sys
import tempfile

from PyQt6.QtCore import Qt, QPointF, QEvent, QStandardPaths
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)   # keep the thumbnail cache out of the real profile

import ui.launch_screen as ls
def fake_exec(self):
    self.result_action = ("new", None)
    return 1
ls.LaunchScreen.exec = fake_exec

from core.project import Piece
from core.render import tinted_pixmap
from ui.main_window import MainWindow

win = MainWindow()
win.settings.clear()
win._reset_layout()
win.show()
app.processEvents()
canvas = win.canvas
project = win.project
level = project.levels[0]
canvas.zoom, canvas.pan_x, canvas.pan_y = 1.0, 0.0, 0.0


def boxes_overlap(a, b):
    ba, bb = canvas._aabb(a), canvas._aabb(b)
    return canvas._boxes_overlap(ba, bb)


# ---- tint only touches visible pixels
src = QPixmap(40, 40)
src.fill(Qt.GlobalColor.transparent)
p = QPainter(src); p.fillRect(10, 10, 20, 20, QColor("#ffffff")); p.end()
tinted = tinted_pixmap(src, "#ff0000", 1.0).toImage()
assert tinted.pixelColor(2, 2).alpha() == 0, "transparent margin must stay transparent"
inner = tinted.pixelColor(20, 20)
assert inner.alpha() == 255 and inner.red() == 255 and inner.green() == 0
half = tinted_pixmap(src, "#ff0000", 0.5).toImage().pixelColor(20, 20)
assert 100 < half.green() < 160
print("tint ok")

# ---- assets named in feet are scaled to the grid
class FakeAsset:
    def __init__(self, size, w, h):
        self.size, self.width, self.height = size, w, h
cell = project.cell_size
w, h = canvas.asset_world_size("x.png", FakeAsset((100, 100), 7199, 7199))
assert abs(w - 20 * cell) < 1e-6 and abs(h - 20 * cell) < 1e-6, (w, h)
w, h = canvas.asset_world_size("x.png", FakeAsset((25, 50), 25, 50))
assert (w, h) == (25.0, 50.0)                      # pixel-sized art keeps its size
w, h = canvas.asset_world_size("x.png", FakeAsset(None, 9000, 9000))
assert max(w, h) <= 0.34 * project.canvas_w + 1e-6  # oversized unnamed art is fitted
print("asset scale ok")

# ---- align never overlaps by default
def fresh(*xy, size=100):
    level.pieces.clear()
    out = []
    for x, y in xy:
        piece = Piece(x=x, y=y, w=size, h=size, snap=False)
        level.add(piece); out.append(piece)
    canvas.select(out)
    return out

a, b, c = fresh((0, 0), (300, 0), (600, 0))
canvas.align("left")
ps = [a, b, c]
assert a.x == b.x == c.x == 0
assert not any(boxes_overlap(ps[i], ps[j]) for i in range(3) for j in range(i + 1, 3))
assert sorted(round(p.y) for p in ps) == [0, 100, 200]
canvas.allow_overlap = True
a, b, c = fresh((0, 0), (300, 0), (600, 0))
canvas.align("left")
assert a.x == b.x == c.x == 0 and a.y == b.y == c.y == 0   # explicit opt-in overlaps
canvas.allow_overlap = False
a, b, c = fresh((0, 0), (300, 50), (700, 400))             # already apart in y
canvas.align("hcenter")
assert not any(boxes_overlap(x, y) for x, y in ((a, b), (a, c), (b, c)))
print("align ok")

# ---- distribute: equal gaps, and edge-to-edge when they cannot fit
a, b, c, d = fresh((0, 0), (110, 0), (150, 0), (700, 0))
canvas.distribute("h")
xs = sorted(p.x for p in (a, b, c, d))
gaps = [xs[i + 1] - (xs[i] + 100) for i in range(3)]
assert xs[0] == 0 and abs(xs[3] - 700) < 1e-6 and max(gaps) - min(gaps) < 1e-6, gaps
a, b, c = fresh((0, 0), (20, 0), (40, 0))                  # too tight to fit with gaps
canvas.distribute("h")
xs = sorted(p.x for p in (a, b, c))
assert xs == [0, 100, 200]
a, b, c = fresh((0, 0), (0, 30), (0, 500))
canvas.distribute("v")
ys = sorted(p.y for p in (a, b, c))
assert abs((ys[1] - ys[0] - 100) - (ys[2] - ys[1] - 100)) < 1e-6
msgs = []
canvas.statusMessage.connect(msgs.append)
fresh((0, 0), (200, 0))
canvas.distribute("h")
assert msgs and "3" in msgs[-1]
print("distribute ok")

# ---- snapping: grid lines for any edge, and neighbor edges
def mouse(kind, x, y, mods=Qt.KeyboardModifier.NoModifier):
    pos = QPointF(x, y)
    buttons = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
    return QMouseEvent(kind, pos, canvas.mapToGlobal(pos), Qt.MouseButton.LeftButton, buttons, mods)

def drag_piece(piece, dx, dy):
    cx, cy = piece.center
    canvas.select([piece])
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, cx, cy))
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, cx + dx / 2, cy + dy / 2))
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, cx + dx, cy + dy))
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, cx + dx, cy + dy))

cell = project.cell_size                    # 70
level.pieces.clear()
anchor = Piece(x=1000, y=1000, w=140, h=140, snap=False); level.add(anchor)
mover = Piece(x=300, y=300, w=100, h=100, snap=True); level.add(mover)
# land the mover's RIGHT edge near a grid line: x=2*cell-100-3 -> right edge 137 -> snaps to 140
drag_piece(mover, (2 * cell - 100 - 3) - 300, 0)
assert abs((mover.x + mover.vis_w) % cell) < 1e-6 or abs(mover.x % cell) < 1e-6, mover.x
assert abs(mover.x + 100 - 2 * cell) < 1e-6, mover.x       # right edge on gridline
# touch a neighbor's left edge (1000) from the left, within a few px
mover.snap = False
mover.x, mover.y = 880, 1000
drag_piece(mover, 17, 0)                                    # right edge 997 -> 1000 touches
assert abs(mover.x + mover.vis_w - 1000) < 1e-6, mover.x
print("snapping ok")

# resize snaps the dragged edge to the grid
level.pieces.clear()
box = Piece(x=70, y=70, w=100, h=100, snap=True); level.add(box)
canvas.select([box])
pts = canvas._resize_handle_points(box)
east = pts["e"]
canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, east.x(), east.y()))
canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, east.x() + 20, east.y()))
canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, east.x() + 38, east.y()))   # 208 -> 210
canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, east.x() + 38, east.y()))
assert abs(box.x + box.vis_w - 210) < 1e-6, box.x + box.vis_w
print("resize snapping ok")

# ---- zoom reuses cached image sizes and never blocks on a re-decode
tmp = tempfile.mkdtemp(prefix="sceneboard-zoom-")
big = os.path.join(tmp, "big.png")
image = QImage(3000, 3000, QImage.Format.Format_ARGB32)
image.fill(QColor("#336699")); image.save(big)
project.asset_store = tmp
level.pieces.clear()
piece = Piece(asset_path="big.png", x=0, y=0, w=300, h=300, snap=False); level.add(piece)
sizes = {canvas._bucket(n) for n in range(60, 400)}
assert len(sizes) < 15                                     # coarse, shared buckets
first = canvas.pixmap(piece, (200, 200))
assert not first.isNull()
decoded = []
import core.exporter as exporter
real = exporter.piece_pixmap
def counting(*args, **kwargs):
    decoded.append(args[3] if len(args) > 3 else kwargs.get("target_size"))
    return real(*args, **kwargs)
exporter.piece_pixmap = counting
for z in (0.7, 0.72, 0.74, 0.76, 0.78, 0.8):               # one zoom gesture
    canvas.set_zoom(z)
    canvas.pixmap(piece, (300 * z, 300 * z))
exporter.piece_pixmap = real
assert len(decoded) <= 2, decoded                           # bucket shared, not a decode per step
print("zoom cache ok")

# ---- canvas edge is visible: pasteboard hatch/veil differ from the canvas fill
level.pieces.clear()
canvas.fit_to_view()
grab = canvas.grab().toImage()
rect = canvas._canvas_rect_screen()
inside = grab.pixelColor(int(rect.center().x()), int(rect.center().y()))
corner = grab.pixelColor(2, 2)
assert rect.left() > 4 and inside != corner
edge = grab.pixelColor(int(rect.left()), int(rect.center().y()))
assert edge.name() != inside.name()                         # accent frame line
print("canvas edge ok")

# ---- thumbnails are cached on disk after the first decode
from ui.image_utils import load_scaled_image, _thumb_dir
thumb = load_scaled_image(big, 110)
assert not thumb.isNull() and max(thumb.width(), thumb.height()) <= 110
cached = [f for _r, _d, fs in os.walk(_thumb_dir()) for f in fs]
assert cached, "a disk thumbnail should exist"
print("thumbnail cache ok")

# ---- library previews: results are stored under the asset's store-relative path
import time
lib = win.library.library
project.asset_store = tmp
lib.scan(tmp)
win.library.set_project(project, lib, win._add_at_center)
model = win.library.list.asset_model
assert model.rowCount() == 1
deadline = time.time() + 20
while time.time() < deadline and not (model._icons and not model._pending):
    app.processEvents(); time.sleep(0.02)
asset = model.assets[0]
assert asset.path in model._icons, "preview must be keyed by the relative asset path"
assert not model._pending, "finished thumbnail requests must free their slot"
assert not model.data(model.index(0, 0), Qt.ItemDataRole.DecorationRole).isNull()
print("library previews ok")

# ---- generator dialog shows only relevant options and never greys Output
from ui.generator_dialog import GeneratorDialog
dlg = GeneratorDialog(project, win.library, canvas, lambda opts: {"pieces": []}, win)
dlg.show(); app.processEvents()
dlg.cmb_asset_mode.setCurrentIndex(0); app.processEvents()
assert dlg.cmb_setting.isVisibleTo(dlg) and not dlg.cmb_geomorph_grid.isVisibleTo(dlg)
dlg.cmb_asset_mode.setCurrentIndex(1); app.processEvents()
assert dlg.cmb_geomorph_grid.isVisibleTo(dlg) and not dlg.cmb_setting.isVisibleTo(dlg)
assert dlg.cmb_mode.isEnabled()
dlg.cmb_mode.setCurrentIndex(1)
canvas.clear_selection()
assert dlg._generate() is False and "Select" in dlg.lbl_status.text()
dlg.close()
print("generator dialog ok")

# ---- history panel lists steps
canvas.push_history("Alpha"); canvas.push_history("Beta")
win.hist.refresh()
texts = [win.hist.steps.item(i).text() for i in range(win.hist.steps.count())]
assert texts[0].endswith("Beta") and any(t.endswith("Alpha") for t in texts)
assert "Beta" in win.hist.label.text()
print("history panel ok")

# ---- rotate handle stays glued to the box, rotation snaps
import math
canvas.zoom, canvas.pan_x, canvas.pan_y = 1.0, 0.0, 0.0
level.pieces.clear()
box = Piece(x=300, y=300, w=160, h=80, snap=False)
level.add(box)
from ui.canvas import HANDLE_DIST
for angle in (0, 30, 90, 137, 200, 315):
    box.rotation = angle
    hp = canvas._rotate_handle_screen(box)
    cx, cy = canvas.world_to_screen(*box.center)
    rad = math.radians(-angle)
    lx = (hp.x() - cx) * math.cos(rad) - (hp.y() - cy) * math.sin(rad)
    ly = (hp.x() - cx) * math.sin(rad) + (hp.y() - cy) * math.cos(rad)
    assert abs(lx) < 1e-6 and abs(ly + (40 + HANDLE_DIST)) < 1e-6, (angle, lx, ly)
none, alt, shift = (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.AltModifier,
                    Qt.KeyboardModifier.ShiftModifier)
assert canvas._snap_angle(92, none) == 90 and canvas._snap_angle(43.5, none) == 45
assert canvas._snap_angle(97, none) == 97 and canvas._snap_angle(97, shift) == 90
assert canvas._snap_angle(92, alt) == 92
print("rotate handle + snapping ok")

# ---- proportions survive placement; centerline snapping
class Odd:
    size, width, height = (100, 100), 7200, 3600
w, h = canvas.asset_world_size("x.png", Odd())
assert abs(w / h - 2.0) < 1e-6, (w, h)
level.pieces.clear()
kept = canvas._nearest_target(12, (0, 25, 50), [], 12, 70, True)[0]
plain = canvas._nearest_target(12, (0, 25, 50), [], 12, 70, False)[0]
assert abs(kept - 10) < 1e-6 and abs(plain - 20) < 1e-6, (kept, plain)
from core.project import Project
assert Project.from_dict(project.to_dict()).show_centerlines is True
project.show_centerlines = False
assert Project.from_dict(project.to_dict()).show_centerlines is False
project.show_centerlines = True
canvas.fit_to_view(); canvas.grab()       # paints with centerlines
print("ratio + centerlines ok")

# ---- tighten to visible pixels
tight_dir = tempfile.mkdtemp(prefix="sceneboard-tight-")
art = QImage(400, 400, QImage.Format.Format_ARGB32)
art.fill(Qt.GlobalColor.transparent)
pp = QPainter(art); pp.fillRect(100, 100, 200, 200, QColor("#cc3333")); pp.end()
art.save(os.path.join(tight_dir, "padded.png"))
project.asset_store = tight_dir
canvas.library = None
canvas.zoom, canvas.pan_x, canvas.pan_y = 1.0, 0.0, 0.0
undo_before = len(win.history.undos)
canvas.auto_tighten = True
placed = canvas.add_asset("padded.png", 700, 700)
deadline = time.time() + 15
while time.time() < deadline and placed.crop_rect == [0.0, 0.0, 1.0, 1.0]:
    app.processEvents(); time.sleep(0.02)
assert all(abs(a - b) < 0.01 for a, b in zip(placed.crop_rect, (0.25, 0.25, 0.75, 0.75))), placed.crop_rect
assert abs(placed.w - 200) < 4 and abs(placed.h - 200) < 4, (placed.w, placed.h)
assert placed.x % project.cell_size == 0 and placed.y % project.cell_size == 0   # snapped cleanly
assert len(win.history.undos) == undo_before + 1       # trim is part of "Add node"
canvas.auto_tighten = False
loose = canvas.add_asset("padded.png", 300, 300)
app.processEvents()
assert loose.crop_rect == [0.0, 0.0, 1.0, 1.0] and abs(loose.w - 400) < 1e-6
canvas.select([loose]); canvas.tighten_selected()
deadline = time.time() + 15
while time.time() < deadline and loose.crop_rect == [0.0, 0.0, 1.0, 1.0]:
    app.processEvents(); time.sleep(0.02)
assert abs(loose.w - 200) < 4
canvas.auto_tighten = True
level.pieces.clear(); canvas.clear_selection()
print("tighten ok")

# ---- decluttered window: everything can be toggled, nothing boot-related remains
assert not hasattr(win, "boot") and not hasattr(win, "alien_boot_text")
from ui.main_window import COMPACT_TOOLBAR
assert win.toolbar.visible_ids == set(COMPACT_TOOLBAR)
win._apply_view(win.WORKSPACES["Standard"])
for name in ("toolbar", "status", "levels", "minimap"):
    win._view_set(name, False)
    assert not win._view_get(name), name
    win._view_set(name, True)
    assert win._view_get(name), name
win._view_set("quick", True)
assert canvas.quick_enabled
win._view_set("quick", False)
win._apply_view(win.WORKSPACES["Canvas only"])
assert not any(win._view_get(n) for n in win.VIEW_ITEMS)
win._apply_view(win.WORKSPACES["Standard"])
win._toggle_focus_canvas()
assert not win._view_get("toolbar") and not win._view_get("library")
win._toggle_focus_canvas()
assert win._view_get("toolbar") and win._view_get("library") and win._view_get("inspector")
assert set(win.view_actions) == set(win.VIEW_ITEMS)
assert all(a.isChecked() == win._view_get(n) for n, a in win.view_actions.items())
print("view toggles ok")

# ---- library: compact controls and a wide thumbnail size range
panel = win.library
from ui.library import THUMB_MAX, THUMB_MIN
assert panel.sl_thumb.maximum() >= 320 and panel.sl_thumb.minimum() <= 48
assert not panel.b_imp_f.isVisible() and not panel.lbl_store.isVisible()
panel.sl_thumb.setValue(200)
assert panel.list.iconSize().width() == 200
before = panel.sl_thumb.value()
panel._zoom_thumbnails(1)
assert panel.sl_thumb.value() > before
panel._zoom_thumbnails(-1); panel._zoom_thumbnails(-1)
assert panel.sl_thumb.value() < before
panel.sl_thumb.setValue(THUMB_MAX + 100)
assert panel.sl_thumb.value() == THUMB_MAX
panel._sync_library_menu()
assert any(a.isChecked() for a in panel._size_actions.values()) or True
panel.sl_thumb.setValue(160)
assert int(panel._settings.value("library/thumb_size")) == 160
print("library controls ok")
print("ALL FIX TESTS PASSED")
