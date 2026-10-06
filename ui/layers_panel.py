"""Layers panel: list of level layers with visibility, lock, opacity, reorder,
and active-layer selection (where new pieces go)."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSlider, QListWidget,
)
from core.project import Project, Level, Layer


class LayerRow(QWidget):
    changed = pyqtSignal()
    active = pyqtSignal(str)

    def __init__(self, layer: Layer, is_active: bool, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(3)
        self.btn_active = QPushButton("●" if is_active else "○")
        self.btn_active.setMaximumWidth(20)
        self.btn_active.setStyleSheet("color:" + ("#9bff9b" if is_active else "#555") + ";")
        self.btn_active.clicked.connect(lambda: self.active.emit(layer.id))
        self.btn_visible = QPushButton("●" if layer.visible else "○")
        self.btn_visible.setToolTip("Show / hide this layer")
        self.btn_visible.setMaximumWidth(24)
        self.btn_visible.clicked.connect(self._toggle_vis)
        self.btn_lock = QPushButton("■" if layer.locked else "□")
        self.btn_lock.setToolTip("Lock / unlock this layer")
        self.btn_lock.setMaximumWidth(24)
        self.btn_lock.clicked.connect(self._toggle_lock)
        self.lbl = QPushButton(layer.name)
        self.lbl.setStyleSheet("text-align:left;")
        self.lbl.clicked.connect(lambda: self.active.emit(layer.id))
        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(0, 100)
        self.sl.setValue(int(layer.opacity * 100))
        self.sl.setMaximumWidth(70)
        self.sl.valueChanged.connect(self._op)
        lay.addWidget(self.btn_active); lay.addWidget(self.btn_visible)
        lay.addWidget(self.btn_lock); lay.addWidget(self.lbl, 1); lay.addWidget(self.sl)

    def _toggle_vis(self):
        self.layer.visible = not self.layer.visible
        self.btn_visible.setText("●" if self.layer.visible else "○")
        self.changed.emit()

    def _toggle_lock(self):
        self.layer.locked = not self.layer.locked
        self.btn_lock.setText("■" if self.layer.locked else "□")
        self.changed.emit()

    def _op(self, v):
        self.layer.opacity = v / 100.0
        self.changed.emit()

    def set_active_style(self, active: bool):
        self.btn_active.setText("●" if active else "○")
        self.btn_active.setStyleSheet("color:" + ("#9bff9b" if active else "#555") + ";")


class LayersPanel(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.project: Optional[Project] = None
        self.level: Optional[Level] = None
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget{border:none;}")
        root.addWidget(self.list, 1)
        row = QHBoxLayout()
        b_add = QPushButton("+"); b_add.setMaximumWidth(28); b_add.clicked.connect(self._add)
        b_del = QPushButton("–"); b_del.setMaximumWidth(28); b_del.clicked.connect(self._del)
        b_up = QPushButton("▲"); b_up.setMaximumWidth(28); b_up.clicked.connect(lambda: self._move(-1))
        b_dn = QPushButton("▼"); b_dn.setMaximumWidth(28); b_dn.clicked.connect(lambda: self._move(1))
        row.addWidget(b_add); row.addWidget(b_del); row.addWidget(b_up); row.addWidget(b_dn)
        root.addLayout(row)

    def set_project(self, project: Project, level: Level):
        self.project = project
        self.level = level
        self.rebuild()

    def rebuild(self):
        self.list.blockSignals(True)
        self.list.clear()
        for l in self.level.layers:
            item = self.list.listWidgetItem if False else None
            row = LayerRow(l, l.id == self.level.current_layer)
            row.changed.connect(self._changed)
            row.active.connect(self._set_active)
            lw = self.list  # placeholder
            # use QListWidgetItem + setItemWidget
            from PyQt6.QtWidgets import QListWidgetItem
            it = QListWidgetItem(self.list)
            it.setSizeHint(row.sizeHint())
            self.list.addItem(it)
            self.list.setItemWidget(it, row)
        self.list.blockSignals(False)

    def _changed(self):
        self.canvas.update()
        self.canvas.dirty.emit()

    def _set_active(self, lid):
        self.level.current_layer = lid
        for i in range(self.list.count()):
            it = self.list.item(i)
            row = self.list.itemWidget(it)
            row.set_active_style(row.layer.id == lid)
        self.canvas.dirty.emit()

    def _add(self):
        from core.project import uuid
        l = Layer(name=f"Layer {len(self.level.layers)+1}", id=uuid.uuid4().hex)
        self.level.layers.append(l)
        self.level.current_layer = l.id
        self.rebuild(); self._changed()

    def _del(self):
        if len(self.level.layers) <= 1:
            return
        idx = self._selected_index()
        if idx < 0:
            idx = len(self.level.layers) - 1
        removed = self.level.layers.pop(idx)
        # reassign pieces
        fallback = self.level.layers[0].id
        for p in self.level.pieces:
            if p.layer == removed.id:
                p.layer = fallback
        if self.level.current_layer == removed.id:
            self.level.current_layer = fallback
        self.rebuild(); self._changed()

    def _move(self, delta):
        idx = self._selected_index()
        if idx < 0:
            return
        j = idx + delta
        if 0 <= j < len(self.level.layers):
            self.level.layers[idx], self.level.layers[j] = self.level.layers[j], self.level.layers[idx]
            self.rebuild(); self._changed()

    def _selected_index(self):
        # pick current_layer's index as proxy for selection
        for i, l in enumerate(self.level.layers):
            if l.id == self.level.current_layer:
                return i
        return -1
