"""Export → MU/TH/UR terminal code: check the hand-placed terminals and hand out the code."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                             QVBoxLayout)

from core import muthur as M


class MuthurExportDialog(QDialog):
    """Asks how many terminals the map has, lists them, and gives the code only when all is well."""

    def __init__(self, main, parent=None):
        super().__init__(parent or main)
        self.main = main
        self.project = main.project
        self.scan = None
        self.setWindowTitle("Export MU/TH/UR terminal code")
        self.resize(760, 560)
        lay = QVBoxLayout(self)
        intro = QLabel("Place a MU/TH/UR terminal marker in every room that has a terminal "
                       "(right-click the map → Place MU/TH/UR terminal here). Then say how many "
                       "terminals the map has. The code is given only when every terminal checks out.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel("How many terminals does this map have?"))
        self.sp_count = QSpinBox()
        self.sp_count.setRange(0, M.MAX_TERMINALS)
        self.sp_count.setValue(0)
        self.sp_count.setToolTip("Type the number of terminals you placed. It must match the markers on the map.")
        row.addWidget(self.sp_count)
        row.addStretch(1)
        lay.addLayout(row)
        self.ck_zones = QCheckBox("Hand-drawn gameplay zones take priority over Geomorph rooms")
        self.ck_zones.setChecked(True)
        self.ck_zones.setToolTip("When a marker is inside both a zone you drew and a Geomorph room, "
                                 "use the zone's name. Untick to use the Geomorph room instead.")
        self.ck_zones.toggled.connect(self._rescan)
        lay.addWidget(self.ck_zones)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Floor", "Room on the map", "Terminal room", "Starts as"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)
        self.lbl_msg = QLabel()
        self.lbl_msg.setWordWrap(True)
        self.lbl_msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.lbl_msg)
        crow = QHBoxLayout()
        self.ed_code = QLineEdit()
        self.ed_code.setReadOnly(True)
        self.ed_code.setPlaceholderText("The code appears here")
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(16)
        font.setBold(True)
        self.ed_code.setFont(font)
        crow.addWidget(self.ed_code, 1)
        self.btn_get = QPushButton("Get code")
        self.btn_get.clicked.connect(self._get_code)
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setEnabled(False)
        self.btn_copy.clicked.connect(self._copy)
        crow.addWidget(self.btn_get)
        crow.addWidget(self.btn_copy)
        lay.addLayout(crow)
        hint = QLabel("In Tabletop Simulator: open GM Controls on the MU/TH/UR terminal, GEN tab, "
                      "paste the code, then pick the end goal and generate.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.sp_count.valueChanged.connect(self._clear_code)
        self._rescan()

    # ------------------------------------------------------------------
    def _rescan(self, *_):
        self.scan = M.scan(self.project, zones_first=self.ck_zones.isChecked())
        self.notes = M.limit_states(self.scan)
        self.table.setRowCount(len(self.scan.found))
        for row, f in enumerate(self.scan.found):
            floor = self.scan.floor_names[f.floor - 1] if 0 < f.floor <= len(self.scan.floor_names) else ""
            self.table.setItem(row, 0, QTableWidgetItem(f"{f.floor}: {floor}"))
            self.table.setItem(row, 1, QTableWidgetItem(f.source_name or "(no room)"))
            if f.terminal is None:
                self.table.setItem(row, 2, QTableWidgetItem("—"))
                self.table.setCellWidget(row, 3, None)
                continue
            label = f.terminal.name.upper() + ("" if f.allowed else "  (not allowed)")
            self.table.setItem(row, 2, QTableWidgetItem(label))
            cb = QComboBox()
            cb.addItems(M.STATES)
            cb.setCurrentIndex(f.terminal.state)
            cb.setToolTip("How the room starts. At most one quarantine and one lockdown per floor.")
            cb.currentIndexChanged.connect(lambda i, f=f: self._set_state(f, i))
            self.table.setCellWidget(row, 3, cb)
        self._clear_code()

    def _set_state(self, f, state):
        f.terminal.state = state
        f.piece.room = dict(f.piece.room or {}, state=state)     # remembered with the map
        self.main._mark_dirty()
        self.notes = []
        self._clear_code()

    def _clear_code(self, *_):
        self.ed_code.clear()
        self.btn_copy.setEnabled(False)
        self.lbl_msg.setStyleSheet("")
        self.lbl_msg.setText("\n".join(getattr(self, "notes", []) or []))

    def _get_code(self):
        self._rescan()
        errors = M.check(self.scan, self.sp_count.value())
        if errors:
            self.lbl_msg.setStyleSheet("color: #ff6b5e;")
            self.lbl_msg.setText("No code yet:\n• " + "\n• ".join(errors))
            return
        spec = M.build_spec(self.scan)
        code = M.encode(spec)
        self.ed_code.setText(code)
        self.btn_copy.setEnabled(True)
        self.lbl_msg.setStyleSheet("color: #7fe87f;")
        self.lbl_msg.setText(f"{len(spec.terminals)} terminal(s) on {spec.floors} floor(s) checked. "
                             f"Code length {len(M.normalize(code))} characters.")

    def _copy(self):
        QGuiApplication.clipboard().setText(self.ed_code.text())
        self.lbl_msg.setText(self.lbl_msg.text().split("\nCopied")[0] + "\nCopied to the clipboard.")
