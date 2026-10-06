"""Internal asset library: a safe, on-disk store for imported map art.

- Import Folder copies supported images into the store (preserving subfolders).
- Import File copies one image into the store.
- Import ZIP copies supported images only, normalizes Windows-style archive
  separators, preserves the archive's folder layout, and never extracts paths
  outside the store. Original archives are left untouched.
- Assets are auto-grouped by folder and auto-tagged by type + map size.
"""
from __future__ import annotations

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
    headers keeps library scans light and lets the generator avoid loading
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
    except (OSError, ValueError, struct.error):
        pass
    return 0, 0


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

    # ---- scanning ----
    def scan(self, root: str) -> None:
        self.root = root
        self.assets = []
        self._by_path = {}
        if not root or not os.path.isdir(root):
            return
        for dirpath, dirs, files in os.walk(root):
            # A failed/interrupted import must never surface partial staging
            # files as ordinary library assets.
            dirs[:] = sorted(d for d in dirs if not d.startswith(".sceneboard-import-"))
            rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
            for fn in sorted(files):
                if fn.lower().endswith(SUPPORTED_EXTS):
                    self._add(os.path.join(dirpath, fn), rel)

    def _add(self, full: str, rel_folder: str):
        name = os.path.basename(full)
        size = parse_size_from_name(os.path.splitext(name)[0])
        rel = os.path.relpath(full, self.root).replace(os.sep, "/")
        folder = "." if rel_folder in (".", "") else rel_folder.replace(os.sep, "/")
        width, height = _image_dimensions(full)
        is_overlay = "overlay" in rel.lower()
        a = Asset(path=rel, name=name, folder=folder,
                  size=size, is_overlay=is_overlay,
                  width=width, height=height)
        self.assets.append(a)
        self._by_path[rel] = a

    def get(self, path: str) -> Optional[Asset]:
        return self._by_path.get(path)

    def abs_path(self, path: str) -> str:
        return os.path.join(self.root, *path.replace("\\", "/").split("/")) if self.root else path

    # ---- import ----
    def import_folder(self, src: str) -> int:
        if not self.root:
            return 0
        count = 0
        for dirpath, dirs, files in os.walk(src):
            dirs.sort()
            rel = os.path.relpath(dirpath, src)
            for fn in sorted(files):
                if fn.lower().endswith(SUPPORTED_EXTS):
                    dest_dir = os.path.join(self.root, rel) if rel != "." else self.root
                    os.makedirs(dest_dir, exist_ok=True)
                    shutil.copy2(os.path.join(dirpath, fn), os.path.join(dest_dir, fn))
                    count += 1
        self.scan(self.root)
        return count

    def import_file(self, src: str) -> Optional[str]:
        if not self.root or not src.lower().endswith(SUPPORTED_EXTS):
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
        return [a for a in self.assets
                if q in a.name.lower() or q in a.folder.lower()
                or any(q in tag for tag in a.tags)]

    def ensure_store(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.scan(root)
