"""Dialogs for placed guides: exact position and quick layouts."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QRadioButton, QSpinBox, QVBoxLayout,
)

from core.guides import format_amount, layout_positions


class GuidePositionDialog(QDialog):
    """Type an exact guide position in squares (feet shown alongside)."""

    def __init__(self, axis: str, pos: float, cell: float, feet_per_square: float,
                 extent: float, parent=None):
        super().__init__(parent)
        self.cell = max(1.0, float(cell))
        self.feet = float(feet_per_square or 5)
        vertical = axis == "v"
        self.setWindowTitle("Vertical guide" if vertical else "Horizontal guide")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(2)
        limit = max(1.0, extent / self.cell)
        self.spin.setRange(-limit, 2 * limit)
        self.spin.setSingleStep(0.5)
        self.spin.setSuffix(" sq")
        self.spin.setValue(pos / self.cell)
        self.spin.valueChanged.connect(self._update_feet)
        form.addRow("From the left edge" if vertical else "From the top edge", self.spin)
        self.feet_label = QLabel()
        form.addRow("", self.feet_label)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_feet()

    def _update_feet(self):
        self.feet_label.setText(
            f"= {format_amount(self.spin.value() * self.feet)} ft "
            f"({format_amount(self.spin.value() * self.cell)} px)")

    def position(self) -> float:
        """The chosen position in world pixels."""
        return self.spin.value() * self.cell


class GuideLayoutDialog(QDialog):
    """Place evenly spaced, margin and center guides in one step."""

    def __init__(self, project, level_count: int = 1, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Guide layout")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        def spin_row(label, checked, value, maximum, suffix=" squares"):
            check = QCheckBox(label)
            check.setChecked(checked)
            spin = QSpinBox()
            spin.setRange(1, maximum)
            spin.setValue(value)
            spin.setSuffix(suffix)
            spin.setEnabled(checked)
            check.toggled.connect(spin.setEnabled)
            row = QHBoxLayout()
            row.addWidget(check, 1)
            row.addWidget(spin)
            form.addRow(row)
            return check, spin

        cols, rows = max(1, project.map_cols), max(1, project.map_rows)
        self.chk_columns, self.spin_columns = spin_row(
            "Vertical guide every", True, 5, max(1, cols))
        self.chk_rows, self.spin_rows = spin_row(
            "Horizontal guide every", True, 5, max(1, rows))
        self.chk_margin, self.spin_margin = spin_row(
            "Margin guides, in from each edge", False, 2,
            max(1, min(cols, rows) // 2))
        self.chk_center = QCheckBox("Guides through the middle of the map")
        form.addRow(self.chk_center)
        self.chk_replace = QCheckBox("Replace existing guides")
        self.chk_replace.setChecked(True)
        form.addRow(self.chk_replace)
        self.rb_level = QRadioButton("This level")
        self.rb_all = QRadioButton("All levels")
        self.rb_level.setChecked(True)
        self.rb_all.setEnabled(level_count > 1)
        group = QButtonGroup(self)
        group.addButton(self.rb_level)
        group.addButton(self.rb_all)
        scope = QHBoxLayout()
        scope.addWidget(self.rb_level)
        scope.addWidget(self.rb_all)
        form.addRow("Apply to", scope)
        layout.addLayout(form)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        for widget in (self.chk_columns, self.chk_rows, self.chk_margin, self.chk_center):
            widget.toggled.connect(self._update_summary)
        for widget in (self.spin_columns, self.spin_rows, self.spin_margin):
            widget.valueChanged.connect(self._update_summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_summary()

    def positions(self) -> tuple[list[float], list[float]]:
        """World x positions of vertical guides and y of horizontal guides."""
        p = self.project
        margin = self.spin_margin.value() if self.chk_margin.isChecked() else 0
        center = self.chk_center.isChecked()
        vertical = layout_positions(
            p.canvas_w, p.cell_size,
            self.spin_columns.value() if self.chk_columns.isChecked() else 0,
            margin, center)
        horizontal = layout_positions(
            p.canvas_h, p.cell_size,
            self.spin_rows.value() if self.chk_rows.isChecked() else 0,
            margin, center)
        return vertical, horizontal

    def replace(self) -> bool:
        return self.chk_replace.isChecked()

    def all_levels(self) -> bool:
        return self.rb_all.isChecked()

    def _update_summary(self):
        vertical, horizontal = self.positions()
        self.summary.setText(f"{len(vertical)} vertical and "
                             f"{len(horizontal)} horizontal guide(s).")
