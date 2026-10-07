"""Verify the packaged asset ZIPs contain supported image files only."""
from __future__ import annotations

import os
import stat
import struct
import tempfile
import zipfile

from core.asset_manager import AssetLibrary, SUPPORTED_EXTS
from filter_asset_packs import filter_zip_to_supported_images


def fake_png_header(width: int = 32, height: int = 32) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
            struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00")


def main():
    with tempfile.TemporaryDirectory() as temp:
        source = os.path.join(temp, "mixed-pack.zip")
        filtered = os.path.join(temp, "filtered", "mixed-pack.zip")
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(r"100x100 Core\E101 [100x100] Module.png",
                             fake_png_header(7199, 7199))
            archive.writestr(r"Symbols\Battery\battery.png", fake_png_header())
            archive.writestr("readme.txt", "documentation")
            archive.writestr("source.psd", b"not a supported raster asset")
            archive.writestr("../outside.png", fake_png_header())
            archive.writestr(r"C:\outside.png", fake_png_header())
            link = zipfile.ZipInfo("link.png")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "target.png")
            archive.writestr(r"custom\duplicate.png", fake_png_header())
            archive.writestr("custom/duplicate.png", fake_png_header())

        report = filter_zip_to_supported_images(source, filtered)
        assert report["images"] == 4, report
        assert report["skipped_non_image"] == 2, report
        assert report["skipped_unsafe"] == 2, report
        assert report["skipped_symlink"] == 1, report
        assert report["renamed_duplicates"] == 1, report

        with zipfile.ZipFile(filtered, "r") as archive:
            names = archive.namelist()
            assert set(names) == {
                "100x100 Core/E101 [100x100] Module.png",
                "Symbols/Battery/battery.png",
                "custom/duplicate.png",
                "custom/duplicate_2.png",
            }, names
            assert all(name.lower().endswith(SUPPORTED_EXTS) for name in names)
            assert archive.testzip() is None

        # The normal runtime importer sees only supported image members too.
        library = AssetLibrary(os.path.join(temp, "store"))
        imported = library.import_zip(filtered)
        assert imported.imported == 4, imported
        assert imported.skipped_non_image == 0, imported
        assert len(library.assets) == 4

    print("Batch 9 image-only asset-pack filtering checks passed.")


if __name__ == "__main__":
    main()
