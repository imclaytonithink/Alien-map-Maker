"""Create or resize a map canvas by grid squares and square pixel size."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QSpinBox, QVBoxLayout,
)


class CanvasSizeDialog(QDialog):
    def __init__(self, columns=60, rows=60, cell_size=70, *,
                 title="Canvas size", accept_label="Apply", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        help_text = QLabel(
            "Choose width and height in grid squares. Square size sets the "
            "pixels per square. While editing, use View → Canvas size or the "
            "Canvas size & grid controls in the Node panel to change it again. "
            "Shrinking clips off-canvas nodes in exports but does not delete them.")
        help_text.setWordWrap(True)
        root.addWidget(help_text)
        form = QFormLayout()

        self.spin_columns = QSpinBox()
        self.spin_columns.setRange(1, 200)
        self.spin_columns.setValue(max(1, min(200, int(columns))))
        self.spin_columns.setToolTip("Number of grid squares across the canvas.")
        form.addRow("Width (squares)", self.spin_columns)

        self.spin_rows = QSpinBox()
        self.spin_rows.setRange(1, 200)
        self.spin_rows.setValue(max(1, min(200, int(rows))))
        self.spin_rows.setToolTip("Number of grid squares down the canvas.")
        form.addRow("Height (squares)", self.spin_rows)

        self.spin_cell_size = QSpinBox()
        self.spin_cell_size.setRange(1, 1000)
        self.spin_cell_size.setValue(max(1, min(1000, int(cell_size))))
        self.spin_cell_size.setSuffix(" px")
        self.spin_cell_size.setToolTip(
            "Pixel width and height of one grid square. This can also be "
            "changed later in the Canvas size & grid controls or from "
            "View → Canvas size.")
        form.addRow("Square size", self.spin_cell_size)
        root.addLayout(form)

        self.lbl_dimensions = QLabel()
        self.lbl_dimensions.setWordWrap(True)
        self.lbl_dimensions.setToolTip(
            "The canvas pixel dimensions are the square counts multiplied by "
            "the square size.")
        root.addWidget(self.lbl_dimensions)
        self.lbl_tip = QLabel(
            "Tip: a 100x100 ft deck-plan tile is 20x20 squares at 5 ft per "
            "square, so 60x60 squares fits a 3x3 assembly. Library tiles named "
            "in feet are scaled to your square size when you place them.")
        self.lbl_tip.setWordWrap(True)
        root.addWidget(self.lbl_tip)
        self.spin_columns.valueChanged.connect(self._update_dimensions)
        self.spin_rows.valueChanged.connect(self._update_dimensions)
        self.spin_cell_size.valueChanged.connect(self._update_dimensions)
        self._update_dimensions()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(accept_label)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    def _update_dimensions(self, *_args):
        width = max(100, self.spin_columns.value() * self.spin_cell_size.value())
        height = max(100, self.spin_rows.value() * self.spin_cell_size.value())
        self.lbl_dimensions.setText(
            f"Canvas dimensions: {width:,} × {height:,} pixels")

    def canvas_size(self) -> dict[str, int]:
        return {
            "map_cols": self.spin_columns.value(),
            "map_rows": self.spin_rows.value(),
            "cell_size": self.spin_cell_size.value(),
        }
