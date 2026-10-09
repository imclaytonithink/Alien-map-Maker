"""Asset library side-panel: import, groups, search, thumbnails, preview."""
from __future__ import annotations

import os
from collections import OrderedDict
from typing import Optional

from PyQt6.QtCore import (Qt, QMimeData, QSettings, QSize, QPoint, QModelIndex,
                          QAbstractListModel, QObject, QRunnable, QThread,
                          QThreadPool, pyqtSignal, QTimer)
from PyQt6.QtGui import (QDrag, QPixmap, QIcon, QMouseEvent, QCursor, QImage,
                         QPainter, QPen, QColor)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QComboBox,
    QLabel, QSlider, QDialog, QFileDialog, QAbstractItemView,
    QListView, QTreeWidget, QTreeWidgetItem, QToolButton, QMenu, QSpinBox,
    QWidgetAction,
)

from core.asset_manager import AssetLibrary
from core.project import Project
from ui.branding import (SETTINGS_ID, default_asset_store_path,
                         prune_demo_assets, seed_bundled_assets)
from ui.image_utils import load_scaled_image, load_scaled_pixmap


ASSET_MIME = "application/x-mapbuilder-asset"


from core.function_tags import label as function_label


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
            self.scanned_library = library
        except Exception as exc:
            errors.append(("Asset library scan", str(exc)))
        self.completed.emit(reports, errors)


THUMB_MIN = 32
THUMB_MAX = 360
THUMB_PRESETS = (("Small", 64), ("Medium", 96), ("Large", 160),
                 ("Extra large", 240), ("Huge", 360))


class ThumbnailSignals(QObject):
    completed = pyqtSignal(str, int, int, QImage)


class ThumbnailTask(QRunnable):
    """Decode one bounded preview away from the GUI thread."""

    def __init__(self, path: str, size: int, generation: int,
                 signals: ThumbnailSignals, key: str = ""):
        super().__init__()
        self.path = path            # file to decode
        self.key = key or path      # asset id the result is reported under
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
                self.key, self.size, self.generation, image)
        except RuntimeError:
            pass   # the panel was torn down while this preview was decoding


class AssetListModel(QAbstractListModel):
    """Small model for arbitrarily large libraries; no widget per asset.

    Every row carries a checkbox: ticking assets (or whole folders in the
    tree) is what picks them for the map generator."""

    thumbnailLoaded = pyqtSignal()
    pickToggled = pyqtSignal(str, bool)     # asset path, now picked?
    MAX_CACHED_ICONS = 96
    MAX_PENDING_THUMBNAILS = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.assets = []
        self.library = None
        self.picked_contains = None         # callable(path) -> bool, set by panel
        self.thumb_size = 56
        self._generation = 0
        self._path_to_row = {}
        self._icons = OrderedDict()
        self._attempted = set()
        self._failed = set()          # decoded but unreadable: show "no preview"
        self._pending = {}
        self._thread_pool = QThreadPool(self)
        # Two decoders balance speed against peak memory for very large PNGs;
        # previews are also kept on disk (see image_utils) so each is slow only
        # once. The editor stays responsive because decoding is off-thread.
        self._thread_pool.setMaxThreadCount(3)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.assets)

    def flags(self, index):
        base = super().flags(index)
        if index.isValid():
            base |= Qt.ItemFlag.ItemIsUserCheckable
        return base

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid() or role != Qt.ItemDataRole.CheckStateRole:
            return False
        asset = self.assets[index.row()]
        checked = value == Qt.CheckState.Checked
        # The panel owns the picked set; it echoes the change back through
        # dataChanged so the checkbox repaints.
        self.pickToggled.emit(asset.path, checked)
        return True

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.assets):
            return None
        asset = self.assets[index.row()]
        if role == Qt.ItemDataRole.CheckStateRole:
            picked = self.picked_contains(asset.path) \
                if callable(self.picked_contains) else False
            return (Qt.CheckState.Checked if picked
                    else Qt.CheckState.Unchecked)
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
            return self._placeholder_icon(failed=asset.path in self._failed)
        if role == Qt.ItemDataRole.ToolTipRole:
            tooltip = asset.path
            if asset.folder and asset.folder not in (".", ""):
                tooltip += f"\nFolder: {asset.folder}"
            if asset.path in self._failed:
                tooltip += "\nPreview could not be generated for this image."
            return tooltip
        if role == Qt.ItemDataRole.UserRole:
            return asset.path
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignHCenter)
        return None

    def _placeholder_icon(self, failed: bool = False):
        """Neutral tile shown until the real preview has been decoded (or a
        distinct one when the image could not be read at all)."""
        cache = getattr(self, "_placeholders", None)
        if cache is None:
            cache = self._placeholders = {}
        cached = cache.get(failed)
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
        painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter,
                         "no preview" if failed else "loading…")
        painter.end()
        icon = QIcon(pm)
        cache[failed] = (size, icon)
        return icon

    def set_assets(self, assets, library=None):
        self.beginResetModel()
        self._generation += 1
        self.assets = assets if isinstance(assets, list) else list(assets)
        self.library = library
        self._path_to_row = {asset.path: row
                             for row, asset in enumerate(self.assets)}
        self._icons.clear()
        self._attempted.clear()
        self._failed.clear()
        self.endResetModel()

    def set_thumb_size(self, size: int):
        size = max(16, int(size))
        if size == self.thumb_size:
            return
        self.thumb_size = size
        self._generation += 1
        self._icons.clear()
        self._attempted.clear()
        self._failed.clear()

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
        # Report under the asset's own (store-relative) path: that is what the
        # model's icon cache, pending table and row lookup are all keyed by.
        self._thread_pool.start(ThumbnailTask(
            image_path, self.thumb_size, self._generation, signals,
            key=asset.path))
        return True

    def drain_background_work(self):
        """Finish any in-flight thumbnail decoding before teardown.

        ``_thread_pool`` is a QThreadPool parented to this model, itself
        parented into the asset-list's widget tree, so Qt's normal child
        teardown cascade destroys it as a nested step of a C++ destructor
        chain with the GIL held throughout. If a worker is mid-run at that
        point it can never reacquire the GIL to finish, deadlocking against
        the thread doing the deleting. Draining explicitly from Python here
        uses the ordinary GIL-releasing call path instead.
        """
        self._thread_pool.clear()
        self._thread_pool.waitForDone()

    def _thumbnail_ready(self, path: str, size: int, generation: int,
                         image: QImage):
        self._pending.pop((generation, path), None)
        if generation != self._generation or size != self.thumb_size:
            self.thumbnailLoaded.emit()
            return
        self._attempted.add(path)
        if image.isNull():
            self._failed.add(path)
            row = self._path_to_row.get(path)
            if row is not None:
                index = self.index(row, 0)
                self.dataChanged.emit(
                    index, index, [int(Qt.ItemDataRole.DecorationRole)])
        else:
            self._failed.discard(path)
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
    thumbnailZoomRequested = pyqtSignal(int)   # +1 / -1 from Ctrl+wheel

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
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = 1 if event.angleDelta().y() > 0 else -1
            self.thumbnailZoomRequested.emit(step)
            event.accept()
            return
        super().wheelEvent(event)

    def count(self):
        """Compatibility helper for callers/tests that previously used QListWidget."""
        return self.asset_model.rowCount()

    def set_assets(self, assets, library=None):
        self.asset_model.set_assets(assets, library)
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

    def drain_background_work(self):
        self.asset_model.drain_background_work()


