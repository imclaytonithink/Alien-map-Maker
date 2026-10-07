"""Dialogs for grid copies, missing images and auto-save backups."""
from __future__ import annotations

import datetime
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout,
)

from core.guides import format_amount


# ---------------------------------------------------------------------------
# Duplicate as grid
# ---------------------------------------------------------------------------
class GridCopyDialog(QDialog):
    """Rows, columns and gaps for repeating the selection as one block."""

    def __init__(self, bounds, cell: float, canvas_size, defaults=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Duplicate as grid")
        self.setMinimumWidth(380)
        self._bounds = bounds                     # x0, y0, x1, y1 in world px
        self._cell = max(1.0, float(cell))
        self._canvas = canvas_size                 # (width, height) world px
        defaults = defaults or {}
        layout = QVBoxLayout(self)
        intro = QLabel("Repeat the selection as one block, in rows going down "
                       "and columns going right. The original stays in the "
                       "top-left corner.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.sp_rows = QSpinBox()
        self.sp_rows.setRange(1, 50)
        self.sp_rows.setValue(int(defaults.get("rows", 1)))
        self.sp_cols = QSpinBox()
        self.sp_cols.setRange(1, 50)
        self.sp_cols.setValue(int(defaults.get("cols", 3)))
        self.sp_gap_x = self._gap_box(defaults.get("gap_x", 0.0))
        self.sp_gap_y = self._gap_box(defaults.get("gap_y", 0.0))
        form.addRow("Rows", self.sp_rows)
        form.addRow("Columns", self.sp_cols)
        form.addRow("Gap between columns", self.sp_gap_x)
        form.addRow("Gap between rows", self.sp_gap_y)
        layout.addLayout(form)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Make copies")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for box in (self.sp_rows, self.sp_cols, self.sp_gap_x, self.sp_gap_y):
            box.valueChanged.connect(self._update_info)
        self._update_info()

    def _gap_box(self, value) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(0.0, 50.0)
        box.setDecimals(2)
        box.setSingleStep(0.5)
        box.setSuffix(" sq")
        box.setValue(float(value or 0.0))
        return box

    def values(self) -> dict:
        return {"rows": self.sp_rows.value(), "cols": self.sp_cols.value(),
                "gap_x": self.sp_gap_x.value(), "gap_y": self.sp_gap_y.value()}

    def _update_info(self):
        v = self.values()
        copies = v["rows"] * v["cols"] - 1
        x0, y0, x1, y1 = self._bounds
        block_w, block_h = (x1 - x0) / self._cell, (y1 - y0) / self._cell
        total_w = v["cols"] * block_w + (v["cols"] - 1) * v["gap_x"]
        total_h = v["rows"] * block_h + (v["rows"] - 1) * v["gap_y"]
        text = (f"Makes {copies} cop{'y' if copies == 1 else 'ies'}. Each block is "
                f"{format_amount(block_w)} × {format_amount(block_h)} squares; the whole "
                f"grid covers {format_amount(total_w)} × {format_amount(total_h)} squares.")
        over_x = (x0 / self._cell + total_w) - self._canvas[0] / self._cell
        over_y = (y0 / self._cell + total_h) - self._canvas[1] / self._cell
        past = []
        if over_x > 0.01:
            past.append(f"{format_amount(over_x)} sq past the right edge")
        if over_y > 0.01:
            past.append(f"{format_amount(over_y)} sq past the bottom edge")
        if past:
            text += ("\n⚠ It reaches " + " and ".join(past) + " of the map; copies "
                     "out there are kept but clipped from exports.")
        self.info.setText(text)
        self.ok_button.setEnabled(copies > 0)


# ---------------------------------------------------------------------------
# Missing images
# ---------------------------------------------------------------------------
class MissingAssetsDialog(QDialog):
    """Lists images a map uses that can't be found, with a by-name relink."""

    def __init__(self, missing: dict, store: str, relink_callback, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Missing images")
        self.setMinimumSize(560, 380)
        self._relink = relink_callback          # () -> (relinked, ambiguous, not_found, missing)
        self._store = store
        layout = QVBoxLayout(self)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        row = QHBoxLayout()
        self.btn_relink = QPushButton("Find by file name")
        self.btn_relink.setAutoDefault(False)
        self.btn_relink.setToolTip(
            "Look through the asset library for images with the same file names "
            "and point the map at them. You can undo this.")
        self.btn_relink.clicked.connect(self._find)
        row.addWidget(self.btn_relink)
        row.addStretch(1)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self._show(missing)

    def _show(self, missing: dict):
        self.list.clear()
        count = len(missing)
        nodes = sum(missing.values())
        if not count:
            self.summary.setText("Every image this map uses was found.")
            self.btn_relink.setEnabled(False)
            return
        self.summary.setText(
            f"{count} image(s), used by {nodes} node(s), can't be found in the asset "
            f"library:\n{self._store or '(no asset folder)'}\n\n"
            "They show as grey boxes with a red outline on the canvas, and as grey "
            "boxes in exports. If they were imported into differently named folders, "
            "Find by file name can relink them. Otherwise import the pack or folder "
            "they came from, then use File → Find missing images… again.")
        for path, uses in sorted(missing.items(), key=lambda item: item[0].casefold()):
            name = os.path.basename(path.replace("\\", "/"))
            folder = os.path.dirname(path.replace("\\", "/")) or "(top level)"
            item = QListWidgetItem(f"{name}   —   {folder}   ·   used {uses}×")
            item.setToolTip(path)
            self.list.addItem(item)

    def _find(self):
        relinked, ambiguous, not_found, missing = self._relink()
        parts = [f"Relinked {relinked} image(s)."]
        if ambiguous:
            parts.append(f"{ambiguous} have several images with that name in the "
                         "library, so they were left alone.")
        if not_found:
            parts.append(f"{not_found} weren't in the library at all.")
        self.result_label.setText(" ".join(parts))
        self._show(missing)


# ---------------------------------------------------------------------------
# Auto-save backups
# ---------------------------------------------------------------------------
def describe_time(timestamp: float, now: datetime.datetime | None = None) -> str:
    moment = datetime.datetime.fromtimestamp(timestamp)
    now = now or datetime.datetime.now()
    if moment.date() == now.date():
        return "Today " + moment.strftime("%H:%M:%S")
    if moment.date() == (now - datetime.timedelta(days=1)).date():
        return "Yesterday " + moment.strftime("%H:%M")
    return moment.strftime("%a %d %b %Y %H:%M")


def describe_size(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    return f"{max(1, round(size / 1000))} KB"


class BackupDialog(QDialog):
    """Pick one of the rolling auto-save backups of the current map."""

    def __init__(self, map_name: str, backups: list[dict], autosave_minutes: int,
                 open_folder_callback, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Restore from backup")
        self.setMinimumSize(460, 320)
        self.chosen_path = ""
        layout = QVBoxLayout(self)
        if autosave_minutes:
            when = ("every minute" if autosave_minutes == 1
                    else f"every {autosave_minutes} minutes")
            note = (f"Auto-save runs {when}. Each time, the version of “{map_name}” "
                    "that was on disk goes into a backup, replacing the oldest of "
                    "the four.")
        else:
            note = ("Auto-save is off, so no new backups are being made. Turn it on "
                    "under File → Auto-save to keep the last four versions.")
        label = QLabel(note + "\n\nRestoring replaces what is open now. You can undo "
                       "it, and nothing is written to disk until the map is saved.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.list = QListWidget()
        for number, backup in enumerate(backups):
            text = f"{describe_time(backup['mtime'])}   ·   {describe_size(backup['size'])}"
            if number == 0:
                text += "   (newest)"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, backup["path"])
            item.setToolTip(backup["path"])
            self.list.addItem(item)
        if not backups:
            item = QListWidgetItem("No backups yet for this map.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        folder = QPushButton("Open backups folder")
        folder.setAutoDefault(False)
        folder.clicked.connect(lambda: open_folder_callback())
        row.addWidget(folder)
        row.addStretch(1)
        self.btn_restore = QPushButton("Restore")
        self.btn_restore.setEnabled(False)
        self.btn_restore.setDefault(True)
        self.btn_restore.clicked.connect(self._restore)
        row.addWidget(self.btn_restore)
        cancel = QPushButton("Close")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        layout.addLayout(row)
        self.list.currentItemChanged.connect(
            lambda current, _previous: self.btn_restore.setEnabled(
                bool(current and current.data(Qt.ItemDataRole.UserRole))))
        self.list.itemDoubleClicked.connect(lambda _item: self._restore())
        if backups:
            self.list.setCurrentRow(0)

    def _restore(self):
        item = self.list.currentItem()
        path = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if path:
            self.chosen_path = path
            self.accept()
