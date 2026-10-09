"""Offscreen checks: layer eye/export buttons, mirror copies, grid copies, the
stamp hotbar, group selection, missing images, the asset-folder fallback,
recent maps, previews, rolling backups, the unsaved-changes prompt, export
names and the app icon."""
import atexit
import faulthandler
import json
import os
import struct
import sys
import tempfile

faulthandler.dump_traceback_later(300, exit=True)   # a stuck dialog fails loudly

from PyQt6.QtCore import QEvent, QPointF, QStandardPaths, Qt
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox

app = QApplication.instance() or QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)   # keep settings and app data out of the real profile

import ui.launch_screen as ls

ls.LaunchScreen.exec = lambda self: (setattr(self, "result_action", ("new", None)) or 1)
for name in ("information", "warning", "critical"):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
# e.g. "Recover auto-saved map?" left behind by an interrupted run: answer No
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
QMenu.exec = lambda self, *args, **kwargs: None      # menus must never block

import ui.tool_dialogs as tool_dialogs
import ui.main_window as mw
from core import exporter
from core.backups import list_backups
from core.project import Piece, Project
from ui.app_icon import ICO_SIZES, app_icon, ico_bytes
from ui.context_menu import build_canvas_menu, build_guide_menu
from ui.export_dialog import ExportDialog
from ui.main_window import MainWindow

shown_missing = []


def fake_missing_exec(self):
    shown_missing.append(self.list.count())
    return QDialog.DialogCode.Accepted


tool_dialogs.MissingAssetsDialog.exec = fake_missing_exec

win = MainWindow()
win._autosave_timer.stop()                    # no timed auto-saves during the checks
win.settings.clear()
win._clear_all_stamps()                       # nothing pinned by an earlier run


def _leave_no_stamps():
    win.settings.setValue("stamps/slots", "[]")
    win.settings.sync()


atexit.register(_leave_no_stamps)             # even if a check fails
win._reset_layout()
win.show()
app.processEvents()
canvas = win.canvas
project = win.project
cell = project.cell_size
canvas.zoom, canvas.pan_x, canvas.pan_y = 1.0, 0.0, 0.0      # screen == world
LEFT = Qt.MouseButton.LeftButton
CTRL = Qt.KeyboardModifier.ControlModifier
NOMOD = Qt.KeyboardModifier.NoModifier
temp_root = tempfile.mkdtemp(prefix="sceneboard-tools-")


def click(x, y, mods=NOMOD):
    pos = QPointF(x, y)
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else LEFT
        event = QMouseEvent(kind, pos, canvas.mapToGlobal(pos), LEFT, buttons, mods)
        if kind == QEvent.Type.MouseButtonPress:
            canvas.mousePressEvent(event)
        else:
            canvas.mouseReleaseEvent(event)


def labels():
    return [label for label, _ in win.history.undos]


def menu_texts(menu):
    out = []
    for action in menu.actions():
        out.append(action.text())
        if action.menu() is not None:
            out.extend("  " + text for text in menu_texts(action.menu()))
    return out


def fresh_level():
    level = canvas.level
    level.pieces.clear()
    level.guides.clear()
    level.zones.clear()
    canvas.select([])
    return level


# ---- app icon: window icon, committed .ico ----------------------------------
assert not win.windowIcon().isNull(), "the main window should carry the app icon"
icon = app_icon()
assert {s.width() for s in icon.availableSizes()} >= {16, 32, 48, 256}
blob = ico_bytes()
assert struct.unpack_from("<HHH", blob, 0) == (0, 1, len(ICO_SIZES))
ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "icons",
                        "SceneBoard.ico")
with open(ico_path, "rb") as handle:
    committed = handle.read()
_reserved, kind, count = struct.unpack_from("<HHH", committed, 0)
assert kind == 1 and count == len(ICO_SIZES), "ui/icons/SceneBoard.ico is a multi-size icon"
sizes = sorted((committed[6 + 16 * i] or 256) for i in range(count))
assert sizes == sorted(ICO_SIZES), sizes
print("icon ok")

