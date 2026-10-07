"""Lightweight image previews for large asset libraries."""
from __future__ import annotations

import os
from functools import lru_cache

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage, QImageReader, QPixmap


@lru_cache(maxsize=256)
def _cached_small_image(path: str, max_dimension: int,
                        modified_ns: int) -> QImage:
    del modified_ns  # participates in the cache key so changed files refresh
    return _read_scaled_image(path, max_dimension)


def _read_scaled_image(path: str, max_dimension: int) -> QImage:
    """Decode a bounded preview as QImage (safe to do on a worker thread)."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    source = reader.size()
    if not source.isValid() or source.width() <= 0 or source.height() <= 0:
        # Avoid accidentally decoding a multi-megapixel image at full size in
        # a scrolling library. The dedicated large-preview dialog can retry
        # with a larger bound, but still never needs the original resolution.
        return QImage()
    scale = min(1.0, max_dimension / max(source.width(), source.height()))
    width = max(1, int(round(source.width() * scale)))
    height = max(1, int(round(source.height() * scale)))
    reader.setScaledSize(QSize(width, height))
    return reader.read()


def load_scaled_image(path: str, max_dimension: int = 110) -> QImage:
    """Load a scaled image without creating a GUI-thread-only QPixmap.

    Asset-list thumbnails use this in a background worker so decoding a large
    PNG cannot freeze the editor while the user scrolls the library.
    """
    max_dimension = max(1, int(max_dimension))
    try:
        modified_ns = os.stat(path).st_mtime_ns
    except OSError:
        return QImage()
    if max_dimension <= 256:
        return _cached_small_image(path, max_dimension, modified_ns)
    return _read_scaled_image(path, max_dimension)


def _read_scaled(path: str, max_dimension: int) -> QPixmap:
    image = _read_scaled_image(path, max_dimension)
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


@lru_cache(maxsize=256)
def _cached_small_preview(path: str, max_dimension: int,
                          modified_ns: int) -> QPixmap:
    del modified_ns  # participates in the cache key so changed files refresh
    return _read_scaled(path, max_dimension)


def load_scaled_pixmap(path: str, max_dimension: int = 110) -> QPixmap:
    """Return a cached small thumbnail, or a bounded one-off larger preview."""
    max_dimension = max(1, int(max_dimension))
    try:
        modified_ns = os.stat(path).st_mtime_ns
    except OSError:
        return QPixmap()
    if max_dimension <= 256:
        return _cached_small_preview(path, max_dimension, modified_ns)
    return _read_scaled(path, max_dimension)
