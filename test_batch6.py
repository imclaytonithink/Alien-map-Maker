"""Pure-Python checks for safe ZIP asset-library imports."""
from __future__ import annotations

import os
import stat
import struct
import tempfile
import zipfile

from core.asset_manager import AssetLibrary


def fake_png_header(width: int, height: int) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
            struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00")


def main():
    with tempfile.TemporaryDirectory() as temp:
        store = os.path.join(temp, "store")
        archive_path = os.path.join(temp, "Mobius Pack.zip")
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                r"100x100 Core\E101 [100x100] Tractor Beam Control.png",
                fake_png_header(7199, 7199))
            archive.writestr(
                r"Symbols\Battery\Battery 001 [20x20].png",
                fake_png_header(1800, 1800))
            archive.writestr("readme.txt", "documentation is not a map image")
            archive.writestr("../outside.png", fake_png_header(1, 1))
            archive.writestr(r"C:\outside.png", fake_png_header(1, 1))
            archive.writestr(r"\server\share.png", fake_png_header(1, 1))
            link = zipfile.ZipInfo("link.png")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "somewhere-else.png")
            archive.writestr(r"custom\duplicate.png", b"first image payload")
            archive.writestr("custom/duplicate.png", b"second image payload")

        library = AssetLibrary(store)
        report = library.import_zip(archive_path)
        assert report.imported == 4, report
        assert report.skipped_non_image == 1, report
        assert report.skipped_unsafe == 3, report
        assert report.skipped_symlinks == 1, report
        assert report.renamed_duplicates == 1, report
        assert len(library.assets) == 4

        geomorph = library.get(
            "Mobius Pack/100x100 Core/E101 [100x100] Tractor Beam Control.png")
        assert geomorph is not None
        assert geomorph.size == (100, 100)
        assert (geomorph.width, geomorph.height) == (7199, 7199)
        assert geomorph.folder == "Mobius Pack/100x100 Core"

        symbol = library.get(
            "Mobius Pack/Symbols/Battery/Battery 001 [20x20].png")
        assert symbol is not None and symbol.size == (20, 20)
        assert os.path.isfile(library.abs_path(symbol.path))
        assert not os.path.exists(os.path.join(temp, "outside.png"))

        repeated = library.import_zip(archive_path)
        assert repeated.imported == 0
        assert repeated.already_imported == 4
        assert len(library.assets) == 4

        second_path = os.path.join(temp, "Another Pack.zip")
        with zipfile.ZipFile(second_path, "w") as archive:
            archive.writestr("100x100 Core\\same.png", b"same name, different pack")
        second = library.import_zip(second_path)
        assert second.imported == 1
        assert library.get("Another Pack/100x100 Core/same.png") is not None
        assert len(library.assets) == 5

        # A changed archive with the same filename gets a new namespace, and
        # that changed version is still detected on a subsequent re-import.
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("new.png", fake_png_header(12, 8))
        updated = library.import_zip(archive_path)
        assert updated.imported == 1
        assert library.get("Mobius Pack (2)/new.png") is not None
        repeated_update = library.import_zip(archive_path)
        assert repeated_update.imported == 0
        assert repeated_update.already_imported == 1
        assert len(library.assets) == 6

        invalid_path = os.path.join(temp, "not-a-zip.zip")
        with open(invalid_path, "wb") as fh:
            fh.write(b"not a zip")
        try:
            library.import_zip(invalid_path)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid ZIP should be rejected")

    print("Batch 6 safe asset ZIP-import checks passed.")


if __name__ == "__main__":
    main()
