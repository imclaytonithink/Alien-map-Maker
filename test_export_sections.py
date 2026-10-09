"""Big maps export as grid-aligned sections that fit Tabletop Simulator's texture limit."""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtGui import QGuiApplication, QImage, QPainter

app = QGuiApplication.instance() or QGuiApplication(sys.argv)

from core import exporter
from core.project import Piece, Project

project = Project()
project.map_cols = project.map_rows = 40
project._sync_canvas()
level = project.levels[0]
layer = level.layers[0].id if level.layers else None
cs = project.cell_size
import random
rnd = random.Random(3)
for i in range(60):                                # patches in assorted colours, some across section edges
    level.add(Piece(layer=layer, is_patch=True, patch_color="#%02x%02x%02x" % (rnd.randint(40, 255), rnd.randint(40, 255), rnd.randint(40, 255)),
                    x=rnd.randint(0, 38) * cs, y=rnd.randint(0, 38) * cs, w=rnd.randint(2, 9) * cs, h=rnd.randint(2, 9) * cs,
                    name=f"p{i}"))

# ---- the plan: whole squares, nothing over the limit, a map that fits stays in one piece ----
scale = 140.0 / cs                                  # 140 px per square: 5600 px for 40 squares
plan = exporter.section_plan(project, scale, 4096)
assert plan["size"] == (5600, 5600) and plan["cols"] == 2 and plan["rows"] == 2
names = [b["name"] for b in plan["boxes"]]
assert names == ["A1", "A2", "B1", "B2"], names
for b in plan["boxes"]:
    x0, y0, x1, y1 = b["box"]
    assert max(x1 - x0, y1 - y0) <= 4096
    assert x0 % 140 == 0 and y0 % 140 == 0 and (x1 % 140 == 0 or x1 == 5600), "cut on grid lines"
small = exporter.section_plan(project, 50.0 / cs, 4096)
assert len(small["boxes"]) == 1 and small["boxes"][0]["name"] == "A1"
huge = exporter.section_plan(project, 300.0 / cs, 4096)        # 12000 px, 300 per square
assert huge["cols"] == 4 and all(max(b["box"][2] - b["box"][0], b["box"][3] - b["box"][1]) <= 4096 for b in huge["boxes"])
assert exporter.PRESETS and exporter.preset_scale(project, "Tabletop Sim (sharp 100px/sq, in sections)") == 100.0 / cs
assert exporter.preset_scale(project, "Tabletop Sim (2048px)") == 2048 / max(project.canvas_w, project.canvas_h)
assert exporter._row_letters(0) == "A" and exporter._row_letters(25) == "Z" and exporter._row_letters(26) == "AA"

# ---- sections stitch back to exactly the full render ----
out = tempfile.mkdtemp(prefix="sections-")
files = exporter.export_level_sections(project, level, out, "Deck1", scale=scale, max_side=4096, include_grid=True)
assert [os.path.basename(f) for f in files] == ["Deck1_A1.png", "Deck1_A2.png", "Deck1_B1.png", "Deck1_B2.png"]
txt = open(os.path.join(out, "Deck1_sections.txt"), encoding="utf-8").read()
assert "2 row(s) x 2 column(s)" in txt and "Deck1_B2.png" in txt and "140 px" in txt
full = exporter.render_level(project, level, True, scale)
stitched = QImage(full.width(), full.height(), full.format())
painter = QPainter(stitched)
for sec, f in zip(plan["boxes"], files):
    img = QImage(f)
    assert (img.width(), img.height()) == (sec["box"][2] - sec["box"][0], sec["box"][3] - sec["box"][1])
    painter.drawImage(sec["box"][0], sec["box"][1], img)
painter.end()
A = stitched.convertToFormat(QImage.Format.Format_RGB32)
B = full.convertToFormat(QImage.Format.Format_RGB32)
assert (A.width(), A.height()) == (B.width(), B.height())
off = worst = 0
for y in range(A.height()):                         # identical, apart from 1/255 anti-aliasing rounding on a seam
    ra = bytes(A.constScanLine(y).asarray(A.width() * 4))
    rb = bytes(B.constScanLine(y).asarray(B.width() * 4))
    if ra != rb:
        d = [abs(a - b) for a, b in zip(ra, rb) if a != b]
        off += len(d)
        worst = max(worst, max(d))
assert worst <= 3 and off < 200, ("the sections together are the same picture", off, worst)

# ---- a map that fits is a single plain file ----
one = exporter.export_level_sections(project, level, out, "Small", scale=50.0 / cs, max_side=4096)
assert [os.path.basename(f) for f in one] == ["Small.png"] and not os.path.exists(os.path.join(out, "Small_sections.txt"))
print("ALL EXPORT SECTION CHECKS PASSED")
