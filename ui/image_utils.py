"""Lightweight image previews for large asset libraries."""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache

from PyQt6.QtCore import QSize, QStandardPaths, Qt
from PyQt6.QtGui import QImage, QImageReader, QPixmap

# Decoding a multi-thousand-pixel PNG takes seconds, so small previews are
# generated once and kept on disk (as 256 px PNGs) for every later session.
DISK_THUMB_MAX = 256
_THUMB_DIR: str | None = None


def _thumb_dir() -> str:
    global _THUMB_DIR
    if _THUMB_DIR is None:
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation)
        _THUMB_DIR = os.path.join(base or os.path.join(
            os.path.expanduser("~"), ".map-studio-cache"), "thumbs")
    return _THUMB_DIR


def _disk_cached_base(path: str, modified_ns: int) -> QImage:
    """256 px preview of ``path``, from the disk cache when present."""
    try:
        size = os.stat(path).st_size
    except OSError:
        size = 0
    key = hashlib.sha1(
        f"{os.path.abspath(path)}|{modified_ns}|{size}".encode("utf-8", "ignore")
    ).hexdigest()
    cache_file = os.path.join(_thumb_dir(), key[:2], key + ".png")
    if os.path.isfile(cache_file):
        image = QImage(cache_file)
        if not image.isNull():
            return image
    image = _read_scaled_image(path, DISK_THUMB_MAX)
    if not image.isNull():
        try:
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            image.save(cache_file, "PNG")
        except OSError:
            pass
    return image


@lru_cache(maxsize=256)
def _cached_small_image(path: str, max_dimension: int,
                        modified_ns: int) -> QImage:
    base = _disk_cached_base(path, modified_ns)
    if base.isNull() or max(base.width(), base.height()) <= max_dimension:
        return base
    return base.scaled(max_dimension, max_dimension,
                       Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)


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
    image = _cached_small_image(path, max_dimension, modified_ns)
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
