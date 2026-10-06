"""User-customizable main toolbar with click-and-hold drag reordering."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QPoint, QTimer, QMimeData, pyqtSignal
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QToolBar, QToolButton, QVBoxLayout,
    QWidget,
)


_TOOL_MIME = "application/x-rpg-map-maker-toolbar-action"


class _DraggableToolButton(QToolButton):
    """A normal tool button until it has been held and dragged a short way."""

    def __init__(self, tool_id: str, contents: "_ToolbarContents", parent=None):
        super().__init__(parent)
        self.tool_id = tool_id
        self.contents = contents
        self._press_pos: QPoint | None = None
        self._hold_ready = False
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(220)
        self._hold_timer.timeout.connect(self._enable_drag)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setAutoRaise(False)
        self.setMinimumHeight(28)
        self.setToolTip("Click to use. Hold and drag to move this tool.")

    def _enable_drag(self):
        self._hold_ready = True

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._hold_ready = False
            self._hold_timer.start()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._press_pos is not None
                and event.buttons() & Qt.MouseButton.LeftButton
                and self._hold_ready):
            current = event.position().toPoint()
            threshold = 8
            if (current - self._press_pos).manhattanLength() >= threshold:
                self._hold_timer.stop()
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData(_TOOL_MIME, self.tool_id.encode("utf-8"))
                drag.setMimeData(mime)
                pixmap = self.grab()
                drag.setPixmap(pixmap)
                drag.setHotSpot(QPoint(pixmap.width() // 2,
                                       pixmap.height() // 2))
                drag.exec(Qt.DropAction.MoveAction)
                # Avoid letting QAbstractButton treat the completed drag as a
                # click.  Calling setDown(False) also clears the pressed style.
                self.setDown(False)
                self._press_pos = None
                self._hold_ready = False
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._hold_timer.stop()
        self._press_pos = None
        self._hold_ready = False
        super().mouseReleaseEvent(event)


class _ToolbarContents(QWidget):
    orderChanged = pyqtSignal(object)
    customizeRequested = pyqtSignal()

    def __init__(self, actions: dict[str, object], order: list[str],
                 visible_ids: set[str], parent=None):
        super().__init__(parent)
        self.actions = actions
        self.order = list(order)
        self.visible_ids = set(visible_ids)
        self.setAcceptDrops(True)
        self.setMinimumHeight(34)
        self.setToolTip("Hold a toolbar button, then drag it to a new position.")

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 1, 2, 1)
        self._layout.setSpacing(3)
        self.buttons: dict[str, _DraggableToolButton] = {}
        for tool_id in self.order:
            action = self.actions[tool_id]
            button = _DraggableToolButton(tool_id, self, self)
            button.setDefaultAction(action)
            # QAction's tooltip is more descriptive than the generic drag hint.
            if action.toolTip():
                button.setToolTip(f"{action.toolTip()}\n\nHold and drag to move.")
            button.setVisible(tool_id in self.visible_ids)
            self.buttons[tool_id] = button

        self.customize_button = QToolButton(self)
        self.customize_button.setText("⚙")
        self.customize_button.setAutoRaise(True)
        self.customize_button.setToolTip("Customize toolbar: show or hide tools")
        self.customize_button.clicked.connect(
            lambda checked=False: self.customizeRequested.emit())
        self._sync_layout()

    def _sync_layout(self):
        for button in self.buttons.values():
            self._layout.removeWidget(button)
        self._layout.removeWidget(self.customize_button)
        for tool_id in self.order:
            self._layout.addWidget(self.buttons[tool_id])
        self._layout.addWidget(self.customize_button)
        self.updateGeometry()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(_TOOL_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(_TOOL_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(_TOOL_MIME):
            event.ignore()
            return
        tool_id = bytes(event.mimeData().data(_TOOL_MIME)).decode("utf-8")
        if tool_id not in self.order:
            event.ignore()
            return

        point = event.position().toPoint()
        target_id = None
        insert_before = True
        for candidate in self.order:
            if candidate == tool_id:
                continue
            button = self.buttons[candidate]
            if not button.isVisible():
                continue
            if point.x() < button.geometry().center().x():
                target_id = candidate
                insert_before = True
                break
            target_id = candidate
            insert_before = False

        new_order = [item for item in self.order if item != tool_id]
        if target_id is None:
            insertion = len(new_order)
        else:
            insertion = new_order.index(target_id)
            if not insert_before:
                insertion += 1
        new_order.insert(insertion, tool_id)
        if new_order != self.order:
            self.order = new_order
            self._sync_layout()
            self.orderChanged.emit(list(self.order))
        event.acceptProposedAction()


class CustomizableToolBar(QToolBar):
    """Toolbar whose order and visibility are stored in user settings."""

    customizeRequested = pyqtSignal()

    def __init__(self, title: str, actions: dict[str, object], settings,
                 parent=None):
        super().__init__(title, parent)
        self._actions = dict(actions)
        self._settings = settings
        self._default_order = list(actions)
        self.setMovable(False)
        self.setFloatable(False)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        self.order = self._load_order()
        self.visible_ids = self._load_visible_ids()
        self.contents = _ToolbarContents(self._actions, self.order,
                                         self.visible_ids, self)
        self.contents.orderChanged.connect(self._save_order)
        self.contents.customizeRequested.connect(self.customizeRequested.emit)
        self.addWidget(self.contents)

    def _read_string_list(self, key: str, default: list[str]) -> list[str]:
        value = self._settings.value(key, default)
        if isinstance(value, str):
            values = [part for part in value.split(",") if part]
        else:
            try:
                values = list(value)
            except TypeError:
                values = list(default)
        return [str(item) for item in values]

    def _load_order(self) -> list[str]:
        saved = self._read_string_list("toolbar/order", self._default_order)
        valid = []
        for tool_id in saved:
            if tool_id in self._actions and tool_id not in valid:
                valid.append(tool_id)
        valid.extend(tool_id for tool_id in self._default_order
                     if tool_id not in valid)
        return valid

    def _load_visible_ids(self) -> set[str]:
        saved = self._read_string_list("toolbar/visible", self._default_order)
        return {tool_id for tool_id in saved if tool_id in self._actions}

    def _save_order(self, order):
        self.order = list(order)
        self._settings.setValue("toolbar/order", self.order)
        self._settings.sync()

    def set_visible_ids(self, visible_ids):
        self.visible_ids = {tool_id for tool_id in visible_ids
                            if tool_id in self._actions}
        self.contents.visible_ids = set(self.visible_ids)
        for tool_id, button in self.contents.buttons.items():
            button.setVisible(tool_id in self.visible_ids)
        self._settings.setValue("toolbar/visible", [tool_id for tool_id in self.order
                                                     if tool_id in self.visible_ids])
        self._settings.sync()

    def reset_to_default(self):
        self.order = list(self._default_order)
        self.visible_ids = set(self._default_order)
        self.contents.order = list(self.order)
        self.contents.visible_ids = set(self.visible_ids)
        for button in self.contents.buttons.values():
            button.setVisible(True)
        self.contents._sync_layout()
        self._settings.setValue("toolbar/order", self.order)
        self._settings.setValue("toolbar/visible", self.order)
        self._settings.sync()

    def _show_context_menu(self, position):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction("Customize toolbar…",
                       lambda checked=False: self.customizeRequested.emit())
        menu.addAction("Reset toolbar",
                       lambda checked=False: self.reset_to_default())
        menu.exec(self.mapToGlobal(position))


class CustomizeToolbarDialog(QDialog):
    """Visibility controls; users reorder tools directly on the toolbar."""

    def __init__(self, toolbar: CustomizableToolBar, parent=None):
        super().__init__(parent or toolbar)
        self.toolbar = toolbar
        self._reset_requested = False
        self.setWindowTitle("Customize toolbar")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Choose which tools to show. Hold and drag toolbar buttons to reorder them."))
        self.list_widget = QListWidget(self)
        for tool_id in toolbar.order:
            action = toolbar._actions[tool_id]
            item = QListWidgetItem(action.text(), self.list_widget)
            item.setData(Qt.ItemDataRole.UserRole, tool_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked
                               if tool_id in toolbar.visible_ids
                               else Qt.CheckState.Unchecked)
            if action.toolTip():
                item.setToolTip(action.toolTip())
        layout.addWidget(self.list_widget)

        bottom = QHBoxLayout()
        self.reset_button = QPushButton("Reset to default")
        self.reset_button.clicked.connect(self._mark_reset)
        bottom.addWidget(self.reset_button)
        bottom.addStretch(1)
        layout.addLayout(bottom)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self)
        self.button_box.accepted.connect(self._apply)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def _mark_reset(self):
        self._reset_requested = True
        for row in range(self.list_widget.count()):
            self.list_widget.item(row).setCheckState(Qt.CheckState.Checked)

    def _apply(self):
        visible = set()
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                visible.add(str(item.data(Qt.ItemDataRole.UserRole)))
        if self._reset_requested:
            self.toolbar.reset_to_default()
        self.toolbar.set_visible_ids(visible)
        self.accept()