class LibraryPanel(QWidget):
    assetActivated = pyqtSignal(str)   # double-click -> add at center
    collectionsChanged = pyqtSignal()
    pinStampRequested = pyqtSignal(int, str)   # hotbar slot (0-8), asset path
    swapRequested = pyqtSignal(str)            # swap selected map nodes to this asset
    swapAllRequested = pyqtSignal(str)         # swap every copy of the selected image
    backdropRequested = pyqtSignal(str, bool)  # asset path, all levels?
    generateRequested = pyqtSignal(list)       # build a map from these asset paths
    pickChanged = pyqtSignal()                 # the generator pick set changed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library = AssetLibrary()
        self.project: Optional[Project] = None
        self.add_at_center_cb = None
        self._view = ("all", None)
        self._library_stats_key = None
        self._folder_counts = {}
        self._tree_items = {}
        self._visible_assets = []
        self._folder_groups = []
        self._zip_worker = None
        self._zip_import_target_root = ""
        self._picked: set = set()      # asset paths ticked for the generator
        self._syncing_tree = False
        self._count_base = ""
        self.stamp_labels = None    # callable -> names on the stamp keys 1-9
        self.canvas_swap_info = None  # callable -> (selected map images, first's path)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Keep search and import actions on separate rows so the narrow side
        # panel never compresses them into clipped, overlapping controls.
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, folder, size or function (medical, cargo…)")
        self.search.textChanged.connect(self._on_search)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(140)
        self._search_timer.timeout.connect(self.refresh)
        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        search_row.addWidget(self.search, 1)
        self.function_filter = QComboBox()
        self.function_filter.setToolTip("Show only tiles for one function: medical, cargo, hangar…")
        self.function_filter.setMinimumContentsLength(10)
        self.function_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.function_filter.addItem("Any function", "")
        self.function_filter.currentIndexChanged.connect(self._on_search)
        self._function_sig = None
        search_row.addWidget(self.function_filter)
        # One obvious way to grow the pool with your own art.
        self.btn_add = QToolButton()
        self.btn_add.setText("＋ Add tiles")
        self.btn_add.setToolTip(
            "Add your own tiles to the pool: a whole folder, single image "
            "files, or ZIP archives. Anything that is not a readable image is "
            "left out during the import.")
        self.btn_add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        add_menu = QMenu(self.btn_add)
        add_menu.addAction("Folder…", self._import_folder)
        add_menu.addAction("Image files…", self._import_file)
        add_menu.addAction("ZIP archive(s)…", self._import_zip)
        self.btn_add.setMenu(add_menu)
        search_row.addWidget(self.btn_add)
        self.btn_menu = QToolButton()
        self.btn_menu.setText("☰")
        self.btn_menu.setObjectName("LibraryMenuButton")
        self.btn_menu.setToolTip(
            "Library menu — import, collections, thumbnail size, folder tree")
        self.btn_menu.setMinimumSize(38, 34)
        self.btn_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        search_row.addWidget(self.btn_menu)
        layout.addLayout(search_row)

        # Import actions live in the library menu (☰ next to search) instead of
        # a permanent row of buttons. The buttons stay as hidden state holders
        # so a running ZIP import can still disable them.
        self.b_imp_f = QPushButton("Folder", self)
        self.b_imp_f.clicked.connect(self._import_folder)
        self.b_imp_p = QPushButton("File", self)
        self.b_imp_p.clicked.connect(self._import_file)
        self.b_imp_zip = QPushButton("ZIP", self)
        self.b_imp_zip.clicked.connect(self._import_zip)
        for button in (self.b_imp_f, self.b_imp_p, self.b_imp_zip):
            button.hide()

        # The asset store's own folder tree - the ZIP's own organisation.
        g_row = QHBoxLayout()
        self.group_tree = QTreeWidget()
        self.group_tree.setHeaderHidden(True)
        self.group_tree.setMaximumHeight(190)
        self.group_tree.setUniformRowHeights(True)
        self.group_tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.group_tree.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.group_tree.setToolTip(
            "The folders your assets arrived in. A ZIP import keeps the "
            "archive's own layout, and nothing is ever re-sorted, re-filed or "
            "renamed - picking a folder only filters what is shown.")
        self.group_tree.currentItemChanged.connect(self._on_tree_pick)
        g_row.addWidget(self.group_tree, 1)
        # Reordering moves to the tree's right-click menu; hidden buttons keep
        # the enabled/disabled state used by that menu.
        self.b_group_up = QPushButton("▲", self)
        self.b_group_up.clicked.connect(self._group_up)
        self.b_group_down = QPushButton("▼", self)
        self.b_group_down.clicked.connect(self._group_down)
        self.b_group_up.hide()
        self.b_group_down.hide()
        self.group_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.group_tree.customContextMenuRequested.connect(self._tree_menu)
        self.group_tree.itemChanged.connect(self._on_tree_check)
        self.group_tree_box = QWidget()
        box_layout = QVBoxLayout(self.group_tree_box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.addLayout(g_row)
        layout.addWidget(self.group_tree_box)

        # collections: the picker only takes space once a collection exists;
        # creating/editing them is in the library menu.
        self.coll_combo = QComboBox()
        self.coll_combo.addItem("All assets")
        self.coll_combo.setMinimumWidth(70)
        self.coll_combo.currentIndexChanged.connect(self._on_collection_pick)
        self.coll_combo.setVisible(False)
        layout.addWidget(self.coll_combo)

        # thumbnail size: lives in the library menu; wide range plus presets
        self._settings = QSettings("ArenaMaps", SETTINGS_ID)
        saved_size = self._settings.value("library/thumb_size", 96)
        try:
            saved_size = int(saved_size)
        except (TypeError, ValueError):
            saved_size = 96
        self.sl_thumb = QSlider(Qt.Orientation.Horizontal)
        self.sl_thumb.setRange(THUMB_MIN, THUMB_MAX)
        self.sl_thumb.setValue(max(THUMB_MIN, min(THUMB_MAX, saved_size)))
        self.sl_thumb.valueChanged.connect(self._thumb_size)
        self._build_library_menu()

        self.list = AssetList()
        # Ticking thumbnails (or folders in the tree) picks them for the
        # generator; the panel owns the picked set.
        self.list.asset_model.picked_contains = lambda p: p in self._picked
        self.list.asset_model.pickToggled.connect(self._on_pick_toggled)
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
        self.list.set_thumbnail_size(self.sl_thumb.value())
        self.list.thumbnailZoomRequested.connect(self._zoom_thumbnails)

        # The store path/help/open actions moved into the library menu; the
        # label stays as a hidden holder for the elided path text.
        self.lbl_store = QLabel("Store: (not set)", self)
        self.lbl_store.hide()

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
            "• The Browse tree is the store's own folder structure - exactly "
            "the\n"
            "  layout the ZIP archive (or the folder you imported) had. "
            "Nothing is\n"
            "  re-sorted or re-filed, so an asset is always where its archive "
            "put it.\n"
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
        pruned = self._prune_demo_assets(project.asset_store)
        if current_root != target_root or not library._scan_complete or pruned:
            library.scan(project.asset_store)
        self._rebuild_collections()
        self._rebuild_groups()
        self.refresh()
        self._update_store_label()

    def _prune_demo_assets(self, store: str) -> int:
        """Remove leftover placeholder images from the app's own store."""
        if not store:
            return 0
        app_data_dir = getattr(self.window(), "_app_data_dir", "")
        default_store = default_asset_store_path(__file__, app_data_dir)
        if os.path.normcase(os.path.abspath(store)) != os.path.normcase(default_store):
            return 0
        return prune_demo_assets(store)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_store_label()

    def _new_tree_item(self, parent, label: str, token: str):
        item = QTreeWidgetItem([label])
        item.setToolTip(0, label)
        # Folders are pickable too: ticking one picks every asset inside it.
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        if parent is None:
            self.group_tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        item.setData(0, Qt.ItemDataRole.UserRole, token)
        self._tree_items[token] = item
        return item

    def _toggle_duplicates(self, show: bool):
        if self.library.set_show_duplicates(show):
            self._rebuild_groups()
            self.refresh()

    def _rebuild_groups(self):
        groups = self.library.groups(self.project.group_order if self.project else None)
        self._folder_groups = groups
        if self.project:
            self.project.group_order = list(groups)
        stats_key = (id(self.library), self.library._scan_revision)
        if stats_key != self._library_stats_key:
            self._folder_counts = {".": len(self.library.assets)}
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
        all_item.setToolTip(0, "Every asset in the store, in the order the "
                               "folders and files were imported.")

        # The only organisation is the one the assets arrived with: the folders
        # inside the ZIP (or the folder you imported), preserved exactly.
        folders_root = self._new_tree_item(
            None, f"Folders ({total:,})", "folder-root")
        folders_root.setToolTip(
            0, "The asset store's own folder structure - the same layout the "
               "ZIP archive (or the folder you imported) had. Nothing is "
               "re-sorted, re-filed or renamed.")
        loose = self._folder_counts.get(".", 0)
        root_item = self._new_tree_item(
            folders_root, f"Loose files in the store root ({loose:,})",
            "folder:.")
        # An empty folder is never shown; with no loose files the top-level
        # folders hang straight off "Folders".
        root_item.setHidden(loose == 0)
        folder_items = {".": folders_root if loose == 0 else root_item}
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
        root_item.setExpanded(self._folder_counts.get(".", 0) > 0)
        self.group_tree.setCurrentItem(all_item)
        self.group_tree.blockSignals(False)
        self._sync_tree_checks()
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
        self.coll_combo.setVisible(self.coll_combo.count() > 1)

    # ------------------------------------------------------------------
    def _asset_double_clicked(self, index):
        path = self.list.path_at(index)
        if path:
            self.assetActivated.emit(path)

    def _on_search(self, _text):
        # Debounce scans of a large asset list while retaining the current
        # folder or collection browsing context.
        self._search_timer.start()

    def _on_tree_pick(self, cur, _prev):
        if not cur:
            self._update_folder_reorder_buttons()
            return
        token = cur.data(0, Qt.ItemDataRole.UserRole) or "all"
        if token.startswith("folder:"):
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

    # -- picking assets for the generator (checkboxes) ----------------------
    def _paths_for_token(self, token: str) -> list[str]:
        """Every asset path a tree checkbox covers."""
        if token in ("all", "folder-root"):
            return [a.path for a in self.library.assets]
        if token.startswith("folder:"):
            folder = token.split(":", 1)[1]
            if folder == ".":
                return [a.path for a in self.library.assets
                        if a.folder.replace("\\", "/").strip("/") in ("", ".")]
            prefix = folder.rstrip("/") + "/"
            return [a.path for a in self.library.assets
                    if a.folder == folder or a.folder.startswith(prefix)]
        return []

    def _on_pick_toggled(self, path: str, checked: bool):
        """A thumbnail's checkbox was clicked."""
        if checked:
            self._picked.add(path)
        else:
            self._picked.discard(path)
        model = self.list.asset_model
        row = model._path_to_row.get(path)
        if row is not None:
            index = model.index(row, 0)
            model.dataChanged.emit(
                index, index, [int(Qt.ItemDataRole.CheckStateRole)])
        self._sync_tree_checks()
        self._update_pick_label()
        self.pickChanged.emit()

    def _on_tree_check(self, item, column: int):
        """A folder checkbox was clicked: pick/unpick everything inside it."""
        if column != 0 or self._syncing_tree:
            return
        token = item.data(0, Qt.ItemDataRole.UserRole) or ""
        if not token:
            return
        checked = item.checkState(0) != Qt.CheckState.Unchecked
        paths = self._paths_for_token(token)
        if checked:
            self._picked.update(paths)
        else:
            self._picked.difference_update(paths)
        self._sync_list_checks()
        self._sync_tree_checks()
        self._update_pick_label()
        self.pickChanged.emit()

    def _sync_list_checks(self):
        model = self.list.asset_model
        if model.rowCount():
            model.dataChanged.emit(
                model.index(0, 0), model.index(model.rowCount() - 1, 0),
                [int(Qt.ItemDataRole.CheckStateRole)])

    def _sync_tree_checks(self):
        """Reflect the picked set in every folder checkbox (some = partial)."""
        self._syncing_tree = True
        try:
            for token, item in self._tree_items.items():
                paths = self._paths_for_token(token)
                if not paths:
                    state = Qt.CheckState.Unchecked
                else:
                    have = sum(1 for p in paths if p in self._picked)
                    if have == 0:
                        state = Qt.CheckState.Unchecked
                    elif have == len(paths):
                        state = Qt.CheckState.Checked
                    else:
                        state = Qt.CheckState.PartiallyChecked
                if item.checkState(0) != state:
                    item.setCheckState(0, state)
        finally:
            self._syncing_tree = False

    def _update_pick_label(self):
        if not hasattr(self, "count_label"):
            return
        extra = (f" · {len(self._picked):,} picked for the generator"
                 if self._picked else "")
        self.count_label.setText(self._count_base + extra)

    def _set_count(self, text: str):
        """Replace the status line, remembering it as the new base."""
        self._count_base = text
        self._update_pick_label()

    def clear_picks(self):
        if not self._picked:
            return
        self._picked.clear()
        self._sync_list_checks()
        self._sync_tree_checks()
        self._update_pick_label()
        self.pickChanged.emit()

    def _remove_assets(self, paths, label: str):
        """Delete pool tiles after a confirmation. Map nodes that used them
        will afterwards show as missing images."""
        paths = [p for p in paths if p]
        if not paths or not self.project:
            return
        from PyQt6.QtWidgets import QMessageBox
        used = {p for p in paths}
        in_use = sum(1 for level in self.project.levels
                     for piece in level.pieces if piece.asset_path in used)
        text = (f"Remove {len(paths):,} tile(s) — {label} — from the asset "
                "pool? The files are deleted from the store.")
        if in_use:
            text += (f"\n\n{in_use} node(s) on the map currently use them and "
                     "will show as missing images afterwards.")
        answer = QMessageBox.question(self, "Remove tiles", text)
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self.library.remove_paths(paths)
        self._picked.difference_update(used)
        self._rebuild_groups()
        self.refresh()
        self.collectionsChanged.emit()
        self.count_label.setText(f"Removed {removed:,} tile(s) from the pool.")

    # ------------------------------------------------------------------
    def refresh(self):
        mode, val = self._view
        if mode == "collection":
            paths = set(self.project.collections.get(val, [])) if self.project else set()
            assets = [asset for asset in self.library.assets if asset.path in paths]
        elif mode == "folder":
            if val in ("", "."):
                # The store root itself: only the files that sit directly in it.
                assets = [asset for asset in self.library.assets
                          if asset.folder.replace("\\", "/").strip("/") in ("", ".")]
            else:
                prefix = val.rstrip("/") + "/"
                assets = [asset for asset in self.library.assets
                          if asset.folder == val or asset.folder.startswith(prefix)]
        else:
            assets = list(self.library.assets)

        self._refresh_function_filter()
        query = self.search.text().strip()
        function = self.function_filter.currentData() or ""
        if query or function:
            terms = query.casefold().split()
            match = self.library.matches
            assets = [asset for asset in assets if match(asset, terms, function)]

        # A model-backed view creates no QListWidgetItem per file, which keeps
        # both memory use and filter/refresh time bounded for large packs.
        self._visible_assets = assets
        self.list.set_assets(assets, self.library)

        scope = "All assets"
        if mode == "folder":
            scope = "Store root" if val in ("", ".") else val
        elif mode == "collection":
            scope = val
        count_text = f"{len(assets):,} asset(s) · {scope}"
        if query:
            count_text += f" · search: {query}"
        if function:
            count_text += f" · function: {function_label(function)}"
        hidden = len(self.library.hidden_duplicates)
        if hidden and self.library.show_duplicates:
            count_text += f" · duplicates shown ({hidden:,} tagged 'duplicate')"
        elif hidden:
            count_text += (f" · {hidden:,} verified duplicate(s) hidden "
                           "(same name and size) — Library menu → Show duplicate copies")
        if self.library.skipped_unreadable:
            count_text += (f" · {self.library.skipped_unreadable:,} unreadable "
                           "file(s) left out")
        self._count_base = count_text
        self._update_pick_label()
        self._schedule_visible_thumbnails()

    def _refresh_function_filter(self):
        """List the functions present in the library (only rebuilt when the set changes)."""
        counts = self.library.function_counts()
        sig = tuple(counts)
        if sig == self._function_sig:
            return
        self._function_sig = sig
        keep = self.function_filter.currentData() or ""
        self.function_filter.blockSignals(True)
        self.function_filter.clear()
        self.function_filter.addItem("Any function", "")
        for func, n in counts:
            self.function_filter.addItem(f"{function_label(func)} ({n:,})", func)
        i = self.function_filter.findData(keep)
        self.function_filter.setCurrentIndex(max(0, i))
        self.function_filter.setVisible(bool(counts))
        self.function_filter.blockSignals(False)

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
            if requested >= 6:
                break
        # then quietly warm the next screenful so scrolling finds them ready
        if visible_rows and requested < 6:
            last = max(visible_rows)
            for row in range(last + 1, min(model.rowCount(), last + 25)):
                index = model.index(row, 0)
                if model.has_requested_thumbnail(index):
                    continue
                if not model.request_thumbnail(index):
                    break
                requested += 1
                if requested >= 6:
                    break

    def _thumb_size(self, v):
        self.list.set_thumbnail_size(v)
        self._schedule_visible_thumbnails()
        self._settings.setValue("library/thumb_size", int(v))
        spin = getattr(self, "spin_thumb", None)
        if spin is not None and spin.value() != v:
            spin.blockSignals(True)
            spin.setValue(int(v))
            spin.blockSignals(False)

    def _zoom_thumbnails(self, direction: int):
        """Ctrl+wheel over the list: ~12% per notch."""
        value = self.sl_thumb.value()
        step = max(8, int(value * 0.12))
        self.sl_thumb.setValue(max(THUMB_MIN, min(THUMB_MAX, value + direction * step)))

    # -- library menu (☰) --------------------------------------------------
    def _build_library_menu(self):
        menu = QMenu(self)
        self.lib_menu = menu
        imp = menu.addMenu("Import")
        imp.addAction("Folder…", self._import_folder)
        imp.addAction("File…", self._import_file)
        imp.addAction("ZIP archive(s)…", self._import_zip)
        self._import_menu = imp
        coll = menu.addMenu("Collections")
        coll.addAction("New collection…", self._new_collection)
        coll.addAction("Add selected asset to the chosen collection",
                       self._add_to_collection)
        coll.addAction("Remove selected asset from the chosen collection",
                       self._remove_from_collection)
        menu.addSeparator()
        menu.addAction("Clear generator picks", self.clear_picks)
        menu.addSeparator()

        size_menu = menu.addMenu("Thumbnail size")
        self._size_actions = {}
        for label, value in THUMB_PRESETS:
            action = size_menu.addAction(f"{label}  ({value}px)")
            action.setCheckable(True)
            action.triggered.connect(
                lambda checked=False, v=value: self.sl_thumb.setValue(v))
            self._size_actions[value] = action
        size_menu.addSeparator()
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(10, 4, 10, 4)
        row.addWidget(QLabel("Custom"))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(THUMB_MIN, THUMB_MAX)
        slider.setMinimumWidth(140)
        self.spin_thumb = QSpinBox()
        self.spin_thumb.setRange(THUMB_MIN, THUMB_MAX)
        self.spin_thumb.setSuffix(" px")
        slider.valueChanged.connect(self.sl_thumb.setValue)
        self.spin_thumb.valueChanged.connect(self.sl_thumb.setValue)
        self.sl_thumb.valueChanged.connect(lambda v: slider.setValue(v)
                                           if slider.value() != v else None)
        row.addWidget(slider, 1)
        row.addWidget(self.spin_thumb)
        widget_action = QWidgetAction(size_menu)
        widget_action.setDefaultWidget(holder)
        size_menu.addAction(widget_action)
        size_menu.addAction("Tip: Ctrl + mouse wheel over the list also resizes").setEnabled(False)
        self._size_slider = slider
        slider.setValue(self.sl_thumb.value())
        self.spin_thumb.setValue(self.sl_thumb.value())

        self.act_dupes = menu.addAction("Show duplicate copies")
        self.act_dupes.setCheckable(True)
        self.act_dupes.setToolTip(
            "A picture with the same name and the same file size as one already "
            "shown is a duplicate and is folded away. Tick this to list those "
            "copies too (tagged 'duplicate') and check them.")
        self.act_dupes.toggled.connect(self._toggle_duplicates)
        self.act_tree = menu.addAction("Show folder tree")
        self.act_tree.setCheckable(True)
        show_tree = self._settings.value("library/show_tree", True, type=bool)
        self.act_tree.setChecked(show_tree)
        self.group_tree_box.setVisible(show_tree)
        self.act_tree.toggled.connect(self._set_tree_visible)
        menu.addSeparator()
        self.act_store = menu.addAction("Store: (not set)")
        self.act_store.setEnabled(False)
        menu.addAction("Open asset folder", self._open_store)
        menu.addAction("Where are my assets?", self._show_store_help)
        menu.aboutToShow.connect(self._sync_library_menu)
        self.btn_menu.setMenu(menu)

    def _sync_library_menu(self):
        importing = not self.b_imp_zip.isEnabled()
        self._import_menu.setEnabled(not importing)
        current = self.sl_thumb.value()
        for value, action in self._size_actions.items():
            action.setChecked(value == current)
        path = (self.project.asset_store if self.project
                and self.project.asset_store else "")
        shown = self.lbl_store.fontMetrics().elidedText(
            "Store: " + (path or "(created on first import)"),
            Qt.TextElideMode.ElideMiddle, 340)
        self.act_store.setText(shown)

    def _set_tree_visible(self, on: bool):
        self.group_tree_box.setVisible(bool(on))
        self._settings.setValue("library/show_tree", bool(on))

    def _tree_menu(self, pos):
        item = self.group_tree.itemAt(pos)
        if item is not None:
            self.group_tree.setCurrentItem(item)
        menu = QMenu(self)
        up = menu.addAction("Move earlier", self._group_up)
        up.setEnabled(self.b_group_up.isEnabled())
        down = menu.addAction("Move later", self._group_down)
        down.setEnabled(self.b_group_down.isEnabled())
        menu.addSeparator()
        menu.addAction("Expand all", self.group_tree.expandAll)
        menu.addAction("Collapse all", self.group_tree.collapseAll)
        token = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else ""
        if token.startswith("folder:"):
            folder = token.split(":", 1)[1]
            label = "the store root" if folder == "." else folder
            remove = menu.addAction(f"Remove folder “{label}” from the pool…")
            remove.setToolTip("Deletes every file in this folder from the store.")
            remove.triggered.connect(
                lambda checked=False, t=token, lbl=label:
                self._remove_assets(self._paths_for_token(t), lbl))
        menu.exec(self.group_tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    def _import_start_dir(self) -> str:
        """Import dialogs open where the last import came from (not the folder
        the program happened to start in)."""
        window = self.window()
        if hasattr(window, "_last_dir"):
            return window._last_dir("files/last_import_dir")
        return os.getcwd()

    def _remember_import_dir(self, path: str):
        window = self.window()
        if path and hasattr(window, "_remember_dir"):
            window._remember_dir(path, "files/last_import_dir")

    def _import_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Import asset folder",
                                            self._import_start_dir())
        if d:
            self._remember_import_dir(os.path.dirname(os.path.abspath(d)))
        if d and self.project:
            self._ensure_store()
            self.library.root = self.project.asset_store
            n = self.library.import_folder(d, preserve_root=True)
            self._after_import(n)

    def _import_file(self):
        fns, _ = QFileDialog.getOpenFileNames(
            self, "Import images", self._import_start_dir(),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff)")
        if fns:
            self._remember_import_dir(fns[0])
        if fns and self.project:
            self._ensure_store()
            self.library.root = self.project.asset_store
            added = sum(1 for fn in fns if self.library.import_file(fn))
            self._after_import(added)

    def _import_zip(self):
        if not self.project or (self._zip_worker and self._zip_worker.isRunning()):
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import asset ZIP archives", self._import_start_dir(),
            "ZIP archives (*.zip)")
        if paths:
            self._remember_import_dir(paths[0])
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
            else:
                # Safe fallback for an unexpected worker-side indexing error.
                self.library.scan(target_root)
            if self._prune_demo_assets(target_root):
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

    def drain_background_work(self):
        """Finish ZIP import and thumbnail-decode work before teardown.

        See ``AssetListModel.drain_background_work`` / ``Canvas.drain_background_
        work`` for why this must happen before the widget tree is torn down
        rather than inside a QThreadPool's own (nested, GIL-holding) destructor.
        """
        self.wait_for_zip_import()
        self.list.drain_background_work()

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
        self.function_filter.blockSignals(True)
        self.function_filter.setCurrentIndex(0)
        self.function_filter.blockSignals(False)
        self.coll_combo.blockSignals(True)
        self.coll_combo.setCurrentIndex(0)
        self.coll_combo.blockSignals(False)
        all_item = self._tree_items.get("all")
        if all_item:
            self.group_tree.blockSignals(True)
            self.group_tree.setCurrentItem(all_item)
            self.group_tree.blockSignals(False)

    def library_changed(self):
        """Pick up images added to the library from elsewhere (a floor texture
        uploaded for a backdrop, say) without losing the folder, collection or
        search the user is browsing."""
        view = self._view
        current = self.group_tree.currentItem()
        token = current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None
        expanded = {tok: item.isExpanded() for tok, item in self._tree_items.items()}
        self._rebuild_groups()              # (resets the tree to "All assets")
        self.group_tree.blockSignals(True)
        for tok, item in self._tree_items.items():
            if tok in expanded:
                item.setExpanded(expanded[tok])
        if view[0] == "collection":
            self.group_tree.setCurrentItem(None)
            self._view = view
        elif token in self._tree_items:
            self.group_tree.setCurrentItem(self._tree_items[token])
            self._view = view
        self.group_tree.blockSignals(False)
        self._update_folder_reorder_buttons()
        self.refresh()

    def _after_import(self, n):
        if n:
            if self.project and self._prune_demo_assets(self.project.asset_store):
                self.library.scan(self.project.asset_store)
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
        selected = [index.data(Qt.ItemDataRole.UserRole)
                    for index in self.list.selectionModel().selectedIndexes()]
        if path not in selected:
            selected = [path]
        menu = QMenu(self)
        if asset.folder and asset.folder.replace("\\", "/").strip("/") not in ("", "."):
            header = menu.addAction(asset.folder)
            header.setEnabled(False)
        act = menu.addAction("View larger…")
        act.triggered.connect(lambda: self._view_large(asset))
        act2 = menu.addAction("Add to canvas")
        act2.triggered.connect(lambda: self.assetActivated.emit(asset.path))
        info_fn = getattr(self, "canvas_swap_info", None)
        swap_count, swap_first = info_fn() if callable(info_fn) else (0, "")
        swap = menu.addAction(
            f"Swap the {swap_count} selected node(s) on the map to this" if swap_count
            else "Swap the selected node(s) on the map to this")
        swap.setToolTip("They keep their place, rotation, flips, layer, tint and cut-outs.")
        swap.setEnabled(swap_count > 0)
        swap.triggered.connect(lambda: self.swapRequested.emit(asset.path))
        every = menu.addAction("Swap every copy of the selected node's image to this")
        every.setToolTip("Every node on every level that uses the same picture as the "
                         "selected node.")
        every.setEnabled(bool(swap_first) and swap_first != asset.path)
        every.triggered.connect(lambda: self.swapAllRequested.emit(asset.path))
        backdrop = menu.addMenu("Use as backdrop (floor texture)")
        backdrop.addAction("This level", lambda: self.backdropRequested.emit(asset.path, False))
        backdrop.addAction("Every level", lambda: self.backdropRequested.emit(asset.path, True))
        pin_menu = menu.addMenu("Pin to stamp key")
        labels = self.stamp_labels() if callable(self.stamp_labels) else [""] * 9
        for index in range(9):
            label = labels[index] if index < len(labels) else ""
            text = f"{index + 1}  —  " + (f"replace “{label}”" if label else "empty")
            pin_menu.addAction(text, lambda n=index, ap=asset.path:
                               self.pinStampRequested.emit(n, ap))
        if self.project and self.project.collections:
            coll = menu.addMenu("Add to collection")
            for name in self.project.collections:
                coll.addAction(name, lambda n=name, ps=selected:
                               [self._add_path_to_collection(n, p) for p in ps])
        menu.addSeparator()
        count = len(selected)
        generate = menu.addAction(
            f"Generate a map from these {count} assets…" if count > 1
            else "Generate a map from this asset…")
        generate.setToolTip(
            "Opens the map generator with exactly these assets selected. "
            "Nothing else from the library is used.")
        generate.triggered.connect(
            lambda checked=False, ps=selected: self.generateRequested.emit(list(ps)))
        menu.addSeparator()
        if all(p in self._picked for p in selected):
            unpick = menu.addAction(
                "Untick for the generator" if count == 1
                else f"Untick these {count} for the generator")
            unpick.triggered.connect(
                lambda checked=False, ps=selected:
                [self._on_pick_toggled(p, False) for p in ps])
        else:
            pick = menu.addAction(
                "Tick for the generator" if count == 1
                else f"Tick these {count} for the generator")
            pick.setToolTip(
                "Ticked assets are what the map generator builds from.")
            pick.triggered.connect(
                lambda checked=False, ps=selected:
                [self._on_pick_toggled(p, True) for p in ps])
        remove = menu.addAction(
            "Remove this tile from the pool…" if count == 1
            else f"Remove these {count} tiles from the pool…")
        remove.setToolTip("Deletes the file(s) from the asset store.")
        remove.triggered.connect(
            lambda checked=False, ps=selected:
            self._remove_assets(list(ps), "selected tiles"))
        menu.exec(self.list.mapToGlobal(pos))

    # -- selection (used by the generator) -----------------------------------
    def selected_paths(self) -> list[str]:
        """What the generator will use: the ticked assets (individual pictures
        or whole folders), in library order. With nothing ticked yet, the
        current highlight in the list is used, so selecting-and-generating
        still works without the checkboxes."""
        if self._picked:
            return [asset.path for asset in self.library.assets
                    if asset.path in self._picked]
        model = self.list.asset_model
        chosen = {index.data(Qt.ItemDataRole.UserRole)
                  for index in self.list.selectionModel().selectedIndexes()}
        return [asset.path for asset in model.assets if asset.path in chosen]

    def selected_assets(self) -> list:
        """The selected Asset objects, in library order."""
        chosen = set(self.selected_paths())
        return [asset for asset in self.library.assets if asset.path in chosen]

    def visible_assets(self) -> list:
        """Every asset the list is showing right now (folder + search filter)."""
        shown = getattr(self, "_visible_assets", None)
        return list(self.library.assets if shown is None else shown)       # an empty filter result is empty

    def select_paths(self, paths) -> int:
        """Pick exactly these assets — as if their checkboxes were ticked —
        and report how many of them exist in the library."""
        wanted = set(paths)
        found = sum(1 for asset in self.library.assets
                    if asset.path in wanted)
        self._picked = {asset.path for asset in self.library.assets
                        if asset.path in wanted}
        self._sync_list_checks()
        self._sync_tree_checks()
        self._update_pick_label()
        self.pickChanged.emit()
        return found

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

    def _add_path_to_collection(self, name: str, path: str):
        if not self.project:
            return
        members = self.project.collections.setdefault(name, [])
        if path not in members:
            members.append(path)
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