# ---- layers: eye + export buttons, export exclusion -----------------------------
level = fresh_level()
floor, walls = level.layers[0], level.layers[1]
win.layers.set_project(project, level)
row = win.layers.list.itemWidget(win.layers.list.item(1))
assert row.layer is walls and not row.btn_visible.icon().isNull()
assert not row.btn_export.icon().isNull() and not row.btn_lock.icon().isNull()
assert "Included in PNG/PDF exports" in row.btn_export.toolTip()
row._fit_controls(220)                    # narrow inspector: the name keeps its room
assert row.sl.isHidden() and row.btn_solo.isHidden() and not row.lbl.isHidden()
row._fit_controls(400)
assert not row.sl.isHidden() and not row.btn_solo.isHidden()
red = Piece(name="red", x=0, y=0, w=200, h=200, layer=walls.id, is_patch=True,
            patch_color="#ff0000", snap=False)
level.add(red)
before = len(labels())
row.btn_export.click()
assert walls.export is False and labels()[-1] == "Toggle layer export"
assert len(labels()) == before + 1
assert "Left out of PNG/PDF exports" in row.btn_export.toolTip()
img = exporter.render_level(project, level, False, 1.0)
assert img.pixelColor(100, 100) == QColor(level.background), "non-export layer leaked"
shot = canvas.grab().toImage()
dpr = shot.devicePixelRatio() or 1.0
on_canvas = shot.pixelColor(int(100 * dpr), int(100 * dpr))
assert on_canvas.red() > 200 and on_canvas.green() < 60, "still shown on the canvas"
win.undo()
walls = canvas.level.layers[1]
assert walls.export is True
img = exporter.render_level(project, canvas.level, False, 1.0)
assert img.pixelColor(100, 100).red() > 200, "included again after undo"
win.layers._toggle_flag(walls.id, "export")
assert canvas.level.layers[1].export is False
dialog = ExportDialog(project, canvas, win)
notes = [w.text() for w in dialog.findChildren(type(dialog.le_path))]
assert any("Left out of exports" in text and walls.name in text for text in notes), notes
win.layers._set_all("export", True)
assert all(layer.export for layer in canvas.level.layers)
win.layers._toggle_flag(canvas.level.layers[1].id, "visible")
assert canvas.level.layers[1].visible is False
img = exporter.render_level(project, canvas.level, False, 1.0)
assert img.pixelColor(100, 100) == QColor(canvas.level.background), "hidden layers never export"
win.layers._set_all("visible", True)
win.layers._set_opacity(canvas.level.layers[2].id, 0.5)    # right-click → Opacity
assert canvas.level.layers[2].opacity == 0.5 and labels()[-1] == "Layer opacity"
win.layers.set_theme("light", "#2563a6")
print("layer buttons ok")

# ---- mirror copies ------------------------------------------------------------------
level = fresh_level()
left = Piece(name="left", asset_path="", x=140, y=140, w=140, h=70, rotation=30, snap=False)
label = Piece(name="label", is_text=True, text="DECK", x=140, y=280, w=140, h=70, snap=False)
middle = Piece(name="middle", x=project.canvas_w / 2 - 35, y=420, w=70, h=70, snap=False)
for piece in (left, label, middle):
    level.add(piece)
left.group_id = label.group_id = "g-orig"
canvas.add_guide("v", 700)
canvas.select([left, label, middle])
made = canvas.mirror_selection("v", 700)
assert len(made) == 3, "all three are off the guide, so all three are mirrored"
copy_left = next(p for p in made if p.name == "left")
copy_label = next(p for p in made if p.name == "label")
assert abs(copy_left.center[0] - (1400 - left.center[0])) < 1e-6
assert copy_left.flip_h and abs(copy_left.rotation - 330) < 1e-6
assert not copy_label.flip_h, "text stays readable"
assert copy_left.group_id == copy_label.group_id not in ("", "g-orig")
assert canvas.selection == {p.id for p in made} and labels()[-1] == "Mirror copy"
win.undo()
assert len(canvas.level.pieces) == 3
canvas.select([p for p in canvas.level.pieces if p.name == "middle"])
assert win._mirror_selection_center("v") == [], "centered on the line: nothing to copy"
canvas.select([p for p in canvas.level.pieces if p.name == "left"])
made = win._mirror_selection_nearest("v")
assert len(made) == 1 and abs(made[0].center[0] - (1400 - left.center[0])) < 1e-6
canvas.select([])
assert win._mirror_selection_center("h") == []
lines = canvas.mirror_lines()
assert lines[0][0] == "v" and lines[1][0] == "h" and lines[2][1] == 700
canvas.select([p for p in canvas.level.pieces if p.id == left.id])
menu_text = menu_texts(build_canvas_menu(win, None))
assert any("Mirror copy" in t for t in menu_text)
assert any("Across the vertical guide at x 10 sq" in t for t in menu_text), menu_text
assert any("Duplicate as grid" in t for t in menu_text)
assert any("Pin to stamp key" in t for t in menu_text)
guide = canvas.level.guides[0]
guide_menu = menu_texts(build_guide_menu(win, {"guide": guide.id}))
assert "Mirror selection across this guide" in guide_menu, guide_menu
project.show_centerlines = True
cx, cy = canvas.world_to_screen(project.canvas_w / 2, project.canvas_h / 2)
assert canvas._centerline_at(cx, 40) == "v" and canvas._centerline_at(40, cy) == "h"
assert canvas._centerline_at(cx + 30, cy + 30) is None
requested = []
canvas.guideMenuRequested.connect(lambda _pos, target: requested.append(target))
canvas.select([])
event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(cx, 60),
                    canvas.mapToGlobal(QPointF(cx, 60)), Qt.MouseButton.RightButton,
                    Qt.MouseButton.RightButton, NOMOD)
