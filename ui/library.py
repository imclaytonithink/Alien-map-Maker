"""Asset library side-panel: import, groups, search, thumbnails, preview."""
from __future__ import annotations

import os
from collections import OrderedDict
from typing import Optional

from PyQt6.QtCore import (Qt, QMimeData, QSize, QPoint, QModelIndex,
                          QAbstractListModel, QObject, QRunnable, QThread,
                          QThreadPool, pyqtSignal, QTimer)
from PyQt6.QtGui import (QDrag, QPixmap, QIcon, QMouseEvent, QCursor, QImage,
                         QPainter, QPen, QColor)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QComboBox,
    QLabel, QSlider, QDialog, QFileDialog, QAbstractItemView,
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
from ui.image_utils import load_scaled_image, load_scaled_pixmap


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
        self.scanned_library = None
        self.scanned_category_tags = None

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
        try:
            # Scanning headers and classifying thousands of images belongs on
            # this worker too; doing it in the completion slot froze the GUI
            # just after a large pack finished importing.
            library.scan(self.asset_store)
            from core.asset_taxonomy import classify_asset_categories
            category_tags = classify_asset_categories(library.assets)
            self.scanned_library = library
            self.scanned_category_tags = category_tags
        except Exception as exc:
            errors.append(("Asset library scan", str(exc)))
        self.completed.emit(reports, errors)


class ThumbnailSignals(QObject):
    completed = pyqtSignal(str, int, int, QImage)


class ThumbnailTask(QRunnable):
    """Decode one bounded preview away from the GUI thread."""

    def __init__(self, path: str, size: int, generation: int,
                 signals: ThumbnailSignals):
        super().__init__()
        self.path = path
        self.size = size
        self.generation = generation
        self.signals = signals

    def run(self):
        try:
            image = load_scaled_image(self.path, self.size)
        except Exception:
            image = QImage()
        try:
            self.signals.completed.emit(
                self.path, self.size, self.generation, image)
        except RuntimeError:
            pass   # the panel was torn down while this preview was decoding


