"""Internal asset library: a safe, on-disk store for imported map art.

- Import Folder copies supported images into the store (preserving subfolders).
- Import File copies one image into the store.
- Import ZIP copies supported images only, normalizes Windows-style archive
  separators, preserves the archive's folder layout, and never extracts paths
  outside the store. Original archives are left untouched.
- Assets keep the folder layout they arrived with: a ZIP's own directories are
  preserved, and the library browses that structure as-is. Assets are never
  re-sorted, re-filed or renamed by the app.
- Only one copy of a name is shown: a picture whose name is already in the
  library is omitted (``hidden_duplicates`` remembers what was folded away).
- Files that cannot be read as an image are omitted during import/install
  (``skipped_unreadable``), so the library never lists broken tiles.
- ``remove_paths`` deletes tiles (and the folders that become empty) from the
  store, for tidying the pool or removing your own uploads again.
"""
from __future__ import annotations

import filecmp
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tempfile
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Optional

from core.project import parse_size_from_name

SUPPORTED_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_IMAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_IMAGE_BYTES_PER_FILE = 512 * 1024 * 1024
_IMPORT_MARKER = ".sceneboard-import.json"


class AssetImportError(ValueError):
    """An asset source could not be imported safely."""


@dataclass
class ZipImportReport:
    archive_name: str
    imported: int = 0
    skipped_non_image: int = 0
    skipped_unsafe: int = 0
    skipped_symlinks: int = 0
    skipped_corrupt: int = 0
    renamed_duplicates: int = 0
    already_imported: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class Asset:
    path: str                 # store-relative path, always slash-separated
    name: str
    folder: str              # store-relative folder
    size: Optional[tuple[int, int]] = None  # map dimensions parsed from name
    is_overlay: bool = False
    tags: list[str] = field(default_factory=list)
    width: int = 0            # source image pixels
    height: int = 0

    def __post_init__(self):
        if not self.tags:
            if self.is_overlay:
                self.tags.append("overlay")
            if self.size:
                self.tags.append(f"{self.size[0]}x{self.size[1]}")


