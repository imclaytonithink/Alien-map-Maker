"""Image-decoding limits shared by the canvas, exporter and library previews."""
from __future__ import annotations

import threading
from contextlib import contextmanager

from PyQt6.QtGui import QImageReader

# Qt rejects any image whose decoded size exceeds 256 MB. High-resolution
# deck-plan tiles (e.g. 200x100 ft at 72 px/ft = 14398 x 7199) are ~415 MB, so
# without raising the limit they silently fail to load, preview, draw or export.
ALLOCATION_LIMIT_MB = 4096
# Decoding one of these needs a transient buffer of the same size, so only one
# very large image is decoded at a time across all threads.
BIG_DECODE_BYTES = 120 * 1024 * 1024
_BIG_DECODE_LOCK = threading.Lock()


def raise_allocation_limit():
    try:
        if QImageReader.allocationLimit() < ALLOCATION_LIMIT_MB:
            QImageReader.setAllocationLimit(ALLOCATION_LIMIT_MB)
    except AttributeError:      # very old Qt without the limit API
        pass


@contextmanager
def decode_guard(width: int, height: int):
    """Serialize decodes of very large images to keep peak memory bounded."""
    if int(width) * int(height) * 4 >= BIG_DECODE_BYTES:
        with _BIG_DECODE_LOCK:
            yield
    else:
        yield


raise_allocation_limit()
