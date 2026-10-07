"""Regression checks for folder imports that preserve generator-significant roots."""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import zipfile

from core.asset_manager import AssetLibrary
from core.generator import classify_geomorph_assets
from ui.branding import (bundled_asset_directory, bundled_asset_pack_paths,
                         default_asset_store_path, seed_bundled_assets)


def fake_png_header(width: int, height: int) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
            struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00")


def main():
    with tempfile.TemporaryDirectory() as temp:
        source_file = os.path.join(temp, "checkout", "ui", "library.py")
        source_store = os.path.abspath(os.path.join(
            os.path.dirname(source_file), "..", "asset_store"))
        assert default_asset_store_path(source_file) == source_store

        # A frozen one-file build must use durable per-user data, not the
        # temporary directory in which PyInstaller unpacks its executable.
        had_frozen = hasattr(sys, "frozen")
        previous_frozen = getattr(sys, "frozen", None)
        sys.frozen = True
        try:
            app_data = os.path.join(temp, "user-data")
            assert default_asset_store_path(source_file, app_data) == os.path.join(
                app_data, "asset_store")
        finally:
            if had_frozen:
                sys.frozen = previous_frozen
            else:
                del sys.frozen

        # Simulate PyInstaller's read-only payload path and check that seeding
        # copies its built-in art into durable storage without clobbering edits.
        had_meipass = hasattr(sys, "_MEIPASS")
        previous_meipass = getattr(sys, "_MEIPASS", None)
        bundle_root = os.path.join(temp, "frozen-payload")
        bundled = os.path.join(bundle_root, "sample_assets", "floors")
        os.makedirs(bundled)
        bundled_name = "room_20x20.png"
        with open(os.path.join(bundled, bundled_name), "wb") as image:
            image.write(fake_png_header(20, 20))
        pack_dir = os.path.join(bundle_root, "asset_packs")
        os.makedirs(pack_dir)
        bundled_zip = os.path.join(pack_dir, "geomorphs.zip")
        with zipfile.ZipFile(bundled_zip, "w") as archive:
            archive.writestr("100x100 Core/E001 [100x100] Module.png", b"image")
        sys.frozen = True
        sys._MEIPASS = bundle_root
        try:
            assert bundled_asset_directory(source_file) == os.path.join(
                bundle_root, "sample_assets")
            assert bundled_asset_pack_paths(source_file) == [bundled_zip]
            frozen_store = os.path.join(temp, "frozen-user-data", "asset_store")
            assert seed_bundled_assets(frozen_store, source_file) == 1
            copied_asset = os.path.join(frozen_store, "floors", bundled_name)
            with open(copied_asset, "wb") as image:
                image.write(b"user changes stay")
            assert seed_bundled_assets(frozen_store, source_file) == 0
            with open(copied_asset, "rb") as image:
                assert image.read() == b"user changes stay"
        finally:
            if had_frozen:
                sys.frozen = previous_frozen
            else:
                del sys.frozen
            if had_meipass:
                sys._MEIPASS = previous_meipass
            else:
                del sys._MEIPASS

        # In a source checkout, the tracked starter assets seed the ordinary
        # library path and subsequent startup does not overwrite the store.
        source_branding = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "ui", "branding.py")
        source_payload = bundled_asset_directory(source_branding)
        built_in_count = sum(
            1 for root, _dirs, files in os.walk(source_payload)
            for name in files if name.lower().endswith(
                (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")))
        source_seed_store = os.path.join(temp, "source-seed-store")
        assert seed_bundled_assets(source_seed_store, source_branding) == built_in_count
        assert built_in_count > 0
        assert seed_bundled_assets(source_seed_store, source_branding) == 0

        source_core = os.path.join(temp, "download", "100x100 Core")
        os.makedirs(source_core)
        filename = "E101 [100x100] Tractor Beam Control.png"
        with open(os.path.join(source_core, filename), "wb") as image:
            image.write(fake_png_header(7199, 7199))

        store = os.path.join(temp, "asset-store")
        library = AssetLibrary(store)
        imported = library.import_folder(source_core, preserve_root=True)
        assert imported == 1

        # Importing the Core directory itself must retain its name in the
        # asset path, otherwise geomorph classification cannot find the tile.
        asset = library.get(f"100x100 Core/{filename}")
        assert asset is not None
        categories = classify_geomorph_assets(library.assets)
        assert len(categories["core"]) == 1
        assert categories["core"][0]["path"] == asset.path

        # A second folder with the same leaf name is kept in a separate group,
        # rather than overwriting the first import.
        second_source = os.path.join(temp, "another-download", "100x100 Core")
        os.makedirs(second_source)
        second_name = "E102 [100x100] Engine Room.png"
        with open(os.path.join(second_source, second_name), "wb") as image:
            image.write(fake_png_header(7199, 7199))
        assert library.import_folder(second_source, preserve_root=True) == 1
        assert library.get(f"100x100 Core (2)/{second_name}") is not None
        assert library.get(f"100x100 Core/{filename}") is not None

        # Existing fixture imports can still merge a selected folder's
        # contents directly into the store by leaving preserve_root disabled.
        flat_store = os.path.join(temp, "flat-store")
        flat = AssetLibrary(flat_store)
        assert flat.import_folder(source_core) == 1
        assert flat.get(filename) is not None

    print("Batch 8 bundled-asset and folder-import checks passed.")


if __name__ == "__main__":
    main()
