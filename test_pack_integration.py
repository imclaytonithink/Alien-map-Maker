"""Integration test for the three high-resolution Mobius ZIP packs.

The packs stay outside Git. CI downloads them to a temporary directory and
passes that directory to this script, so the real filenames and image headers
exercise the two things this app promises about them:

* importing a ZIP reproduces the archive's own folder layout exactly - that
  layout is the library's only organisation, nothing is re-sorted or re-filed;
* a map built from a selection of the real assets lands on the grid at the size
  its own name and pixels imply.

``--inventory-report`` writes a folder-by-folder audit of the real packs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import os
import re
import struct
import tempfile
import traceback
import zipfile

from core.asset_manager import SUPPORTED_EXTS, AssetLibrary, _normalize_archive_path
from core.mapbuilder import build_map, make_item


PACK_MARKERS = {
    "geomorphs": "geomorphs-geomorphs-high-res",
    "custom_tiles": "custom-tiles-high-res",
    "symbols": "symbols-high-res",
}

CELL = 70          # map pixels per grid square
FEET = 5           # feet per grid square


def find_pack_archives(directory: str) -> dict[str, str]:
    archives = [os.path.join(directory, name) for name in os.listdir(directory)
                if name.lower().endswith(".zip")]
    matches = {}
    for kind, marker in PACK_MARKERS.items():
        candidates = [path for path in archives
                      if marker in os.path.basename(path).casefold()]
        if len(candidates) != 1:
            raise AssertionError(
                f"Expected one {kind} high-resolution ZIP ({marker}), "
                f"found {len(candidates)}.")
        matches[kind] = candidates[0]
    if len(archives) != len(PACK_MARKERS):
        raise AssertionError(
            f"Expected exactly three pack ZIPs, found {len(archives)}.")
    return matches


def _header_png(width: int, height: int) -> bytes:
    """A minimal PNG: a valid IHDR with real dimensions but no pixel data.

    Import and the map builder only read image headers, so header-only PNGs
    are enough for a stand-in pack (and keep this dependency-free)."""
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" +
            struct.pack(">II", width, height) +
            b"\x08\x06\x00\x00\x00" + b"\x00\x00\x00\x00IEND\xaeB`\x82")


def create_stand_in_packs(directory: str) -> list[str]:
    """Write three synthetic packs shaped like the published high-res ZIPs.

    They exist so the integration test can run in a sandbox that cannot reach
    the real download host; CI supplies the genuine archives instead."""
    os.makedirs(directory, exist_ok=True)
    packs = {
        "RPG-Mobius-Geomorphs-Geomorphs-High-Res-Teal.zip": {
            **{f"100x100 Core/E0{i:02d} [100x100] Module {i}.png":
               _header_png(400, 400) for i in range(1, 17)},
            "100x100 Core/Variant/E999 [100x100] Module 99.png":
                _header_png(400, 400),
            "readme.txt": b"stand-in geomorph pack",
        },
        "RPG-Mobius-Custom-Tiles-High-Res-Teal.zip": {
            **{f"50x50 Tiles/A{i} [50x50] Tile.png": _header_png(200, 200)
               for i in range(1, 7)},
            **{f"25x25 Trim/B{i} [25x25] Trim.png": _header_png(100, 100)
               for i in range(1, 7)},
            "readme.txt": b"stand-in custom-tile pack",
        },
        "RPG-Mobius-Symbols-High-Res-Teal.zip": {
            **{f"Symbols/Storage/S{i} Locker.png": _header_png(140, 140)
               for i in range(1, 8)},
            **{f"Symbols/Misc/M{i} Sign.png": _header_png(140, 140)
               for i in range(1, 8)},
            "readme.txt": b"stand-in symbols pack",
        },
    }
    paths = []
    for name, members in packs.items():
        target = os.path.join(directory, name)
        with zipfile.ZipFile(target, "w") as archive:
            for member, blob in members.items():
                archive.writestr(member, blob)
        paths.append(target)
    return paths


def archive_folder(archive_path: str) -> str:
    return os.path.splitext(os.path.basename(archive_path))[0]


def _in_archive(assets, archive_path: str):
    """Return imported assets under AssetLibrary's archive-specific folder."""
    prefix = archive_folder(archive_path) + "/"
    return [asset for asset in assets if asset.path.startswith(prefix)]


