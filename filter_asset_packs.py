"""Build image-only copies of the release asset ZIPs for the Windows bundle.

This deliberately uses the same supported-extension list and archive-path
normalizer as the application importer, so documentation and unsupported file
types never become part of the packaged asset payload.
"""
from __future__ import annotations

import argparse
import os
import stat
import zipfile
import zlib

from core.asset_manager import (
    MAX_ARCHIVE_IMAGE_BYTES,
    MAX_ARCHIVE_IMAGE_BYTES_PER_FILE,
    MAX_ARCHIVE_MEMBERS,
    SUPPORTED_EXTS,
    _normalize_archive_path,
)


def _unique_archive_path(relative: str, used: set[str]) -> str:
    """Match the importer's case-insensitive duplicate-name behavior."""
    candidate = relative
    key = candidate.casefold()
    if key not in used:
        used.add(key)
        return candidate

    directory, filename = os.path.split(relative)
    stem, extension = os.path.splitext(filename)
    index = 2
    while True:
        name = f"{stem}_{index}{extension}"
        candidate = os.path.join(directory, name).replace(os.sep, "/")
        key = candidate.casefold()
        if key not in used:
            used.add(key)
            return candidate
        index += 1


def filter_zip_to_supported_images(source_path: str, output_path: str) -> dict:
    """Write an image-only, path-safe ZIP while preserving internal folders."""
    if not zipfile.is_zipfile(source_path):
        raise ValueError(f"Not a readable ZIP archive: {source_path}")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    temp_path = output_path + ".tmp"
    report = {"images": 0, "skipped_non_image": 0, "skipped_unsafe": 0,
              "skipped_symlink": 0, "renamed_duplicates": 0}
    try:
        with zipfile.ZipFile(source_path, "r") as source:
            infos = source.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise ValueError(
                    f"Archive has too many entries ({len(infos):,}; limit "
                    f"{MAX_ARCHIVE_MEMBERS:,}).")

            image_entries = []
            declared_bytes = 0
            for info in infos:
                relative = _normalize_archive_path(info.filename)
                if relative is None:
                    report["skipped_unsafe"] += 1
                    continue
                if info.is_dir() or info.filename.endswith(("/", "\\")):
                    continue
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    report["skipped_symlink"] += 1
                    continue
                if not relative.lower().endswith(SUPPORTED_EXTS):
                    report["skipped_non_image"] += 1
                    continue
                if (info.file_size < 0 or
                        info.file_size > MAX_ARCHIVE_IMAGE_BYTES_PER_FILE):
                    raise ValueError(
                        f"Image entry is too large to package safely: {relative}")
                declared_bytes += info.file_size
                if declared_bytes > MAX_ARCHIVE_IMAGE_BYTES:
                    raise ValueError(
                        "Archive expands beyond the 2 GiB image packaging limit.")
                image_entries.append((info, relative))

            # Build a new archive containing only supported raster files.
            # Images are already compressed; level 1 limits build time while
            # preserving a compact payload for the one-file executable.
            with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED,
                                 compresslevel=1) as output:
                used_paths: set[str] = set()
                copied_bytes = 0
                for info, relative in image_entries:
                    # Resolve duplicates against one shared set after collecting
                    # paths, ensuring collisions are handled across all entries.
                    original_relative = relative
                    relative = _unique_archive_path(relative, used_paths)
                    if relative != original_relative:
                        report["renamed_duplicates"] += 1
                    entry = zipfile.ZipInfo(relative, date_time=info.date_time)
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    with source.open(info, "r") as input_file, \
                            output.open(entry, "w") as output_file:
                        written = 0
                        while True:
                            chunk = input_file.read(1024 * 1024)
                            if not chunk:
                                break
                            written += len(chunk)
                            copied_bytes += len(chunk)
                            if (written > info.file_size or
                                    copied_bytes > MAX_ARCHIVE_IMAGE_BYTES):
                                raise ValueError(
                                    "Archive expanded beyond its declared safe size.")
                            output_file.write(chunk)
                        if written != info.file_size:
                            raise zipfile.BadZipFile(
                                f"Unexpected size for image entry {relative}")
                    report["images"] += 1

        if not report["images"]:
            raise ValueError(f"No supported image files found in {source_path}")
        os.replace(temp_path, output_path)
        return report
    except (zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
        raise ValueError(f"Could not filter ZIP {source_path}: {exc}") from exc
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def filter_asset_pack_directory(source_dir: str, output_dir: str) -> list[tuple[str, dict]]:
    """Filter every ZIP in ``source_dir`` into ``output_dir``."""
    if not os.path.isdir(source_dir):
        raise ValueError(f"Asset-pack directory not found: {source_dir}")
    os.makedirs(output_dir, exist_ok=True)
    # The build cache may contain outputs from an older pack set. Remove those
    # first so only the current input archives can be bundled.
    for name in os.listdir(output_dir):
        path = os.path.join(output_dir, name)
        if name.lower().endswith(".zip") and os.path.isfile(path):
            os.remove(path)
    results = []
    for name in sorted(os.listdir(source_dir)):
        source_path = os.path.join(source_dir, name)
        if not name.lower().endswith(".zip") or not os.path.isfile(source_path):
            continue
        output_path = os.path.join(output_dir, name)
        results.append((name, filter_zip_to_supported_images(source_path, output_path)))
    if not results:
        raise ValueError(f"No ZIP archives found in {source_dir}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", help="directory containing downloaded release ZIPs")
    parser.add_argument("output_dir", help="directory for image-only ZIPs")
    args = parser.parse_args()
    try:
        results = filter_asset_pack_directory(args.source_dir, args.output_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for name, report in results:
        print(f"{name}: kept {report['images']:,} images; "
              f"skipped {report['skipped_non_image']:,} non-image files; "
              f"rejected {report['skipped_unsafe'] + report['skipped_symlink']:,} unsafe entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
