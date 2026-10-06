"""Asset library side-panel: import, groups, search, thumbnails, preview."""
from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt, QMimeData, QSize, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QDrag, QPixmap, QIcon, QMouseEvent, QCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QLabel, QSlider, QDialog, QFileDialog,
    QListView, QTreeWidget, QTreeWidgetItem,
)

from core.asset_manager import AssetLibrary
from core.asset_taxonomy import (
    CATEGORY_GROUPS,
    CATEGORY_LABELS,
    SMART_CATEGORY_TREE,
    classify_asset_categories,
)
from core.project import Project
from ui.branding import default_asset_store_path, seed_bundled_assets
from ui.image_utils import load_scaled_pixmap


ASSET_MIME = "application/x-mapbuilder-asset"


class PreviewDialog(QDialog):
    def __init__(self, asset, add_cb, parent=None):
        super().__init__(parent)
        self.setWindowTitle(asset.name)
        self.setMinimumWidth(360)
        v = QVBoxLayout(self)
        self.pm = QPixmap()
        v.addWidget(QLabel(f"Name: {asset.name}"))
        if asset.size:
            v.addWidget(QLabel(f"Size: {asset.size[0]} x {asset.size[1]}"))
        self.img = QLabel()
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.img, 1)
        row = QHBoxLayout()
        b = QPushButton("Add to canvas")
        b.clicked.connect(lambda: (add_cb(asset.path), self.accept()))
        row.addStretch(1)
        row.addWidget(b)
        v.addLayout(row)
        self._asset = asset
        self._add_cb = add_cb

    def set_pixmap(self, pm: QPixmap):
        self.pm = pm
        self.img.setPixmap(pm.scaled(320, 320, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))


class ZipImportWorker(QThread):
    """Import large archives without blocking the editor's event loop."""
    progress = pyqtSignal(str, int, int)   # archive name, current, total
    completed = pyqtSignal(object, object)  # reports, (archive, error) pairs

    def __init__(self, archive_paths, asset_store: str, parent=None):
        super().__init__(parent)
        self.archive_paths = list(archive_paths)
        self.asset_store = asset_store

    def run(self):
        library = AssetLibrary(self.asset_store)
        reports, errors = [], []
        total = len(self.archive_paths)
        for index, path in enumerate(self.archive_paths, start=1):
            self.progress.emit(os.path.basename(path), index, total)
            try:
                reports.append(library.import_zip(path, rescan=False))
            except Exception as exc:
                errors.append((os.path.basename(path), str(exc)))
        self.completed.emit(reports, errors)