def archive_image_members(archive_path: str) -> list[str]:
    """Supported image paths inside the ZIP, normalised the way import does."""
    members = []
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir() or info.filename.endswith(("/", "\\")):
                continue
            relative = _normalize_archive_path(info.filename)
            if relative and relative.lower().endswith(SUPPORTED_EXTS):
                members.append(relative)
    return members


def _folder_inventory(assets, prefix: str) -> Counter:
    counts: Counter = Counter()
    for asset in assets:
        relative = asset.path[len(prefix):]
        folder = os.path.dirname(relative).replace("\\", "/") or "[root]"
        counts[folder] += 1
    return counts


def _write_inventory_report(path, archives, pack_assets):
    """Write a compact audit of the real packs' own folder structure."""
    lines = [
        "# Real RPG-Mobius asset inventory",
        "",
        "Every asset is listed under the folder it arrived in. The app adds no",
        "categories of its own: these folders are the whole organisation, and",
        "the same tree is what the library shows.",
        "",
    ]
    for kind, archive_path in archives.items():
        prefix = archive_folder(archive_path) + "/"
        assets = pack_assets[kind]
        extensions = Counter()
        with zipfile.ZipFile(archive_path) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            for info in members:
                member = info.filename.replace("\\", "/").strip("/")
                extensions[os.path.splitext(member)[1].casefold() or "[none]"] += 1
        folders = _folder_inventory(assets, prefix)
        lines.extend([
            f"## {kind.replace('_', ' ').title()}",
            "",
            f"- ZIP entries: {len(members):,} "
            f"({len(assets):,} supported images imported)",
            "- Original file types: " + ", ".join(
                f"{extension}: {count:,}"
                for extension, count in sorted(extensions.items())),
            f"- Folders preserved: {len(folders):,}",
            "",
            "| Folder | Images | Example |",
            "| --- | --- | --- |",
        ])
        by_folder = {}
        for asset in assets:
            relative = asset.path[len(prefix):]
            by_folder.setdefault(
                os.path.dirname(relative).replace("\\", "/") or "[root]",
                []).append(os.path.basename(relative))
        for folder, count in sorted(folders.items(),
                                    key=lambda pair: pair[0].casefold()):
            example = sorted(by_folder[folder])[0]
            lines.append(f"| `{folder}` | {count:,} | `{example}` |")
        lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as output:
        output.write("\n".join(lines))


def core_modules(assets):
    """Real high-resolution deck modules, picked by the folder they came in."""
    chosen = []
    for asset in assets:
        folders = [part.casefold() for part in
                   asset.folder.replace("\\", "/").split("/")]
        if not any(re.fullmatch(r"\d+x\d+ core", part) for part in folders):
            continue
        item = make_item(asset, CELL, FEET)
        if item and item["cells_w"] >= 10 and item["cells_h"] >= 10:
            chosen.append(asset)
    return chosen


def rectangles(pieces):
    return [piece["_box"] for piece in pieces]


