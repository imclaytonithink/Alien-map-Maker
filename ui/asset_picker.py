"""Pick tiles for the 'Your own tiles' generator: the library's folder tree plus previews.

The tree is the same one the library shows at the top left: the store's own folders,
with counts and tick boxes (a ticked folder takes everything inside it). Nothing is
listed until you open a folder; then its pictures appear as previews with their own
tick boxes. Ticks are the library's generator picks, so both stay in step.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QSplitter, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from ui.library import AssetList


class AssetPicker(QWidget):
    def __init__(self, panel, parent=None, colors=None):
        super().__init__(parent)
        self.panel = panel                      # LibraryPanel: owns the picked set
        self.library = panel.library
        self.colors = colors or {}
        self._items = {}
        self._syncing = False
        self._folder = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tiles by name or size (e.g. corridor, 40x120)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._show())
        lay.addWidget(self.search)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(180)
        self.tree.setToolTip("The library's folders. Click a folder to see its pictures; "
                             "tick a folder to use everything inside it.")
        self.tree.currentItemChanged.connect(self._on_current)
        self.tree.itemChanged.connect(self._on_tree_check)
        split.addWidget(self.tree)
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        self.lbl = QLabel()
        self.lbl.setWordWrap(True)
        if self.colors.get("muted"):
            self.lbl.setStyleSheet(f"color:{self.colors['muted']};")
        rlay.addWidget(self.lbl)
        self.list = AssetList()
        self.list.asset_model.picked_contains = lambda path: path in self.panel._picked
        self.list.asset_model.pickToggled.connect(self._on_pick)
        self.list.set_thumbnail_size(72)
        rlay.addWidget(self.list, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 420])
        lay.addWidget(split, 1)
        self._thumbs = QTimer(self)
        self._thumbs.setInterval(60)
        self._thumbs.timeout.connect(self._load_visible)
        self._thumbs.start()
        self.list.asset_model.thumbnailLoaded.connect(self.list.viewport().update)
        panel.pickChanged.connect(self._sync_checks)
        self.rebuild()

    # ------------------------------------------------------------------
    def _new(self, parent, label, token):
        item = QTreeWidgetItem([label])
        item.setToolTip(0, label)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        item.setData(0, Qt.ItemDataRole.UserRole, token)
        (self.tree.addTopLevelItem(item) if parent is None else parent.addChild(item))
        self._items[token] = item
        return item

    def rebuild(self):
        """The library's folder tree, collapsed below the top level."""
        assets = self.library.assets
        counts = {}
        for a in assets:
            folder = a.folder.replace("\\", "/").strip("/")
            if folder in ("", "."):
                counts["."] = counts.get(".", 0) + 1
                continue
            parts = folder.split("/")
            for depth in range(1, len(parts) + 1):
                key = "/".join(parts[:depth])
                counts[key] = counts.get(key, 0) + 1
        project = getattr(self.panel, "project", None)
        groups = self.library.groups(project.group_order if project else None)
        self._syncing = True
        self.tree.clear()
        self._items = {}
        root = self._new(None, f"Folders ({len(assets):,})", "folder-root")
        made = {}
        if counts.get("."):
            self._new(root, f"Loose files ({counts['.']:,})", "folder:.")
        for group in groups:
            folder = group.replace("\\", "/").strip("/")
            if folder in ("", "."):
                continue
            parts = folder.split("/")
            for depth in range(1, len(parts) + 1):
                key = "/".join(parts[:depth])
                if key in made:
                    continue
                parent = made["/".join(parts[:depth - 1])] if depth > 1 else root
                made[key] = self._new(parent, f"{parts[depth - 1]} ({counts.get(key, 0):,})", f"folder:{key}")
        root.setExpanded(True)
        self._syncing = False
        self._sync_checks()
        self._folder = None
        self._show()

    def _paths(self, token):
        if token == "folder-root":
            return [a.path for a in self.library.assets]
        folder = token.split(":", 1)[1]
        if folder == ".":
            return [a.path for a in self.library.assets if a.folder.replace("\\", "/").strip("/") in ("", ".")]
        prefix = folder + "/"
        return [a.path for a in self.library.assets
                if a.folder.replace("\\", "/").strip("/") == folder or a.folder.replace("\\", "/").startswith(prefix)]

    def _on_current(self, cur, _prev):
        token = cur.data(0, Qt.ItemDataRole.UserRole) if cur else None
        self._folder = token.split(":", 1)[1] if token and token.startswith("folder:") else None
        self._show()

    def _show(self):
        text = self.search.text().strip().lower()
        lib = self.library
        if text:
            shown = [a for a in lib.assets if text in a.name.lower() or text in a.path.lower()
                     or (a.size and text == f"{a.size[0]}x{a.size[1]}")]
            where = f"matching “{self.search.text().strip()}”"
        elif self._folder is not None:
            shown = [a for a in lib.assets if a.folder.replace("\\", "/").strip("/") in
                     ((self._folder,) if self._folder != "." else ("", "."))]
            where = f"in {self._folder if self._folder != '.' else 'the store root'}"
        else:
            shown = []
            where = ""
        self.list.set_assets(shown, lib)
        if shown:
            self.lbl.setText(f"{len(shown):,} picture(s) {where}. Tick the ones to use; hover for a larger preview.")
        elif text:
            self.lbl.setText(f"No tiles {where}.")
        elif self._folder is not None:
            self.lbl.setText("This folder has no pictures of its own; open one of its subfolders in the tree "
                             "(or tick the folder to use everything inside it).")
        else:
            self.lbl.setText("Open a folder on the left to see its pictures, or search by name.")

    # ------------------------------------------------------------------
    def _on_pick(self, path, checked):
        self.panel._on_pick_toggled(path, checked)

    def _on_tree_check(self, item, column):
        if self._syncing or column != 0:
            return
        token = item.data(0, Qt.ItemDataRole.UserRole) or ""
        paths = set(self._paths(token))
        picked = set(self.panel._picked)
        if item.checkState(0) != Qt.CheckState.Unchecked:
            picked |= paths
        else:
            picked -= paths
        self.panel.select_paths(picked)

    def _sync_checks(self):
        self._syncing = True
        try:
            picked = self.panel._picked
            for token, item in self._items.items():
                paths = self._paths(token)
                have = sum(1 for p in paths if p in picked)
                state = (Qt.CheckState.Unchecked if not paths or have == 0 else
                         Qt.CheckState.Checked if have == len(paths) else Qt.CheckState.PartiallyChecked)
                if item.checkState(0) != state:
                    item.setCheckState(0, state)
        finally:
            self._syncing = False
        model = self.list.asset_model
        if model.rowCount():
            model.dataChanged.emit(model.index(0, 0), model.index(model.rowCount() - 1, 0),
                                   [int(Qt.ItemDataRole.CheckStateRole)])

    def _load_visible(self):
        view, model = self.list, self.list.asset_model
        if not model.rowCount() or not self.isVisible():
            return
        rect, grid = view.viewport().rect(), view.gridSize()
        sx, sy = max(20, grid.width() // 2), max(20, grid.height() // 2)
        rows = set()
        for y in range(rect.top(), rect.bottom() + 1, sy):
            for x in range(rect.left(), rect.right() + 1, sx):
                index = view.indexAt(QPoint(x, y))
                if index.isValid():
                    rows.add(index.row())
        asked = 0
        for row in sorted(rows):
            index = model.index(row, 0)
            if not model.has_requested_thumbnail(index) and model.request_thumbnail(index):
                asked += 1
                if asked >= 6:
                    break

    def drain_background_work(self):
        self._thumbs.stop()
        self.list.drain_background_work()
