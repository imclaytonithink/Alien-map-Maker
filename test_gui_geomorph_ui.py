"""The generator window's layout: Ship / Site show only their own options, options that would do nothing are
hidden, the preview zooms and pans and still reports clicks in map pixels, and the legend can be switched off."""
import sys

from PyQt6.QtCore import QPoint, QPointF, QStandardPaths, Qt
from PyQt6.QtGui import QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)

import ui.launch_screen as ls

ls.LaunchScreen.exec = lambda self: (setattr(self, "result_action", ("new", None)), 1)[1]

from ui.geomorph_dialog import GeomorphDialog
from ui.main_window import MainWindow

MainWindow._offer_recovery = lambda self: self.overlay.open_menu()
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.information = staticmethod(lambda *a, **k: None)

win = MainWindow()
win.settings.clear()
win.show()
app.processEvents()
win.overlay.hide()
dlg = GeomorphDialog(win, win, sync=True)
dlg.resize(1280, 760)
dlg.show()
app.processEvents()

# ---- one kind at a time ----------------------------------------------------------------------------------
dlg.tabs.setCurrentIndex(0)
app.processEvents()
assert dlg.cb_ship_type.isVisibleTo(dlg) and not dlg.cb_arch.isVisibleTo(dlg)
assert not dlg.more.body.isVisible(), "advanced options start folded away"
dlg.more.button.setChecked(True)
app.processEvents()
assert dlg.box_ship.isVisibleTo(dlg) and not dlg.box_site.isVisibleTo(dlg)
dlg.tabs.setCurrentIndex(1)
app.processEvents()
assert dlg.cb_arch.isVisibleTo(dlg) and not dlg.cb_ship_type.isVisibleTo(dlg)
assert dlg.box_site.isVisibleTo(dlg) and not dlg.box_ship.isVisibleTo(dlg)
dlg.more.button.setChecked(False)
dlg.tabs.setCurrentIndex(0)

# ---- options that would do nothing are hidden or greyed --------------------------------------------------
dlg.cb_incident.setCurrentIndex(dlg.cb_incident.findData("none"))
app.processEvents()
assert not dlg.cb_where.isVisibleTo(dlg) and not dlg.cb_origin.isVisibleTo(dlg)
dlg.cb_incident.setCurrentIndex(dlg.cb_incident.findData("overrun"))
app.processEvents()
assert dlg.cb_where.isVisibleTo(dlg) and not dlg.cb_origin.isVisibleTo(dlg), "where, but not the spread settings"
dlg.cb_where.setCurrentIndex(dlg.cb_where.findData("spread"))
app.processEvents()
assert dlg.cb_origin.isVisibleTo(dlg) and dlg.cb_reach.isVisibleTo(dlg)
for c in dlg.overlay_checks.values():
    c.setChecked(False)
assert not dlg.sl_int.isEnabled(), "'how many' needs a state"
dlg.overlay_checks["power_failure"].setChecked(True)
assert dlg.sl_int.isEnabled()
dlg.ck_decor.setChecked(False)
assert not dlg.sl_decor.isEnabled()
dlg.ck_decor.setChecked(True)
assert dlg.sl_decor.isEnabled()

# ---- room counts the ship has no room for are reported --------------------------------------------------
dlg.sp_tonnage.setValue(600)
dlg.count_spins["medical"].setValue(6)
dlg.ed_seed.setText("ui-test")
dlg.generate()
assert any("asked for 6 medical" in i for i in dlg.result.issues), dlg.result.issues
dlg.count_spins["medical"].setValue(-1)
dlg.sp_tonnage.setValue(1500)
dlg.overlay_checks["lockdown"].setChecked(True)
dlg.sl_int.setValue(100)
dlg.generate()
app.processEvents()

# ---- the preview zooms, pans and fits ---------------------------------------------------------------------
view = dlg.preview
pm = view.pixmap()
assert pm is not None and not pm.isNull()
view.fit()
z0 = view.zoom()
centre = QPointF(view.viewport().width() / 2, view.viewport().height() / 2)
wheel = QWheelEvent(centre, view.viewport().mapToGlobal(centre), QPoint(0, 0), QPoint(0, 240),
                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
view.wheelEvent(wheel)
assert view.zoom() > z0 * 1.3, (z0, view.zoom())
before = view.mapToScene(int(centre.x()), int(centre.y()))
press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, centre, Qt.MouseButton.RightButton,
                    Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
move = QMouseEvent(QMouseEvent.Type.MouseMove, centre + QPointF(80, 50), Qt.MouseButton.NoButton,
                   Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
release = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, centre + QPointF(80, 50), Qt.MouseButton.RightButton,
                      Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
clicks = []
view.clicked.connect(lambda x, y, b: clicks.append((x, y, b)))
view.mousePressEvent(press)
view.mouseMoveEvent(move)
view.mouseReleaseEvent(release)
after = view.mapToScene(int(centre.x()), int(centre.y()))
assert abs(after.x() - before.x()) > 5 or abs(after.y() - before.y()) > 5, "right-drag pans"
assert not clicks, "a right drag is not a right click"
# a left click is reported in map pixels, whatever the zoom
spot = view.mapFromScene(QPointF(120, 90))
lp = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(spot), Qt.MouseButton.LeftButton,
                 Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
view.mousePressEvent(lp)
view.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(spot), Qt.MouseButton.LeftButton,
                                   Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
assert clicks and abs(clicks[-1][0] - 120) <= 1 and abs(clicks[-1][1] - 90) <= 1 and clicks[-1][2] == 1, clicks
view.fit()
assert abs(view.zoom() - z0) < 1e-6

# ---- the legend toggle: preview and export --------------------------------------------------------------
with_legend = view.pixmap().toImage()
dlg.ck_legend.setChecked(False)
app.processEvents()
without = view.pixmap().toImage()
assert with_legend != without, "the symbols and legend go away"
dlg.ck_legend.setChecked(True)
assert win.settings.value("geomorph/legend", type=bool) is True
print("generator layout ok")