def _image_dimensions(path: str) -> tuple[int, int]:
    """Read common image dimensions from headers without decoding pixels.

    Large geomorph PNGs can be 7,199 x 7,199 pixels. Reading just their image
    headers keeps library scans light and lets the map builder avoid loading
    every full-resolution source merely to learn its dimensions.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
            if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
                return struct.unpack(">II", head[16:24])
            if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
                return struct.unpack("<HH", head[6:10])
            if head[:2] == b"BM" and len(head) >= 26:
                width = int.from_bytes(head[18:22], "little", signed=True)
                height = int.from_bytes(head[22:26], "little", signed=True)
                return abs(width), abs(height)
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                width, height = _webp_dimensions(head)
                if width and height:
                    return width, height
            if head[:2] == b"\xff\xd8":
                return _jpeg_dimensions(fh)
            if head[:4] in (b"II*\x00", b"MM\x00*"):
                return _tiff_dimensions(fh)
    except (OSError, ValueError, struct.error):
        pass
    return 0, 0


def _tiff_dimensions(fh) -> tuple[int, int]:
    """Read ImageWidth/ImageLength from the first TIFF IFD (either endianness)."""
    fh.seek(0)
    head = fh.read(8)
    if len(head) < 8:
        return 0, 0
    endian = "little" if head[:4] == b"II*\x00" else "big"
    fh.seek(int.from_bytes(head[4:8], endian))
    count_bytes = fh.read(2)
    if len(count_bytes) != 2:
        return 0, 0
    width = height = 0
    for _ in range(min(int.from_bytes(count_bytes, endian), 1024)):
        entry = fh.read(12)
        if len(entry) != 12:
            break
        tag = int.from_bytes(entry[0:2], endian)
        if tag not in (0x0100, 0x0101):
            continue
        typ = int.from_bytes(entry[2:4], endian)
        if int.from_bytes(entry[4:8], endian) != 1:
            continue
        value = int.from_bytes(entry[8:10] if typ == 3 else entry[8:12], endian)
        if tag == 0x0100:
            width = value
        else:
            height = value
        if width and height:
            return width, height
    return width, height


def _webp_dimensions(head: bytes) -> tuple[int, int]:
    chunk = head[12:16]
    if chunk == b"VP8X" and len(head) >= 30:
        width = 1 + int.from_bytes(head[24:27], "little")
        height = 1 + int.from_bytes(head[27:30], "little")
        return width, height
    if chunk == b"VP8 " and len(head) >= 30 and head[23:26] == b"\x9d\x01\x2a":
        width = int.from_bytes(head[26:28], "little") & 0x3FFF
        height = int.from_bytes(head[28:30], "little") & 0x3FFF
        return width, height
    if chunk == b"VP8L" and len(head) >= 25 and head[20] == 0x2F:
        b1, b2, b3, b4 = head[21:25]
        width = 1 + (((b2 & 0x3F) << 8) | b1)
        height = 1 + (((b4 & 0x0F) << 10) | (b3 << 2) | ((b2 >> 6) & 0x03))
        return width, height
    return 0, 0


def _jpeg_dimensions(fh) -> tuple[int, int]:
    """Walk JPEG marker segments until a Start Of Frame marker is found."""
    fh.seek(2)
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                   0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while True:
        byte = fh.read(1)
        if not byte:
            return 0, 0
        if byte != b"\xff":
            continue
        marker = fh.read(1)
        while marker == b"\xff":
            marker = fh.read(1)
        if not marker or marker in (b"\x00", b"\xd9", b"\xda"):
            return 0, 0
        code = marker[0]
        if code in (0xD8, 0x01) or 0xD0 <= code <= 0xD7:
            continue
        length_bytes = fh.read(2)
        if len(length_bytes) != 2:
            return 0, 0
        segment_length = int.from_bytes(length_bytes, "big")
        if segment_length < 2:
            return 0, 0
        if code in sof_markers:
            frame = fh.read(5)
            if len(frame) != 5:
                return 0, 0
            height = int.from_bytes(frame[1:3], "big")
            width = int.from_bytes(frame[3:5], "big")
            return width, height
        fh.seek(segment_length - 2, os.SEEK_CUR)


def _normalize_archive_path(member_name: str) -> Optional[str]:
    """Return a safe relative archive path, or None for an unsafe name.

    ZIPs created on Windows commonly store `\\` separators. Treat both slash
    styles as separators before checking for traversal and absolute paths.
    """
    if not member_name or "\x00" in member_name:
        return None
    name = member_name.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts = []
    for raw in name.split("/"):
        if raw in ("", "."):
            continue
        if raw == ".." or any(ord(char) < 32 for char in raw):
            return None
        if ":" in raw:
            # Avoid drive syntax and NTFS alternate data stream names.
            return None
        part = raw.rstrip(" .")
        if not part:
            continue
        device_name = part.split(".", 1)[0].rstrip(" .").upper()
        if device_name in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(
                r"(?:COM|LPT)[1-9]", device_name
        ):
            part = "_" + part
        parts.append(part)
    return "/".join(parts) if parts else None


def _safe_archive_folder(archive_path: str) -> str:
    base = os.path.basename(archive_path.replace("\\", "/"))
    stem = os.path.splitext(base)[0]
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).rstrip(" .")
    if not stem:
        stem = "Imported archive"
    if stem.upper() in {"CON", "PRN", "AUX", "NUL"}:
        stem = "_" + stem
    return stem[:120]


def _scan_order_key(asset: "Asset") -> list:
    """Where ``AssetLibrary.scan`` lists an asset: folders in name order, and a
    folder's own files (sorted by name) before its subfolders."""
    folder = asset.folder.replace("\\", "/")
    parts = [] if folder in (".", "") else folder.split("/")
    return [(1, part) for part in parts] + [(0, asset.name)]


