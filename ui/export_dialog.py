"""Export-to-PNG/PDF dialog with presets, transparent option, and separate
export-grid settings."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QRadioButton, QButtonGroup, QSpinBox,
    QDoubleSpinBox, QCheckBox, QPushButton, QFileDialog, QColorDialog,
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QComboBox, QSlider,
)
from PyQt6.QtGui import QColor

from core import exporter


class ExportDialog(QDialog):
    def __init__(self, project, canvas, parent=None):
        super().__init__(parent)
        self.project = project
        self.canvas = canvas
        self.setWindowTitle("Export")
        self.setMinimumWidth(400)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.scope = QButtonGroup(self)
        self.rb_current = QRadioButton("Current level only")
        self.rb_all = QRadioButton("All levels (one PNG each)")
        self.rb_current.setChecked(True)
        self.scope.addButton(self.rb_current, 0)
        self.scope.addButton(self.rb_all, 1)
        scope_row = QHBoxLayout(); scope_row.addWidget(self.rb_current); scope_row.addWidget(self.rb_all)
        form.addRow("Export", scope_row)

        self.cmb_preset = QComboBox()
        self.cmb_preset.addItems(list(exporter.PRESETS.keys()))
        form.addRow("Size preset", self.cmb_preset)

        self.chk_trans = QCheckBox("Transparent background (PNG)")
        form.addRow(self.chk_trans)

        self.chk_grid = QCheckBox("Include grid")
        self.chk_grid.setChecked(self.project.export_grid)
        self.chk_grid.toggled.connect(self._grid_toggled)
        form.addRow(self.chk_grid)

        self.btn_color = QPushButton("Grid color")
        self.btn_color.clicked.connect(self._pick)
        form.addRow(self.btn_color)
        self.sl_op = QSlider(Qt.Orientation.Horizontal)
        self.sl_op.setRange(0, 100)
        self.sl_op.setValue(int(self.project.export_grid_opacity * 100))
        form.addRow("Grid opacity", self.sl_op)
        self._grid_toggled(self.chk_grid.isChecked())

        self.le_path = QLabel("")
        self.btn_path = QPushButton("Choose…")
        self.btn_path.clicked.connect(self._choose)
        path_row = QHBoxLayout(); path_row.addWidget(self.le_path, 1); path_row.addWidget(self.btn_path)
        form.addRow("Output", path_row)

        layout.addLayout(form)
        self.status = QLabel("")
        layout.addWidget(self.status)
        btns = QHBoxLayout()
        b_cancel = QPushButton("Cancel"); b_cancel.clicked.connect(self.reject)
        b_ok = QPushButton("Export"); b_ok.clicked.connect(self._export)
        btns.addStretch(1); btns.addWidget(b_cancel); btns.addWidget(b_ok)
        layout.addLayout(btns)

    def _grid_toggled(self, on):
        self.btn_color.setEnabled(on)
        self.sl_op.setEnabled(on)

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.project.export_grid_color), self)
        if c.isValid():
            self.project.export_grid_color = c.name()
            self.btn_color.setStyleSheet(f"background:{c.name()}")

    def _choose(self):
        if self.rb_all.isChecked():
            d = QFileDialog.getExistingDirectory(self, "Output folder")
            if d:
                self.le_path.setText(d)
        else:
            d, _ = QFileDialog.getSaveFileName(self, "Save PNG", "map.png", "PNG (*.png)")
            if d:
                self.le_path.setText(d)

    def _export(self):
        path = self.le_path.text().strip()
        if not path:
            QMessageBox.warning(self, "Export", "Choose an output destination first.")
            return
        preset = self.cmb_preset.currentText()
        scale = exporter.preset_scale(self.project, preset)
        transparent = self.chk_trans.isChecked()
        include_grid = self.chk_grid.isChecked()
        self.project.export_grid = include_grid
        self.project.export_grid_opacity = self.sl_op.value() / 100.0
        try:
            if self.rb_all.isChecked():
                files = exporter.export_all_levels(self.project, path, include_grid,
                                                  scale, "map", transparent)
                self.status.setText(f"Exported {len(files)} file(s) to:\n{path}")
            else:
                level = self.canvas.level
                if level is None:
                    QMessageBox.warning(self, "Export", "No level selected.")
                    return
                exporter.export_level_to_file(self.project, level, path,
                                             include_grid, scale, transparent)
                self.status.setText(f"Saved:\n{path}")
            QMessageBox.information(self, "Export", "Export complete.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
