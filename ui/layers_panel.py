"""Layers panel: list of level layers with visibility, lock, opacity, reorder,
and active-layer selection (where new pieces go)."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSlider, QListWidget,
    QListWidgetItem, QLineEdit, QMenu, QAbstractItemView, QColorDialog,
)
from core.project import Project, Level, Layer
from ui.theme import theme_colors


LABEL_COLORS = (("Red", "#e5534b"), ("Orange", "#e5933b"), ("Yellow", "#d8c43a"),
                ("Green", "#4fb36a"), ("Blue", "#4a90e2"), ("Purple", "#a070d8"))


class ReorderList(QListWidget):
    """List whose rows can be dragged to reorder; reports the new id order."""
    orderChanged = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def dropEvent(self, event):
        super().dropEvent(event)
        ids = [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]
        self.orderChanged.emit(ids)


class LayerRow(QWidget):
    changed = pyqtSignal()
    active = pyqtSignal(str)
    solo = pyqtSignal(str)
    renameRequested = pyqtSignal(str, str)     # layer id, new name
    menuRequested = pyqtSignal(str, object)    # layer id, global pos
    committed = pyqtSignal(str, bool)          # history label, coalesce

    def __init__(self, layer: Layer, is_active: bool, parent=None,
                 theme_accent="#69b7f5", theme_muted="#9aa9b8", soloed=False):
        super().__init__(parent)
        self.layer = layer
        self.theme_accent = theme_accent
        self.theme_muted = theme_muted
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(3)
        self.stripe = QLabel()
        self.stripe.setFixedWidth(4)
        self.stripe.setStyleSheet(
            f"background:{layer.color or 'transparent'}; border-radius:2px;")
        lay.addWidget(self.stripe)
        self.btn_active = QPushButton("●" if is_active else "○")
        self.btn_active.setMaximumWidth(20)
        self._apply_active_style(is_active)
        self.btn_active.clicked.connect(lambda: self.active.emit(layer.id))
        self.btn_visible = QPushButton("●" if layer.visible else "○")
        self.btn_visible.setToolTip("Show / hide this layer")
        self.btn_visible.setMaximumWidth(24)
        self.btn_visible.clicked.connect(self._toggle_vis)
        self.btn_lock = QPushButton("■" if layer.locked else "□")
        self.btn_lock.setToolTip("Lock / unlock this layer")
        self.btn_lock.setMaximumWidth(24)
        self.btn_lock.clicked.connect(self._toggle_lock)
        self.btn_solo = QPushButton("S")
        self.btn_solo.setCheckable(True)
        self.btn_solo.setChecked(soloed)
        self.btn_solo.setMaximumWidth(24)
        self.btn_solo.setToolTip("Solo: show only this layer while editing (not exported)")
        self.btn_solo.clicked.connect(lambda: self.solo.emit(layer.id))
        self.lbl = QPushButton(layer.name)
        self.lbl.setStyleSheet("text-align:left;")
        self.lbl.setToolTip("Click to make active · double-click to rename · right-click for more")
        self.lbl.clicked.connect(lambda: self.active.emit(layer.id))
        self.lbl.installEventFilter(self)
        self.editor = QLineEdit(layer.name)
        self.editor.hide()
        self.editor.editingFinished.connect(self._finish_rename)
        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(0, 100)
        self.sl.setValue(int(layer.opacity * 100))
        self.sl.setMaximumWidth(70)
        self.sl.valueChanged.connect(self._op)
        lay.addWidget(self.btn_active); lay.addWidget(self.btn_visible)
        lay.addWidget(self.btn_lock); lay.addWidget(self.btn_solo)
        lay.addWidget(self.lbl, 1); lay.addWidget(self.editor, 1); lay.addWidget(self.sl)

    def eventFilter(self, obj, event):
        if obj is self.lbl:
            if event.type() == event.Type.MouseButtonDblClick:
                self.begin_rename()
                return True
            if event.type() == event.Type.ContextMenu:
                self.menuRequested.emit(self.layer.id, event.globalPos())
                return True
        return super().eventFilter(obj, event)

    def begin_rename(self):
        self.editor.setText(self.layer.name)
        self.lbl.hide()
        self.editor.show()
        self.editor.setFocus()
        self.editor.selectAll()

    def _finish_rename(self):
        if not self.editor.isVisible():
            return
        new = self.editor.text().strip()
        self.editor.hide()
        self.lbl.show()
        if new and new != self.layer.name:
            self.renameRequested.emit(self.layer.id, new)

    def _toggle_vis(self):
        self.committed.emit("Toggle layer visibility", False)
        self.layer.visible = not self.layer.visible
        self.btn_visible.setText("●" if self.layer.visible else "○")
        self.changed.emit()

    def _toggle_lock(self):
        self.committed.emit("Toggle layer lock", False)
        self.layer.locked = not self.layer.locked
        self.btn_lock.setText("■" if self.layer.locked else "□")
        self.changed.emit()

    def _op(self, v):
        self.committed.emit("Layer opacity", True)
        self.layer.opacity = v / 100.0
        self.changed.emit()

    def _apply_active_style(self, active: bool):
        color = self.theme_accent if active else self.theme_muted
        self.btn_active.setStyleSheet(f"color:{color};")

    def set_theme_colors(self, accent: str, muted: str):
        self.theme_accent = accent
        self.theme_muted = muted
        self._apply_active_style(self.btn_active.text() == "●")

    def set_active_style(self, active: bool):
        self.btn_active.setText("●" if active else "○")
        self._apply_active_style(active)


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
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter layers…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        root.addWidget(self.filter)
        self.list = ReorderList()
        self.list.setStyleSheet("QListWidget{border:none;}")
        self.list.orderChanged.connect(self._reordered)
        root.addWidget(self.list, 1)
        row = QHBoxLayout()
        b_add = QPushButton("+"); b_add.setMaximumWidth(28); b_add.clicked.connect(self._add)
        b_del = QPushButton("–"); b_del.setMaximumWidth(28); b_del.clicked.connect(self._del)
        b_up = QPushButton("▲"); b_up.setMaximumWidth(28); b_up.clicked.connect(lambda: self._move(-1))
        b_dn = QPushButton("▼"); b_dn.setMaximumWidth(28); b_dn.clicked.connect(lambda: self._move(1))
        row.addWidget(b_add); row.addWidget(b_del); row.addWidget(b_up); row.addWidget(b_dn)
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
        solo = self.canvas.solo_layer_id
        for l in self.level.layers:
            row = LayerRow(l, l.id == self.level.current_layer,
                           theme_accent=self.theme_accent,
                           theme_muted=self.theme_muted, soloed=(l.id == solo))
            row.changed.connect(self._changed)
            row.active.connect(self._set_active)
            row.solo.connect(self._toggle_solo)
            row.renameRequested.connect(self._rename)
            row.menuRequested.connect(self._show_menu)
            row.committed.connect(self._commit)
            it = QListWidgetItem(self.list)
            it.setSizeHint(row.sizeHint())
            it.setData(Qt.ItemDataRole.UserRole, l.id)
            self.list.addItem(it)
            self.list.setItemWidget(it, row)
        self.list.blockSignals(False)
        self._apply_filter(self.filter.text())

    def _apply_filter(self, text):
        needle = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            row = self.list.itemWidget(it)
            name = row.layer.name.lower() if row else ""
            it.setHidden(bool(needle) and needle not in name)

    def _commit(self, label, coalesce):
        self.canvas.push_history(label, coalesce=coalesce)

    def _layer(self, lid):
        return self.level.layer_by_id(lid) if self.level else None

    def _rename(self, lid, name):
        layer = self._layer(lid)
        if layer is None:
            return
        self.canvas.push_history("Rename layer")
        layer.name = name
        self.rebuild(); self._changed()
        self._refresh_dependents()

    def _refresh_dependents(self):
        """Piece inspector shows layer names; ask it to reload."""
        self.canvas.selectionChanged.emit(self.canvas.selected_pieces())

    def _reordered(self, ids):
        by_id = {l.id: l for l in self.level.layers}
        if sorted(ids) != sorted(by_id) or ids == [l.id for l in self.level.layers]:
            QTimer.singleShot(0, self.rebuild)
            return
        self.canvas.push_history("Reorder layers")
        self.level.layers[:] = [by_id[i] for i in ids]
        # item widgets do not survive a drag-move; rebuild after the drop settles
        QTimer.singleShot(0, self.rebuild)
        self._changed()
        self._refresh_dependents()

    def _toggle_solo(self, lid):
        self.canvas.set_solo_layer(None if self.canvas.solo_layer_id == lid else lid)
        self.rebuild()

    def _set_color(self, lid, color):
        layer = self._layer(lid)
        if layer is None:
            return
        self.canvas.push_history("Layer color label")
        layer.color = color
        self.rebuild(); self._changed()

    def _show_menu(self, lid, pos):
        layer = self._layer(lid)
        if layer is None:
            return
        menu = QMenu(self)
        menu.addAction("Rename", lambda: self._start_rename(lid))
        solo = menu.addAction("Solo" if self.canvas.solo_layer_id != lid else "Un-solo")
        solo.triggered.connect(lambda: self._toggle_solo(lid))
        colors = menu.addMenu("Color label")
        for name, hexc in LABEL_COLORS:
            colors.addAction(name, lambda c=hexc: self._set_color(lid, c))
        colors.addAction("None", lambda: self._set_color(lid, ""))
        menu.addSeparator()
        menu.addAction("Duplicate layer (with its pieces)", lambda: self._duplicate(lid))
        menu.addAction("Delete layer", lambda: self._delete_id(lid))
        menu.exec(pos)

    def _start_rename(self, lid):
        for i in range(self.list.count()):
            row = self.list.itemWidget(self.list.item(i))
            if row and row.layer.id == lid:
                row.begin_rename()
                return

    def _duplicate(self, lid):
        from core.project import uuid, Piece
        src = self._layer(lid)
        if src is None:
            return
        self.canvas.push_history("Duplicate layer")
        new = Layer(**{**src.__dict__, "id": uuid.uuid4().hex,
                       "name": f"{src.name} copy"})
        self.level.layers.insert(self.level.layers.index(src) + 1, new)
        for p in [p for p in self.level.pieces if p.layer == lid]:
            clone = Piece(**p.to_dict())
            clone.id = uuid.uuid4().hex
            clone.layer = new.id
            clone.group_id = ""
            self.level.add(clone)
        self.level.current_layer = new.id
        self.rebuild(); self._changed()

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
        self.canvas.push_history("Add layer")
        l = Layer(name=f"Layer {len(self.level.layers)+1}", id=uuid.uuid4().hex)
        self.level.layers.append(l)
        self.level.current_layer = l.id
        self.rebuild(); self._changed()

    def _del(self):
        idx = self._selected_index()
        if idx < 0:
            idx = len(self.level.layers) - 1
        self._delete_id(self.level.layers[idx].id)

    def _delete_id(self, lid):
        if len(self.level.layers) <= 1:
            return
        idx = next((i for i, l in enumerate(self.level.layers) if l.id == lid), -1)
        if idx < 0:
            return
        self.canvas.push_history("Delete layer")
        removed = self.level.layers.pop(idx)
        # reassign pieces
        fallback = self.level.layers[0].id
        for p in self.level.pieces:
            if p.layer == removed.id:
                p.layer = fallback
        if self.level.current_layer == removed.id:
            self.level.current_layer = fallback
        if self.canvas.solo_layer_id == removed.id:
            self.canvas.set_solo_layer(None)
        self.rebuild(); self._changed()

    def _move(self, delta):
        idx = self._selected_index()
        if idx < 0:
            return
        j = idx + delta
        if 0 <= j < len(self.level.layers):
            self.canvas.push_history("Reorder layers")
            self.level.layers[idx], self.level.layers[j] = self.level.layers[j], self.level.layers[idx]
            self.rebuild(); self._changed()

    def _selected_index(self):
        # pick current_layer's index as proxy for selection
        for i, l in enumerate(self.level.layers):
            if l.id == self.level.current_layer:
                return i
        return -1
