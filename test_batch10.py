"""The library's only organisation is the folder layout the assets arrived in.

A ZIP import keeps the archive's own directories; the store lists assets folder
by folder with names sorted; and nothing the app does re-sorts, re-files or
renames an asset. There is no keyword taxonomy, no automatic sorting and no
virtual category left in the code.
"""
from __future__ import annotations

import importlib
import os
import tempfile
import zipfile

from core.asset_manager import AssetLibrary, _scan_order_key


def fake_png(path, width=32, height=32):
    import struct
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
                     struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00")
    return path


def main():
    with tempfile.TemporaryDirectory() as temp:
        # ---- a ZIP keeps exactly the folders it carried -------------------
        archive = os.path.join(temp, "RPG-Pack.zip")
        with zipfile.ZipFile(archive, "w") as pack:
            pack.writestr("100x100 Core/E101 [100x100] Bridge.png", b"a")
            pack.writestr("100x100 Core/E102 [100x100] Engine.png", b"b")
            pack.writestr("Symbols/Misc/Crate 001.png", b"c")
            pack.writestr("Symbols/Misc/deep/nested/Crate 002.png", b"d")
            pack.writestr("readme.txt", b"not an image")

        store = os.path.join(temp, "store")
        library = AssetLibrary(store)
        report = library.import_zip(archive)
        assert report.imported == 4 and report.skipped_non_image == 1

        paths = [asset.path for asset in library.assets]
        assert paths == [
            "RPG-Pack/100x100 Core/E101 [100x100] Bridge.png",
            "RPG-Pack/100x100 Core/E102 [100x100] Engine.png",
            "RPG-Pack/Symbols/Misc/Crate 001.png",
            "RPG-Pack/Symbols/Misc/deep/nested/Crate 002.png",
        ], paths
        assert [asset.folder for asset in library.assets] == [
            "RPG-Pack/100x100 Core",
            "RPG-Pack/100x100 Core",
            "RPG-Pack/Symbols/Misc",
            "RPG-Pack/Symbols/Misc/deep/nested",
        ]
        # the listing is the folder tree itself: folders in name order, each
        # folder's own files before its subfolders
        assert paths == [asset.path for asset in
                         sorted(library.assets, key=_scan_order_key)], paths

        # ---- groups are the folders, in that same order -------------------
        assert library.groups() == [
            "RPG-Pack/100x100 Core",
            "RPG-Pack/Symbols/Misc",
            "RPG-Pack/Symbols/Misc/deep/nested",
        ], library.groups()
        # an explicit order is honoured, and folders not in it are appended
        reordered = library.groups(["RPG-Pack/Symbols/Misc/deep/nested"])
        assert reordered[0] == "RPG-Pack/Symbols/Misc/deep/nested"
        assert set(reordered) == set(library.groups())
        assert [asset.path for asset in
                library.assets_in_group("RPG-Pack/100x100 Core")] == paths[:2]

        # ---- nothing is filed anywhere else: search is over name/folder ---
        assert [asset.name for asset in library.search("crate")] == \
            ["Crate 001.png", "Crate 002.png"]
        assert len(library.search("Symbols")) == 2
        assert library.search("engine")[0].name == "E102 [100x100] Engine.png"

        # ---- a folder import keeps the folder you picked ------------------
        source = os.path.join(temp, "download", "100x100 Core")
        fake_png(os.path.join(source, "E201 [100x100] Galley.png"))
        assert library.import_folder(source, preserve_root=True) == 1
        assert library.get("100x100 Core/E201 [100x100] Galley.png") is not None
        # the same leaf folder from elsewhere stays separate, never merged
        other = os.path.join(temp, "elsewhere", "100x100 Core")
        fake_png(os.path.join(other, "E202 [100x100] Medbay.png"))
        assert library.import_folder(other, preserve_root=True) == 1
        assert library.get("100x100 Core (2)/E202 [100x100] Medbay.png") is not None
        assert library.get("100x100 Core/E201 [100x100] Galley.png") is not None

        # ---- the classifier modules are gone from the codebase ------------
        root = os.path.dirname(os.path.abspath(__file__))
        for removed in ("core.asset_taxonomy", "core.asset_roles",
                        "core.assembly", "core.generator"):
            assert not os.path.isfile(
                os.path.join(root, *removed.split("."))) , removed
            try:
                importlib.import_module(removed)
            except ModuleNotFoundError:
                pass
            else:
                raise AssertionError(f"{removed} should no longer exist")
        assert not hasattr(library, "roles")
        assert not hasattr(library, "set_role")

    print("Batch 10 ZIP-organisation checks passed.")


if __name__ == "__main__":
    main()
