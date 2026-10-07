"""Searchable command palette built from the menu bar's actions."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
)


def collect_commands(menu_bar) -> list[tuple[str, str, object]]:
    """Return (path, shortcut, QAction) for every leaf action in the menus."""
    found: list[tuple[str, str, object]] = []

    def walk(menu, trail):
        for action in menu.actions():
            if action.isSeparator():
                continue
            text = action.text().replace("&", "").rstrip("…").strip()
            sub = action.menu()
            if sub is not None:
                walk(sub, trail + [text])
            elif text:
                found.append((" › ".join(trail + [text]),
                              action.shortcut().toString(), action))

    for top in menu_bar.actions():
        if top.menu() is not None:
            walk(top.menu(), [top.text().replace("&", "")])
    return found


def match_score(query: str, haystack: str) -> int | None:
    """Subsequence match; lower is better. None when it does not match."""
    q, h = query.lower().strip(), haystack.lower()
    if not q:
        return 0
    if q in h:
        return h.index(q)
    pos = -1
    score = 100
    for ch in q.replace(" ", ""):
        pos = h.find(ch, pos + 1)
        if pos < 0:
            return None
        score += pos
    return score


class CommandPalette(QDialog):
    def __init__(self, parent, menu_bar):
        super().__init__(parent)
        self.setWindowTitle("Command palette")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.resize(520, 380)
        self.commands = collect_commands(menu_bar)
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type a command…  (↑↓ to choose, Enter to run)")
        self.search.textChanged.connect(self._refilter)
        self.results = QListWidget()
        self.results.itemActivated.connect(self._run_item)
        lay.addWidget(self.search)
        lay.addWidget(self.results, 1)
        self.search.installEventFilter(self)
        self._refilter("")

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == event.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self.results.currentRow() + (1 if key == Qt.Key.Key_Down else -1)
                self.results.setCurrentRow(max(0, min(self.results.count() - 1, row)))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.results.currentItem()
                if item:
                    self._run_item(item)
                return True
        return super().eventFilter(obj, event)

    def _refilter(self, text):
        scored = []
        for path, shortcut, action in self.commands:
            score = match_score(text, path)
            if score is not None:
                scored.append((score, path, shortcut, action))
        scored.sort(key=lambda r: (r[0], r[1]))
        self.results.clear()
        for _, path, shortcut, action in scored:
            label = f"{path}    [{shortcut}]" if shortcut else path
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, action)
            if not action.isEnabled():
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _run_item(self, item):
        action = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        if action is not None and action.isEnabled():
            action.trigger()
