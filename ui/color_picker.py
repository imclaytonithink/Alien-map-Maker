"""Shared color dialog with an eyedropper for sampling the composed map."""
from __future__ import annotations

from PyQt6.QtCore import QEventLoop, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog, QDialog, QGridLayout, QPushButton, QVBoxLayout,
)


_SAMPLE_REQUESTED = 7319


def _sample_from_canvas(canvas, parent=None) -> QColor:
    """Temporarily close the picker and wait for a canvas sample or cancel."""
    if canvas is None or not hasattr(canvas, "begin_color_pick"):
        return QColor()

    result: dict[str, QColor] = {}
    loop = QEventLoop(parent)

    def complete(color):
        result["color"] = QColor(color) if color is not None else QColor()
        loop.quit()

    if not canvas.begin_color_pick(complete):
        return QColor()
    loop.exec()
    return result.get("color", QColor())


def choose_color(initial: QColor | str, parent=None, canvas=None,
                 title: str = "Choose color") -> QColor:
    """Show a QColorDialog that can also sample a pixel from the map.

    The dialog is closed while the user clicks the canvas, so the canvas can
    receive the click normally.  The picker then reopens with the sampled color
    selected; the user can still adjust it or cancel the entire operation.
    """
    initial_color = QColor(initial)
    if not initial_color.isValid():
        initial_color = QColor("#ffffff")

    dialog = QColorDialog(initial_color, parent)
    dialog.setWindowTitle(title)
    dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)

    sample_button = QPushButton("Pick from map…", dialog)
    sample_button.setToolTip(
        "Temporarily close this dialog, then click a map pixel. "
        "Press Esc or right-click to cancel sampling.")
    sample_button.clicked.connect(
        lambda checked=False: dialog.done(_SAMPLE_REQUESTED))

    layout = dialog.layout()
    if isinstance(layout, QGridLayout):
        columns = max(1, layout.columnCount())
        layout.addWidget(sample_button, layout.rowCount(), 0, 1, columns)
    elif isinstance(layout, QVBoxLayout):
        layout.addWidget(sample_button)
    elif layout is not None:
        layout.addWidget(sample_button)
    dialog.adjustSize()

    while True:
        result = int(dialog.exec())
        if result == _SAMPLE_REQUESTED:
            sampled = _sample_from_canvas(canvas, parent)
            if sampled.isValid():
                dialog.setCurrentColor(sampled)
            continue
        if result == int(QDialog.DialogCode.Accepted):
            return dialog.currentColor()
        return QColor()
