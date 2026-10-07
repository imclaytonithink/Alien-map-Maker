"""Rolling backups of a saved map, kept by auto-save (no Qt imports).

Right before an auto-save overwrites a map file, the version currently on
disk is copied into one of a few numbered slots, replacing the oldest slot.
The slots therefore always hold the last few versions of the map, even after
a mistake was auto-saved and the app was closed (which clears undo history).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time

BACKUP_SLOTS = 4
SOURCE_NOTE = "source.txt"


def backup_folder(root: str, map_path: str) -> str:
    """Backup folder for one map: its name plus a hash of its full path, so two
    maps with the same file name in different folders never share backups."""
    absolute = os.path.normcase(os.path.abspath(map_path))
    digest = hashlib.sha1(absolute.encode("utf-8", "surrogatepass")).hexdigest()[:10]
    stem = os.path.splitext(os.path.basename(map_path))[0]
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip().rstrip(". ")[:40] or "map"
    return os.path.join(root, f"{stem}-{digest}")


def slot_paths(folder: str, slots: int = BACKUP_SLOTS) -> list[str]:
    return [os.path.join(folder, f"backup-{index}.bmap")
            for index in range(1, max(1, int(slots)) + 1)]


def list_backups(folder: str, slots: int = BACKUP_SLOTS) -> list[dict]:
    """Existing backups, newest first: [{path, slot, mtime, size}]."""
    found = []
    for index, path in enumerate(slot_paths(folder, slots), 1):
        try:
            info = os.stat(path)
        except OSError:
            continue
        found.append({"path": path, "slot": index, "mtime": info.st_mtime,
                      "size": info.st_size})
    found.sort(key=lambda item: (item["mtime"], item["slot"]), reverse=True)
    return found


def _same_bytes(a: str, b: str) -> bool:
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        digests = []
        for path in (a, b):
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digests.append(digest.digest())
        return digests[0] == digests[1]
    except OSError:
        return False


def rotate_backup(map_path: str, folder: str, slots: int = BACKUP_SLOTS,
                  now: float | None = None) -> str | None:
    """Copy the map as it is on disk into the first empty slot, or over the
    oldest one. Returns the backup written, or None when there was nothing to
    keep (no file yet, or it is identical to the newest backup)."""
    if not map_path or not os.path.isfile(map_path):
        return None
    os.makedirs(folder, exist_ok=True)
    existing = list_backups(folder, slots)
    if existing and _same_bytes(existing[0]["path"], map_path):
        return None
    taken = {item["path"] for item in existing}
    empty = [path for path in slot_paths(folder, slots) if path not in taken]
    if empty:
        target = empty[0]
    else:
        target = min(existing, key=lambda item: (item["mtime"], item["slot"]))["path"]
    temp = target + ".tmp"
    try:
        shutil.copyfile(map_path, temp)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass
    stamp = time.time() if now is None else float(now)
    os.utime(target, (stamp, stamp))
    try:
        with open(os.path.join(folder, SOURCE_NOTE), "w", encoding="utf-8") as note:
            note.write(os.path.abspath(map_path) + "\n")
    except OSError:
        pass
    return target
