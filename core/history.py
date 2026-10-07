"""Snapshot-based undo/redo history.

Snapshots are stored compressed, and large embedded images are kept once in a
shared blob table instead of being copied into every snapshot, so deep history
stays cheap on big maps. Rapid repeats of the same continuous edit (slider
drags, spinbox scrubbing, held arrow keys) can be coalesced into one step.
"""
from __future__ import annotations

import hashlib
import json
import time
import zlib
from typing import Optional

from core.project import Project

BLOB_PREFIX = "@blob:"
BLOB_MIN_LEN = 256


class History:
    COALESCE_WINDOW = 1.0   # seconds between edits that still count as one step

    def __init__(self, limit: int = 80, clock=time.monotonic):
        self.limit = limit
        self._clock = clock
        # entries are (label, compressed_snapshot, blob_keys)
        self._undos: list[tuple[str, bytes, frozenset]] = []
        self._redos: list[tuple[str, bytes, frozenset]] = []
        self._blobs: dict[str, str] = {}
        self._last_push = 0.0
        self._last_label: Optional[str] = None

    # -- label-only views, used by the UI -----------------------------------
    @property
    def undos(self) -> list[tuple[str, bytes]]:
        return [(label, data) for label, data, _ in self._undos]

    @property
    def redos(self) -> list[tuple[str, bytes]]:
        return [(label, data) for label, data, _ in self._redos]

    # -- packing -------------------------------------------------------------
    def _pack(self, project: Project) -> tuple[bytes, frozenset]:
        state = project.to_dict()
        keys = set()
        for level in state.get("levels", []):
            for piece in level.get("pieces", []):
                blob = piece.get("embedded") or ""
                if len(blob) >= BLOB_MIN_LEN:
                    key = hashlib.sha1(blob.encode("ascii", "ignore")).hexdigest()
                    self._blobs.setdefault(key, blob)
                    piece["embedded"] = BLOB_PREFIX + key
                    keys.add(key)
        data = zlib.compress(json.dumps(state).encode("utf-8"), 1)
        return data, frozenset(keys)

    def _unpack(self, data: bytes) -> Project:
        state = json.loads(zlib.decompress(data).decode("utf-8"))
        for level in state.get("levels", []):
            for piece in level.get("pieces", []):
                blob = piece.get("embedded") or ""
                if blob.startswith(BLOB_PREFIX):
                    piece["embedded"] = self._blobs[blob[len(BLOB_PREFIX):]]
        return Project.from_dict(state)

    def _prune_blobs(self):
        live = set()
        for _, _, keys in self._undos:
            live |= keys
        for _, _, keys in self._redos:
            live |= keys
        for key in [k for k in self._blobs if k not in live]:
            del self._blobs[key]

    # -- public API ----------------------------------------------------------
    def push(self, project: Project, label: str, coalesce: bool = False) -> bool:
        """Record the state *before* an edit. Returns False when the edit was
        merged into the previous step instead of creating a new one."""
        now = self._clock()
        merge = (coalesce and self._undos and not self._redos
                 and label == self._last_label
                 and now - self._last_push <= self.COALESCE_WINDOW)
        self._last_push = now
        self._last_label = label if coalesce else None
        if merge:
            return False
        data, keys = self._pack(project)
        self._undos.append((label, data, keys))
        if len(self._undos) > self.limit:
            self._undos.pop(0)
        self._redos.clear()
        self._prune_blobs()
        return True

    def can_undo(self) -> bool:
        return bool(self._undos)

    def can_redo(self) -> bool:
        return bool(self._redos)

    def undo(self, project: Project) -> Optional[str]:
        if not self._undos:
            return None
        label, data, _ = self._undos.pop()
        current, keys = self._pack(project)
        self._redos.append((label, current, keys))
        project.restore_from(self._unpack(data))
        self._last_label = None
        self._prune_blobs()
        return label

    def redo(self, project: Project) -> Optional[str]:
        if not self._redos:
            return None
        label, data, _ = self._redos.pop()
        current, keys = self._pack(project)
        self._undos.append((label, current, keys))
        project.restore_from(self._unpack(data))
        self._last_label = None
        self._prune_blobs()
        return label