class AssetListModel(QAbstractListModel):
    """Small model for arbitrarily large libraries; no widget per asset."""

    thumbnailLoaded = pyqtSignal()
    MAX_CACHED_ICONS = 96
    MAX_PENDING_THUMBNAILS = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.assets = []
        self.category_tags = {}
        self.library = None
        self.thumb_size = 56
        self._generation = 0
        self._path_to_row = {}
        self._icons = OrderedDict()
        self._attempted = set()
        self._pending = {}
        self._thread_pool = QThreadPool(self)
        # Two decoders balance speed against peak memory for very large PNGs;
        # previews are also kept on disk (see image_utils) so each is slow only
        # once. The editor stays responsive because decoding is off-thread.
        self._thread_pool.setMaxThreadCount(2)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.assets)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.assets):
            return None
        asset = self.assets[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            label = asset.name
            if asset.size:
                label += f"\n{asset.size[0]}x{asset.size[1]}"
            if asset.is_overlay:
                label += " · overlay"
            return label
        if role == Qt.ItemDataRole.DecorationRole:
            icon = self._icons.get(asset.path)
            if icon is not None:
                self._icons.move_to_end(asset.path)
                return icon
            return self._placeholder_icon()
        if role == Qt.ItemDataRole.ToolTipRole:
            tags = self.category_tags.get(asset.path, ())
            labels = sorted(CATEGORY_LABELS[tag] for tag in tags
                            if tag in CATEGORY_LABELS)
            tooltip = asset.path
            if labels:
                tooltip += "\nSmart categories: " + ", ".join(labels)
            return tooltip
        if role == Qt.ItemDataRole.UserRole:
            return asset.path
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignHCenter)
        return None

    def _placeholder_icon(self):
        """Neutral tile shown until the real preview has been decoded."""
        cached = getattr(self, "_placeholder", None)
        if cached is not None and cached[0] == self.thumb_size:
            return cached[1]
        size = self.thumb_size
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        pen = QPen(QColor(128, 140, 155, 150))
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(2, 2, size - 5, size - 5)
        painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "loading…")
        painter.end()
        icon = QIcon(pm)
        self._placeholder = (size, icon)
        return icon

    def set_assets(self, assets, category_tags=None, library=None):
        self.beginResetModel()
        self._generation += 1
        self.assets = assets if isinstance(assets, list) else list(assets)
        self.category_tags = category_tags or {}
        self.library = library
        self._path_to_row = {asset.path: row
                             for row, asset in enumerate(self.assets)}
        self._icons.clear()
        self._attempted.clear()
        self.endResetModel()

    def set_thumb_size(self, size: int):
        size = max(16, int(size))
        if size == self.thumb_size:
            return
        self.thumb_size = size
        self._generation += 1
        self._icons.clear()
        self._attempted.clear()

    def asset_at(self, index):
        if index is None or not index.isValid():
            return None
        row = index.row()
        return self.assets[row] if 0 <= row < len(self.assets) else None

    def icon_for_index(self, index):
        asset = self.asset_at(index)
        if not asset:
            return None
        icon = self._icons.get(asset.path)
        if icon is not None:
            self._icons.move_to_end(asset.path)
        return icon

    def has_requested_thumbnail(self, index):
        asset = self.asset_at(index)
        return bool(asset and (asset.path in self._attempted
                               or asset.path in self._icons
                               or (self._generation, asset.path) in self._pending))

    def request_thumbnail(self, index):
        asset = self.asset_at(index)
        if not asset or self.has_requested_thumbnail(index):
            return False
        if len(self._pending) >= self.MAX_PENDING_THUMBNAILS:
            return False
        key = (self._generation, asset.path)
        signals = ThumbnailSignals()
        signals.completed.connect(self._thumbnail_ready)
        self._pending[key] = signals
        image_path = (self.library.abs_path(asset.path)
                      if self.library else asset.path)
        self._thread_pool.start(ThumbnailTask(
            image_path, self.thumb_size, self._generation, signals))
        return True

    def _thumbnail_ready(self, path: str, size: int, generation: int,
                         image: QImage):
        self._pending.pop((generation, path), None)
        if generation != self._generation or size != self.thumb_size:
            self.thumbnailLoaded.emit()
            return
        self._attempted.add(path)
        if not image.isNull():
            self._icons[path] = QIcon(QPixmap.fromImage(image))
            self._icons.move_to_end(path)
            while len(self._icons) > self.MAX_CACHED_ICONS:
                evicted, _icon = self._icons.popitem(last=False)
                self._attempted.discard(evicted)
            row = self._path_to_row.get(path)
            if row is not None:
                index = self.index(row, 0)
                self.dataChanged.emit(
                    index, index, [int(Qt.ItemDataRole.DecorationRole)])
        self.thumbnailLoaded.emit()