def assert_aligned_and_apart(pieces, label):
    for x, y, w, h in rectangles(pieces):
        assert abs(x / CELL - round(x / CELL)) < 1e-6, (label, x)
        assert abs(y / CELL - round(y / CELL)) < 1e-6, (label, y)
    rects = rectangles(pieces)
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            assert (a[0] + a[2] <= b[0] + 1e-6 or b[0] + b[2] <= a[0] + 1e-6
                    or a[1] + a[3] <= b[1] + 1e-6 or b[1] + b[3] <= a[1] + 1e-6), \
                f"{label}: two pieces overlap"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs-dir", required=True,
                        help="directory containing the downloaded ZIP archives")
    parser.add_argument("--inventory-report", default="",
                        help="optional path for a real-pack folder audit")
    parser.add_argument("--create-stand-ins", action="store_true",
                        help="write synthetic stand-in packs into --packs-dir "
                             "(for sandboxes that cannot download the real "
                             "ones) and exit")
    args = parser.parse_args()
    if args.create_stand_ins:
        create_stand_in_packs(args.packs_dir)
        print(f"Wrote stand-in packs to {args.packs_dir}")
        return
    archives = find_pack_archives(args.packs_dir)

    with tempfile.TemporaryDirectory(prefix="sceneboard-real-packs-") as temp:
        store = os.path.join(temp, "asset-store")
        library = AssetLibrary(store)
        import_counts = {}
        for kind, archive in archives.items():
            report = library.import_zip(archive, rescan=False)
            assert report.imported > 0, report
            assert report.skipped_corrupt == 0, report
            import_counts[kind] = report.imported
        library.scan(store)
        pack_assets = {
            kind: _in_archive(library.assets, archive)
            for kind, archive in archives.items()
        }

        # ---- the archive's own layout survives the import intact ----------
        shown_names = {asset.name.casefold() for asset in library.assets}
        for kind, archive in archives.items():
            assets = pack_assets[kind]
            assert len(assets) <= import_counts[kind], (
                f"{kind}: imported {import_counts[kind]} images but scanned "
                f"{len(assets)} under its archive folder.")
            members = archive_image_members(archive)
            expected = {archive_folder(archive) + "/" + member for member in members}
            actual = {asset.path for asset in assets}
            extra = sorted(actual - expected)[:5]
            assert not extra, (
                f"{kind}: shown paths that are not in the archive: {extra}")
            # A member that is not shown must be a same-named duplicate the
            # library folded away (one copy of a name is shown); nothing may
            # simply vanish.
            vanished = [member for member in sorted(expected - actual)
                        if os.path.basename(member).casefold() not in shown_names][:5]
            assert not vanished, (
                f"{kind}: images neither shown nor deduplicated: {vanished}")
            assert len(actual) + len(expected - actual) == len(members), (
                f"{kind}: the ZIP holds {len(members)} supported images but "
                f"{len(actual)} shown + {len(expected - actual)} deduplicated "
                "were accounted for.")
            # each asset's folder is its member's directory, unchanged
            for asset in assets:
                relative = asset.path[len(archive_folder(archive)) + 1:]
                assert asset.folder == (archive_folder(archive) + "/" +
                                        os.path.dirname(relative)).rstrip("/"), \
                    asset.path
        # the library's groups are exactly those folders, in the order the
        # archive held them (folders by name, a folder's own files first)
        seen = []
        for asset in library.assets:
            if asset.folder not in seen:
                seen.append(asset.folder)
        assert library.groups() == seen, library.groups()[:10]
        assert library.groups() == sorted(
            seen, key=lambda folder: [part.casefold() for part in folder.split("/")]), \
            "folders must be listed in the archive's own name order"

        if args.inventory_report:
            _write_inventory_report(args.inventory_report, archives, pack_assets)

        # ---- build a map from a selection of the real assets -------------
        cores = core_modules(library.assets)
        assert len(cores) >= 9, (
            f"Expected at least nine high-resolution Core modules across the "
            f"packs, found {len(cores)}.")
        nine = cores[:9]
        items = [make_item(asset, CELL, FEET) for asset in nine]
        assert all((item["cells_w"], item["cells_h"]) == (20, 20)
                   for item in items), \
            [ (item["cells_w"], item["cells_h"]) for item in items ]

        used_selections = []
        result = build_map({
            "selection": nine, "cell_size": CELL, "feet_per_square": FEET,
            "region": (0, 0, 59, 59), "layout": "grid", "seed": 601006,
        })
        assert len(result["pieces"]) == 9, result["counts"]
        assert result["used_cells"] == (60, 60), result["used_cells"]
        assert not result["warnings"], result["warnings"]
        assert_aligned_and_apart(result["pieces"], "real 3x3 assembly")
        assert {piece["asset_path"] for piece in result["pieces"]} == \
            {asset.path for asset in nine}
        # nine 20x20 decks tile a 60x60 map edge to edge
        origins = sorted((round(piece["x"] / CELL), round(piece["y"] / CELL))
                         for piece in result["pieces"])
        assert origins == sorted((x, y) for y in (0, 20, 40)
                                 for x in (0, 20, 40)), origins
        used_selections.append((result, nine))

        # the same kind of selection, scattered and rotated, stays on the grid
        picked = cores[:12]
        scattered = build_map({
            "selection": picked, "cell_size": CELL, "feet_per_square": FEET,
            "region": (0, 0, 159, 159), "layout": "scatter", "rotate": True,
            "seed": 601007,
        })
        assert scattered["counts"]["pieces"] == len(picked), scattered["counts"]
        assert {piece["asset_path"] for piece in scattered["pieces"]} == \
            {asset.path for asset in picked}
        assert_aligned_and_apart(scattered["pieces"], "real scatter")
        used_selections.append((scattered, picked))

        # small Symbols art fills an area when that is what is asked for
        symbols_prefix = archive_folder(archives["symbols"]) + "/"
        small = [asset for asset in library.assets
                 if asset.path.startswith(symbols_prefix)
                 and make_item(asset, CELL, FEET)
                 and make_item(asset, CELL, FEET)["cells_w"] <= 8]
        fill_pieces = 0
        if small:
            filled = build_map({
                "selection": small[:40], "cell_size": CELL,
                "feet_per_square": FEET, "region": (0, 0, 39, 39),
                "layout": "fill", "seed": 601008,
            })
            fill_pieces = filled["counts"]["pieces"]
            assert fill_pieces > 0, filled["warnings"]
            assert filled["used_cells"] == (40, 40), filled["used_cells"]
            assert_aligned_and_apart(filled["pieces"], "real fill")
            used_selections.append((filled, small[:40]))

        # nothing outside the selection is ever used
        for run, chosen in used_selections:
            allowed = {asset.path for asset in chosen}
            assert {piece["asset_path"] for piece in run["pieces"]} <= allowed

        print(
            "Real-pack integration passed: "
            f"geomorph ZIP={import_counts['geomorphs']} PNGs, "
            f"custom ZIP={import_counts['custom_tiles']} PNGs, "
            f"Symbols ZIP={import_counts['symbols']} PNGs; "
            f"{len(library.assets):,} assets in "
            f"{len(set(a.folder for a in library.assets)):,} preserved folders; "
            f"{len(cores)} Core modules; 3x3 assembled, "
            f"{scattered['counts']['pieces']} scattered, "
            f"{fill_pieces} Symbols tiles filled a 40x40 area.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        frames = traceback.extract_tb(exc.__traceback__)
        frame = next((item for item in reversed(frames)
                      if item.filename.endswith("test_pack_integration.py")), None)
        line = frame.lineno if frame else 1
        detail = " ".join(str(exc).split()) or type(exc).__name__
        if os.environ.get("GITHUB_ACTIONS") == "true":
            escaped = (detail.replace("%", "%25")
                       .replace("\r", "%0D").replace("\n", "%0A"))
            print(f"::error file=test_pack_integration.py,line={line},"
                  f"title=Real pack integration failed::{escaped}", flush=True)
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a", encoding="utf-8") as output:
                    output.write(f"## Real pack integration failed\n\n"
                                 f"`test_pack_integration.py:{line}`: "
                                 f"{detail}\n")
        else:
            print(f"Real pack integration failed at line {line}: {detail}",
                  flush=True)
        raise
