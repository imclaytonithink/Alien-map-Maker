"""Portable project bundles (project JSON, referenced assets, and PNG renders)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import PurePosixPath

BUNDLE_FORMAT = "rpg-map-pack"
BUNDLE_VERSION = 1
PROJECT_MEMBER = "project.bmap"
MAX_ARCHIVE_MEMBERS = 20_000
MAX_UNPACKED_BYTES = 5 * 1024 * 1024 * 1024


class BundleError(ValueError):
    """Raised when a project bundle is malformed or unsafe to extract."""


def _safe_name(value: str, fallback: str = "map") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._")
    return value[:96] or fallback


def _asset_member(asset_path: str, source_path: str, content: bytes) -> tuple[str, str]:
    """Return (archive member, project-relative asset path)."""
    original = str(asset_path or "").replace("\\", "/")
    pure = PurePosixPath(original)
    unsafe = (pure.is_absolute() or ".." in pure.parts or ":" in original)
    if unsafe:
        name = _safe_name(os.path.basename(source_path))
        digest = hashlib.sha256(content).hexdigest()[:16]
        relative = f"external/{digest}_{name}"
    else:
        relative = "/".join(part for part in pure.parts if part not in {"", "."})
        if not relative:
            name = _safe_name(os.path.basename(source_path))
            digest = hashlib.sha256(content).hexdigest()[:16]
            relative = f"external/{digest}_{name}"
    return f"assets/{relative}", relative


def export_project_bundle(project, out_path: str, include_renders: bool = True) -> str:
    """Write a self-contained .rpgpack ZIP and return its path.

    The project remains an editable `.bmap`; referenced external assets are
    copied into the archive and paths are rewritten relative to its `assets/`
    directory. Embedded uploads stay embedded. Rendered level PNGs are included
    by default so the pack is useful as an immediate hand-off as well.
    """
    data = project.to_dict()
    data["asset_store"] = "assets"
    asset_files: dict[str, str] = {}
    missing_assets: list[str] = []

    def pack_asset(asset_path: str) -> str:
        """Copy one referenced library image into the pack and return its path
        inside the pack ("" when it can't be found)."""
        source_path = project.resolve_asset(asset_path)
        if not os.path.isfile(source_path):
            missing_name = os.path.basename(asset_path.replace("\\", "/"))
            missing_assets.append(missing_name or "unresolved image")
            # Do not leak machine-specific absolute paths in a portable pack.
            return ""
        with open(source_path, "rb") as source:
            content = source.read()
        member, relative = _asset_member(asset_path, source_path, content)
        prior = asset_files.get(member)
        if prior and prior != source_path:
            # Same original relative path can refer to different stores.
            digest = hashlib.sha256(content).hexdigest()[:16]
            stem = _safe_name(os.path.basename(source_path))
            member = f"assets/external/{digest}_{stem}"
            relative = member[len("assets/"):]
        asset_files[member] = source_path
        return relative

    for level_data in data.get("levels", []):
        texture = str(level_data.get("backdrop_texture", "") or "")
        if texture:
            level_data["backdrop_texture"] = pack_asset(texture)
        for piece_data in level_data.get("pieces", []):
            if piece_data.get("embedded"):
                piece_data["asset_path"] = ""
                continue
            asset_path = str(piece_data.get("asset_path", "") or "")
            if not asset_path:
                continue
            piece_data["asset_path"] = pack_asset(asset_path)

    render_scale = min(1.0, 2048.0 / max(
        1.0, float(project.canvas_w), float(project.canvas_h)))
    project_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    manifest = {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_VERSION,
        "project": str(getattr(project, "name", "Untitled Map")),
        "project_file": PROJECT_MEMBER,
        "levels": [str(level.name) for level in project.levels],
        "assets": sorted(asset_files),
        "missing_assets": sorted(set(missing_assets)),
        "includes_renders": bool(include_renders),
        "render_scale": render_scale,
    }

    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix=".rpgpack-", suffix=".tmp", dir=parent)
        os.close(fd)
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            archive.writestr(PROJECT_MEMBER, project_bytes)
            archive.writestr("manifest.json", json.dumps(
                manifest, indent=2, ensure_ascii=False).encode("utf-8"))
            for member, source_path in asset_files.items():
                archive.write(source_path, member)

            if include_renders:
                # Import only on demand so model/bundle validation remains pure Python.
                from core import exporter
                with tempfile.TemporaryDirectory(prefix="rpgpack-renders-") as temp_dir:
                    for index, level in enumerate(project.levels, 1):
                        png_name = f"level_{index:02d}_{_safe_name(level.name, 'level')}.png"
                        png_path = os.path.join(temp_dir, png_name)
                        exporter.export_level_to_file(
                            project, level, png_path,
                            include_grid=bool(project.export_grid),
                            scale=render_scale,
                            include_node_borders=bool(project.export_node_borders),
                            include_zones=bool(project.export_zones),
                            include_centerlines=bool(getattr(project, "export_centerlines", False)),
                            include_guides=bool(getattr(project, "export_guides", False)),
                            include_coordinates=bool(getattr(project, "export_coordinates", False)))
                        archive.write(png_path, f"exports/{png_name}")
        os.replace(temp_path, out_path)
        temp_path = None
        return out_path
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def read_bundle_manifest(bundle_path: str) -> dict:
    """Read and validate the manifest without extracting the archive."""
    try:
        with zipfile.ZipFile(bundle_path, "r") as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise BundleError(f"Map pack manifest is missing or invalid: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != BUNDLE_FORMAT:
        raise BundleError("The archive is not an RPG Map Pack.")
    version = manifest.get("format_version")
    if not isinstance(version, int) or version < 1 or version > BUNDLE_VERSION:
        raise BundleError(f"Unsupported RPG Map Pack format version: {version!r}")
    return manifest


def unpack_project_bundle(bundle_path: str, destination_root: str) -> str:
    """Safely extract a bundle to a fresh local folder and return its `.bmap`.

    Extraction rejects traversal/absolute members and limits archive size. The
    application opens the extracted working copy; the original pack is never
    modified by Save/Auto-save.
    """
    if not zipfile.is_zipfile(bundle_path):
        raise BundleError("The selected file is not a valid RPG Map Pack archive.")
    read_bundle_manifest(bundle_path)
    os.makedirs(destination_root, exist_ok=True)
    stem = _safe_name(os.path.splitext(os.path.basename(bundle_path))[0], "map-pack")
    destination = os.path.join(destination_root, f"{stem}-{uuid.uuid4().hex[:8]}")
    os.makedirs(destination, exist_ok=False)
    total_size = 0
    try:
        with zipfile.ZipFile(bundle_path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise BundleError("The map pack contains too many files.")
            for info in infos:
                name = info.filename.replace("\\", "/")
                path = PurePosixPath(name)
                if (not name or path.is_absolute() or ".." in path.parts
                        or ":" in name or name.startswith("/")):
                    raise BundleError(f"Unsafe path in map pack: {info.filename}")
                if name not in {PROJECT_MEMBER, "manifest.json"} and not name.startswith(
                        ("assets/", "exports/")):
                    raise BundleError(f"Unexpected file in map pack: {info.filename}")
                total_size += max(0, info.file_size)
                if total_size > MAX_UNPACKED_BYTES:
                    raise BundleError("The map pack is larger than the 5 GB import limit.")
            names = {info.filename.replace("\\", "/") for info in infos}
            if PROJECT_MEMBER not in names:
                raise BundleError("The map pack does not contain project.bmap.")
            for info in infos:
                name = info.filename.replace("\\", "/")
                if info.is_dir():
                    os.makedirs(os.path.join(destination, *PurePosixPath(name).parts),
                                exist_ok=True)
                    continue
                target = os.path.abspath(os.path.join(
                    destination, *PurePosixPath(name).parts))
                if os.path.commonpath((os.path.abspath(destination), target)) != os.path.abspath(destination):
                    raise BundleError(f"Unsafe path in map pack: {info.filename}")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with archive.open(info, "r") as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
        return os.path.join(destination, PROJECT_MEMBER)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