class AssetList(QListView):
    """Thumbnail grid whose items stay virtual and whose images load lazily."""

    viewportResized = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.asset_model = AssetListModel(self)
        self.setModel(self.asset_model)
        self.setIconSize(QSize(56, 56))
        self.setSpacing(4)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(128)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._press_index = QModelIndex()
        self._press_pos = None
        self._pop = None
        self._pop_path = ""
        self._pop_timer = QTimer(self)
        self._pop_timer.setSingleShot(True)
        self._pop_timer.timeout.connect(self._show_popout)
        self.entered.connect(self._on_entered)
        self.asset_model.thumbnailLoaded.connect(self._thumbnail_ready)
        self.setMouseTracking(True)
        self._update_grid_size()

    def count(self):
        """Compatibility helper for callers/tests that previously used QListWidget."""
        return self.asset_model.rowCount()

    def set_assets(self, assets, category_tags=None, library=None):
        self.asset_model.set_assets(assets, category_tags, library)
        self.viewport().update()

    def set_thumbnail_size(self, size: int):
        size = max(16, int(size))
        self.setIconSize(QSize(size, size))
        self._update_grid_size(size)
        self.asset_model.set_thumb_size(size)
        self.viewport().update()

    def _update_grid_size(self, thumb_size=None):
        size = int(thumb_size or self.iconSize().width())
        self.setGridSize(QSize(max(120, size + 40), size + 68))

    def path_at(self, pos):
        index = pos if isinstance(pos, QModelIndex) else self.indexAt(pos)
        value = self.asset_model.data(index, Qt.ItemDataRole.UserRole)
        return value or ""

    def current_path(self):
        return self.path_at(self.currentIndex())

    def mousePressEvent(self, event: QMouseEvent):
        self._press_index = self.indexAt(event.position().toPoint())
        self._press_pos = event.position().toPoint()
        self._pop_timer.stop()
        self._pop_path = ""
        self._hide_popout()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if (self._press_index.isValid()
                and event.buttons() & Qt.MouseButton.LeftButton):
            if (event.position().toPoint() - self._press_pos).manhattanLength() > 6:
                path = self.path_at(self._press_index)
                if path:
                    self._start_drag(path, self._press_index)
                self._press_index = QModelIndex()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        super().mouseReleaseEvent(event)
        self._press_index = QModelIndex()

    def leaveEvent(self, event):
        self._pop_timer.stop()
        self._pop_path = ""
        self._hide_popout()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_grid_size()
        self.viewportResized.emit()

    def _on_entered(self, index):
        self._pop_timer.stop()
        self._hide_popout()
        self._pop_path = self.path_at(index)
        if self._pop_path:
            self._pop_timer.start(600)

    def _show_popout(self):
        index = self.indexAt(self.mapFromGlobal(QCursor.pos()))
        if not index.isValid():
            return
        path = self.path_at(index)
        if not path:
            return
        icon = self.asset_model.icon_for_index(index)
        if not icon or icon.isNull():
            self._pop_path = path
            self.asset_model.request_thumbnail(index)
            return
        pixmap = icon.pixmap(QSize(160, 160))
        if pixmap.isNull():
            return
        if self._pop is None:
            self._pop = QLabel(self.window())
            self._pop.setObjectName("AssetPreviewPopout")
            self._pop.setWindowFlags(Qt.WindowType.ToolTip)
        self._pop.setPixmap(pixmap.scaled(
            160, 160, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        self._pop.adjustSize()
        self._pop.move(QCursor.pos() + QPoint(12, 12))
        self._pop.show()

    def _thumbnail_ready(self):
        if not self._pop_path:
            return
        index = self.indexAt(self.mapFromGlobal(QCursor.pos()))
        if index.isValid() and self.path_at(index) == self._pop_path:
            self._show_popout()

    def _hide_popout(self):
        if self._pop:
            self._pop.hide()

    def _start_drag(self, path: str, index):
        mime = QMimeData()
        mime.setData(ASSET_MIME, path.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        icon = self.asset_model.icon_for_index(index)
        if icon and not icon.isNull():
            pixmap = icon.pixmap(QSize(64, 64))
            if not pixmap.isNull():
                drag.setPixmap(pixmap)
        drag.exec(Qt.DropAction.CopyAction)
        self._press_index = QModelIndex()


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
        self._preclassified_key = None
        self._preclassified_tags = None
        self._library_stats_key = None
        self._category_counts = {}
        self._category_group_counts = {}
        self._folder_counts = {}
        self._tree_items = {}
        self._folder_groups = []
        self._zip_worker = None
        self._zip_import_target_root = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Keep search and import actions on separate rows so the narrow side
        # panel never compresses them into clipped, overlapping controls.
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search filename, folder, size…")
        self.search.textChanged.connect(self._on_search)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(140)
        self._search_timer.timeout.connect(self.refresh)
        layout.addWidget(self.search)

        import_row = QHBoxLayout()
        import_row.setSpacing(4)
        self.b_imp_f = QPushButton("Folder")
        self.b_imp_f.setToolTip(
            "Import Folder — copy images into the internal library, keeping the selected "
            "folder name and its subfolders as an expandable file tree. "
            "Useful for Core / Symbols packs. Importing does not place them "
            "on the map; drag or double-click "
            "an asset afterward.")
        self.b_imp_f.clicked.connect(self._import_folder)
        self.b_imp_p = QPushButton("File")
        self.b_imp_p.setToolTip(
            "Import File — copy one image into the internal library. Drag or double-click "
            "it afterward to place it on the map.")
        self.b_imp_p.clicked.connect(self._import_file)
        self.b_imp_zip = QPushButton("ZIP")
        self.b_imp_zip.setToolTip(
            "Import ZIP — import supported images from one or more archives. "
            "Folder paths are preserved; non-image files are skipped.")
        self.b_imp_zip.clicked.connect(self._import_zip)
        for button in (self.b_imp_f, self.b_imp_p, self.b_imp_zip):
            button.setMinimumWidth(0)
            import_row.addWidget(button, 1)
        layout.addLayout(import_row)

        # Hierarchical file paths and virtual smart-category views.
        g_row = QHBoxLayout()
        self.group_tree = QTreeWidget()
        self.group_tree.setHeaderHidden(True)
        self.group_tree.setMaximumHeight(190)
        self.group_tree.setUniformRowHeights(True)
        self.group_tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.group_tree.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.group_tree.setToolTip(
            "Browse the preserved asset-store folder paths or use overlapping "
            "smart categories. Categories filter files in place; they never "
            "move or duplicate assets.")
        self.group_tree.currentItemChanged.connect(self._on_tree_pick)
        g_row.addWidget(self.group_tree, 1)
        g_btns = QVBoxLayout()
        self.b_group_up = QPushButton("▲")
        self.b_group_up.setObjectName("PanelIconButton")
        self.b_group_up.setFixedSize(38, 34)
        self.b_group_up.setToolTip("Move this folder earlier among its siblings")
        self.b_group_up.clicked.connect(self._group_up)
        self.b_group_down = QPushButton("▼")
        self.b_group_down.setObjectName("PanelIconButton")
        self.b_group_down.setFixedSize(38, 34)
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
        self.coll_combo.setMinimumWidth(70)
        self.coll_combo.currentIndexChanged.connect(self._on_collection_pick)
        btn_new = QPushButton("New"); btn_new.setMaximumWidth(52)
        btn_new.setToolTip("Create a collection")
        btn_new.clicked.connect(self._new_collection)
        btn_add = QPushButton("Add"); btn_add.setMaximumWidth(52)
        btn_add.setToolTip("Add the selected asset to this collection")
        btn_add.clicked.connect(self._add_to_collection)
        btn_del = QPushButton("×"); btn_del.setMaximumWidth(36)
        btn_del.setToolTip("Remove the selected asset from this collection")
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
        self.list.doubleClicked.connect(self._asset_double_clicked)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._ctx_menu)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(45)
        self._thumb_timer.timeout.connect(self._load_visible_thumbnails)
        self.list.verticalScrollBar().valueChanged.connect(
            lambda _value: self._schedule_visible_thumbnails())
        self.list.viewportResized.connect(self._schedule_visible_thumbnails)
        self.list.asset_model.thumbnailLoaded.connect(
            self._schedule_visible_thumbnails)
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
        b_where.setMaximumWidth(36)
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
        self.count_label.setWordWrap(True)
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
        target_root = (os.path.normcase(os.path.abspath(project.asset_store))
                       if project.asset_store else "")
        current_root = (os.path.normcase(os.path.abspath(library.root))
                        if library.root else "")
        if current_root != target_root or not library._scan_complete:
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
        item.setToolTip(0, label)
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
        stats_key = (id(self.library), self.library._scan_revision)
        if stats_key != self._library_stats_key:
            if self._preclassified_key == stats_key:
                self._category_tags = self._preclassified_tags or {}
                self._preclassified_key = None
                self._preclassified_tags = None
            else:
                self._category_tags = classify_asset_categories(self.library.assets)
            self._category_counts = {
                category_id: 0
                for _group, categories in SMART_CATEGORY_TREE
                for category_id, _label in categories
            }
            group_category_ids = {
                group_name: {category_id for category_id, _label in categories}
                for group_name, categories in SMART_CATEGORY_TREE
            }
            self._category_group_counts = {
                group_name: 0 for group_name, _categories in SMART_CATEGORY_TREE
            }
            self._folder_counts = {".": len(self.library.assets)}
            for tags in self._category_tags.values():
                for tag in tags:
                    if tag in self._category_counts:
                        self._category_counts[tag] += 1
                for group_name, category_ids in group_category_ids.items():
                    if not tags.isdisjoint(category_ids):
                        self._category_group_counts[group_name] += 1
            for asset in self.library.assets:
                folder = asset.folder.replace("\\", "/").strip("/")
                if folder in ("", "."):
                    continue
                parts = folder.split("/")
                for depth in range(1, len(parts) + 1):
                    prefix = "/".join(parts[:depth])
                    self._folder_counts[prefix] = \
                        self._folder_counts.get(prefix, 0) + 1
            self._library_stats_key = stats_key

        self.group_tree.blockSignals(True)
        self.group_tree.clear()
        self._tree_items = {}

        total = len(self.library.assets)
        all_item = self._new_tree_item(None, f"All assets ({total:,})", "all")

        smart_root = self._new_tree_item(
            None, f"Smart categories ({total:,})", "smart-root")
        for group_name, categories in SMART_CATEGORY_TREE:
            group_item = self._new_tree_item(
                smart_root, f"{group_name} ({self._category_group_counts[group_name]:,})",
                f"category-group:{group_name}")
            for category_id, label in categories:
                self._new_tree_item(
                    group_item,
                    f"{label} ({self._category_counts[category_id]:,})",
                    f"category:{category_id}")
            group_item.setExpanded(True)
        smart_root.setExpanded(True)

        folders_root = self._new_tree_item(
            None, f"Folders / file paths ({total:,})", "folder-root")
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
        self.group_tree.setCurrentItem(all_item)
        self.group_tree.blockSignals(False)
        self._view = ("all", None)
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
    def _asset_double_clicked(self, index):
        path = self.list.path_at(index)
        if path:
            self.assetActivated.emit(path)

    def _on_search(self, _text):
        # Debounce scans of a large asset list while retaining the current
        # folder, category, or collection browsing context.
        self._search_timer.start()

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
            needle = query.casefold()
            assets = [asset for asset in assets
                      if needle in asset.name.casefold()
                      or needle in asset.folder.casefold()
                      or any(needle in tag.casefold() for tag in asset.tags)]

        # A model-backed view creates no QListWidgetItem per file, which keeps
        # both memory use and filter/refresh time bounded for large packs.
        self.list.set_assets(assets, self._category_tags, self.library)

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

    def _schedule_visible_thumbnails(self, *_args):
        if hasattr(self, "_thumb_timer"):
            self._thumb_timer.start()

    def _load_visible_thumbnails(self):
        """Request only visible thumbnails; image decoding runs in one worker."""
        if not self.library.root or not self.list.count():
            return
        view = self.list
        model = view.asset_model
        visible = view.viewport().rect()
        grid = view.gridSize()
        step_x = max(20, grid.width() // 2)
        step_y = max(20, grid.height() // 2)
        visible_rows = set()
        for y in range(visible.top(), visible.bottom() + 1, step_y):
            for x in range(visible.left(), visible.right() + 1, step_x):
                index = view.indexAt(QPoint(x, y))
                if index.isValid():
                    visible_rows.add(index.row())
        requested = 0
        for row in sorted(visible_rows):
            index = model.index(row, 0)
            if model.has_requested_thumbnail(index):
                continue
            if model.request_thumbnail(index):
                requested += 1
            if requested >= 3:
                break

    def _thumb_size(self, v):
        self.list.set_thumbnail_size(v)
        self._schedule_visible_thumbnails()

    # ------------------------------------------------------------------
    def _import_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Import asset folder",
                                            os.getcwd())
        if d and self.project:
            self._ensure_store()
            self.library.root = self.project.asset_store
            n = self.library.import_folder(d, preserve_root=True)
            self._after_import(n)

    def _import_file(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Import image",
                                           os.getcwd(),
                                           "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff)")
        if fn and self.project:
            self._ensure_store()
            self.library.root = self.project.asset_store
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
            worker = self._zip_worker
            scanned = getattr(worker, "scanned_library", None)
            if scanned is not None:
                self.library.adopt_scan(scanned)
                self._preclassified_key = (
                    id(self.library), self.library._scan_revision)
                self._preclassified_tags = worker.scanned_category_tags
            else:
                # Safe fallback for an unexpected worker-side indexing error.
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
        self._search_timer.stop()
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
        path = self.list.path_at(pos)
        asset = self.library.get(path) if path else None
        if not asset:
            return
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        act = menu.addAction("View larger…")
        act.triggered.connect(lambda: self._view_large(asset))
        act2 = menu.addAction("Add to canvas")
        act2.triggered.connect(lambda: self.assetActivated.emit(asset.path))
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
        path = self.list.current_path()
        if not path or not self.project:
            return
        idx = self.coll_combo.currentIndex()
        if idx <= 0:
            return
        name = self.coll_combo.itemText(idx).lstrip("★ ").strip()
        self.project.collections.setdefault(name, [])
        if path not in self.project.collections[name]:
            self.project.collections[name].append(path)
        self.collectionsChanged.emit()

    def _remove_from_collection(self):
        path = self.list.current_path()
        idx = self.coll_combo.currentIndex()
        if idx <= 0 or not path or not self.project:
            return
        name = self.coll_combo.itemText(idx).lstrip("★ ").strip()
        coll = self.project.collections.get(name, [])
        if path in coll:
            coll.remove(path)
        self.collectionsChanged.emit()
        self.refresh()
