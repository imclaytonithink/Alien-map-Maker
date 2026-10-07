"""Layers panel: list of level layers with visibility, export, lock, opacity,
reorder, and active-layer selection (where new pieces go)."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel,
    QListWidget, QListWidgetItem, QSizePolicy, QLineEdit, QMenu,
    QAbstractItemView,
)
from core.project import Project, Level, Layer
from ui.glyphs import eye_icon, export_icon, lock_icon
from ui.theme import theme_colors

ROW_BUTTON = (28, 30)      # layer row icon buttons (width, height)
ICON_PX = 17
# Narrow inspector: the layer name keeps its room; the opacity slider (also in
# the right-click menu) and then the solo button step aside below these widths.
SLIDER_MIN_ROW = 262
SOLO_MIN_ROW = 228
OPACITY_PRESETS = (100, 75, 50, 25)


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
                 theme_accent="#69b7f5", theme_muted="#9aa9b8", soloed=False,
                 theme_text="#dce4ed"):
        super().__init__(parent)
        self.layer = layer
        self._full_name = layer.name
        self._is_active = is_active
        self.theme_accent = theme_accent
        self.theme_muted = theme_muted
        self.theme_text = theme_text
        self.setMinimumHeight(44)
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(3)
        self.stripe = QLabel()
        self.stripe.setFixedWidth(4)
        self.stripe.setStyleSheet(
            f"background:{layer.color or 'transparent'}; border-radius:2px;")
        lay.addWidget(self.stripe)

        self.btn_active = QPushButton()
        self.btn_active.setObjectName("LayerIconButton")
        self.btn_active.setFixedSize(*ROW_BUTTON)
        self.btn_active.clicked.connect(lambda: self.active.emit(layer.id))
        self._set_active_state(is_active)

        # eye: show / hide the layer on the canvas (hidden layers never export)
        self.btn_visible = QPushButton()
        self.btn_visible.setObjectName("LayerIconButton")
        self.btn_visible.setFixedSize(*ROW_BUTTON)
        self.btn_visible.setIconSize(QSize(ICON_PX, ICON_PX))
        self.btn_visible.clicked.connect(self._toggle_vis)
        self._update_visibility_button()

        # picture: include the layer in PNG/PDF exports, or keep it editor-only
        self.btn_export = QPushButton()
        self.btn_export.setObjectName("LayerIconButton")
        self.btn_export.setFixedSize(*ROW_BUTTON)
        self.btn_export.setIconSize(QSize(ICON_PX, ICON_PX))
        self.btn_export.clicked.connect(self._toggle_export)
        self._update_export_button()

        self.btn_lock = QPushButton()
        self.btn_lock.setObjectName("LayerIconButton")
        self.btn_lock.setFixedSize(*ROW_BUTTON)
        self.btn_lock.setIconSize(QSize(ICON_PX, ICON_PX))
        self.btn_lock.clicked.connect(self._toggle_lock)
        self.btn_solo = QPushButton("S")
        self.btn_solo.setObjectName("LayerIconButton")
        self.btn_solo.setFixedSize(*ROW_BUTTON)
        self.btn_solo.setCheckable(True)
        self.btn_solo.setChecked(soloed)
        self.btn_solo.setToolTip("Solo: show only this layer while editing (not exported)")
        self.btn_solo.clicked.connect(lambda: self.solo.emit(layer.id))
        self._update_lock_button()

        self.lbl = QPushButton(layer.name)
        self.lbl.setObjectName("LayerNameButton")
        self.lbl.setFlat(True)
        self.lbl.setMinimumWidth(0)
        self.lbl.setSizePolicy(QSizePolicy.Policy.Ignored,
                               QSizePolicy.Policy.Preferred)
        self.lbl.setToolTip(
            f"{layer.name} — click to make active · double-click to rename · "
            "right-click for more")
        self.lbl.setAccessibleName(f"Set {layer.name} as the active layer")
        self.lbl.clicked.connect(lambda: self.active.emit(layer.id))
        self.lbl.installEventFilter(self)
        self.editor = QLineEdit(layer.name)
        self.editor.hide()
        self.editor.editingFinished.connect(self._finish_rename)

        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(0, 100)
        self.sl.setValue(int(layer.opacity * 100))
        self.sl.setMinimumWidth(30)
        self.sl.setMaximumWidth(56)
        self.sl.setToolTip("Layer opacity")
        self.sl.valueChanged.connect(self._op)

        lay.addWidget(self.btn_active)
        lay.addWidget(self.btn_visible)
        lay.addWidget(self.btn_export)
        lay.addWidget(self.btn_lock)
        lay.addWidget(self.btn_solo)
        lay.addWidget(self.lbl, 3)
        lay.addWidget(self.editor, 3)
        lay.addWidget(self.sl, 1)

    def eventFilter(self, obj, event):
        if obj is self.lbl:
            if event.type() == event.Type.Resize:
                self._elide_name()          # the label's final width is known now
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "lbl"):
            return
        self._fit_controls(event.size().width())
        self._elide_name()

    def _elide_name(self):
        self.lbl.setText(self.lbl.fontMetrics().elidedText(
            self._full_name, Qt.TextElideMode.ElideRight,
            max(0, self.lbl.width() - 6)))

    def _fit_controls(self, width: int):
        """Keep the layer name readable in a narrow panel."""
        self.sl.setVisible(width >= SLIDER_MIN_ROW)
        self.btn_solo.setVisible(width >= SOLO_MIN_ROW or self.btn_solo.isChecked())

    def _update_visibility_button(self):
        visible = self.layer.visible
        self.btn_visible.setIcon(eye_icon(
            self.theme_text if visible else self.theme_muted, open_=visible))
        state = "visible — click to hide" if visible else "hidden — click to show"
        self.btn_visible.setToolTip(
            f"Layer is {state}.\nHidden layers are left out of exports too.")
        self.btn_visible.setAccessibleName(f"{self.layer.name} layer {state}")

    def _update_export_button(self):
        included = getattr(self.layer, "export", True)
        self.btn_export.setIcon(export_icon(
            self.theme_text if included else self.theme_muted, included=included))
        if included:
            tip = ("Included in PNG/PDF exports — click to keep this layer on the "
                   "canvas only (for tracing images, notes, work in progress).")
            state = "included in exports"
        else:
            tip = ("Left out of PNG/PDF exports (still shown on the canvas) — click "
                   "to include it again.")
            state = "left out of exports"
        self.btn_export.setToolTip(tip)
        self.btn_export.setAccessibleName(f"{self.layer.name} layer {state}")

    def _update_lock_button(self):
        locked = self.layer.locked
        self.btn_lock.setIcon(lock_icon(
            self.theme_text if locked else self.theme_muted, locked=locked))
        state = "locked — click to unlock" if locked else "unlocked — click to lock"
        self.btn_lock.setToolTip(f"Layer is {state}.")
        self.btn_lock.setAccessibleName(f"{self.layer.name} layer {state}")

    def _toggle_vis(self):
        self.committed.emit("Toggle layer visibility", False)
        self.layer.visible = not self.layer.visible
        self._update_visibility_button()
        self.changed.emit()

    def _toggle_export(self):
        self.committed.emit("Toggle layer export", False)
        self.layer.export = not getattr(self.layer, "export", True)
        self._update_export_button()
        self.changed.emit()

    def _toggle_lock(self):
        self.committed.emit("Toggle layer lock", False)
        self.layer.locked = not self.layer.locked
        self._update_lock_button()
        self.changed.emit()

    def _op(self, v):
        self.committed.emit("Layer opacity", True)
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

    def set_theme_colors(self, accent: str, muted: str, text: str | None = None):
        self.theme_accent = accent
        self.theme_muted = muted
        if text:
            self.theme_text = text
        self._apply_active_style(self._is_active)
        self._update_visibility_button()
        self._update_export_button()
        self._update_lock_button()

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
        self.theme_text = colors["text"]
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        top = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter layers…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        top.addWidget(self.filter, 1)
        add_button = QPushButton("+")
        add_button.setObjectName("PanelIconButton")
        add_button.setFixedSize(38, 34)
        add_button.setToolTip("Add a layer (right-click the list for more)")
        add_button.setAccessibleName("Add a layer")
        add_button.clicked.connect(self._add)
        top.addWidget(add_button)
        root.addLayout(top)
        self.list = ReorderList()
        self.list.setStyleSheet("QListWidget{border:none;}")
        self.list.orderChanged.connect(self._reordered)
        self.list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setUniformItemSizes(True)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        root.addWidget(self.list, 1)

    def _list_menu(self, pos):
        """Right-click on empty list space: add / delete / move the active layer."""
        if self.level is None:
            return
        menu = QMenu(self)
        menu.addAction("Add layer", self._add)
        index = self._selected_index()
        delete = menu.addAction("Delete active layer", self._del)
        delete.setEnabled(len(self.level.layers) > 1)
        up = menu.addAction("Move active layer up", lambda: self._move(-1))
        up.setEnabled(index > 0)
        down = menu.addAction("Move active layer down", lambda: self._move(1))
        down.setEnabled(0 <= index < len(self.level.layers) - 1)
        menu.addSeparator()
        menu.addAction("Show all layers", lambda: self._set_all("visible", True))
        menu.addAction("Include all layers in exports", lambda: self._set_all("export", True))
        menu.addAction("Unlock all layers", lambda: self._set_all("locked", False))
        menu.addAction("Clear solo", lambda: self._toggle_solo(self.canvas.solo_layer_id)
                       if self.canvas.solo_layer_id else None)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _set_all(self, attr: str, value: bool):
        if not self.level or all(getattr(l, attr) == value for l in self.level.layers):
            return
        self.canvas.push_history({"visible": "Layer visibility",
                                  "export": "Layer export"}.get(attr, "Layer lock"))
        for layer in self.level.layers:
            setattr(layer, attr, value)
        self.rebuild(); self._changed()

    def set_theme(self, mode: str, accent: str):
        colors = theme_colors(mode, accent)
        self.theme_accent = colors["accent"]
        self.theme_muted = colors["muted"]
        self.theme_text = colors["text"]
        for i in range(self.list.count()):
            row = self.list.itemWidget(self.list.item(i))
            if row:
                row.set_theme_colors(self.theme_accent, self.theme_muted, self.theme_text)

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
                           theme_muted=self.theme_muted, soloed=(l.id == solo),
                           theme_text=self.theme_text)
            row.changed.connect(self._changed)
            row.active.connect(self._set_active)
            row.solo.connect(self._toggle_solo)
            row.renameRequested.connect(self._rename)
            row.menuRequested.connect(self._show_menu)
            row.committed.connect(self._commit)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, l.id)
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            item.setSizeHint(QSize(
                max(0, self.list.viewport().width()),
                max(44, row.sizeHint().height())))
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
        visible = menu.addAction("Visible on the canvas")
        visible.setCheckable(True)
        visible.setChecked(layer.visible)
        visible.triggered.connect(lambda: self._toggle_flag(lid, "visible"))
        exported = menu.addAction("Include in PNG/PDF exports")
        exported.setCheckable(True)
        exported.setChecked(getattr(layer, "export", True))
        exported.triggered.connect(lambda: self._toggle_flag(lid, "export"))
        opacity = menu.addMenu(f"Opacity ({round(layer.opacity * 100)}%)")
        for percent in OPACITY_PRESETS:
            action = opacity.addAction(f"{percent}%")
            action.setCheckable(True)
            action.setChecked(round(layer.opacity * 100) == percent)
            action.triggered.connect(
                lambda _=False, value=percent: self._set_opacity(lid, value / 100.0))
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

    def _set_opacity(self, lid, value: float):
        layer = self._layer(lid)
        if layer is None or abs(layer.opacity - value) < 1e-6:
            return
        self.canvas.push_history("Layer opacity")
        layer.opacity = max(0.0, min(1.0, float(value)))
        self.rebuild(); self._changed()

    def _toggle_flag(self, lid, attr: str):
        layer = self._layer(lid)
        if layer is None:
            return
        self.canvas.push_history("Toggle layer visibility" if attr == "visible"
                                 else "Toggle layer export")
        setattr(layer, attr, not getattr(layer, attr, True))
        self.rebuild(); self._changed()

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "list"):
            return
        width = max(0, self.list.viewport().width())
        for index in range(self.list.count()):
            item = self.list.item(index)
            row = self.list.itemWidget(item)
            height = max(44, row.sizeHint().height()) if row else 34
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
