"""Options for trimming nodes to their visible pixels."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt

from ui.image_utils import TRIM_THRESHOLDS


class TightenDialog(QDialog):
    """Pick how aggressively to trim, keep some margin, and which sides to trim.

    ``result_action`` after exec is "tighten" or "restore" (or None if cancelled).
    """

    def __init__(self, options: dict, count: int, cell_size: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tighten to visible pixels")
        self.setMinimumWidth(420)
        self.result_action = None
        self._cell = max(1, int(cell_size))

        root = QVBoxLayout(self)
        intro = QLabel(
            "Trims the empty (transparent) border off the selected image nodes so "
            "their outline matches the artwork and snaps cleanly to the grid. "
            "It is a non-destructive crop: the image file is never changed, and "
            "<b>Restore full image</b> brings the whole picture back.")
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.lbl_count = QLabel(f"Applies to {count} selected node(s).")
        root.addWidget(self.lbl_count)

        form = QFormLayout()
        row = QHBoxLayout()
        self.sl_threshold = QSlider(Qt.Orientation.Horizontal)
        self.sl_threshold.setRange(0, len(TRIM_THRESHOLDS) - 1)
        current = min(range(len(TRIM_THRESHOLDS)),
                      key=lambda i: abs(TRIM_THRESHOLDS[i] - int(options["threshold"])))
        self.sl_threshold.setValue(current)
        self.lbl_threshold = QLabel()
        self.sl_threshold.valueChanged.connect(self._update_threshold_label)
        row.addWidget(self.sl_threshold, 1)
        row.addWidget(self.lbl_threshold)
        holder = QWidget(); holder.setLayout(row)
        form.addRow("Ignore faint pixels", holder)
        hint = QLabel("Low = keep even faint glows and shadows. High = trim "
                      "everything except solid artwork.")
        hint.setWordWrap(True)
        form.addRow("", hint)
        self._update_threshold_label()

        self.spin_padding = QDoubleSpinBox()
        self.spin_padding.setRange(0, 1000)
        self.spin_padding.setDecimals(1)
        self.spin_padding.setSuffix(" px")
        self.spin_padding.setValue(float(options["padding"]))
        self.spin_padding.setToolTip("Margin to leave around the artwork (map pixels).")
        self.lbl_squares = QLabel()
        self.spin_padding.valueChanged.connect(self._update_squares)
        pad_row = QHBoxLayout()
        pad_row.addWidget(self.spin_padding)
        pad_row.addWidget(self.lbl_squares, 1)
        pad_holder = QWidget(); pad_holder.setLayout(pad_row)
        form.addRow("Keep a margin of", pad_holder)
        self._update_squares()

        sides = options["sides"]
        self.side_boxes = []
        side_row = QHBoxLayout()
        for label, enabled in zip(("Left", "Top", "Right", "Bottom"), sides):
            box = QCheckBox(label)
            box.setChecked(bool(enabled))
            side_row.addWidget(box)
            self.side_boxes.append(box)
        side_holder = QWidget(); side_holder.setLayout(side_row)
        form.addRow("Trim these sides", side_holder)
        root.addLayout(form)

        self.chk_default = QCheckBox(
            "Use these settings for new nodes (auto-tighten on placement)")
        root.addWidget(self.chk_default)

        buttons = QHBoxLayout()
        restore = QPushButton("Restore full image")
        restore.setToolTip("Show the whole source image again on the selected nodes.")
        restore.clicked.connect(lambda: self._finish("restore"))
        buttons.addWidget(restore)
        buttons.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        apply_button = box.addButton("Tighten", QDialogButtonBox.ButtonRole.AcceptRole)
        apply_button.setDefault(True)
        apply_button.clicked.connect(lambda: self._finish("tighten"))
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        root.addLayout(buttons)

    def _update_threshold_label(self, *_):
        alpha = TRIM_THRESHOLDS[self.sl_threshold.value()]
        self.lbl_threshold.setText(f"{round(alpha / 255 * 100)}% opacity")

    def _update_squares(self, *_):
        squares = self.spin_padding.value() / self._cell
        self.lbl_squares.setText(f"≈ {squares:.2f} grid square(s)")

    def _finish(self, action: str):
        self.result_action = action
        self.accept()

    def options(self) -> dict:
        return {
            "threshold": TRIM_THRESHOLDS[self.sl_threshold.value()],
            "padding": float(self.spin_padding.value()),
            "sides": tuple(box.isChecked() for box in self.side_boxes),
        }

    def make_default(self) -> bool:
        return self.chk_default.isChecked()
