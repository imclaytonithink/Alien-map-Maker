"""Start-up warm-up: do the slow one-time work in the background.

Shrinking the 7,000-pixel tile PNGs and reading a big library for the first
time is slow, but it only has to happen once. This runs it on a worker thread
and shows a loading screen (with a "keep going in the background" button) so
the window never freezes. Later launches find everything cached and skip it.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar,
                             QPushButton, QVBoxLayout, QWidget)

SLOW_TILE_THRESHOLD = 8        # show the loading screen only for real work


class WarmupWorker(QThread):
    planned = pyqtSignal(int)              # tile thumbnails still to build
    progress = pyqtSignal(int, int, str)   # done, total, what
    ready = pyqtSignal()

    def __init__(self, store: str, parent=None):
        super().__init__(parent)
        self.store = store
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            self._scan_sizes()
            self._tile_thumbnails()
        except Exception:                  # warm-up is an optimisation only
            pass
        self.ready.emit()

    # -- library: read every image header once so the next scan is instant ---
    def _scan_sizes(self):
        from core import asset_manager as am
        if not self.store or not os.path.isdir(self.store):
            return
        for dirpath, dirs, files in os.walk(self.store):
            dirs[:] = [d for d in dirs if not d.startswith(".sceneboard-import-")]
            for fn in files:
                if self._stop:
                    return
                if fn.lower().endswith(am.SUPPORTED_EXTS):
                    am._cached_dimensions(os.path.join(dirpath, fn))
        am.save_dimension_cache()

    # -- geomorph tiles: pre-build every preview thumbnail ------------------
    def _tile_thumbnails(self):
        from ui.geomorph_dialog import find_tiles_dir
        tiles_dir = find_tiles_dir(self.store)
        if not tiles_dir:
            self.planned.emit(0)
            return
        from geomorph.registry import Registry
        from geomorph.render import TileImages
        reg = Registry.load(tiles_dir=tiles_dir)
        images = TileImages(tiles_dir)
        from pathlib import Path
        root = Path(tiles_dir)
        todo = [t for t in reg.tiles.values()
                if (root / t.image).exists() and not images.is_cached(t)]
        self.planned.emit(len(todo))
        total = len(todo)
        step = 16
        for i in range(0, total, step):
            if self._stop:
                return
            images.prefetch(todo[i:i + step], workers=4)
            self.progress.emit(min(total, i + step), total, "Preparing tile previews")


class WarmupOverlay(QFrame):
    """Loading screen laid over the main window until the warm-up is ready."""
    skipped = pyqtSignal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("warmupOverlay")
        self.setStyleSheet("#warmupOverlay { background: rgba(8, 12, 14, 235); }")
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        card = QVBoxLayout()
        self.title = QLabel("Getting things ready")
        self.title.setStyleSheet("font-size: 22px; font-weight: bold;")
        self.detail = QLabel("One-time setup: building fast previews of the map tiles.\n"
                             "This only happens the first time.")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bar = QProgressBar()
        self.bar.setFixedWidth(420)
        self.bar.setTextVisible(True)
        self.btn = QPushButton("Continue — finish in the background")
        self.btn.clicked.connect(self.skipped.emit)
        for w in (self.title, self.detail):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.addWidget(self.title)
        card.addWidget(self.detail)
        card.addSpacing(8)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.bar)
        row.addStretch(1)
        card.addLayout(row)
        card.addSpacing(8)
        row2 = QHBoxLayout()
        row2.addStretch(1)
        row2.addWidget(self.btn)
        row2.addStretch(1)
        card.addLayout(row2)
        lay.addLayout(card)
        lay.addStretch(1)
        self.hide()

    def fit(self):
        p = self.parentWidget()
        if p is not None:
            self.setGeometry(p.rect())

    def set_progress(self, done: int, total: int, text: str):
        self.bar.setMaximum(max(1, total))
        self.bar.setValue(done)
        self.bar.setFormat(f"{text}  {done}/{total}")


class Warmup:
    """Owns the worker and overlay for one window."""

    def __init__(self, window, status):
        self.window = window
        self.status = status
        self.worker: WarmupWorker | None = None
        self.overlay = WarmupOverlay(window)
        self.overlay.skipped.connect(self._skip)
        self._background = False

    def running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def start(self, store: str):
        if self.running() or not store:
            return
        self._background = False
        self.worker = WarmupWorker(store)
        self.worker.planned.connect(self._planned)
        self.worker.progress.connect(self._progress)
        self.worker.ready.connect(self._ready)
        self.worker.start()

    def _planned(self, n: int):
        if n >= SLOW_TILE_THRESHOLD and not self._background:
            self.overlay.fit()
            self.overlay.set_progress(0, n, "Preparing tile previews")
            self.overlay.show()
            self.overlay.raise_()

    def _progress(self, done: int, total: int, text: str):
        self.overlay.set_progress(done, total, text)
        if self._background:
            self.status(f"{text}… {done}/{total}", 0)

    def _skip(self):
        self._background = True
        self.overlay.hide()

    def _ready(self):
        self.overlay.hide()
        if self._background:
            self.status("Tile previews ready.", 6000)
        self._background = False

    def drain(self):
        """Stop and join the worker before the window is torn down."""
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(5000)
