"""Floating bar shown at the top of the canvas while the cut-out tool is on.

Pick the selection shape (rectangle, ellipse, lasso, polygon) and grid
snapping, then act on the selected area: delete it, cut or copy it (paste
makes a new node), turn it into a new node in place, or keep only it.
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QBoxLayout, QButtonGroup, QCheckBox, QFrame, QHBoxLayout,
                             QLabel, QToolButton)

from ui.glyphs import cut_shape_icon
from ui.theme import theme_colors

SHAPES = (("rect", "Rectangle — drag. Shift = square."),
          ("ellipse", "Ellipse — drag. Shift = circle."),
          ("lasso", "Lasso — drag around the area freehand."),
          ("polygon", "Polygon — click the corners; double-click, Enter or click the "
                      "first point to close. Backspace removes the last point."))
ACTIONS = (("delete", "Delete", "Hide the area (Delete). The image file is never changed; "
                                "undo or Restore cut-outs brings it back."),
           ("cut", "Cut", "Cut the area out (Ctrl+X); Ctrl+V pastes it as a new node."),
           ("copy", "Copy", "Copy the area (Ctrl+C); Ctrl+V pastes it as a new node."),
           ("new_node", "New node", "Cut the area out into a new node right where it "
                                    "is, ready to drag somewhere else."),
           ("keep", "Keep only", "Keep only the area and hide the rest of the image."))


class CutoutBar(QFrame):
    shapeChosen = pyqtSignal(str)
    snapToggled = pyqtSignal(bool)
    actionRequested = pyqtSignal(str)     # delete | cut | copy | new_node | keep | done

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CutoutBar")
        self.colors = theme_colors("dark")
        # Two rows of controls side by side; they stack when the canvas is narrow.
        self.outer = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.outer.setContentsMargins(8, 4, 6, 4)
        self.outer.setSpacing(4)
        layout = QHBoxLayout()
        layout.setSpacing(4)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        self.outer.addLayout(layout)
        self.outer.addLayout(actions)
        self.title = QLabel("Cut out")
        self.title.setObjectName("CutoutTitle")
        layout.addWidget(self.title)
        self.shape_group = QButtonGroup(self)
        self.shape_group.setExclusive(True)
        self.shape_buttons: dict[str, QToolButton] = {}
        for shape, tip in SHAPES:
            button = QToolButton()
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setIconSize(QSize(20, 20))
            button.setFixedSize(30, 28)
            button.setToolTip(tip)
            button.setAccessibleName(tip.split(" —")[0])
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, s=shape: self.shapeChosen.emit(s))
            self.shape_group.addButton(button)
            self.shape_buttons[shape] = button
            layout.addWidget(button)
        self.chk_snap = QCheckBox("Snap to grid")
        self.chk_snap.setToolTip("Rectangle, ellipse and polygon corners land on grid "
                                 "lines (hold Alt to place them freely).")
        self.chk_snap.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chk_snap.toggled.connect(self.snapToggled.emit)
        layout.addWidget(self.chk_snap)
        layout.addStretch(1)
        self.separator = self._separator()
        actions.addWidget(self.separator)
        self.action_buttons: dict[str, QToolButton] = {}
        for name, label, tip in ACTIONS:
            button = QToolButton()
            button.setText(label)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, n=name: self.actionRequested.emit(n))
            self.action_buttons[name] = button
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(self._separator())
        self.btn_done = QToolButton()
        self.btn_done.setText("Done")
        self.btn_done.setToolTip("Put the cut-out tool away (Esc).")
        self.btn_done.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_done.clicked.connect(lambda: self.actionRequested.emit("done"))
        actions.addWidget(self.btn_done)
        self.set_theme("dark", self.colors["accent"])
        self.hide()

    def fit_width(self, available: int):
        """One row when it fits in ``available`` px, otherwise two."""
        self.outer.setDirection(QBoxLayout.Direction.LeftToRight)
        self.separator.show()
        self.adjustSize()
        if self.sizeHint().width() > available:
            self.outer.setDirection(QBoxLayout.Direction.TopToBottom)
            self.separator.hide()
        self.adjustSize()

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(8)
        return line

    def set_theme(self, mode: str, accent: str):
        self.colors = colors = theme_colors(mode, accent)
        panel = QColor(colors["panel"])
        self.setStyleSheet(
            "QFrame#CutoutBar {"
            f" background: rgba({panel.red()}, {panel.green()}, {panel.blue()}, 232);"
            f" border: 1px solid {colors['border_hot']}; border-radius: 7px; }}"
            f" QLabel#CutoutTitle {{ color: {colors['accent']}; font-weight: bold;"
            " padding-right: 4px; }"
            f" QToolButton {{ color: {colors['text']}; padding: 2px 5px;"
            " border-radius: 4px; border: 1px solid transparent; }"
            f" QToolButton:hover {{ background: {colors['hover']}; }}"
            f" QToolButton:checked {{ background: {colors['selection']};"
            f" border-color: {colors['border_hot']}; }}"
            f" QToolButton:disabled {{ color: {colors['muted']}; }}"
            f" QCheckBox {{ color: {colors['text']}; padding: 0 4px; }}"
            f" QCheckBox:disabled {{ color: {colors['muted']}; }}"
            f" QFrame[frameShape=\"5\"] {{ color: {colors['border']}; }}")
        for shape, button in self.shape_buttons.items():
            button.setIcon(cut_shape_icon(shape, colors["text"]))

    def set_state(self, shape: str, snap: bool, has_area: bool, targets: int):
        for name, button in self.shape_buttons.items():
            button.setChecked(name == shape)
        self.chk_snap.blockSignals(True)
        self.chk_snap.setChecked(bool(snap))
        self.chk_snap.blockSignals(False)
        self.chk_snap.setEnabled(shape != "lasso")
        for button in self.action_buttons.values():
            button.setEnabled(bool(has_area and targets))
        if targets == 1:
            self.title.setText("Cut out · 1 image")
        elif targets > 1:
            self.title.setText(f"Cut out · {targets} images")
        else:
            self.title.setText("Cut out")
        self.adjustSize()