class AssetList(QListWidget):
    viewportResized = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIconSize(QSize(56, 56))
        self.setSpacing(4)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self._press_item = None
        self._press_pos = None
        self._pop = None
        self._pop_timer = QTimer(self)
        self._pop_timer.setSingleShot(True)
        self._pop_timer.timeout.connect(self._show_popout)
        self.itemEntered.connect(self._on_entered)
        self.setMouseTracking(True)

    def mousePressEvent(self, e: QMouseEvent):
        self._press_item = self.itemAt(e.position().toPoint())
        self._press_pos = e.position().toPoint()
        self._pop_timer.stop()
        self._hide_popout()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._press_item and e.buttons() & Qt.MouseButton.LeftButton:
            if (e.position().toPoint() - self._press_pos).manhattanLength() > 6:
                path = self._press_item.data(Qt.ItemDataRole.UserRole)
                if path:
                    self._start_drag(path)
                self._press_item = None
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._pop_timer.stop()
        self._hide_popout()
        super().leaveEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.viewportResized.emit()

    def _on_entered(self, item):
        self._pop_timer.stop()
        self._hide_popout()
        if item:
            self._pop_timer.start(600)

    def _show_popout(self):
        item = self.itemAt(self.mapFromGlobal(QCursor.pos()))
        if not item:
            return
        pm = item.icon().pixmap(QSize(160, 160))
        if pm.isNull():
            return
        if self._pop is None:
            self._pop = QLabel(self.window())
            self._pop.setObjectName("AssetPreviewPopout")
            self._pop.setWindowFlags(Qt.WindowType.ToolTip)
        self._pop.setPixmap(pm.scaled(160, 160, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        self._pop.adjustSize()
        self._pop.move(QCursor.pos() + QSize(12, 12))
        self._pop.show()

    def _hide_popout(self):
        if self._pop:
            self._pop.hide()

    def _start_drag(self, path: str):
        mime = QMimeData()
        mime.setData(ASSET_MIME, path.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pm = self._press_item.icon().pixmap(QSize(64, 64))
        if not pm.isNull():
            drag.setPixmap(pm)
        drag.exec(Qt.DropAction.CopyAction)
        self._press_item = None


class LibraryPanel(QWidget):
    assetActivated = pyqtSignal(str)   # double-click -> add at center
    collectionsChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library = AssetLibrary()
        self.project: Optional[Project] = None
        self.add_at_center_cb = None
        self._view = ("all", None)
        self._category_tags = {}
        self._tree_items = {}
        self._folder_groups = []
        self._zip_worker = None
        self._zip_import_target_root = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # search + import
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search filename, folder, size…")
        self.search.textChanged.connect(self._on_search)
        top.addWidget(self.search, 1)
        self.b_imp_f = QPushButton("Import Folder")
        self.b_imp_f.setToolTip(
            "Copy images into the internal library, keeping the selected "
            "folder name and its subfolders as an expandable file tree. "
            "Useful for Core / Symbols packs. Importing does not place them "
            "on the map; drag or double-click "
            "an asset afterward.")
        self.b_imp_f.clicked.connect(self._import_folder)
        self.b_imp_p = QPushButton("Import File")
        self.b_imp_p.setToolTip(
            "Copy one image into the internal library. Drag or double-click "
            "it afterward to place it on the map.")
        self.b_imp_p.clicked.connect(self._import_file)
        self.b_imp_zip = QPushButton("Import ZIP")
        self.b_imp_zip.setToolTip(
            "Import supported images from one or more ZIP archives. "
            "Folder paths are preserved; non-image files are skipped.")
        self.b_imp_zip.clicked.connect(self._import_zip)
        top.addWidget(self.b_imp_f)
        top.addWidget(self.b_imp_p)
        top.addWidget(self.b_imp_zip)
        layout.addLayout(top)

        # Hierarchical file paths and virtual smart-category views.
        g_row = QHBoxLayout()
        self.group_tree = QTreeWidget()
        self.group_tree.setHeaderHidden(True)
        self.group_tree.setMaximumHeight(230)
        self.group_tree.setUniformRowHeights(True)
        self.group_tree.setToolTip(
            "Browse the preserved asset-store folder paths or use overlapping "
            "smart categories. Categories filter files in place; they never "
            "move or duplicate assets.")
        self.group_tree.currentItemChanged.connect(self._on_tree_pick)
        g_row.addWidget(self.group_tree, 1)
        g_btns = QVBoxLayout()
        self.b_group_up = QPushButton("▲")
        self.b_group_up.setToolTip("Move this folder earlier among its siblings")
        self.b_group_up.clicked.connect(self._group_up)
        self.b_group_down = QPushButton("▼")
        self.b_group_down.setToolTip("Move this folder later among its siblings")
        self.b_group_down.clicked.connect(self._group_down)
        g_btns.addWidget(self.b_group_up)
        g_btns.addWidget(self.b_group_down)
        g_row.addLayout(g_btns)
        layout.addLayout(g_row)

        # collections
        coll_row = QHBoxLayout()
        self.coll_combo = QComboBox()
        self.coll_combo.addItem("All assets")
        self.coll_combo.setMinimumWidth(120)
        self.coll_combo.currentIndexChanged.connect(self._on_collection_pick)
        btn_new = QPushButton("New"); btn_new.setMaximumWidth(46)
        btn_new.clicked.connect(self._new_collection)
        btn_add = QPushButton("+ Add sel."); btn_add.setMaximumWidth(70)
        btn_add.clicked.connect(self._add_to_collection)
        btn_del = QPushButton("x"); btn_del.setMaximumWidth(24)
        btn_del.clicked.connect(self._remove_from_collection)
        coll_row.addWidget(self.coll_combo, 1)
        coll_row.addWidget(btn_new); coll_row.addWidget(btn_add); coll_row.addWidget(btn_del)
        layout.addLayout(coll_row)

        # thumb size slider
        ts = QHBoxLayout()
        ts.addWidget(QLabel("Thumb"))
        self.sl_thumb = QSlider(Qt.Orientation.Horizontal)
        self.sl_thumb.setRange(32, 110)
        self.sl_thumb.setValue(56)
        self.sl_thumb.valueChanged.connect(self._thumb_size)
        ts.addWidget(self.sl_thumb, 1)
        layout.addLayout(ts)

        self.list = AssetList()
        self.list.itemDoubleClicked.connect(
            lambda it: self.assetActivated.emit(it.data(Qt.ItemDataRole.UserRole)))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._ctx_menu)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(45)
        self._thumb_timer.timeout.connect(self._load_visible_thumbnails)
        self.list.verticalScrollBar().valueChanged.connect(
            lambda _value: self._schedule_visible_thumbnails())
        self.list.viewportResized.connect(self._schedule_visible_thumbnails)
        layout.addWidget(self.list, 1)

        # where the internal store lives + how to get help
        store_row = QHBoxLayout()
        self.lbl_store = QLabel("Store: (not set)")
        self.lbl_store.setToolTip(
            "Internal asset store.\n\nEverything you import is COPIED into "
            "this folder and lives there permanently — maps reference those "
            "copies. Subfolders remain visible in the expandable file tree; "
            "smart categories are virtual views only.\n\n"
            "PNGs dropped straight onto the canvas are different: they are "
            "embedded inside the .bmap file itself.")
        self.lbl_store.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        store_row.addWidget(self.lbl_store, 1)
        b_where = QPushButton("?")
        b_where.setMaximumWidth(26)
        b_where.setToolTip("Explain where imported assets are stored")
        b_where.clicked.connect(self._show_store_help)
        store_row.addWidget(b_where)
        b_open = QPushButton("Open")
        b_open.setMaximumWidth(52)
        b_open.setToolTip("Open the internal asset store folder in your "
                          "file manager")
        b_open.clicked.connect(self._open_store)
        store_row.addWidget(b_open)
        layout.addLayout(store_row)

        self.count_label = QLabel("")
        layout.addWidget(self.count_label)
        self._update_store_label()

    def _update_store_label(self):
        path = ""
        if self.project and self.project.asset_store:
            path = self.project.asset_store
        if path:
            fm = self.lbl_store.fontMetrics()
            shown = fm.elidedText("Store: " + path,
                                  Qt.TextElideMode.ElideMiddle,
                                  max(120, self.lbl_store.width() - 10))
            self.lbl_store.setText(shown)
            self.lbl_store.setToolTip(
                "Internal asset store (full path:\n" + path + ")\n\n"
                "Imported assets are copied into this folder permanently. "
                "Hover for details, click '?' for a full explanation, or "
                "click 'Open' to browse it.")
        else:
            self.lbl_store.setText("Store: (import something to create it)")

    def _show_store_help(self):
        from PyQt6.QtWidgets import QMessageBox
        path = (self.project.asset_store if self.project
                and self.project.asset_store else "(created on first import)")
        QMessageBox.information(self, "Where are my assets?", (
            "INTERNAL ASSET STORE\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"Location:\n  {path}\n\n"
            "• 'Import Folder' / 'Import File' / 'Import ZIP' COPY supported "
            "images into that folder.\n"
            "  ZIP imports preserve subfolders, normalize Windows path "
            "separators, and skip non-images. The archive is never changed.\n"
            "  Imported copies stay there — this app never depends on the "
            "original files.\n"
            "• The Browse tree preserves every subfolder. Smart categories "
            "are extra\n"
            "  overlapping views (rooms, floors, doors, furniture, consoles, "
            "hazards,\n"
            "  symbols and more); files stay in their original paths. Images "
            "the app\n"
            "  cannot classify remain available under Other / Unclassified.\n"
            "• Rename files with sizes like 'wall_25x50.png' so the app can "
            "auto-size them.\n"
            "• PNGs dragged straight onto the canvas are NOT copied there — "
            "they are\n"
            "  embedded inside your .bmap save file instead, so that map "
            "travels alone.\n\n"
            "Tip: back up that one folder and you back up every tile you "
            "own."))

    def _open_store(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        self._ensure_store()
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.project.asset_store))
        self._update_store_label()

    # ------------------------------------------------------------------
    def set_project(self, project: Project, library: AssetLibrary, add_at_center_cb=None):
        self.project = project
        self.library = library
        self.add_at_center_cb = add_at_center_cb
        library.scan(project.asset_store)
        self._rebuild_collections()
        self._rebuild_groups()
        self.refresh()
        self._update_store_label()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_store_label()

    def _new_tree_item(self, parent, label: str, token: str):
        item = QTreeWidgetItem([label])
        if parent is None:
            self.group_tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        item.setData(0, Qt.ItemDataRole.UserRole, token)
        self._tree_items[token] = item
        return item

    def _rebuild_groups(self):
        groups = self.library.groups(self.project.group_order if self.project else None)
        self._folder_groups = groups
        if self.project:
            self.project.group_order = list(groups)
        self._category_tags = classify_asset_categories(self.library.assets)

        self.group_tree.blockSignals(True)
        self.group_tree.clear()
        self._tree_items = {}

        total = len(self.library.assets)
        all_item = self._new_tree_item(None, f"All assets ({total:,})", "all")

        smart_root = self._new_tree_item(
            None, f"Smart categories ({total:,})", "smart-root")
        for group_name, categories in SMART_CATEGORY_TREE:
            category_ids = tuple(category_id for category_id, _label in categories)
            matched_paths = {
                path for path, tags in self._category_tags.items()
                if tags.intersection(category_ids)
            }
            group_item = self._new_tree_item(
                smart_root, f"{group_name} ({len(matched_paths):,})",
                f"category-group:{group_name}")
            for category_id, label in categories:
                count = sum(category_id in tags
                            for tags in self._category_tags.values())
                self._new_tree_item(
                    group_item, f"{label} ({count:,})",
                    f"category:{category_id}")
            group_item.setExpanded(True)
        smart_root.setExpanded(True)

        folders_root = self._new_tree_item(
            None, f"Folders / file paths ({total:,})", "folder-root")
        self._folder_counts = {".": total}
        for asset in self.library.assets:
            folder = asset.folder.replace("\\", "/").strip("/")
            if folder in ("", "."):
                continue
            parts = folder.split("/")
            for depth in range(1, len(parts) + 1):
                prefix = "/".join(parts[:depth])
                self._folder_counts[prefix] = self._folder_counts.get(prefix, 0) + 1

        root_item = self._new_tree_item(
            folders_root, f"Asset store root ({total:,})", "folder:.")
        folder_items = {".": root_item}
        for group in groups:
            folder = group.replace("\\", "/").strip("/")
            if folder in ("", "."):
                continue
            parts = folder.split("/")
            for depth in range(1, len(parts) + 1):
                prefix = "/".join(parts[:depth])
                if prefix in folder_items:
                    continue
                parent_path = "/".join(parts[:depth - 1]) or "."
                parent = folder_items[parent_path]
                count = self._folder_counts.get(prefix, 0)
                label = f"{parts[depth - 1]} ({count:,})"
                folder_items[prefix] = self._new_tree_item(
                    parent, label, f"folder:{prefix}")

        folders_root.setExpanded(True)
        root_item.setExpanded(False)
        self.group_tree.blockSignals(False)
        self.group_tree.setCurrentItem(all_item)
        self._update_folder_reorder_buttons()

    def _rebuild_collections(self):
        self.coll_combo.blockSignals(True)
        self.coll_combo.clear()
        self.coll_combo.addItem("All assets")
        if self.project:
            for name in self.project.collections:
                self.coll_combo.addItem(f"★ {name}")
        self.coll_combo.blockSignals(False)

    # ------------------------------------------------------------------
    def _on_search(self, _text):
        # Search narrows the current folder, category, or collection instead of
        # unexpectedly discarding the user's browsing context.
        self.refresh()

    def _on_tree_pick(self, cur, _prev):
        if not cur:
            self._update_folder_reorder_buttons()
            return
        token = cur.data(0, Qt.ItemDataRole.UserRole) or "all"
        if token == "all" or token in {"smart-root", "folder-root"}:
            self._view = ("all", None)
        elif token.startswith("category-group:"):
            self._view = ("category_group", token.split(":", 1)[1])
        elif token.startswith("category:"):
            self._view = ("category", token.split(":", 1)[1])
        elif token.startswith("folder:"):
            self._view = ("folder", token.split(":", 1)[1])
        else:
            self._view = ("all", None)

        self.coll_combo.blockSignals(True)
        self.coll_combo.setCurrentIndex(0)
        self.coll_combo.blockSignals(False)
        self._update_folder_reorder_buttons()
        self.refresh()

    def _on_collection_pick(self, idx):
        if idx <= 0:
            self._view = ("all", None)
            item = self._tree_items.get("all")
            if item and self.group_tree.currentItem() is not item:
                self.group_tree.blockSignals(True)
                self.group_tree.setCurrentItem(item)
                self.group_tree.blockSignals(False)
        else:
            name = self.coll_combo.itemText(idx).lstrip("★ ").strip()
            self._view = ("collection", name)
            self.group_tree.blockSignals(True)
            self.group_tree.setCurrentItem(None)
            self.group_tree.blockSignals(False)
            self._update_folder_reorder_buttons()
        self.refresh()

    def _selected_folder_group(self):
        item = self.group_tree.currentItem()
        if not item:
            return None
        token = item.data(0, Qt.ItemDataRole.UserRole) or ""
        if not token.startswith("folder:"):
            return None
        path = token.split(":", 1)[1]
        if path == "." or path not in self._folder_groups:
            return None
        return path

    def _update_folder_reorder_buttons(self):
        path = self._selected_folder_group()
        groups = self._folder_groups
        if path is None:
            self.b_group_up.setEnabled(False)
            self.b_group_down.setEnabled(False)
            return
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        siblings = [group for group in groups
                    if group != "." and
                    (group.rsplit("/", 1)[0] if "/" in group else "") == parent]
        index = siblings.index(path)
        self.b_group_up.setEnabled(index > 0)
        self.b_group_down.setEnabled(index < len(siblings) - 1)

    def _move_group(self, delta: int):
        path = self._selected_folder_group()
        if path is None or not self.project:
            return
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        order = list(self._folder_groups)
        siblings = [group for group in order
                    if group != "." and
                    (group.rsplit("/", 1)[0] if "/" in group else "") == parent]
        index = siblings.index(path)
        target = index + delta
        if target < 0 or target >= len(siblings):
            return
        first, second = order.index(siblings[index]), order.index(siblings[target])
        order[first], order[second] = order[second], order[first]
        self.project.group_order = order
        self._rebuild_groups()
        item = self._tree_items.get(f"folder:{path}")
        if item:
            parent = item.parent()
            while parent:
                parent.setExpanded(True)
                parent = parent.parent()
            self.group_tree.setCurrentItem(item)

    def _group_up(self):
        self._move_group(-1)

    def _group_down(self):
        self._move_group(1)

    # ------------------------------------------------------------------
    def refresh(self):
        self.list.clear()
        mode, val = self._view
        if mode == "collection":
            paths = set(self.project.collections.get(val, [])) if self.project else set()
            assets = [asset for asset in self.library.assets if asset.path in paths]
        elif mode == "category":
            assets = [asset for asset in self.library.assets
                      if val in self._category_tags.get(asset.path, set())]
        elif mode == "category_group":
            category_ids = set(CATEGORY_GROUPS.get(val, ()))
            assets = [asset for asset in self.library.assets
                      if self._category_tags.get(asset.path, set()).intersection(category_ids)]
        elif mode == "folder" and val not in ("", "."):
            prefix = val.rstrip("/") + "/"
            assets = [asset for asset in self.library.assets
                      if asset.folder == val or asset.folder.startswith(prefix)]
        else:
            assets = list(self.library.assets)

        query = self.search.text().strip()
        if query:
            matching_paths = {asset.path for asset in self.library.search(query)}
            assets = [asset for asset in assets if asset.path in matching_paths]

        for asset in assets:
            item = QListWidgetItem()
            label = asset.name
            if asset.size:
                label += f"\n{asset.size[0]}x{asset.size[1]}"
            if asset.is_overlay:
                label += "  [overlay]"
            item.setText(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setData(Qt.ItemDataRole.UserRole, asset.path)
            tags = self._category_tags.get(asset.path, set())
            labels = sorted(CATEGORY_LABELS[tag] for tag in tags
                            if tag in CATEGORY_LABELS)
            tooltip = asset.path
            if labels:
                tooltip += "\nSmart categories: " + ", ".join(labels)
            item.setToolTip(tooltip)
            self.list.addItem(item)

        scope = "All assets"
        if mode == "folder" and val not in ("", "."):
            scope = val
        elif mode == "category":
            scope = CATEGORY_LABELS.get(val, val)
        elif mode == "category_group":
            scope = val
        elif mode == "collection":
            scope = val
        count_text = f"{len(assets):,} asset(s) · {scope}"
        if query:
            count_text += f" · search: {query}"
        self.count_label.setText(count_text)
        self._schedule_visible_thumbnails()

    def _schedule_visible_thumbnails(self):
        if hasattr(self, "_thumb_timer"):
            self._thumb_timer.start()

    def _load_visible_thumbnails(self):
        """Load icons only for visible rows, keeping large libraries responsive."""
        if not self.library.root or not self.list.count():
            return
        visible = self.list.viewport().rect()
        thumb_size = self.sl_thumb.value()
        for index in range(self.list.count()):
            item = self.list.item(index)
            in_view = self.list.visualItemRect(item).intersects(visible)
            if in_view:
                if item.icon().isNull():
                    rel_path = item.data(Qt.ItemDataRole.UserRole)
                    pm = load_scaled_pixmap(self.library.abs_path(rel_path), thumb_size)
                    if not pm.isNull():
                        item.setIcon(QIcon(pm))
            elif not item.icon().isNull():
                # Do not retain thousands of QPixmaps as users scroll through
                # high-resolution asset packs. The small LRU cache avoids
                # repeated decoding when they scroll back.
                item.setIcon(QIcon())

    def _thumb_size(self, v):
        self.list.setIconSize(QSize(v, v))
        self.refresh()

    # ------------------------------------------------------------------
    def _import_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Import asset folder",
                                            os.getcwd())
        if d and self.project:
            self._ensure_store()
            self.library.root = self.project.asset_store
            self.library.scan(self.project.asset_store)
            n = self.library.import_folder(d, preserve_root=True)
            self._after_import(n)

    def _import_file(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Import image",
                                           os.getcwd(),
                                           "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff)")
        if fn and self.project:
            self._ensure_store()
            self.library.root = self.project.asset_store
            self.library.scan(self.project.asset_store)
            rel = self.library.import_file(fn)
            self._after_import(1 if rel else 0)

    def _import_zip(self):
        if not self.project or (self._zip_worker and self._zip_worker.isRunning()):
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import asset ZIP archives", os.getcwd(), "ZIP archives (*.zip)")
        if paths:
            self.import_zip_paths(
                paths, status_text=f"Importing {len(paths)} ZIP archive(s)… "
                                   "original files are unchanged.")

    def import_zip_paths(self, paths, *, status_text: str = "",
                         progress_callback=None, completed_callback=None) -> bool:
        """Start a background import for user-selected or bundled ZIPs."""
        paths = [os.path.abspath(path) for path in paths if path]
        if (not paths or not self.project or
                (self._zip_worker and self._zip_worker.isRunning())):
            return False
        self._ensure_store()
        root = os.path.abspath(self.project.asset_store)
        self._zip_import_target_root = root
        self.count_label.setText(
            status_text or f"Importing {len(paths)} ZIP archive(s)…")
        self._set_import_buttons_enabled(False)
        self._zip_worker = ZipImportWorker(paths, root, self)
        self._zip_worker.progress.connect(self._zip_import_progress)
        self._zip_worker.completed.connect(self._zip_import_completed)
        if progress_callback:
            self._zip_worker.progress.connect(progress_callback)
        if completed_callback:
            self._zip_worker.completed.connect(completed_callback)
        self._zip_worker.finished.connect(self._zip_import_finished)
        self._zip_worker.start()
        return True

    def _zip_import_progress(self, archive_name: str, current: int, total: int):
        self.count_label.setText(
            f"Installing/importing ZIP {current} of {total}: {archive_name}…")

    def _set_import_buttons_enabled(self, enabled: bool):
        for button in (self.b_imp_f, self.b_imp_p, self.b_imp_zip):
            button.setEnabled(enabled)

    def _zip_import_completed(self, reports, errors):
        target_root = self._zip_import_target_root
        same_store = bool(self.project and target_root and
                          os.path.normcase(os.path.abspath(self.project.asset_store)) ==
                          os.path.normcase(target_root))
        if same_store:
            self.library.scan(target_root)
            self._rebuild_groups()
            self._show_all_assets()
            self.refresh()
            if any(report.imported for report in reports):
                self.collectionsChanged.emit()

        imported = sum(report.imported for report in reports)
        existing = sum(report.already_imported for report in reports)
        non_images = sum(report.skipped_non_image for report in reports)
        unsafe = sum(report.skipped_unsafe for report in reports)
        symlinks = sum(report.skipped_symlinks for report in reports)
        corrupt = sum(report.skipped_corrupt for report in reports)
        renamed = sum(report.renamed_duplicates for report in reports)
        summary = [f"Imported {imported:,} image(s) from {len(reports)} archive(s)."]
        if existing:
            summary.append(f"{existing:,} image(s) were already imported.")
        if non_images:
            summary.append(f"Skipped {non_images:,} non-image file(s).")
        if unsafe:
            summary.append(f"Rejected {unsafe:,} unsafe archive path(s).")
        if symlinks:
            summary.append(f"Skipped {symlinks:,} symbolic link(s).")
        if corrupt:
            summary.append(f"Skipped {corrupt:,} corrupt image(s).")
        if renamed:
            summary.append(f"Renamed {renamed:,} duplicate path(s) to avoid overwriting.")
        if not imported and not existing:
            summary.append("No supported images were added.")
        if not same_store and imported:
            summary.append("They were copied into the project that was active when import started.")
        if errors:
            summary.append(f"{len(errors)} archive(s) could not be imported.")
        self.count_label.setText(" ".join(summary))
        if errors:
            from PyQt6.QtWidgets import QMessageBox
            details = "\n".join(f"• {name}: {error}" for name, error in errors)
            QMessageBox.warning(self, "Some ZIPs could not be imported", details)

    def _zip_import_finished(self):
        self._zip_worker = None
        self._set_import_buttons_enabled(True)

    def wait_for_zip_import(self):
        """Do not destroy a live QThread while the editor is closing."""
        worker = self._zip_worker
        if worker and worker.isRunning():
            worker.wait()

    def _ensure_store(self):
        app_data_dir = getattr(self.window(), "_app_data_dir", "")
        default_store = default_asset_store_path(__file__, app_data_dir)
        if not self.project.asset_store:
            self.project.asset_store = default_store
        os.makedirs(self.project.asset_store, exist_ok=True)
        if os.path.normcase(os.path.abspath(self.project.asset_store)) == \
                os.path.normcase(default_store):
            seed_bundled_assets(self.project.asset_store, __file__)

    def _show_all_assets(self):
        """Clear library filters after an import so new assets are visible."""
        self._view = ("all", None)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.coll_combo.blockSignals(True)
        self.coll_combo.setCurrentIndex(0)
        self.coll_combo.blockSignals(False)
        all_item = self._tree_items.get("all")
        if all_item:
            self.group_tree.blockSignals(True)
            self.group_tree.setCurrentItem(all_item)
            self.group_tree.blockSignals(False)

    def _after_import(self, n):
        if n:
            self._rebuild_groups()
            # Don't leave a fresh import hidden behind an old search, group,
            # or collection filter. Show the complete updated library right
            # away so users can confirm the copy succeeded.
            self._show_all_assets()
            self.refresh()
            self.count_label.setText(
                f"Imported {n:,} image(s). Drag or double-click an asset "
                "to place it on the map.")
            self.collectionsChanged.emit()
        else:
            self.count_label.setText(
                "No supported images were imported. Use PNG, JPG, WEBP, BMP, or TIFF.")

    # ------------------------------------------------------------------
    def _ctx_menu(self, pos):
        item = self.list.itemAt(pos)
        if not item:
            return
        from PyQt6.QtWidgets import QMenu
        a = self.library.get(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        act = menu.addAction("View larger…")
        act.triggered.connect(lambda: self._view_large(a))
        act2 = menu.addAction("Add to canvas")
        act2.triggered.connect(lambda: self.assetActivated.emit(a.path))
        menu.exec(self.list.mapToGlobal(pos))

    def _view_large(self, asset):
        dlg = PreviewDialog(asset, self.assetActivated.emit)
        pm = load_scaled_pixmap(self.library.abs_path(asset.path), 1000)
        dlg.set_pixmap(pm)
        dlg.exec()

    # ------------------------------------------------------------------
    def _new_collection(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New collection", "Collection name:")
        if ok and name.strip() and self.project:
            name = name.strip()
            self.project.collections.setdefault(name, [])
            self._rebuild_collections()
            self.collectionsChanged.emit()

    def _add_to_collection(self):
        item = self.list.currentItem()
        if not item or not self.project:
            return
        idx = self.coll_combo.currentIndex()
        if idx <= 0:
            return
        name = self.coll_combo.itemText(idx).lstrip("★ ").strip()
        path = item.data(Qt.ItemDataRole.UserRole)
        self.project.collections.setdefault(name, [])
        if path not in self.project.collections[name]:
            self.project.collections[name].append(path)
        self.collectionsChanged.emit()

    def _remove_from_collection(self):
        item = self.list.currentItem()
        idx = self.coll_combo.currentIndex()
        if idx <= 0 or not item or not self.project:
            return
        name = self.coll_combo.itemText(idx).lstrip("★ ").strip()
        path = item.data(Qt.ItemDataRole.UserRole)
        coll = self.project.collections.get(name, [])
        if path in coll:
            coll.remove(path)
        self.collectionsChanged.emit()
        self.refresh()