canvas._open_context_menu(event)
assert requested and requested[-1] == {"centerline": "v"}, requested
center_menu = menu_texts(build_guide_menu(win, {"centerline": "v"}))
assert center_menu[0].startswith("Mirror selection across this line"), center_menu
assert "Hide center lines" in center_menu
win._set_show_centerlines(False)
assert not project.show_centerlines and not win.props.chk_centerlines.isChecked()
win._set_show_centerlines(True)
print("mirror ok")

# ---- duplicate as grid ------------------------------------------------------------------
level = fresh_level()
pod = Piece(name="pod", x=140, y=140, w=140, h=70, snap=True)
level.add(pod)
canvas.select([pod])
made = canvas.duplicate_as_grid(2, 3, gap_x=cell, gap_y=0)
assert len(made) == 5 and labels()[-1] == "Duplicate as grid"
spots = sorted((p.x, p.y) for p in canvas.level.pieces)
assert spots == sorted([(140 + c * 210, 140 + r * 70) for r in range(2) for c in range(3)]), spots
assert len(canvas.selection) == 6
win.undo()
assert len(canvas.level.pieces) == 1
canvas.select(canvas.level.pieces)
grid_dialog = tool_dialogs.GridCopyDialog(canvas.selection_bounds(), cell,
                                          (project.canvas_w, project.canvas_h),
                                          {"rows": 1, "cols": 4}, win)
assert "Makes 3 copies" in grid_dialog.info.text()
grid_dialog.sp_cols.setValue(50)
assert "past the right edge" in grid_dialog.info.text()
grid_dialog.sp_cols.setValue(1)
assert not grid_dialog.ok_button.isEnabled()
tool_dialogs.GridCopyDialog.exec = lambda self: (self.sp_rows.setValue(1),
                                                 self.sp_cols.setValue(2),
                                                 QDialog.DialogCode.Accepted)[-1]
win._duplicate_as_grid_dialog()
assert len(canvas.level.pieces) == 2
assert json.loads(win.settings.value("tools/grid_copy"))["cols"] == 2
print("grid copy ok")

# ---- group selection ------------------------------------------------------------------------
level = fresh_level()
a = Piece(name="a", x=70, y=70, w=70, h=70, snap=False)
b = Piece(name="b", x=210, y=70, w=70, h=70, snap=False)
c = Piece(name="c", x=350, y=70, w=70, h=70, snap=False)
for piece in (a, b, c):
    level.add(piece)
canvas.select([a, b])
canvas.group()
canvas.select([])
click(100, 100)
assert canvas.selection == {a.id, b.id}, "a click picks the whole group"
click(100, 100, CTRL)
assert canvas.selection == {a.id}, "Ctrl+click picks one member"
canvas.select([])
click(380, 100)
assert canvas.selection == {c.id}
canvas.select([a, b])
canvas.duplicate()
copies = canvas.selected_pieces()
assert len(copies) == 2 and copies[0].group_id == copies[1].group_id
assert copies[0].group_id not in ("", a.group_id), "copies form their own group"
print("groups ok")

