"""Click-to-correct editor for a tile's edge/door data.

Auto-detection reads each tile's four borders from its image; low-confidence
tiles are flagged. Here you pick a tile, see its border squares as a strip per
side, and click a square to cycle wall -> door -> open. Corrections are saved
to ``edge_overrides.json`` and win over the detected data next time.
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
                             QWidget)

COLORS = {0: "#2b4a4f", 1: "#f0aa3c", 2: "#101418"}
NAMES = {0: "wall", 1: "door", 2: "open / outside"}


class _Strip(QWidget):
    def __init__(self, side, cls, vertical, on_change):
        super().__init__()
        self.side, self.cls, self.vertical, self.on_change = side, list(cls), vertical, on_change
        n = len(cls)
        self.setMinimumSize(24 if vertical else 14 * n, 14 * n if vertical else 24)

    def _rect(self, i):
        n = len(self.cls)
        if self.vertical:
            h = self.height() / n
            return QRectF(2, i * h, self.width() - 4, h)
        w = self.width() / n
        return QRectF(i * w, 2, w, self.height() - 4)

    def paintEvent(self, _e):
        p = QPainter(self)
        for i, c in enumerate(self.cls):
            p.setBrush(QBrush(QColor(COLORS[c])))
            p.setPen(QPen(QColor("#7fe8ee")))
            p.drawRect(self._rect(i))

    def mousePressEvent(self, e):
        for i in range(len(self.cls)):
            if self._rect(i).contains(e.position()):
                self.cls[i] = (self.cls[i] + (1 if e.button() == Qt.MouseButton.LeftButton else 2)) % 3
                self.update()
                self.on_change(self.side, list(self.cls))
                return


class EdgeEditor(QDialog):
    def __init__(self, parent, registry, images, overrides_path):
        super().__init__(parent)
        self.registry, self.images, self.path = registry, images, Path(overrides_path)
        self.overrides = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.setWindowTitle("Tile edge / door editor")
        self.resize(760, 560)
        outer = QHBoxLayout(self)
        self.list = QListWidget()
        tiles = sorted(registry.tiles.values(), key=lambda t: (not t.review, t.type, t.id))
        for t in tiles:
            it = QListWidgetItem(("⚠ " if t.review else "") + f"{t.id}  {t.type}  {t.title}")
            it.setData(Qt.ItemDataRole.UserRole, t.id)
            self.list.addItem(it)
        self.list.currentItemChanged.connect(self._pick)
        outer.addWidget(self.list, 1)
        right = QVBoxLayout()
        self.title = QLabel("Pick a tile. Click a square to cycle wall / door / open (right-click goes back).")
        self.title.setWordWrap(True)
        right.addWidget(self.title)
        self.holder = QVBoxLayout()
        right.addLayout(self.holder)
        legend = QLabel("  ".join(f"■ {n}" for n in NAMES.values()))
        right.addWidget(legend)
        self.btn_save = QPushButton("Save corrections")
        self.btn_save.clicked.connect(self._save)
        right.addWidget(self.btn_save)
        right.addStretch(1)
        outer.addLayout(right, 2)
        self.tile = None

    def _pick(self, cur, _prev=None):
        if cur is None:
            return
        self.tile = self.registry.tiles[cur.data(Qt.ItemDataRole.UserRole)]
        t = self.tile
        self.title.setText(f"{t.id} — {t.title}   ({t.type}, {t.w}x{t.h} squares)")
        while self.holder.count():
            w = self.holder.takeAt(0).widget()
            if w:
                w.deleteLater()
        for side in "NESW":
            row = QHBoxLayout()
            row.addWidget(QLabel(side))
            row.addWidget(_Strip(side, t.edges[side]["cls"], side in "EW", self._changed), 1)
            wrap = QWidget()
            wrap.setLayout(row)
            self.holder.addWidget(wrap)

    def _changed(self, side, cls):
        self.overrides.setdefault(self.tile.id, {})[side] = cls
        self.tile.edges[side]["cls"] = list(cls)
        self.tile.edges[side]["conf"] = 1.0

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.overrides, indent=1), encoding="utf-8")
        for tid in self.overrides:
            self.registry.tiles[tid].review = False
        self.title.setText(f"Saved {len(self.overrides)} corrected tile(s) to {self.path}")
