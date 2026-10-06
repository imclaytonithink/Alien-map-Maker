"""Right-hand properties panel: piece / multi-piece controls, text, tint,
layers, grid settings, and reference-floor overlay."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QFormLayout, QDoubleSpinBox, QSpinBox,
    QSlider, QCheckBox, QPushButton, QHBoxLayout, QLabel, QColorDialog,
    QComboBox, QLineEdit, QTextEdit,
)
from core.project import Piece, Project, snap_value
from core.render import compute_text_size

FONTS = ["Monospace", "Consolas", "Courier New", "Arial", "Times New Roman"]


class PropertiesPanel(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.project: Optional[Project] = None
        self.pieces: list[Piece] = []
        self._build_ui()
        self.canvas.selectionChanged.connect(self.load_selection)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        # ---- single piece ----
        self.single = QGroupBox("Selected piece")
        sf = QFormLayout(self.single)
        self.lbl_name = QLabel("(none)"); sf.addRow("Name", self.lbl_name)
        self.spin_x = QDoubleSpinBox(); self.spin_x.setRange(-100000, 100000)
        self.spin_y = QDoubleSpinBox(); self.spin_y.setRange(-100000, 100000)
        self.spin_x.valueChanged.connect(lambda v: self._set("x", v))
        self.spin_y.valueChanged.connect(lambda v: self._set("y", v))
        sf.addRow("X", self.spin_x); sf.addRow("Y", self.spin_y)
        rot_row = QHBoxLayout()
        self.spin_rot = QDoubleSpinBox(); self.spin_rot.setRange(-360, 360); self.spin_rot.setSuffix("°")
        self.spin_rot.valueChanged.connect(lambda v: self._set("rotation", v))
        b90 = QPushButton("⟲90"); b90.clicked.connect(lambda: self._rotate(90))
        b_90 = QPushButton("⟳90"); b_90.clicked.connect(lambda: self._rotate(-90))
        rot_row.addWidget(self.spin_rot); rot_row.addWidget(b90); rot_row.addWidget(b_90)
        sf.addRow("Rotation", rot_row)
        self.spin_scale = QDoubleSpinBox(); self.spin_scale.setRange(0.05, 10); self.spin_scale.setSingleStep(0.05)
        self.spin_scale.valueChanged.connect(lambda v: self._set("scale", v))
        sf.addRow("Scale", self.spin_scale)
        op_row = QHBoxLayout()
        self.sl_op = QSlider(Qt.Orientation.Horizontal); self.sl_op.setRange(0, 100); self.sl_op.setValue(100)
        self.sl_op.valueChanged.connect(lambda v: self._set("opacity", v / 100.0))
        op_row.addWidget(self.sl_op); sf.addRow("Opacity", op_row)
        self.chk_snap = QCheckBox("Snap to grid"); self.chk_snap.toggled.connect(lambda v: self._set("snap", v))
        sf.addRow(self.chk_snap)
        self.chk_lock = QCheckBox("Locked"); self.chk_lock.toggled.connect(lambda v: self._set("locked", v))
        sf.addRow(self.chk_lock)
        flip_row = QHBoxLayout()
        fh = QPushButton("Flip H"); fh.clicked.connect(lambda: self._toggle("flip_h"))
        fv = QPushButton("Flip V"); fv.clicked.connect(lambda: self._toggle("flip_v"))
        flip_row.addWidget(fh); flip_row.addWidget(fv); sf.addRow("Flip", flip_row)
        self.cmb_layer = QComboBox(); self.cmb_layer.currentIndexChanged.connect(self._set_layer)
        sf.addRow("Layer", self.cmb_layer)
        z_row = QHBoxLayout()
        bf = QPushButton("Front"); bf.clicked.connect(lambda: self._z("front"))
        bk = QPushButton("Back"); bk.clicked.connect(lambda: self._z("back"))
        bup = QPushButton("▲"); bup.clicked.connect(lambda: self.canvas._raise(self.pieces))
        bdn = QPushButton("▼"); bdn.clicked.connect(lambda: self.canvas._lower(self.pieces))
        z_row.addWidget(bf); z_row.addWidget(bk); z_row.addWidget(bup); z_row.addWidget(bdn)
        sf.addRow("Order", z_row)
        root.addWidget(self.single)

        # ---- text (single text piece) ----
        self.text_box = QGroupBox("Text")
        tf = QFormLayout(self.text_box)
        self.edit_text = QTextEdit(); self.edit_text.setMaximumHeight(60)
        self.edit_text.textChanged.connect(self._text_changed)
        tf.addRow("Text", self.edit_text)
        self.cmb_font = QComboBox(); self.cmb_font.addItems(FONTS)
        self.cmb_font.currentTextChanged.connect(self._font_changed)
        tf.addRow("Font", self.cmb_font)
        self.spin_fsize = QSpinBox(); self.spin_fsize.setRange(6, 400); self.spin_fsize.setValue(24)
        self.spin_fsize.valueChanged.connect(self._fsize_changed)
        tf.addRow("Size", self.spin_fsize)
        self.chk_bold = QCheckBox("Bold"); self.chk_bold.toggled.connect(self._bold_changed)
        tf.addRow(self.chk_bold)
        self.btn_textcol = QPushButton("Text color")
        self.btn_textcol.clicked.connect(self._pick_text_color)
        tf.addRow(self.btn_textcol)
        root.addWidget(self.text_box)

        # ---- multi piece ----
        self.multi = QGroupBox("Multiple selected")
        mf = QFormLayout(self.multi)
        self.lbl_count = QLabel("0")
        mf.addRow("Count", self.lbl_count)
        tint_row = QHBoxLayout()
        self.btn_tint = QPushButton("Tint color")
        self.btn_tint.clicked.connect(self._pick_tint)
        self.sl_tint = QSlider(Qt.Orientation.Horizontal); self.sl_tint.setRange(0, 100); self.sl_tint.setValue(0)
        self.sl_tint.valueChanged.connect(self._tint_strength)
        tint_row.addWidget(self.btn_tint); tint_row.addWidget(self.sl_tint)
        mf.addRow("Tint", tint_row)
        align = QHBoxLayout()
        for lbl, fn in [("< L", "left"), ("> R", "right"), ("^ T", "top"),
                        ("v B", "bottom"), ("- H", "hcenter"), ("| V", "vcenter")]:
            b = QPushButton(lbl); b.setMaximumWidth(40); b.clicked.connect(lambda _, f=fn: self.canvas.align(f))
            align.addWidget(b)
        mf.addRow("Align", align)
        dist = QHBoxLayout()
        dh = QPushButton("Distribute H"); dh.clicked.connect(lambda: self.canvas.distribute("h"))
        dv = QPushButton("Distribute V"); dv.clicked.connect(lambda: self.canvas.distribute("v"))
        dist.addWidget(dh); dist.addWidget(dv); mf.addRow("Distribute", dist)
        grp = QHBoxLayout()
        bg = QPushButton("Group"); bg.clicked.connect(self.canvas.group)
        bu = QPushButton("Ungroup"); bu.clicked.connect(self.canvas.ungroup)
        grp.addWidget(bg); grp.addWidget(bu); mf.addRow("Glue", grp)
        root.addWidget(self.multi)

        # ---- grid ----
        g = QGroupBox("Grid & canvas")
        gf = QFormLayout(g)
        self.chk_grid = QCheckBox("Show grid"); self.chk_grid.toggled.connect(self._apply_grid)
        gf.addRow(self.chk_grid)
        self.sl_gop = QSlider(Qt.Orientation.Horizontal); self.sl_gop.setRange(0, 100); self.sl_gop.setValue(50)
        self.sl_gop.valueChanged.connect(self._apply_grid)
        gf.addRow("Grid opacity", self.sl_gop)
        self.cmb_style = QComboBox(); self.cmb_style.addItems(["solid", "dashed", "dotted"])
        self.cmb_style.currentTextChanged.connect(self._apply_grid)
        gf.addRow("Grid style", self.cmb_style)
        self.spin_major = QSpinBox(); self.spin_major.setRange(1, 50); self.spin_major.setValue(5)
        self.spin_major.valueChanged.connect(self._apply_grid)
        gf.addRow("Major every", self.spin_major)
        self.spin_cell = QSpinBox(); self.spin_cell.setRange(1, 1000); self.spin_cell.setValue(70)
        self.spin_cell.valueChanged.connect(self._apply_grid)
        gf.addRow("Cell px", self.spin_cell)
        self.cmb_feet = QComboBox(); self.cmb_feet.addItems(["5 ft", "10 ft"])
        self.cmb_feet.currentTextChanged.connect(self._apply_grid)
        gf.addRow("Square =", self.cmb_feet)
        self.btn_color = QPushButton("Grid color"); self.btn_color.clicked.connect(self._pick_color)
        gf.addRow(self.btn_color)
        row = QHBoxLayout()
        self.spin_cols = QSpinBox(); self.spin_cols.setRange(1, 200); self.spin_cols.setValue(30)
        self.spin_rows = QSpinBox(); self.spin_rows.setRange(1, 200); self.spin_rows.setValue(30)
        self.spin_cols.valueChanged.connect(self._apply_size)
        self.spin_rows.valueChanged.connect(self._apply_size)
        row.addWidget(self.spin_cols); row.addWidget(QLabel("x")); row.addWidget(self.spin_rows)
        gf.addRow("Size (sq)", row)
        root.addWidget(g)

        # ---- reference overlay ----
        r = QGroupBox("Reference floor overlay")
        rf = QFormLayout(r)
        self.chk_ref = QCheckBox("Show reference floor"); self.chk_ref.toggled.connect(self._apply_ref)
        rf.addRow(self.chk_ref)
        self.cmb_ref = QComboBox(); self.cmb_ref.addItems(["Floor below", "Floor above"])
        self.cmb_ref.currentIndexChanged.connect(self._apply_ref)
        rf.addRow("Reference", self.cmb_ref)
        self.sl_ref = QSlider(Qt.Orientation.Horizontal); self.sl_ref.setRange(5, 80); self.sl_ref.setValue(28)
        self.sl_ref.valueChanged.connect(self._apply_ref)
        rf.addRow("Opacity", self.sl_ref)
        root.addWidget(r)

        root.addStretch(1)

    # ------------------------------------------------------------------
    def set_project(self, project: Project):
        self.project = project
        self.chk_grid.setChecked(project.show_grid)
        self.sl_gop.setValue(int(project.grid_opacity * 100))
        self.cmb_style.setCurrentText(project.grid_style)
        self.spin_major.setValue(project.grid_major)
        self.spin_cell.setValue(project.cell_size)
        self.cmb_feet.setCurrentText(f"{project.feet_per_square} ft")
        self.spin_cols.setValue(project.map_cols)
        self.spin_rows.setValue(project.map_rows)
        self._update_color_btn()
        self._refresh_layers()

    def _refresh_layers(self):
        self.cmb_layer.blockSignals(True)
        self.cmb_layer.clear()
        if self.project and self.canvas.level:
            for l in self.canvas.level.layers:
                self.cmb_layer.addItem(l.name, l.id)
        self.cmb_layer.blockSignals(False)

    def _update_color_btn(self):
        c = QColor(self.project.grid_color)
        self.btn_color.setStyleSheet(f"background:{c.name()}; color:{'#000' if c.lightness()>128 else '#fff'}")

    # ------------------------------------------------------------------
    def load_selection(self, pieces: list[Piece]):
        self.pieces = pieces or []
        n = len(self.pieces)
        self.single.setVisible(n == 1)
        self.text_box.setVisible(n == 1 and self.pieces[0].is_text)
        self.multi.setVisible(n >= 2)
        self._refresh_layers()
        if n == 1:
            p = self.pieces[0]
            self.lbl_name.setText(p.name)
            for w in (self.spin_x, self.spin_y, self.spin_rot, self.spin_scale, self.sl_op):
                w.blockSignals(True); w.setEnabled(True)
            self.chk_snap.setEnabled(True); self.chk_lock.setEnabled(True)
            self.spin_x.setValue(p.x); self.spin_y.setValue(p.y)
            self.spin_rot.setValue(p.rotation); self.spin_scale.setValue(p.scale)
            self.sl_op.setValue(int(p.opacity * 100))
            self.chk_snap.setChecked(p.snap); self.chk_lock.setChecked(p.locked)
            idx = self.cmb_layer.findData(p.layer)
            if idx >= 0:
                self.cmb_layer.setCurrentIndex(idx)
            if p.is_text:
                self.edit_text.blockSignals(True)
                self.edit_text.setPlainText(p.text)
                self.edit_text.blockSignals(False)
                self.cmb_font.setCurrentText(p.font_family)
                self.spin_fsize.setValue(p.font_size)
                self.chk_bold.setChecked(p.font_bold)
        elif n == 0:
            self.lbl_name.setText("(none)")
            for w in (self.spin_x, self.spin_y, self.spin_rot, self.spin_scale, self.sl_op):
                w.blockSignals(True); w.setEnabled(False)
            self.chk_snap.setEnabled(False); self.chk_lock.setEnabled(False)
        else:
            self.lbl_count.setText(str(n))

    # ------------------------------------------------------------------
    def _set(self, attr, value):
        if not self.pieces:
            return
        self.canvas.push_history(f"Edit {attr}")
        for p in self.pieces:
            setattr(p, attr, value)
            if attr in ("x", "y") and p.snap and self.project:
                setattr(p, attr, snap_value(value, self.project.cell_size))
                getattr(self, f"spin_{attr}").blockSignals(True)
                getattr(self, f"spin_{attr}").setValue(getattr(p, attr))
                getattr(self, f"spin_{attr}").blockSignals(False)
        self.canvas.update(); self.canvas.dirty.emit()

    def _rotate(self, delta):
        if not self.pieces:
            return
        self.canvas.push_history("Rotate")
        for p in self.pieces:
            p.rotation = (p.rotation + delta) % 360
        self.spin_rot.blockSignals(True); self.spin_rot.setValue(self.pieces[0].rotation); self.spin_rot.blockSignals(False)
        self.canvas.update(); self.canvas.dirty.emit()

    def _toggle(self, attr):
        if not self.pieces:
            return
        self.canvas.push_history("Flip")
        for p in self.pieces:
            setattr(p, attr, not getattr(p, attr))
        self.canvas.update(); self.canvas.dirty.emit()

    def _set_layer(self, idx):
        if not self.pieces:
            return
        lid = self.cmb_layer.itemData(idx)
        self.canvas.push_history("Layer")
        for p in self.pieces:
            p.layer = lid
        self.canvas.dirty.emit()

    def _z(self, where):
        if not self.pieces or not self.canvas.level:
            return
        self.canvas.push_history("Order")
        pieces = self.canvas.level.pieces
        pieces.sort(key=lambda q: q.z)
        if where == "front":
            z = max(p.z for p in pieces) + 1
            for p in self.pieces:
                p.z = z
        else:
            z = min(p.z for p in pieces) - 1
            for p in self.pieces:
                p.z = z
        self.canvas.update(); self.canvas.dirty.emit()

    # ---- text ----
    def _text_changed(self):
        if not self.pieces or not self.pieces[0].is_text:
            return
        p = self.pieces[0]
        p.text = self.edit_text.toPlainText()
        p.w, p.h = compute_text_size(p.text, p.font_family, p.font_size, p.font_bold)
        self.canvas.update(); self.canvas.dirty.emit()

    def _font_changed(self, name):
        if not self.pieces or not self.pieces[0].is_text:
            return
        p = self.pieces[0]; p.font_family = name
        p.w, p.h = compute_text_size(p.text, p.font_family, p.font_size, p.font_bold)
        self.canvas.update(); self.canvas.dirty.emit()

    def _fsize_changed(self, v):
        if not self.pieces or not self.pieces[0].is_text:
            return
        p = self.pieces[0]; p.font_size = v
        p.w, p.h = compute_text_size(p.text, p.font_family, p.font_size, p.font_bold)
        self.canvas.update(); self.canvas.dirty.emit()

    def _bold_changed(self, v):
        if not self.pieces or not self.pieces[0].is_text:
            return
        p = self.pieces[0]; p.font_bold = v
        p.w, p.h = compute_text_size(p.text, p.font_family, p.font_size, p.font_bold)
        self.canvas.update(); self.canvas.dirty.emit()

    def _pick_text_color(self):
        if not self.pieces:
            return
        c = QColorDialog.getColor(QColor(self.pieces[0].text_color), self)
        if c.isValid():
            for p in self.pieces:
                p.text_color = c.name()
            self.canvas.update(); self.canvas.dirty.emit()

    # ---- tint (multi/single) ----
    def _pick_tint(self):
        c = QColorDialog.getColor(QColor("#ffffff"), self)
        if c.isValid():
            self.canvas.push_history("Tint")
            for p in self.pieces:
                p.tint_color = c.name()
            self.canvas.update(); self.canvas.dirty.emit()

    def _tint_strength(self, v):
        if not self.pieces:
            return
        self.canvas.push_history("Tint strength")
        for p in self.pieces:
            p.tint_strength = v / 100.0
        self.canvas.update(); self.canvas.dirty.emit()

    # ------------------------------------------------------------------
    def _apply_grid(self):
        if not self.project:
            return
        self.project.show_grid = self.chk_grid.isChecked()
        self.project.grid_opacity = self.sl_gop.value() / 100.0
        self.project.grid_style = self.cmb_style.currentText()
        self.project.grid_major = self.spin_major.value()
        self.project.cell_size = self.spin_cell.value()
        self.project.feet_per_square = int(self.cmb_feet.currentText().split()[0])
        self.project._sync_canvas()
        self.canvas.update(); self.canvas.dirty.emit()

    def _apply_size(self):
        if not self.project:
            return
        self.project.map_cols = self.spin_cols.value()
        self.project.map_rows = self.spin_rows.value()
        self.project._sync_canvas()
        self.canvas.fit_to_view(); self.canvas.dirty.emit()

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self.project.grid_color), self)
        if c.isValid():
            self.project.grid_color = c.name()
            self._update_color_btn(); self.canvas.update(); self.canvas.dirty.emit()

    def _apply_ref(self):
        enabled = self.chk_ref.isChecked()
        offset = -1 if self.cmb_ref.currentIndex() == 0 else 1
        opacity = self.sl_ref.value() / 100.0
        self.canvas.set_ref(enabled, offset, opacity)
        self.canvas.dirty.emit()