# ---- reference floor uses its opacity -----------------------------------------------------------
level = fresh_level()
other = project.add_level("Other")
other.add(Piece(x=0, y=0, w=300, h=300, is_patch=True, patch_color="#ffffff", snap=False))
canvas.level_index = 0
canvas.set_ref(True, 1, 0.3)
shot = canvas.grab().toImage()
ghost = shot.pixelColor(int(150 * (shot.devicePixelRatio() or 1)),
                        int(150 * (shot.devicePixelRatio() or 1)))
assert ghost.red() < 200, f"reference floor should be faint, got {ghost.name()}"
canvas.set_ref(False, -1, 0.28)
project.levels.remove(other)
print("reference opacity ok")

# ---- stamp hotbar ---------------------------------------------------------------------------------
store = os.path.join(temp_root, "store")
os.makedirs(os.path.join(store, "props"))
crate = QImage(64, 64, QImage.Format.Format_ARGB32)
crate.fill(QColor("#33aa55"))
crate.save(os.path.join(store, "props", "crate.png"))
project.asset_store = store
win.library.set_project(project, win.library.library, win._add_at_center)
level = fresh_level()
assert not win.stamp_bar.isVisible(), "an empty hotbar stays out of the way"
win._pin_asset_stamp(0, "props/crate.png")
assert win.stamp_bar.isVisible() and win.stamp_slots[0]["asset_path"] == "props/crate.png"
assert win.library.stamp_labels()[0] == "crate"
# door mode comes from the name alone now: there is no asset classification
os.makedirs(os.path.join(store, "doors"), exist_ok=True)
hatch = QImage(64, 64, QImage.Format.Format_ARGB32)
hatch.fill(QColor("#aa5533"))
hatch.save(os.path.join(store, "doors", "Iris Hatch.png"))
win.library.set_project(project, win.library.library, win._add_at_center)
assert not win._is_door_asset("props/crate.png")
assert win._is_door_asset("doors/Iris Hatch.png")
win._pin_asset_stamp(1, "doors/Iris Hatch.png")
assert win.stamp_slots[1]["edge"], "a door is pinned in door mode"
stored = mw.slots_from_json(win.settings.value("stamps/slots"))
assert stored[0]["asset_path"] == "props/crate.png", "pinned keys are remembered"
key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_1, NOMOD, "1")
win.keyPressEvent(key)
assert canvas.stamp_tool and canvas.stamp_slot == 0
assert win.stamp_bar.buttons[0].active
canvas._stamp_hover = (350.0, 350.0)
canvas.grab()                                   # draws the faint preview
placed = canvas._place_stamp(350.0, 350.0)
assert placed.asset_path == "props/crate.png" and placed in canvas.level.pieces
assert labels()[-1] == "Stamp node"
win.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_1, NOMOD, "1"))
assert not canvas.stamp_tool and not win.stamp_bar.buttons[0].active, "same key puts it away"
win.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_5, NOMOD, "5"))
assert not canvas.stamp_tool, "an empty key does nothing"
text_node = Piece(is_text=True, text="EXIT", x=70, y=70, w=140, h=70, rotation=90)
canvas.level.add(text_node)
canvas.select([text_node])
win._pin_selected_stamp(2)
assert win.stamp_slots[2]["kind"] == "node" and win.stamp_slots[2]["template"]["rotation"] == 90
win._arm_stamp(2)
copy_text = canvas._place_stamp(500.0, 500.0)
assert copy_text.is_text and copy_text.text == "EXIT" and copy_text.rotation == 90
canvas.cancel_extra_tool()
embedded = Piece(embedded="aGVsbG8=", w=10, h=10)
canvas.level.add(embedded)
canvas.select([embedded])
win._pin_selected_stamp(3)
assert win.stamp_slots[3] is None, "embedded images are refused"
win._view_set("stamps", False)
assert not win.stamp_bar.isVisible()
win._view_set("stamps", True)
assert win.stamp_bar.isVisible()
win._clear_stamp(0)
assert win.stamp_slots[0] is None
win._clear_all_stamps()
assert not any(win.stamp_slots) and not win.stamp_bar.isVisible()
print("stamp hotbar ok")

