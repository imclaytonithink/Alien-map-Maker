"""Offscreen checks: the inspector (Node / Layers / Zones / History) stays
readable at every width it can be dragged to and at small, normal and large
text sizes - no label, button, checkbox, list, number box or group title is cut
off, squeezed or sticks out - plus the wrapping building blocks it uses."""
import atexit
import faulthandler
import os
import sys
import tempfile
import time

faulthandler.dump_traceback_later(300, exit=True)   # a stuck dialog fails loudly

from PyQt6.QtCore import QPoint, QStandardPaths, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QImage
from PyQt6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QApplication, QComboBox,
                             QFormLayout, QGroupBox, QLabel, QMenu, QMessageBox, QPushButton,
                             QSlider, QSpinBox, QVBoxLayout, QWidget)

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)

import ui.launch_screen as ls

ls.LaunchScreen.exec = lambda self: (setattr(self, "result_action", ("new", None)) or 1)
for name in ("information", "warning", "critical"):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
QMenu.exec = lambda self, *args, **kwargs: None      # menus must never block

from core.project import Piece, ZoneRegion
from ui import theme as thememod
from ui.main_window import INSPECTOR_MIN_WIDTH, MainWindow, inspector_min_width
from ui.responsive import FitFormLayout, FlowLayout, WrapButton, WrapCheckBox, fixed_label


def settle(rounds: int = 12):
    for _ in range(rounds):
        app.processEvents()
        time.sleep(0.01)


# ---- building blocks ----------------------------------------------------------
thememod.apply_stylesheet(app, thememod.DEFAULT_ACCENT, 1.0)
host = QWidget()
column = QVBoxLayout(host)
box = QGroupBox("Backdrop (this level)")
form = FitFormLayout(box)
button = WrapButton("Use the highlighted library image")
form.addRow(button)
check = WrapCheckBox("Canvas centerlines (middle of the map)")
form.addRow(check)
order = FlowLayout()
order_buttons = [QPushButton(text) for text in ("Front", "Back", "▲", "▼")]
for index, widget in enumerate(order_buttons):
    order.add(widget, stick=index % 2 == 1)          # Front+Back and ▲+▼ stay together
form.addRow("Order", order)
cols, rows = QSpinBox(), QSpinBox()
for spin in (cols, rows):
    spin.setRange(1, 200)
pair = FlowLayout()
pair.add(cols)
pair.add(fixed_label("×"))
pair.add(rows, stick=True)
form.addRow("Canvas size (squares)", pair)
form.addRow("Opacity", QSlider(Qt.Orientation.Horizontal))
column.addWidget(box)
host.show()

host.resize(480, 400)
settle()
assert button.text() == "Use the highlighted library image", "text() is the label as written"
assert "\n" not in QAbstractButton.text(button) and button.height() < 30, "one line when it fits"
assert form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.DontWrapRows, "labels beside fields"
one_line = button.sizeHint().width()
y_order = {widget.geometry().y() for widget in order_buttons}
assert len(y_order) == 1, "the order buttons share one line"
assert rows.geometry().y() == cols.geometry().y(), "30 × 30 on one line"
assert sum(widget.width() for widget in order_buttons) > \
    sum(widget.sizeHint().width() for widget in order_buttons), "spare room is shared out"
assert form.sizeHint().width() == QFormLayout.sizeHint(form).width() + 1

host.resize(190, 400)
settle()
assert button.text() == "Use the highlighted library image"
assert QAbstractButton.text(button).count("\n") >= 1, "wraps when narrow"
assert button.height() >= button._box(QAbstractButton.text(button)).height(), "and grows taller"
assert button.sizeHint().width() == one_line, "horizontal hints don't depend on the wrapping"
assert button.minimumSizeHint().width() < one_line
assert QAbstractButton.text(check).count("\n") >= 1 and \
    check.height() >= check._box(QAbstractButton.text(check)).height()
