"""The export dialog's section option writes grid-aligned PNG sections for a big map."""
import os
import sys
import tempfile
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication(sys.argv)
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))

from core.project import Piece, Project
from ui.export_dialog import ExportDialog

project = Project()
project.map_cols = project.map_rows = 60
project._sync_canvas()
level = project.levels[0]
layer = level.layers[0].id if level.layers else None
cs = project.cell_size
level.add(Piece(layer=layer, is_patch=True, patch_color="#aa3344", x=5 * cs, y=5 * cs, w=50 * cs, h=50 * cs, name="slab"))
canvas = types.SimpleNamespace(level=level)

dlg = ExportDialog(project, canvas, None, file_format="png",
                   default_preset="Tabletop Sim (sharp 100px/sq, in sections)")
assert dlg.row_split.isVisibleTo(dlg) and dlg.chk_split.isChecked(), "sections are on for the sharp TTS preset"
assert dlg.cmb_max.currentData() == 4096
pdf = ExportDialog(project, canvas, None, file_format="pdf")
assert not pdf.row_split.isVisibleTo(pdf), "sections are for PNG only"

out = tempfile.mkdtemp(prefix="gui-sections-")
dlg.output_path = os.path.join(out, "Deck.png")
dlg.accept = lambda: None
dlg._export()
names = sorted(os.listdir(out))
assert names == ["Deck_A1.png", "Deck_A2.png", "Deck_B1.png", "Deck_B2.png", "Deck_sections.txt"], names
for n in names:
    if n.endswith(".png"):
        img = QImage(os.path.join(out, n))
        assert max(img.width(), img.height()) <= 4096 and img.width() % 100 == 0 and img.height() % 100 == 0

# a smaller limit makes more pieces; a map that fits stays one file
dlg.cmb_max.setCurrentIndex(0)                      # 2048 px: 20 squares per section at 100 px/square -> 3 x 3
out2 = tempfile.mkdtemp(prefix="gui-sections-")
dlg.output_path = os.path.join(out2, "Deck.png")
dlg._export()
assert len([n for n in os.listdir(out2) if n.endswith(".png")]) == 9 and os.path.exists(os.path.join(out2, "Deck_C3.png"))
dlg.cmb_preset.setCurrentText("Tabletop Sim (1024px)")
dlg.chk_split.setChecked(True)
out3 = tempfile.mkdtemp(prefix="gui-sections-")
dlg.output_path = os.path.join(out3, "Small.png")
dlg._export()
assert os.listdir(out3) == ["Small.png"], os.listdir(out3)
print("ALL GUI EXPORT SECTION CHECKS PASSED")