def _fingerprint(entries) -> str:
    digest = hashlib.sha256()
    for relative, crc, size, offset in sorted(
            entries, key=lambda row: (row[0].casefold(), row[0], row[3])):
        digest.update(relative.encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
        digest.update(f"{crc:08x}:{size}:{offset}".encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


class AssetLibrary:
    def __init__(self, root: str = ""):
        self.root = root
        self.assets: list[Asset] = []
        self._by_path: dict[str, Asset] = {}
        self._by_name: dict[str, str] = {}
        # Same-named pictures: only one copy is shown; the others stay on disk
        # but are hidden, remembered here as (hidden_path, shown_path).
        self.hidden_duplicates: list[tuple[str, str]] = []
        # Images that could not be read at all are omitted from the library.
        self.skipped_unreadable = 0
        self._scan_complete = False
        self._scan_revision = 0

    # ---- scanning ----
    def scan(self, root: str) -> None:
        self._scan_revision += 1
        self._scan_complete = False
        self.root = root
        self.assets = []
        self._by_path = {}
        self._by_name = {}
        self.hidden_duplicates = []
        self.skipped_unreadable = 0
        if not root or not os.path.isdir(root):
            self._scan_complete = True
            return
        for dirpath, dirs, files in os.walk(root):
            # A failed/interrupted import must never surface partial staging
            # files as ordinary library assets.
            dirs[:] = sorted(d for d in dirs if not d.startswith(".sceneboard-import-"))
            rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
            for fn in sorted(files):
                if fn.lower().endswith(SUPPORTED_EXTS):
                    self._add(os.path.join(dirpath, fn), rel)
        self._scan_complete = True

    def adopt_scan(self, snapshot: "AssetLibrary") -> None:
        """Adopt a scan produced by a worker without rescanning on the GUI thread."""
        if not snapshot._scan_complete:
            raise ValueError("Cannot adopt an incomplete asset-library scan")
        self.root = snapshot.root
        self.assets = snapshot.assets
        self._by_path = snapshot._by_path
        self._by_name = snapshot._by_name
        self.hidden_duplicates = snapshot.hidden_duplicates
        self.skipped_unreadable = snapshot.skipped_unreadable
        self._scan_complete = True
        self._scan_revision += 1

    def _make_asset(self, full: str, rel_folder: str) -> Asset:
        name = os.path.basename(full)
        size = parse_size_from_name(os.path.splitext(name)[0])
        rel = os.path.relpath(full, self.root).replace(os.sep, "/")
        folder = "." if rel_folder in (".", "") else rel_folder.replace(os.sep, "/")
        width, height = _image_dimensions(full)
        is_overlay = "overlay" in rel.lower()
        return Asset(path=rel, name=name, folder=folder,
                     size=size, is_overlay=is_overlay,
                     width=width, height=height)

    def _register(self, asset: Asset, *, record_hidden: bool = True) -> str:
        """Decide whether a freshly-made Asset joins the visible library.

        Returns ``"added"``, ``"unreadable"`` (the image header could not be
        read, so the file cannot be imported) or ``"duplicate"`` (another shown
        asset already has the same name, and only one copy is shown).
        """
        if asset.width <= 0 or asset.height <= 0:
            self.skipped_unreadable += 1
            return "unreadable"
        kept = self._by_name.get(asset.name.casefold())
        if kept is not None:
            if record_hidden:
                self.hidden_duplicates.append((asset.path, kept))
            return "duplicate"
        self._by_name[asset.name.casefold()] = asset.path
        return "added"

    def _add(self, full: str, rel_folder: str):
        a = self._make_asset(full, rel_folder)
        if self._register(a) != "added":
            return                      # omitted: unreadable or a repeat name
        self.assets.append(a)
        self._by_path[a.path] = a

    def _index_file(self, full: str) -> Optional[str]:
        """Add one file inside the store to the index without rescanning the
        whole store, in the place a full scan would list it. Returns the
        store-relative path the file is shown under, or None when the file
        cannot be shown (unreadable, or a name that is already shown).

        A copied file that ends up hidden is removed again, so the store never
        accumulates pictures the library does not show."""
        rel = os.path.relpath(full, self.root).replace(os.sep, "/")
        if rel in self._by_path:
            return rel
        asset = self._make_asset(full, os.path.dirname(rel) or ".")
        status = self._register(asset, record_hidden=False)
        if status == "duplicate":
            shown = self._by_name.get(asset.name.casefold(), rel)
            try:
                os.remove(full)
            except OSError:
                pass
            try:
                os.removedirs(os.path.dirname(full))
            except OSError:
                pass                      # the folder still holds other files
            return shown
        if status == "unreadable":
            try:
                os.remove(full)
            except OSError:
                pass
            return None
        key = _scan_order_key(asset)
        position = next((i for i, other in enumerate(self.assets)
                         if _scan_order_key(other) > key), len(self.assets))
        self.assets.insert(position, asset)
        self._by_path[rel] = asset
        self._scan_revision += 1            # groups and counts refresh
        return rel

    def store_path_of(self, path: str) -> Optional[str]:
        """Store-relative path of a file that already lives in the store, or
        None when it is somewhere else."""
        if not self.root or not path:
            return None
        root = os.path.realpath(self.root)
        full = os.path.realpath(path)
        try:
            inside = os.path.commonpath([os.path.normcase(root), os.path.normcase(full)]) \
                == os.path.normcase(root)
        except ValueError:                  # another drive on Windows
            return None
        if not inside or os.path.normcase(full) == os.path.normcase(root):
            return None
        rel = os.path.relpath(full, root).replace(os.sep, "/")
        if any(part.startswith(".sceneboard-import-") for part in rel.split("/")):
            return None                     # unfinished import staging, never listed
        return rel

    def import_into_folder(self, src: str, folder: str, *,
                           reuse_identical: bool = True) -> Optional[str]:
        """Copy one image into ``folder`` at the top of the store (made if
        needed) and return its store-relative path, or None when ``src`` is not
        a supported image file.

        The index is updated in place rather than rescanning the whole store.
        A picture that already lives in the store is used where it is. With
        ``reuse_identical`` a byte-identical copy already in the folder (same
        name, or the name with a ``_N`` suffix) is reused instead of copied
        again; a different picture never overwrites one with the same name.
        """
        if (not self.root or not src or not src.lower().endswith(SUPPORTED_EXTS)
                or not os.path.isfile(src)):
            return None
        inside = self.store_path_of(src)
        if inside is not None:
            return self._index_file(os.path.join(self.root, *inside.split("/")))
        parts = [part for part in folder.replace("\\", "/").split("/")
                 if part not in ("", ".", "..")]
        dest_dir = os.path.join(self.root, *parts)
        os.makedirs(dest_dir, exist_ok=True)
        base, ext = os.path.splitext(os.path.basename(src))
        dest = os.path.join(dest_dir, base + ext)
        suffix = 1
        while os.path.exists(dest):
            if (reuse_identical and os.path.isfile(dest)
                    and filecmp.cmp(src, dest, shallow=False)):
                return self._index_file(dest)
            dest = os.path.join(dest_dir, f"{base}_{suffix}{ext}")
            suffix += 1
        shutil.copy2(src, dest)
        return self._index_file(dest)

    def get(self, path: str) -> Optional[Asset]:
        return self._by_path.get(path)

    def abs_path(self, path: str) -> str:
        return os.path.join(self.root, *path.replace("\\", "/").split("/")) if self.root else path

    # ---- import ----
    def import_folder(self, src: str, *, preserve_root: bool = False) -> int:
        """Copy supported images from a directory into the asset store.

        ``preserve_root`` keeps the selected directory's own name as a group
        above its contents. The UI uses this for user imports so selecting a
        structural directory such as ``100x100 Core`` or ``Symbols`` does not
        erase the folder name the asset arrived in. The
        default remains false for callers importing known fixture folders
        whose contents are intentionally merged into the store.
        """
        if not self.root:
            return 0

        # Gather the source paths before creating the destination. In the
        # unusual case where the source is an ancestor of the store, this
        # prevents a newly-created destination from being walked recursively.
        source_files = []
        for dirpath, dirs, files in os.walk(src):
            dirs.sort()
            rel = os.path.relpath(dirpath, src)
            for fn in sorted(files):
                if fn.lower().endswith(SUPPORTED_EXTS):
                    source_files.append((dirpath, rel, fn))

        if not source_files:
            self.scan(self.root)
            return 0

        folder_name = ""
        if preserve_root:
            folder_name = os.path.basename(os.path.normpath(src)) or "Imported folder"
            candidate = folder_name
            suffix = 2
            while os.path.lexists(os.path.join(self.root, candidate)):
                candidate = f"{folder_name} ({suffix})"
                suffix += 1
            folder_name = candidate

        count = 0
        for dirpath, rel, fn in source_files:
            components = []
            if preserve_root:
                components.append(folder_name)
            if rel != ".":
                components.extend(rel.split(os.sep))
            dest_dir = os.path.join(self.root, *components) if components else self.root
            os.makedirs(dest_dir, exist_ok=True)
            source = os.path.join(dirpath, fn)
            destination = os.path.join(dest_dir, fn)
            if os.path.abspath(source) == os.path.abspath(destination):
                continue
            shutil.copy2(source, destination)
            count += 1
        self.scan(self.root)
        return count

    def import_file(self, src: str) -> Optional[str]:
        """Copy one image into the store and show it.

        Returns the store-relative path the picture is shown under, or None
        when it is not a supported, readable image. A picture whose name is
        already shown is not copied at all (one copy of a name is all that is
        shown); the path of the shown copy is returned."""
        if (not self.root or not src.lower().endswith(SUPPORTED_EXTS)
                or not os.path.isfile(src)):
            return None
        inside = self.store_path_of(src)
        if inside is not None:
            return self._index_file(os.path.join(self.root, *inside.split("/")))
        fn = os.path.basename(src)
        dest = os.path.join(self.root, fn)
        # avoid clobbering
        base, ext = os.path.splitext(fn)
        i = 1
        while os.path.exists(dest):
            dest = os.path.join(self.root, f"{base}_{i}{ext}")
            i += 1
        shutil.copy2(src, dest)
        return self._index_file(dest)

    def import_zip(self, archive_path: str, *, rescan: bool = True) -> ZipImportReport:
        """Import supported images from a ZIP without trusting its paths.

        The archive is extracted into a temporary directory inside the asset
        store and renamed into place only after a successful import. Backslash
        paths are normalized; `..`, absolute paths, drive paths, symlinks, and
        non-image files are never extracted. Each archive gets its own folder
        so similarly named assets from different packs cannot overwrite each
        other. Re-importing an identical archive is detected from its ZIP
        central-directory metadata.
        """
        if not self.root:
            raise AssetImportError("Choose or create an asset-store folder first.")
        if not os.path.isfile(archive_path):
            raise AssetImportError(f"ZIP file not found: {archive_path}")
        if not zipfile.is_zipfile(archive_path):
            raise AssetImportError(f"Not a readable ZIP archive: {archive_path}")

        os.makedirs(self.root, exist_ok=True)
        report = ZipImportReport(archive_name=os.path.basename(archive_path))
        try:
            archive = zipfile.ZipFile(archive_path, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise AssetImportError(f"Could not open ZIP: {exc}") from exc

        with archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise AssetImportError(
                    f"Archive has too many entries ({len(infos):,}; limit "
                    f"{MAX_ARCHIVE_MEMBERS:,}).")

            entries = []
            image_bytes = 0
            for info in infos:
                relative = _normalize_archive_path(info.filename)
                if relative is None:
                    report.skipped_unsafe += 1
                    continue
                if info.is_dir() or info.filename.endswith(("/", "\\")):
                    continue
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    report.skipped_symlinks += 1
                    continue
                if not relative.lower().endswith(SUPPORTED_EXTS):
                    report.skipped_non_image += 1
                    continue
                if info.file_size < 0 or info.file_size > MAX_ARCHIVE_IMAGE_BYTES_PER_FILE:
                    raise AssetImportError(
                        f"Image entry is too large to import safely: {relative}")
                image_bytes += info.file_size
                if image_bytes > MAX_ARCHIVE_IMAGE_BYTES:
                    raise AssetImportError(
                        "Archive expands to more than the 2 GiB image import limit.")
                entries.append((info, relative))

            fingerprint = _fingerprint(
                (relative, info.CRC, info.file_size, info.header_offset)
                for info, relative in entries)
            base_folder_name = _safe_archive_folder(archive_path)
            suffix = 1
            while True:
                folder_name = (base_folder_name if suffix == 1
                               else f"{base_folder_name} ({suffix})")
                destination = os.path.join(self.root, folder_name)
                if not os.path.exists(destination):
                    break
                marker = os.path.join(destination, _IMPORT_MARKER)
                if os.path.isfile(marker):
                    try:
                        with open(marker, encoding="utf-8") as fh:
                            previous = json.load(fh)
                        if previous.get("fingerprint") == fingerprint:
                            report.already_imported = int(previous.get("imported", 0))
                            if rescan:
                                self.scan(self.root)
                            return report
                    except (OSError, ValueError, TypeError):
                        pass
                suffix += 1

            temp_parent = tempfile.mkdtemp(prefix=".sceneboard-import-", dir=self.root)
            staging = os.path.join(temp_parent, folder_name)
            os.makedirs(staging, exist_ok=True)
            used_paths: set[str] = set()
            copied_bytes = 0
            try:
                for info, relative in entries:
                    dest_relative = relative
                    key = dest_relative.casefold()
                    if key in used_paths:
                        directory, filename = os.path.split(dest_relative)
                        stem, ext = os.path.splitext(filename)
                        duplicate_index = 2
                        while key in used_paths:
                            candidate = f"{stem}_{duplicate_index}{ext}"
                            dest_relative = os.path.join(directory, candidate).replace(os.sep, "/")
                            key = dest_relative.casefold()
                            duplicate_index += 1
                        report.renamed_duplicates += 1
                    used_paths.add(key)
                    target = os.path.join(staging, *dest_relative.split("/"))
                    if os.path.commonpath((os.path.realpath(staging),
                                           os.path.realpath(os.path.dirname(target)))) != os.path.realpath(staging):
                        report.skipped_unsafe += 1
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    try:
                        with archive.open(info, "r") as source, open(target, "xb") as output:
                            written = 0
                            while True:
                                chunk = source.read(1024 * 1024)
                                if not chunk:
                                    break
                                written += len(chunk)
                                copied_bytes += len(chunk)
                                if written > info.file_size or copied_bytes > MAX_ARCHIVE_IMAGE_BYTES:
                                    raise AssetImportError(
                                        "ZIP expanded beyond its declared safe image-size limit.")
                                output.write(chunk)
                            if written != info.file_size:
                                raise zipfile.BadZipFile(
                                    f"Unexpected size for archive entry {relative}")
                    except (zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
                        try:
                            os.remove(target)
                        except OSError:
                            pass
                        report.skipped_corrupt += 1
                        if len(report.warnings) < 10:
                            report.warnings.append(f"Skipped corrupt image {relative}: {exc}")
                        continue
                    except Exception:
                        try:
                            os.remove(target)
                        except OSError:
                            pass
                        raise
                    report.imported += 1

                if report.imported:
                    metadata = {
                        "format": "sceneboard-asset-archive",
                        "version": 1,
                        "source": report.archive_name,
                        "fingerprint": fingerprint,
                        "imported": report.imported,
                    }
                    with open(os.path.join(staging, _IMPORT_MARKER), "w",
                              encoding="utf-8") as fh:
                        json.dump(metadata, fh, ensure_ascii=False, indent=2)
                    os.replace(staging, destination)
                else:
                    shutil.rmtree(staging, ignore_errors=True)
            except Exception:
                shutil.rmtree(temp_parent, ignore_errors=True)
                raise
            finally:
                if os.path.isdir(temp_parent):
                    shutil.rmtree(temp_parent, ignore_errors=True)

        if rescan:
            self.scan(self.root)
        return report

    # ---- groups (auto by folder) ----
    def groups(self, order: list[str] | None = None) -> list[str]:
        # Keep first-seen folder order without a list-membership scan for every
        # asset. Large packs can contain thousands of images spread across
        # many paths, making the former implementation quadratic.
        seen = []
        seen_set = set()
        for asset in self.assets:
            if asset.folder not in seen_set:
                seen_set.add(asset.folder)
                seen.append(asset.folder)
        if order:
            # Honor explicit order, then append any new groups.
            ordered = [group for group in order if group in seen_set]
            ordered_set = set(ordered)
            seen = ordered + [group for group in seen if group not in ordered_set]
        return seen

    def assets_in_group(self, group: str) -> list[Asset]:
        return [a for a in self.assets if a.folder == group]

    def search(self, query: str) -> list[Asset]:
        q = query.strip().lower()
        if not q:
            return list(self.assets)
        return [a for a in self.assets
                if q in a.name.lower() or q in a.folder.lower()
                or any(q in tag for tag in a.tags)]

    def ensure_store(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.scan(root)

    # ---- removing tiles from the pool ------------------------------------
    def remove_paths(self, paths) -> int:
        """Delete store files (and any folders that become empty) and rescan.

        Only files inside the store are touched; anything else is ignored.
        Returns how many files were removed."""
        removed = 0
        touched_dirs = set()
        for rel in list(paths):
            if not rel:
                continue
            full = self.abs_path(rel)
            if self.store_path_of(full) is None:
                continue
            try:
                os.remove(full)
                removed += 1
                touched_dirs.add(os.path.dirname(full))
            except OSError:
                continue
        for directory in touched_dirs:
            # Folders that end up empty are gone from the library too.
            try:
                while directory and os.path.isdir(directory) and \
                        os.path.commonpath((os.path.realpath(directory),
                                            os.path.realpath(self.root))) == \
                        os.path.realpath(self.root) and \
                        directory != os.path.realpath(self.root):
                    if os.listdir(directory):
                        break
                    parent = os.path.dirname(directory)
                    os.rmdir(directory)
                    directory = parent
            except OSError:
                pass
        if removed:
            self.scan(self.root)
        return removed
