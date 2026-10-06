"""Internal asset library: an on-disk store the app always keeps.

- Import Folder copies PNGs into the store (preserving subfolders).
- Import File copies a single PNG into the store.
- Custom uploads requested to be embedded are NOT stored here (they live in
  the .bmap via Piece.embedded) — handled by the caller.
- Assets are auto-grouped (by folder) and auto-tagged (type + size).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Optional

from core.project import parse_size_from_name

SUPPORTED_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")


@dataclass
class Asset:
    path: str                 # store-relative path
    name: str
    folder: str              # store-relative folder
    size: Optional[tuple[int, int]] = None
    is_overlay: bool = False
    tags: list[str] = None    # type + size tags
    width: int = 0
    height: int = 0

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
            if self.is_overlay:
                self.tags.append("overlay")
            if self.size:
                self.tags.append(f"{self.size[0]}x{self.size[1]}")


class AssetLibrary:
    def __init__(self, root: str = ""):
        self.root = root
        self.assets: list[Asset] = []
        self._by_path: dict[str, Asset] = {}

    # ---- scanning ----
    def scan(self, root: str) -> None:
        self.root = root
        self.assets = []
        self._by_path = {}
        if not root or not os.path.isdir(root):
            return
        for dirpath, _dirs, files in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            for fn in sorted(files):
                if fn.lower().endswith(SUPPORTED_EXTS):
                    self._add(os.path.join(dirpath, fn), rel)

    def _add(self, full: str, rel_folder: str):
        name = os.path.basename(full)
        size = parse_size_from_name(os.path.splitext(name)[0])
        is_overlay = "overlay" in full.lower()
        rel = os.path.relpath(full, self.root).replace(os.sep, "/")
        a = Asset(path=rel, name=name,
                  folder="." if rel_folder == "." else rel_folder,
                  size=size, is_overlay=is_overlay)
        self.assets.append(a)
        self._by_path[rel] = a

    def get(self, path: str) -> Optional[Asset]:
        return self._by_path.get(path)

    def abs_path(self, path: str) -> str:
        return os.path.join(self.root, path) if self.root else path

    # ---- import ----
    def import_folder(self, src: str) -> int:
        if not self.root:
            return 0
        count = 0
        for dirpath, _dirs, files in os.walk(src):
            rel = os.path.relpath(dirpath, src)
            for fn in files:
                if fn.lower().endswith(SUPPORTED_EXTS):
                    dest_dir = os.path.join(self.root, rel) if rel != "." else self.root
                    os.makedirs(dest_dir, exist_ok=True)
                    shutil.copy2(os.path.join(dirpath, fn), os.path.join(dest_dir, fn))
                    count += 1
        self.scan(self.root)
        return count

    def import_file(self, src: str) -> Optional[str]:
        if not self.root:
            return None
        fn = os.path.basename(src)
        dest = os.path.join(self.root, fn)
        # avoid clobbering
        base, ext = os.path.splitext(fn)
        i = 1
        while os.path.exists(dest):
            dest = os.path.join(self.root, f"{base}_{i}{ext}")
            i += 1
        shutil.copy2(src, dest)
        self.scan(self.root)
        return os.path.relpath(dest, self.root).replace(os.sep, "/")

    # ---- groups (auto by folder) ----
    def groups(self, order: list[str] | None = None) -> list[str]:
        seen = []
        for a in self.assets:
            if a.folder not in seen:
                seen.append(a.folder)
        if order:
            # honor explicit order, then append any new
            seen = [g for g in order if g in seen] + [g for g in seen if g not in order]
        return seen

    def assets_in_group(self, group: str) -> list[Asset]:
        return [a for a in self.assets if a.folder == group]

    def search(self, query: str) -> list[Asset]:
        q = query.strip().lower()
        if not q:
            return list(self.assets)
        out = []
        for a in self.assets:
            if (q in a.name.lower() or q in a.folder.lower()
                    or any(q in t for t in a.tags)):
                out.append(a)
        return out

    def ensure_store(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.scan(root)
