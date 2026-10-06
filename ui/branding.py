"""Application labels and persistent user-data paths."""
from __future__ import annotations

import os
import shutil
import sys

SUPPORTED_BUILTIN_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")

APP_NAME = "SceneBoard"
APP_DESCRIPTOR = "The Map-Making Studio & Generator"
APP_TAGLINE = "Bring your assets. Generate a map. Refine every detail."
ALIEN_NAME = "MU-TH-UR 6000"

# Keep this stable when the display name changes so existing QSettings and
# QStandardPaths data (theme, autosave, and recovery files) remain in place.
SETTINGS_ID = "Map Studio"


def default_asset_store_path(source_file: str, app_data_dir: str = "") -> str:
    """Return the normal asset-store location for this kind of install.

    Source runs keep the checked-in demo library beside the program. A
    PyInstaller one-file executable unpacks into a temporary directory, so its
    writable library must instead live in the user's persistent app-data folder.
    """
    if getattr(sys, "frozen", False):
        if not app_data_dir:
            try:
                from PyQt6.QtCore import QStandardPaths
                app_data_dir = QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppDataLocation)
            except (ImportError, AttributeError):
                app_data_dir = ""
        if not app_data_dir:
            app_data_dir = os.path.join(os.path.expanduser("~"), ".map-studio")
        return os.path.abspath(os.path.join(app_data_dir, "asset_store"))
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(source_file)), "..", "asset_store"))


def _runtime_resource_root(source_file: str) -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(source_file)), ".."))


def bundled_asset_directory(source_file: str) -> str:
    """Locate the read-only starter art shipped alongside the application."""
    return os.path.join(_runtime_resource_root(source_file), "sample_assets")


def bundled_asset_pack_paths(source_file: str) -> list[str]:
    """Return high-resolution pack ZIPs embedded by the Windows build."""
    pack_dir = os.path.join(_runtime_resource_root(source_file), "asset_packs")
    if not os.path.isdir(pack_dir):
        return []
    return [os.path.join(pack_dir, name) for name in sorted(os.listdir(pack_dir))
            if name.lower().endswith(".zip") and
            os.path.isfile(os.path.join(pack_dir, name))]


def seed_bundled_assets(store_path: str, source_file: str) -> int:
    """Copy bundled starter art into a writable store without replacing user files.

    The executable's bundled directory is read-only and temporary for a
    PyInstaller one-file build. Copy the built-in images on demand into the
    persistent store; existing files are left untouched. Returns the number of
    files copied.
    """
    source_root = bundled_asset_directory(source_file)
    if not os.path.isdir(source_root):
        return 0

    copied = 0
    for dirpath, dirs, files in os.walk(source_root):
        dirs.sort()
        relative = os.path.relpath(dirpath, source_root)
        destination_dir = (store_path if relative == "." else
                           os.path.join(store_path, relative))
        for filename in sorted(files):
            if not filename.lower().endswith(SUPPORTED_BUILTIN_EXTS):
                continue
            destination = os.path.join(destination_dir, filename)
            if os.path.exists(destination):
                continue
            os.makedirs(destination_dir, exist_ok=True)
            shutil.copy2(os.path.join(dirpath, filename), destination)
            copied += 1
    return copied