# ---- missing images: marker, dialog on open, relink ---------------------------------------------
level = fresh_level()
lost = Piece(asset_path="old-pack/props/crate.png", name="crate", x=70, y=70, w=140, h=140)
level.add(lost)
assert project.missing_assets() == {"old-pack/props/crate.png": 1}
canvas.grab()                                          # red marker path draws
shown_missing.clear()
win._find_missing_images()
assert shown_missing == [1], shown_missing
relinked, ambiguous, not_found, still = win._relink_missing()
assert (relinked, ambiguous, not_found, still) == (1, 0, 0, {})
assert lost.asset_path == "props/crate.png" and labels()[-1] == "Relink missing images"
print("missing images ok")

# ---- asset folder: saved as "this app's library", fallback on another PC -------------------------
default_store = win._default_store()
project.asset_store = default_store
map_path = os.path.join(temp_root, "maps", "Deck.bmap")
os.makedirs(os.path.dirname(map_path))
win._current_file = map_path
assert win._write(map_path)
with open(map_path, encoding="utf-8") as handle:
    saved = json.load(handle)
assert saved["asset_store"] == "", "the app's own library is saved store-independently"
assert not os.path.exists(os.path.join(temp_root, "maps", "Deck.png")), \
    "no preview next to the map"
assert os.path.isfile(win._thumbnail_for(map_path))
saved["asset_store"] = os.path.join(temp_root, "some-other-pc", "asset_store")
foreign = os.path.join(temp_root, "maps", "Foreign.bmap")
with open(foreign, "w", encoding="utf-8") as handle:
    json.dump(saved, handle)
shown_missing.clear()
assert win._load_file(foreign)
app.processEvents()
assert win._same_path(win.project.asset_store, default_store), win.project.asset_store
assert "isn't on this computer" in win.status.currentMessage()
pack_store = os.path.join(temp_root, "pack", "assets")
os.makedirs(pack_store)
saved["asset_store"] = pack_store
with open(foreign, "w", encoding="utf-8") as handle:
    json.dump(saved, handle)
win._load_file(foreign)
assert win._same_path(win.project.asset_store, pack_store)
assert win.project.fallback_asset_stores == [default_store], "lookups fall back to the app library"
project = win.project
canvas = win.canvas
print("asset folder ok")

# ---- recent maps live in app data -------------------------------------------------------------------
assert win._recent_file.startswith(QStandardPaths.writableLocation(
    QStandardPaths.StandardLocation.AppDataLocation))
assert mw.RECENT_FILE == win._recent_file
assert os.path.abspath(foreign) == win.recent[0]
assert mw.load_recent(win._recent_file)[0] == os.path.abspath(foreign)
win._push_recent(map_path)
assert win.recent[:2] == [os.path.abspath(map_path), os.path.abspath(foreign)]
legacy = os.path.join(temp_root, "legacy-recent.json")
with open(legacy, "w", encoding="utf-8") as handle:
    json.dump(["C:/old/map.bmap"], handle)
original_legacy, original_recent = mw.LEGACY_RECENT_FILE, win._recent_file
mw.LEGACY_RECENT_FILE = legacy
win._recent_file = os.path.join(temp_root, "fresh-appdata", "recent.json")
assert win._load_recent_list() == ["C:/old/map.bmap"], "a source checkout's list carries over"
assert os.path.isfile(win._recent_file)
mw.LEGACY_RECENT_FILE, win._recent_file = original_legacy, original_recent
mw.RECENT_FILE = original_recent
print("recent maps ok")

# ---- previews never overwrite an export -------------------------------------------------------------
export_png = os.path.join(temp_root, "maps", "Deck.png")
big = QImage(900, 700, QImage.Format.Format_RGB32)
big.fill(QColor("#123456"))
big.save(export_png)
win._current_file = map_path
win._write(map_path)
assert QImage(export_png).size().width() == 900, "saving must not touch Deck.png"
win._delete_map_thumbnails(map_path)
assert os.path.isfile(export_png), "a full-size PNG beside the map is never deleted"
assert not os.path.isfile(mw.thumbnail_path(win._app_data_dir, map_path))
small = os.path.join(temp_root, "maps", "Small.png")
QImage(256, 200, QImage.Format.Format_RGB32).save(small)
win._delete_map_thumbnails(os.path.join(temp_root, "maps", "Small.bmap"))
assert not os.path.isfile(small), "an old-style thumbnail beside the map is cleaned up"
print("previews ok")

# ---- rolling backups from auto-save, restore is undoable --------------------------------------------
win.autosave_interval_minutes = 5
folder = win._backup_folder()
for index in range(6):
    win.project.name = f"Deck v{index}"
    win._mark_dirty(True)
    win._autosave_tick()