assert form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows, "labels move above fields"
assert rows.geometry().y() > cols.geometry().y(), "the second size moves to the next line"
assert order_buttons[0].geometry().y() == order_buttons[1].geometry().y(), "stuck together"
button.setText("Pick a texture")
settle()
assert button.text() == "Pick a texture" and "\n" not in QAbstractButton.text(button)
host.hide()
host.deleteLater()
print("wrapping buttons, flow rows and fitting forms ok")


# ---- the real inspector -------------------------------------------------------
temp_root = tempfile.mkdtemp(prefix="sceneboard-narrow-")
store = os.path.join(temp_root, "store")
os.makedirs(os.path.join(store, "tiles"))
tile_image = QImage(140, 140, QImage.Format.Format_ARGB32)
tile_image.fill(QColor("#556677"))
tile_image.save(os.path.join(store, "tiles", "tile.png"))

win = MainWindow()
win._autosave_timer.stop()
win.settings.clear()
win._clear_all_stamps()


def _leave_no_stamps():
    win.settings.setValue("stamps/slots", "[]")
    win.settings.sync()


atexit.register(_leave_no_stamps)
win._reset_layout()
win.resize(1500, 900)
win.show()
settle()
win.overlay.close_menu()
win.project.asset_store = store
win.library.set_project(win.project, win.library.library, win._add_at_center)
canvas = win.canvas
assert win.inspector.minimumWidth() == INSPECTOR_MIN_WIDTH == 220


def set_inspector_width(px: int) -> int:
    splitter = win.splitter
    sizes = splitter.sizes()
    index = splitter.indexOf(win.inspector)
    total = sum(sizes)
    sizes[index] = px
    sizes[1] = total - sizes[0] - px
    splitter.setSizes(sizes)
    settle()
    return win.inspector.width()


def needed(widget):
    """(width it needs, description) or None when it isn't judged."""
    if isinstance(widget, QLabel):
        if not widget.text() or (widget.pixmap() is not None and not widget.pixmap().isNull()):
            return None
        if widget.wordWrap():
            height = widget.heightForWidth(widget.width())
            if height > widget.height() + 1:
                return 10 ** 6, f"wrapped label {widget.text()[:30]!r} is " \
                                f"{widget.height()} px tall, needs {height}"
            metrics = QFontMetrics(widget.font())
            words = widget.text().replace("\u200b", " ").split()
            longest = max((metrics.horizontalAdvance(word) for word in words), default=0)
            return longest + widget.margin() * 2, f"longest word of {widget.text()[:30]!r}"
        return widget.sizeHint().width(), f"label {widget.text()!r}"
    if isinstance(widget, QAbstractButton) and hasattr(widget, "_full_text"):
        shown = QAbstractButton.text(widget)
        box_size = widget._box(shown)
        if box_size.height() > widget.height() + 1:
            return 10 ** 6, f"{widget.text()!r} is {widget.height()} px tall, " \
                            f"needs {box_size.height()}"
        return box_size.width(), f"{type(widget).__name__} {widget.text()!r}"
    if isinstance(widget, QAbstractButton):
        if widget.minimumWidth() == widget.maximumWidth():          # fixed-size icon buttons
            metrics = QFontMetrics(widget.font())
            return metrics.horizontalAdvance(widget.text() or " ") + 4, \
                f"fixed button {widget.text()!r}"
        return widget.sizeHint().width(), f"{type(widget).__name__} {widget.text()!r}"
    if isinstance(widget, QComboBox):
        return widget.sizeHint().width(), f"combo {widget.currentText()!r}"
    if isinstance(widget, QAbstractSpinBox):
        return widget.sizeHint().width(), f"{type(widget).__name__} {widget.text()!r}"
    if isinstance(widget, QGroupBox):
        metrics = QFontMetrics(widget.font())
        return metrics.horizontalAdvance(widget.title().replace("&&", "&")) + 16, \
            f"group title {widget.title()!r}"
    return None


