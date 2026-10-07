"""Files the app keeps on the user's behalf (no Qt imports).

The recent-maps list, map thumbnails and backups live in the per-user app-data
folder. Nothing is written next to the program (a one-file EXE runs from a
temporary folder that is deleted on exit) or next to the user's maps (where a
thumbnail named after the map could overwrite an exported PNG of the same
name).
"""
from __future__ import annotations

import hashlib
import json
import os
import re

RECENT_LIMIT = 12
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_file_stem(name, fallback: str = "map") -> str:
    """A file name stem that is valid on Windows, macOS and Linux."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".")[0].strip().upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned[:100]


def map_base_name(project_name: str, current_file: str | None) -> str:
    """Name exports after the saved map file, else after the map's title."""
    if current_file:
        return safe_file_stem(os.path.splitext(os.path.basename(current_file))[0])
    return safe_file_stem(project_name or "map")


def path_key(path: str) -> str:
    absolute = os.path.normcase(os.path.abspath(path))
    return hashlib.sha1(absolute.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def thumbnail_path(root: str, map_path: str) -> str:
    return os.path.join(root, "thumbnails", path_key(map_path) + ".png")


def recent_file_path(root: str) -> str:
    return os.path.join(root, "recent.json")


def load_path_list(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    seen, out = set(), []
    for item in data:
        if isinstance(item, str) and item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def save_path_list(items, path: str, limit: int = RECENT_LIMIT) -> bool:
    data = [item for item in items if isinstance(item, str) and item][:limit]
    temp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1)
        os.replace(temp, path)
        return True
    except OSError:
        try:
            if os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass
        return False
