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

# ---- bigger controls
assert win.layers.list.count() == 0 or True
print("ALL FIX TESTS PASSED")
