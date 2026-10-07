"""Lightweight image previews for large asset libraries."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from functools import lru_cache

from PyQt6.QtCore import QSize, QStandardPaths, Qt
from PyQt6.QtGui import QImage, QImageReader, QPixmap

from core.imaging import decode_guard

# Decoding a multi-thousand-pixel PNG takes seconds, so small previews are
# generated once and kept on disk (as 256 px PNGs) for every later session.
DISK_THUMB_MAX = 384
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
        f"{os.path.abspath(path)}|{modified_ns}|{size}|{DISK_THUMB_MAX}".encode(
            "utf-8", "ignore")
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
    with decode_guard(source.width(), source.height()):
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
    if max_dimension <= DISK_THUMB_MAX:
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
    if max_dimension <= DISK_THUMB_MAX:
        return _cached_small_preview(path, max_dimension, modified_ns)
    return _read_scaled(path, max_dimension)


# ---------------------------------------------------------------------------
# Visible-pixel bounds: where the artwork actually is inside its PNG, so a node
# can be tightened to it instead of keeping a transparent margin that makes
# snapping look off. Bounds are stored for several opacity cut-offs so the
# threshold can be changed without decoding the image again.
# ---------------------------------------------------------------------------
BOUNDS_ANALYSIS_SIZE = 1024
TRIM_THRESHOLDS = (1, 8, 16, 32, 64, 128, 200)   # alpha (0-255) cut-offs
DEFAULT_TRIM_THRESHOLD = 16
_BOUNDS_LOCK = threading.Lock()
_BOUNDS: dict | None = None


def nearest_threshold(threshold: int) -> int:
    return min(TRIM_THRESHOLDS, key=lambda value: abs(value - int(threshold)))


def _bounds_file() -> str:
    return os.path.join(os.path.dirname(_thumb_dir()), "visible_bounds_v2.json")


def _bounds_key(path: str) -> str | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return hashlib.sha1(
        f"{os.path.abspath(path)}|{stat.st_mtime_ns}|{stat.st_size}".encode(
            "utf-8", "ignore")).hexdigest()


def _load_bounds() -> dict:
    global _BOUNDS
    if _BOUNDS is None:
        try:
            with open(_bounds_file(), "r", encoding="utf-8") as handle:
                _BOUNDS = json.load(handle)
        except (OSError, ValueError):
            _BOUNDS = {}
    return _BOUNDS


def _save_bounds():
    try:
        os.makedirs(os.path.dirname(_bounds_file()), exist_ok=True)
        with open(_bounds_file(), "w", encoding="utf-8") as handle:
            json.dump(_BOUNDS, handle)
    except OSError:
        pass


def all_bounds_for_image(image: QImage) -> dict:
    """{threshold: (left, top, right, bottom) fractions or None} for every cut-off."""
    from PIL import Image

    out: dict = {}
    if image.isNull():
        return {t: None for t in TRIM_THRESHOLDS}
    alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
    width, height = alpha.width(), alpha.height()
    stride = alpha.bytesPerLine()
    raw = bytes(alpha.constBits().asarray(stride * height))
    mask = Image.frombuffer("L", (width, height), raw, "raw", "L", stride, 1)
    for threshold in TRIM_THRESHOLDS:
        box = mask.point(lambda a, t=threshold: 255 if a >= t else 0).getbbox()
        out[threshold] = (None if box is None else
                          (box[0] / width, box[1] / height,
                           box[2] / width, box[3] / height))
    return out


def visible_bounds_for_image(image: QImage, threshold: int = DEFAULT_TRIM_THRESHOLD):
    """(left, top, right, bottom) 0..1 fractions of the pixels at or above the
    opacity cut-off, or None when nothing is that visible."""
    return all_bounds_for_image(image).get(nearest_threshold(threshold))


def peek_visible_bounds(path: str, threshold: int = DEFAULT_TRIM_THRESHOLD):
    """Cached bounds for ``path`` or None if they have not been computed yet
    (also None when nothing is visible at that cut-off)."""
    key = _bounds_key(path)
    if key is None:
        return None
    with _BOUNDS_LOCK:
        entry = _load_bounds().get(key)
    if not isinstance(entry, dict):
        return None
    value = entry.get(str(nearest_threshold(threshold)))
    return tuple(value) if value else None


def has_cached_bounds(path: str) -> bool:
    key = _bounds_key(path)
    if key is None:
        return False
    with _BOUNDS_LOCK:
        return isinstance(_load_bounds().get(key), dict)


def visible_bounds_for_file(path: str, threshold: int = DEFAULT_TRIM_THRESHOLD):
    """Visible-pixel bounds of an image file (cached on disk). Decoding a large
    PNG takes seconds, so call this off the GUI thread."""
    if not has_cached_bounds(path):
        key = _bounds_key(path)
        if key is None:
            return None
        table = all_bounds_for_image(_read_scaled_image(path, BOUNDS_ANALYSIS_SIZE))
        with _BOUNDS_LOCK:
            _load_bounds()[key] = {
                str(t): (list(v) if v else None) for t, v in table.items()}
            _save_bounds()
    return peek_visible_bounds(path, threshold)


def bounds_table_for_file(path: str) -> dict:
    """{threshold: bounds or None} for an image file (computes + caches once)."""
    visible_bounds_for_file(path)
    return {t: peek_visible_bounds(path, t) for t in TRIM_THRESHOLDS}
