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


# SHA-256 of the placeholder images that earlier builds copied into every
# user's asset store. They are demo art, not user content, so they are removed
# automatically. Matching is by path *and* exact bytes, so anything the user
# edited, renamed or re-imported under another name is left alone.
DEMO_ASSET_HASHES = {
    "floors/corridor_40x120.png":
        "56d24c8aa1c79c1bc52efc07b86311926272043a570076c3242c266764c3ac45",
    "floors/room_100x100.png":
        "1c39da6b4b701b482b817b45154c84164d7a137d38cabd96e16bbcec90b44cab",
    "floors/room_200x100.png":
        "6f61d815f183e97b2521ceb14b0f27639554a4122bc04c92a2df059309274cab",
    "overlays/overlay_glow_100x100.png":
        "863ac871a43fd5d0ae1d14c08e15a17897cb9049a069d02bfa55353013271867",
    "overlays/overlay_hazard_40x120.png":
        "ffdc17f0d0f33ef558644db619011bfc21b23cc7e719ee703a6d4667063ea6a9",
    "props/computer_10x50.png":
        "13666ce3e9eadea63a1aa06c6b574ca792536342adc6ebf26a3195942ca14c12",
    "props/keyboard_40x15.png":
        "b2de87ee30f0f95e76be096c11b329b172c62bdec736348671c57c53758b1de4",
    "props/terminal_25x50.png":
        "a656b8892de69b66a619664d13f46f9ddb458ad868b48ad48e7e645853272ea3",
    "walls/wall_10x50.png":
        "7df77bb3b116911c1183ef4079591d1690ed26838e0c7639226bf022734bad53",
    "walls/wall_25x50.png":
        "0614cbeaaaa59f04ab2ce8333ecf5082d4602b152089ebe6c1d1af12e9499eed",
}


def prune_demo_assets(store_path: str) -> int:
    """Delete untouched placeholder demo images from an asset store.

    Returns how many files were removed; folders left empty are removed too.
    """
    import hashlib

    if not store_path or not os.path.isdir(store_path):
        return 0
    removed = 0
    for rel, digest in DEMO_ASSET_HASHES.items():
        target = os.path.join(store_path, *rel.split("/"))
        try:
            if not os.path.isfile(target):
                continue
            with open(target, "rb") as handle:
                if hashlib.sha256(handle.read()).hexdigest() != digest:
                    continue
            os.remove(target)
            removed += 1
            folder = os.path.dirname(target)
            if os.path.normcase(folder) != os.path.normcase(store_path) \
                    and not os.listdir(folder):
                os.rmdir(folder)
        except OSError:
            continue
    return removed
