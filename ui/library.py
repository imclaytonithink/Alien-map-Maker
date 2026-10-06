"""Asset library side-panel: import, groups, search, thumbnails, preview."""
from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt, QMimeData, QSize, pyqtSignal, QTimer, QEvent
from PyQt6.QtGui import QDrag, QPixmap, QIcon, QMouseEvent, QCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QLabel, QSlider, QDialog, QFileDialog,
    QListView,
)

from core.asset_manager import AssetLibrary
from core.project import Project


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


class AssetList(QListWidget):
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

    def _on_entered(self, item):
        self._pop_timer.stop()
        self._hide_popout()
        if item:
            self._pop_timer.start(600)

    def _show_popout(self):
        item = self.itemAt(self.mapFromGlobal(QCursor.pos()))
        if not item:
            return
        pm = item.data(Qt.ItemDataRole.UserRole + 1)
        if not pm or pm.isNull():
            return
        if self._pop is None:
            self._pop = QLabel(self.window())
            self._pop.setWindowFlags(Qt.WindowType.ToolTip)
            self._pop.setStyleSheet("border:1px solid #2e6fdf; background:#0d1219;")
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
        pm = QPixmap()
        data = self._press_item.data(Qt.ItemDataRole.UserRole + 1)
        if data:
            drag.setPixmap(data.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation))
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
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # search + import
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name / size (40x120)")
        self.search.textChanged.connect(self._on_search)
        top.addWidget(self.search, 1)
        b_imp_f = QPushButton("Import Folder")
        b_imp_f.clicked.connect(self._import_folder)
        b_imp_p = QPushButton("Import File")
        b_imp_p.clicked.connect(self._import_file)
        top.addWidget(b_imp_f)
        top.addWidget(b_imp_p)
        layout.addLayout(top)

        # group list with reorder
        g_row = QHBoxLayout()
        self.group_list = QListWidget()
        self.group_list.setMaximumHeight(110)
        self.group_list.currentItemChanged.connect(self._on_group_pick)
        g_row.addWidget(self.group_list, 1)
        g_btns = QVBoxLayout()
        up = QPushButton("▲"); up.clicked.connect(self._group_up)
        dn = QPushButton("▼"); dn.clicked.connect(self._group_down)
        g_btns.addWidget(up); g_btns.addWidget(dn)
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
        layout.addWidget(self.list, 1)

        # where the internal store lives + how to get help
        store_row = QHBoxLayout()
        self.lbl_store = QLabel("Store: (not set)")
        self.lbl_store.setToolTip(
            "Internal asset store.\n\nEverything you import is COPIED into "
            "this folder and lives there permanently — maps reference those "
            "copies. Subfolders become groups in the list above.\n\n"
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
            "• 'Import Folder' / 'Import File' COPY your images into that "
            "folder.\n"
            "  They stay there forever — this app never depends on the "
            "original files.\n"
            "• Subfolders inside the store become the groups shown in the "
            "list,\n"
            "  and tiles are auto-tagged by name (room/floor, wall, "
            "corridor, terminal…).\n"
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

    def _rebuild_groups(self):
        self.group_list.blockSignals(True)
        self.group_list.clear()
        order = self.project.group_order
        groups = self.library.groups(order)
        # ensure order stored
        self.project.group_order = groups
        for g in groups:
            self.group_list.addItem(g)
        self.group_list.blockSignals(False)

    def _rebuild_collections(self):
        self.coll_combo.blockSignals(True)
        self.coll_combo.clear()
        self.coll_combo.addItem("All assets")
        if self.project:
            for name in self.project.collections:
                self.coll_combo.addItem(f"★ {name}")
        self.coll_combo.blockSignals(False)

    # ------------------------------------------------------------------
    def _on_search(self, text):
        if text.strip():
            self._view = ("search", text)
        else:
            self._view = ("all", None)
        self.refresh()

    def _on_group_pick(self, cur, prev):
        if cur:
            self._view = ("group", cur.text())
            self.refresh()

    def _on_collection_pick(self, idx):
        if idx <= 0:
            self._view = ("all", None)
        else:
            name = self.coll_combo.itemText(idx).lstrip("★ ").strip()
            self._view = ("collection", name)
        self.refresh()

    def _group_up(self):
        i = self.group_list.currentRow()
        if i > 0:
            self.group_list.insertItem(i - 1, self.group_list.takeItem(i))
            self.group_list.setCurrentRow(i - 1)
            self._sync_group_order()

    def _group_down(self):
        i = self.group_list.currentRow()
        if 0 <= i < self.group_list.count() - 1:
            self.group_list.insertItem(i + 1, self.group_list.takeItem(i))
            self.group_list.setCurrentRow(i + 1)
            self._sync_group_order()

    def _sync_group_order(self):
        self.project.group_order = [self.group_list.item(r).text()
                                    for r in range(self.group_list.count())]

    # ------------------------------------------------------------------
    def refresh(self):
        self.list.clear()
        mode, val = self._view
        if mode == "search":
            assets = self.library.search(val)
        elif mode == "group":
            assets = self.library.assets_in_group(val)
        elif mode == "collection":
            paths = set(self.project.collections.get(val, []))
            assets = [a for a in self.library.assets if a.path in paths]
        else:
            assets = self.library.assets
        for a in assets:
            item = QListWidgetItem()
            pm = QPixmap(self.library.abs_path(a.path))
            if not pm.isNull():
                item.setIcon(QIcon(pm.scaled(self.sl_thumb.value(),
                                           self.sl_thumb.value(),
                                           Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)))
                item.setData(Qt.ItemDataRole.UserRole + 1, pm)
            label = a.name
            if a.size:
                label += f"\n{a.size[0]}x{a.size[1]}"
            if a.is_overlay:
                label += "  [overlay]"
            item.setText(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setData(Qt.ItemDataRole.UserRole, a.path)
            self.list.addItem(item)
        self.count_label.setText(f"{len(assets)} asset(s)")

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
            n = self.library.import_folder(d)
            self._after_import(n)

    def _import_file(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Import PNG",
                                           os.getcwd(),
                                           "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if fn and self.project:
            self._ensure_store()
            self.library.root = self.project.asset_store
            self.library.scan(self.project.asset_store)
            rel = self.library.import_file(fn)
            self._after_import(1 if rel else 0)

    def _ensure_store(self):
        if not self.project.asset_store:
            store = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "asset_store")
            self.project.asset_store = os.path.abspath(store)
        os.makedirs(self.project.asset_store, exist_ok=True)

    def _after_import(self, n):
        if n:
            self._rebuild_groups()
            self.refresh()
            self.collectionsChanged.emit()

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
        pm = QPixmap(self.library.abs_path(asset.path))
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
