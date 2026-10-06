"""Lightweight image previews for large asset libraries."""
from __future__ import annotations

import os
from functools import lru_cache

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImageReader, QPixmap


@lru_cache(maxsize=256)
def _cached_small_preview(path: str, max_dimension: int, modified_ns: int) -> QPixmap:
    del modified_ns  # participates in the cache key so changed files refresh
    return _read_scaled(path, max_dimension)


def _read_scaled(path: str, max_dimension: int) -> QPixmap:
    """Decode an image directly to a bounded preview instead of a full QPixmap."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    source = reader.size()
    if not source.isValid() or source.width() <= 0 or source.height() <= 0:
        # Avoid accidentally decoding a multi-megapixel image at full size in
        # a scrolling library. The dedicated large-preview dialog can retry
        # with a larger bound, but still never needs the original resolution.
        return QPixmap()
    scale = min(1.0, max_dimension / max(source.width(), source.height()))
    width = max(1, int(round(source.width() * scale)))
    height = max(1, int(round(source.height() * scale)))
    reader.setScaledSize(QSize(width, height))
    image = reader.read()
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


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
