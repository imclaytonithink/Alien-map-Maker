"""Quick-stamp hotbar: nine numbered slots floating at the bottom of the canvas.

Press 1-9 (or click a slot) to arm a stamp, then click the map to place
copies; Esc or right-click stops. Right-click a slot to pin the selected node
or clear it; drag a library asset onto a slot to pin that asset.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QToolButton

from core.stamps import SLOT_COUNT
from ui.theme import theme_colors

SLOT_PX = 40
ASSET_MIME = "application/x-mapbuilder-asset"     # library drag format


class StampSlotButton(QToolButton):
    def __init__(self, index: int, bar: "StampBar"):
        super().__init__(bar)
        self.index = index
        self.bar = bar
        self.filled = False
        self.active = False
        self.edge = False           # door mode: copies sit on grid lines
        self.setObjectName("StampSlot")
        self.setFixedSize(SLOT_PX, SLOT_PX)
        self.setIconSize(QSize(SLOT_PX - 10, SLOT_PX - 10))
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName(f"Stamp key {index + 1}")
        self.clicked.connect(lambda: bar.slotClicked.emit(index))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: bar.slotMenuRequested.emit(index, self.mapToGlobal(pos)))

    def paintEvent(self, event):
        colors = self.bar.colors
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        background = QColor(colors["selection"] if self.active else colors["panel2"])
        if self.underMouse() and not self.active:
            background = QColor(colors["hover"])
        pen = QPen(QColor(colors["border_hot"] if (self.active or self.filled)
                          else colors["border"]))
        pen.setWidthF(2.0 if self.active else 1.0)
        if not self.filled:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 5.0, 5.0)
        icon = self.icon()
        if self.filled and not icon.isNull():
            pixmap = icon.pixmap(self.iconSize())
            x = (self.width() - pixmap.width() / max(1.0, pixmap.devicePixelRatio())) / 2.0
            y = (self.height() - pixmap.height() / max(1.0, pixmap.devicePixelRatio())) / 2.0 + 2
            painter.drawPixmap(int(x), int(y), pixmap)
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        badge = QRectF(2.0, 1.0, 13.0, 13.0)
        if self.filled:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawRoundedRect(badge, 3.0, 3.0)
            painter.setPen(QColor("#ffffff"))
        else:
            painter.setPen(QColor(colors["muted"]))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(self.index + 1))
        if self.filled and self.edge:
            # door-mode badge: a short wall segment with a door gap
            corner = QRectF(self.width() - 15.0, self.height() - 12.0, 13.0, 10.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 160))
            painter.drawRoundedRect(corner, 3.0, 3.0)
            pen = QPen(QColor(colors["accent"]))
            pen.setWidthF(1.8)
            painter.setPen(pen)
            y = corner.center().y()
            painter.drawLine(int(corner.left() + 2), int(y), int(corner.left() + 4.5), int(y))
            painter.drawLine(int(corner.right() - 4.5), int(y), int(corner.right() - 2), int(y))
            painter.drawLine(int(corner.left() + 4.5), int(y), int(corner.right() - 4.5),
                             int(corner.top() + 2))
        painter.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(ASSET_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(ASSET_MIME):
            event.ignore()
            return
        path = bytes(event.mimeData().data(ASSET_MIME)).decode("utf-8")
        event.acceptProposedAction()
        if path:
            self.bar.assetDropped.emit(self.index, path)


class StampBar(QFrame):
    slotClicked = pyqtSignal(int)              # 0-based slot
    slotMenuRequested = pyqtSignal(int, QPoint)
    assetDropped = pyqtSignal(int, str)        # slot, library asset path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StampBar")
        self.colors = theme_colors("dark")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 4, 5, 4)
        layout.setSpacing(3)
        self.buttons = [StampSlotButton(index, self) for index in range(SLOT_COUNT)]
        for button in self.buttons:
            layout.addWidget(button)
        self.set_theme("dark", self.colors["accent"])
        self.adjustSize()

    def set_theme(self, mode: str, accent: str):
        self.colors = theme_colors(mode, accent)
        panel = QColor(self.colors["panel"])
        self.setStyleSheet(
            "QFrame#StampBar {"
            f" background: rgba({panel.red()}, {panel.green()}, {panel.blue()}, 225);"
            f" border: 1px solid {self.colors['border_hot']}; border-radius: 7px; }}")
        for button in self.buttons:
            button.update()

    def set_slots(self, icons: list, tips: list[str], edges: list | None = None):
        edges = list(edges or [])
        edges += [False] * (len(self.buttons) - len(edges))
        for button, icon, tip, edge in zip(self.buttons, icons, tips, edges):
            button.filled = icon is not None
            button.edge = bool(edge)
            button.setIcon(icon if isinstance(icon, QIcon) else QIcon())
            button.setToolTip(tip)
            button.update()

    def set_active(self, index):
        for button in self.buttons:
            button.active = index is not None and button.index == index
            button.update()