def audit(label: str) -> list[str]:
    problems = []
    tab = win.inspector.currentWidget()
    for widget in tab.findChildren(QWidget):
        if not widget.isVisible() or widget.width() <= 0:
            continue
        result = needed(widget)
        if result is None:
            continue
        need, what = result
        if need > widget.width() + 1:
            problems.append(f"[{label}] {what}: has {widget.width()} px, needs {need}")
        right = widget.mapTo(tab, QPoint(widget.width(), 0)).x()
        if right > tab.width() + 1 and not isinstance(widget, QGroupBox):
            problems.append(f"[{label}] {what}: sticks out {right - tab.width()} px")
    for widget in [tab] + tab.findChildren(QWidget):
        layout = widget.layout()
        if layout is None or not widget.isVisible():
            continue
        for flow in [layout] + layout.findChildren(FlowLayout):
            if isinstance(flow, FlowLayout) and flow.count():
                geometry = flow.geometry()
                need = flow.heightForWidth(geometry.width())
                if need > geometry.height() + 1:
                    problems.append(f"[{label}] a row of controls is {geometry.height()} px "
                                    f"tall, needs {need}")
    return problems


def states():
    level = canvas.level
    level.pieces.clear()
    level.zones.clear()
    win.inspector.setCurrentWidget(win.props)
    canvas.select([])
    settle()
    yield "Node, nothing selected"
    tile = Piece(asset_path="tiles/tile.png", x=0, y=0, w=140, h=140,
                 name="RPG-Mobius_Deck_Plate_Grey_Large_Corner_01.png")
    level.add(tile)
    level.backdrop_texture = "floors/Hallwaywithdoorsandlightsandgratings.png"
    win.props.refresh_backdrop()
    canvas.select([tile])
    yield "Node, image"
    tile.clone_home = [0, 0, 1, 1]
    canvas.select([])
    canvas.select([tile])
    yield "Node, clone patch"
    tile.clone_home = []
    other = Piece(asset_path="tiles/tile.png", name="tile2.png", x=210, y=0, w=140, h=140)
    level.add(other)
    canvas.select([tile, other])
    yield "Node, several selected"
    for name, flag in (("text", "is_text"), ("patch", "is_patch"),
                       ("scale bar", "is_scale_bar"), ("connector", "is_connector")):
        piece = Piece(name=name, x=0, y=300, w=200, h=60, **{flag: True})
        if flag == "is_text":
            piece.text = "Hello"
        level.add(piece)
        canvas.select([piece])
        yield f"Node, {name}"
    canvas.select([])
    win.inspector.setCurrentWidget(win.layers)
    yield "Layers"
    win.inspector.setCurrentWidget(win.zones)
    yield "Zones"
    zone = ZoneRegion(name="Airlock zone", points=[[0, 0], [140, 0], [140, 140], [0, 140]])
    level.zones.append(zone)
    win.zones.refresh_level()
    canvas.select_zone(zone.id)
    assert win.zones.zone_box.isVisible() or win.zones.zone_box.isEnabled()
    yield "Zones, a zone selected"
    win.inspector.setCurrentWidget(win.hist)
    yield "History"


def check_widths(widths, note=""):
    problems = []
    for width in widths:
        for state in states():
            set_inspector_width(width + 12)       # lay every list and page out at this size
            actual = set_inspector_width(width)
            assert actual == max(width, win.inspector.minimumWidth()), (actual, width)
            problems += audit(f"{note}{actual} px · {state}")
    assert not problems, "\n".join(problems[:40])


check_widths([220, 228, 236, 250, 265, 300, 380])
print("inspector fits at 220-380 px (normal text) ok")

for size, widths in ((8, [220, 300]), (18, [360, 420])):
    win.project.text_scale = size / 11.0
    win._apply_theme()
    settle()
    assert win.inspector.minimumWidth() == inspector_min_width(size / 11.0)
    check_widths(widths, note=f"{size} px text · ")
    print(f"inspector fits at {size} px text ok")
assert inspector_min_width(8 / 11.0) == 220 and inspector_min_width(18 / 11.0) == 360
win.project.text_scale = 1.0
win._apply_theme()
settle()
assert win.inspector.minimumWidth() == 220

grid_titles = [box.title() for box in win.props.findChildren(QGroupBox)]
assert "Canvas size && grid" in grid_titles, "a single & would show as an underline"
print("ALL NARROW INSPECTOR TESTS PASSED")
