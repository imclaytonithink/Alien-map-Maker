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
# ---- levels, which way they go, a cargo lift ---------------------------------------------------------------
dlg.tabs.setCurrentIndex(1)
dlg.cb_arch.setCurrentIndex(dlg.cb_arch.findData("Research facility"))
app.processEvents()
assert dlg.sp_levels.isEnabled() and dlg.cb_direction.isEnabled() and dlg.cb_cargo.isEnabled()
assert dlg.sp_levels.specialValueText().startswith("Auto (")
dlg.sp_levels.setValue(5)
dlg.cb_direction.setCurrentIndex(dlg.cb_direction.findData("both"))
dlg.cb_cargo.setCurrentIndex(1)
o = dlg.options()
assert o["levels"] == 5 and o["direction"] == "both" and o["cargo_lift"] is True, o
dlg.ed_seed.setText("ui-levels")
dlg.generate()
assert [g.name for g in dlg.result.grids][0] == "Floor 3" and dlg.result.grids[-1].name == "Basement 2"
assert any(v["kind"] == "cargo_lift" for v in dlg.result.layout.vertical)
dlg.apply_options(dict(o, levels=2, direction="down", cargo_lift=False))
assert dlg.sp_levels.value() == 2 and dlg.cb_direction.currentData() == "down" and dlg.cb_cargo.currentData() is False
dlg.cb_arch.setCurrentIndex(dlg.cb_arch.findData("Space station (ring / spindle / cylinder)")
                            if dlg.cb_arch.findData("Space station (ring / spindle / cylinder)") >= 0
                            else next(i for i in range(dlg.cb_arch.count())
                                      if dlg.archetypes[dlg.cb_arch.itemData(i)]["topology"] == "ring"))
app.processEvents()
assert not dlg.sp_levels.isEnabled() and not dlg.cb_direction.isEnabled(), "a ring station is one level"
assert "single level" in dlg.sp_levels.toolTip()
assert "levels" not in dlg.options()
dlg.cb_arch.setCurrentIndex(dlg.cb_arch.findData("Deep mine"))
app.processEvents()
assert dlg.sp_levels.isEnabled() and not dlg.cb_direction.isEnabled(), "a mine only goes down"
dlg.cb_arch.setCurrentIndex(dlg.cb_arch.findData("Research facility"))
dlg.sp_levels.setValue(0)
dlg.cb_direction.setCurrentIndex(0)
dlg.cb_cargo.setCurrentIndex(0)
dlg.tabs.setCurrentIndex(0)

# ---- a room's state, chosen by hand -------------------------------------------------------------------------
from geomorph import dressing
dlg.overlay_checks["power_failure"].setChecked(True)
dlg.ed_seed.setText("ui-states")
dlg.generate()
res = dlg.result
dark = res.overlays["power_failure"][0]
dlg.cb_zone.setCurrentIndex(dlg.cb_zone.findData(dark))
assert dlg.cb_room_state.currentData() == "power_failure"
dlg.cb_room_state.setCurrentIndex(dlg.cb_room_state.findData("lockdown"))
dlg._room_state_chosen()
assert dressing.room_state(res, dark) == "lockdown" and dark not in res.overlays["power_failure"]
assert "LOCKDOWN" in dlg.txt_key.toPlainText()
dlg._undo()
assert dressing.room_state(dlg.result, dark) == "power_failure" and dlg.cb_room_state.currentData() == "power_failure"
inner = next((z for z in res.zones if not any(
    dressing._hull_point(p, side) for g in res.grids for p in g.placed if p.zone == z for side in "NESW")), None)
if inner:
    dlg.cb_zone.setCurrentIndex(dlg.cb_zone.findData(inner))
    dlg.cb_room_state.setCurrentIndex(dlg.cb_room_state.findData("breach"))
    dlg._room_state_chosen()
    assert dressing.room_state(dlg.result, inner) != "breach" and "hull" in dlg.lbl_status.text()
# ---- the description and key can be edited, and survive making the map again ------------------------------
dlg.tabs.setCurrentIndex(0)
dlg.ck_live.setChecked(False)
dlg.ed_seed.setText("ui-edits")
dlg.generate()
res = dlg.result
assert not dlg.txt_main.isReadOnly() and not dlg.txt_key.isReadOnly()
text = dlg.txt_main.toPlainText().split("\n")
text[0] = "The Night Ferry"
hooks_at = text.index("ADVENTURE HOOKS")
text.insert(hooks_at + 1, "  [rumour] The captain never left her cabin.")
dlg.txt_main.setPlainText("\n".join(text))
assert res.text["title"] == "The Night Ferry"
assert {"type": "rumour", "text": "The captain never left her cabin."} in res.text["hooks"]
key_lines = dlg.txt_key.toPlainText().split("\n")
first = res.key[0]
key_lines[0] = f"{first['n']}. Captain's cabin (level {first['level'] + 1}) — Locked from the inside."
dlg.txt_key.setPlainText("\n".join(key_lines))
assert first["title"] == "Captain's cabin" and first["text"] == "Locked from the inside." and first.get("edited")
dlg.lst_notes.setCurrentRow(0)
assert dlg.ed_note_gm.toPlainText() == "Locked from the inside.", "Room notes show the same words"
dlg.ed_note_player.setPlainText("A heavy door, scorched round the lock.")
# another option, same ship: the edits stay
dlg.cb_cond.setCurrentIndex(dlg.cb_cond.findData("Derelict"))
dlg.generate()
new = dlg.result
assert new is not res
assert new.text["title"] == "The Night Ferry" and "The Night Ferry" in dlg.txt_main.toPlainText()
same = next((e for e in new.key if (e.get("zone"), e.get("tile")) == (first.get("zone"), first.get("tile"))), None)
if same is not None:
    assert same["text"] == "Locked from the inside." and same["player"] == "A heavy door, scorched round the lock."
assert "edits were kept" in dlg.lbl_status.text()
# a different kind of place starts fresh
dlg.tabs.setCurrentIndex(1)
dlg.generate()
assert dlg.result.text["title"] != "The Night Ferry"
# ---- with Auto-update on, changes on the right are not thrown away by a remake -------------------------------
dlg.tabs.setCurrentIndex(0)
dlg.overlay_checks["power_failure"].setChecked(True)
dlg.ed_seed.setText("ui-live")
dlg.generate()
dlg.ck_live.setChecked(True)
dlg._live_timer.stop()
res = dlg.result
zone = next(z for z in res.zones if dressing.room_state(res, z) == "normal")
dlg.cb_zone.setCurrentIndex(dlg.cb_zone.findData(zone))
dlg.cb_room_state.setCurrentIndex(dlg.cb_room_state.findData("lockdown"))
dlg._room_state_chosen()
assert not dlg._live_timer.isActive(), "a room's state is not an option: no remake"
assert dlg.result is res and dressing.room_state(res, zone) == "lockdown"
tiles_before = [(p.tile.id, p.x, p.y) for g in res.grids for p in g.placed]
dlg._reroll_zone()
app.processEvents()
assert not dlg._live_timer.isActive(), "a re-roll does not set off a remake"
assert dlg.result is res, "the re-rolled map is the one kept"
dlg.cb_cond.setCurrentIndex((dlg.cb_cond.currentIndex() + 1) % dlg.cb_cond.count())
assert dlg._live_timer.isActive(), "options on the left still remake the map"
dlg._live_timer.stop()
dlg.ck_live.setChecked(False)
print("generator layout ok")