backups = list_backups(folder)
assert len(backups) == 4, [b["path"] for b in backups]
names = [Project.from_dict(json.load(open(b["path"], encoding="utf-8"))).name
         for b in backups]
assert names == ["Deck v4", "Deck v3", "Deck v2", "Deck v1"], names
current_store = win.project.asset_store
assert win._restore_backup(backups[-1]["path"])
assert win.project.name == "Deck v1" and win._dirty
assert win.project.asset_store == current_store
assert labels()[-1] == "Restore backup"
win.undo()
assert win.project.name == "Deck v5"
chosen = {}


def fake_backup_exec(self):
    chosen["rows"] = self.list.count()
    self.chosen_path = backups[0]["path"]
    return QDialog.DialogCode.Accepted


tool_dialogs.BackupDialog.exec = fake_backup_exec
win._restore_backup_dialog()
assert chosen["rows"] == 4 and win.project.name == "Deck v4"
saved_current = win._current_file
win._current_file = None
win._restore_backup_dialog()                     # unsaved map: explains instead
win._current_file = saved_current
print("backups ok")

# ---- unsaved changes: Save / Don't Save / Cancel -------------------------------------------------------
answers = []
win._ask_unsaved_changes = lambda: answers.pop(0)
win._mark_dirty(True)
answers.append("cancel")
assert win._confirm_discard() is False
answers.append("discard")
assert win._confirm_discard() is True
saves = []
win._save = lambda: (saves.append(1), False)[-1]
answers.append("save")
assert win._confirm_discard() is False, "a failed/cancelled save keeps the map open"
win._save = lambda: (saves.append(2), True)[-1]
answers.append("save")
assert win._confirm_discard() is True and saves == [1, 2]
answers.append("cancel")
close_event = __import__("PyQt6.QtGui", fromlist=["QCloseEvent"]).QCloseEvent()
win.closeEvent(close_event)
assert not close_event.isAccepted(), "Cancel keeps the window open"
win._mark_dirty(False)
assert win._confirm_discard() is True and not answers
print("unsaved prompt ok")

# ---- export names, folders and the missing-image warning ------------------------------------------------
level = canvas.level
level.pieces.clear()
win._current_file = map_path
export_dir = os.path.join(temp_root, "exports")
os.makedirs(export_dir)
win.remember_export_dir(os.path.join(export_dir, "x.png"))
base, folder = win.export_defaults()
assert base == "Deck" and win._same_path(folder, export_dir)
dialog = ExportDialog(project, canvas, win)
assert dialog._default_file_name() == "Deck.png"
win.project.add_level("Lower deck")
assert dialog._default_file_name() == "Deck - Level 1.png"
pdf_dialog = ExportDialog(project, canvas, win, file_format="pdf")
assert pdf_dialog._default_file_name() == "Deck.pdf"
level.add(Piece(asset_path="nowhere/vent.png", x=0, y=0, w=70, h=70))
asked = []
ExportDialog._confirm_missing_images = lambda self, missing: (asked.append(dict(missing)), False)[-1]
dialog.output_path = os.path.join(temp_root, "elsewhere", "out.png")
dialog._export()
assert asked == [{"nowhere/vent.png": 1}] and not os.path.exists(dialog.output_path)
ExportDialog._confirm_missing_images = lambda self, missing: True
dialog._export()
assert os.path.isfile(dialog.output_path)
assert win._same_path(win.export_defaults()[1], os.path.dirname(dialog.output_path))
all_dir = os.path.join(temp_root, "all-levels")
os.makedirs(all_dir)
all_dialog = ExportDialog(project, canvas, win)
all_dialog.rb_all.setChecked(True)
all_dialog.output_path = all_dir
all_dialog._export()
assert sorted(os.listdir(all_dir))[0].startswith("Deck_01_"), os.listdir(all_dir)
assert win._same_path(win.library._import_start_dir(), win._last_dir()), \
    "imports start in the last folder used, not where the app started"
win.library._remember_import_dir(os.path.join(export_dir, "pack.zip"))
assert win._same_path(win.library._import_start_dir(), export_dir)
print("export names ok")

win._clear_all_stamps()
win.settings.clear()
win._mark_dirty(False)
print("ALL TOOL TESTS PASSED")
