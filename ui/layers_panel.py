"""Layers panel: list of level layers with visibility, lock, opacity, reorder,
and active-layer selection (where new pieces go)."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider,
    QListWidget, QListWidgetItem, QSizePolicy,
)
from core.project import Project, Level, Layer
from ui.theme import theme_colors


class LayerRow(QWidget):
    changed = pyqtSignal()
    active = pyqtSignal(str)

    def __init__(self, layer: Layer, is_active: bool, parent=None,
                 theme_accent="#69b7f5", theme_muted="#9aa9b8"):
        super().__init__(parent)
        self.layer = layer
        self._full_name = layer.name
        self._is_active = is_active
        self.theme_accent = theme_accent
        self.theme_muted = theme_muted
        self.setMinimumHeight(34)
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(3)

        self.btn_active = QPushButton()
        self.btn_active.setObjectName("LayerIconButton")
        self.btn_active.setFixedSize(27, 26)
        self.btn_active.clicked.connect(lambda: self.active.emit(layer.id))
        self._set_active_state(is_active)

        self.btn_visible = QPushButton()
        self.btn_visible.setObjectName("LayerIconButton")
        self.btn_visible.setFixedSize(27, 26)
        self.btn_visible.clicked.connect(self._toggle_vis)
        self._update_visibility_button()

        self.btn_lock = QPushButton()
        self.btn_lock.setObjectName("LayerIconButton")
        self.btn_lock.setFixedSize(27, 26)
        self.btn_lock.clicked.connect(self._toggle_lock)
        self._update_lock_button()

        self.lbl = QPushButton(layer.name)
        self.lbl.setObjectName("LayerNameButton")
        self.lbl.setFlat(True)
        self.lbl.setMinimumWidth(0)
        self.lbl.setSizePolicy(QSizePolicy.Policy.Ignored,
                               QSizePolicy.Policy.Preferred)
        self.lbl.setToolTip(
            f"{layer.name} — click to make this the active layer.")
        self.lbl.setAccessibleName(f"Set {layer.name} as the active layer")
        self.lbl.clicked.connect(lambda: self.active.emit(layer.id))

        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(0, 100)
        self.sl.setValue(int(layer.opacity * 100))
        self.sl.setMinimumWidth(42)
        self.sl.setMaximumWidth(70)
        self.sl.setToolTip("Layer opacity")
        self.sl.valueChanged.connect(self._op)

        lay.addWidget(self.btn_active)
        lay.addWidget(self.btn_visible)
        lay.addWidget(self.btn_lock)
        lay.addWidget(self.lbl, 1)
        lay.addWidget(self.sl)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "lbl"):
            return
        self.lbl.setText(self.lbl.fontMetrics().elidedText(
            self._full_name, Qt.TextElideMode.ElideRight,
            max(0, self.lbl.width())))

    def _update_visibility_button(self):
        self.btn_visible.setText("V" if self.layer.visible else "H")
        state = "visible — click to hide" if self.layer.visible else "hidden — click to show"
        self.btn_visible.setToolTip(f"Layer is {state}.")
        self.btn_visible.setAccessibleName(f"{self.layer.name} layer {state}")

    def _update_lock_button(self):
        self.btn_lock.setText("L" if self.layer.locked else "U")
        state = "locked — click to unlock" if self.layer.locked else "unlocked — click to lock"
        self.btn_lock.setToolTip(f"Layer is {state}.")
        self.btn_lock.setAccessibleName(f"{self.layer.name} layer {state}")

    def _toggle_vis(self):
        self.layer.visible = not self.layer.visible
        self._update_visibility_button()
        self.changed.emit()

    def _toggle_lock(self):
        self.layer.locked = not self.layer.locked
        self._update_lock_button()
        self.changed.emit()

    def _op(self, v):
        self.layer.opacity = v / 100.0
        self.changed.emit()

    def _apply_active_style(self, active: bool):
        color = self.theme_accent if active else self.theme_muted
        self.btn_active.setStyleSheet(f"color:{color};")

    def _set_active_state(self, active: bool):
        self._is_active = active
        self.btn_active.setText("●" if active else "○")
        if active:
            self.btn_active.setToolTip(
                "Active layer — new pieces are added here.")
            self.btn_active.setAccessibleName("Active layer")
        else:
            self.btn_active.setToolTip(
                "Make this the active layer for new pieces.")
            self.btn_active.setAccessibleName("Set as active layer")
        self._apply_active_style(active)

    def set_theme_colors(self, accent: str, muted: str):
        self.theme_accent = accent
        self.theme_muted = muted
        self._apply_active_style(self._is_active)

    def set_active_style(self, active: bool):
        self._set_active_state(active)


class LayersPanel(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.project: Optional[Project] = None
        self.level: Optional[Level] = None
        colors = theme_colors("dark")
        self.theme_accent = colors["accent"]
        self.theme_muted = colors["muted"]
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget{border:none;}")
        self.list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setUniformItemSizes(True)
        root.addWidget(self.list, 1)
        row = QHBoxLayout()
        actions = [
            ("+", "Add a layer", self._add),
            ("–", "Delete the active layer", self._del),
            ("▲", "Move the active layer up", lambda: self._move(-1)),
            ("▼", "Move the active layer down", lambda: self._move(1)),
        ]
        for label, tooltip, callback in actions:
            button = QPushButton(label)
            button.setObjectName("PanelIconButton")
            button.setFixedSize(30, 28)
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch(1)
        root.addLayout(row)

    def set_theme(self, mode: str, accent: str):
        colors = theme_colors(mode, accent)
        self.theme_accent = colors["accent"]
        self.theme_muted = colors["muted"]
        for i in range(self.list.count()):
            row = self.list.itemWidget(self.list.item(i))
            if row:
                row.set_theme_colors(self.theme_accent, self.theme_muted)

    def set_project(self, project: Project, level: Level):
        self.project = project
        self.level = level
        self.rebuild()

    def rebuild(self):
        self.list.blockSignals(True)
        self.list.clear()
        for layer in self.level.layers:
            row = LayerRow(layer, layer.id == self.level.current_layer,
                           theme_accent=self.theme_accent,
                           theme_muted=self.theme_muted)
            row.changed.connect(self._changed)
            row.active.connect(self._set_active)
            item = QListWidgetItem()
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            item.setSizeHint(QSize(
                max(0, self.list.viewport().width()),
                max(34, row.sizeHint().height())))
        self.list.blockSignals(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "list"):
            return
        width = max(0, self.list.viewport().width())
        for index in range(self.list.count()):
            item = self.list.item(index)
            row = self.list.itemWidget(item)
            height = max(34, row.sizeHint().height()) if row else 34
            item.setSizeHint(QSize(width, height))

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
