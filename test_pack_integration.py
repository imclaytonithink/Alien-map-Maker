"""Integration test for the three high-resolution Mobius ZIP packs.

The packs stay outside Git. CI downloads them to a temporary directory and
passes that directory to this script so the real filenames and image headers
exercise import, classification, pairing, and both applicable generation paths.
"""
from __future__ import annotations

import argparse
from collections import Counter
import os
import tempfile
import traceback
import zipfile

from core.asset_manager import AssetLibrary
from core.asset_taxonomy import SMART_CATEGORY_TREE, classify_asset_categories
from core.generator import (classify_assets, classify_geomorph_assets, generate,
                            generate_geomorphs)


PACK_MARKERS = {
    "geomorphs": "geomorphs-geomorphs-high-res",
    "custom_tiles": "custom-tiles-high-res",
    "symbols": "symbols-high-res",
}


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


def _in_archive(assets, archive_path: str):
    """Return imported assets under AssetLibrary's archive-specific folder."""
    folder = os.path.splitext(os.path.basename(archive_path))[0]
    prefix = folder + "/"
    return [asset for asset in assets if asset.path.startswith(prefix)]


def _write_taxonomy_report(path, archives, pack_assets, tags):
    """Write a compact audit of real pack paths and their smart categories."""
    lines = [
        "# Real RPG-Mobius asset inventory and taxonomy",
        "",
        "Counts below are multi-label: one image may appear in several smart "
        "categories, but remains stored once at its original path.",
        "",
    ]
    for kind, archive_path in archives.items():
        archive_name = os.path.basename(archive_path)
        prefix = os.path.splitext(archive_name)[0] + "/"
        assets = pack_assets[kind]
        extensions = Counter()
        top_folders = Counter()
        with zipfile.ZipFile(archive_path) as archive:
            file_infos = [info for info in archive.infolist() if not info.is_dir()]
            for info in file_infos:
                member = info.filename.replace("\\", "/").strip("/")
                extension = os.path.splitext(member)[1].casefold() or "[none]"
                extensions[extension] += 1
                parts = member.split("/")
                top_folders[parts[0] if len(parts) > 1 else "[root]"] += 1

        lines.extend([
            f"## {kind.replace('_', ' ').title()}",
            "",
            f"- ZIP entries: {len(file_infos):,} ({len(assets):,} supported images imported)",
            "- Original file types: " + ", ".join(
                f"{extension}: {count:,}"
                for extension, count in sorted(extensions.items())),
            "- Internal top-level folders: " + ", ".join(
                f"`{folder}` ({count:,})"
                for folder, count in sorted(top_folders.items(),
                                            key=lambda pair: pair[0].casefold())),
            "",
            "### Smart-category counts and real filename examples",
            "",
        ])
        for group_name, categories in SMART_CATEGORY_TREE:
            lines.append(f"#### {group_name}")
            lines.append("")
            for category_id, label in categories:
                matching = [asset for asset in assets
                            if category_id in tags.get(asset.path, set())]
                lines.append(f"- **{label}:** {len(matching):,}")
                for asset in sorted(matching,
                                    key=lambda item: item.path.casefold())[:4]:
                    relative = asset.path[len(prefix):]
                    lines.append(f"  - `{relative}`")
            lines.append("")

        unclassified = [asset for asset in assets
                        if "other" in tags.get(asset.path, set())]
        lines.append(f"Unclassified examples ({len(unclassified):,} total):")
        for asset in sorted(unclassified,
                            key=lambda item: item.path.casefold())[:8]:
            lines.append(f"- `{asset.path[len(prefix):]}`")
        lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as output:
        output.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs-dir", required=True,
                        help="directory containing the downloaded ZIP archives")
    parser.add_argument("--taxonomy-report", default="",
                        help="optional path for a real-pack category audit")
    args = parser.parse_args()
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
        for kind, assets in pack_assets.items():
            assert len(assets) == import_counts[kind], (
                f"{kind}: imported {import_counts[kind]} images but scanned "
                f"{len(assets)} under its archive folder.")

        taxonomy_tags = classify_asset_categories(library.assets)
        if args.taxonomy_report:
            _write_taxonomy_report(
                args.taxonomy_report, archives, pack_assets, taxonomy_tags)

        geomorph_pack = classify_geomorph_assets(pack_assets["geomorphs"])
        custom_pack = classify_geomorph_assets(pack_assets["custom_tiles"])
        symbols_pack = classify_geomorph_assets(pack_assets["symbols"])
        assert geomorph_pack["core"], (
            "The real Geomorphs ZIP yielded no compatible 100x100 Core modules.")
        assert symbols_pack["symbols"], (
            "The real Symbols ZIP yielded no recognized Symbols assets.")

        # Custom Tiles can contain complete Core geomorphs, smaller floorplan
        # pieces, or both. Exercise every route the imported pack supports and
        # fail if none of its real assets can be fed to either generator.
        custom_small = classify_assets(pack_assets["custom_tiles"])
        custom_small_pool = custom_small["floor"] + custom_small["corridor"]
        small_tile_count = 0
        if custom_small_pool:
            tile_result = generate({
                "seed": 601006,
                "cell_size": 70,
                "region": (0, 0, 29, 29),
                "setting": "Starship",
                "layout": "Grid",
                "clutter": 0.2,
                "rooms": 6,
                "categories": custom_small,
            })
            small_tile_count = tile_result["counts"]["pieces"]
            assert small_tile_count > 0, (
                "The smaller-tile generator placed no imported Custom Tiles.")
            assert tile_result["connected"], tile_result["warnings"]

        custom_geomorph_count = 0
        if custom_pack["core"]:
            assert 250 <= custom_pack["pixels_per_square"] <= 350, (
                "Custom Tiles should be recognized as the 300 px/square "
                f"high-resolution set, got {custom_pack['pixels_per_square']:.1f}.")
            custom_result = generate_geomorphs({
                "seed": 601007,
                "cell_size": 70,
                "region": (0, 0, 59, 59),
                "mode": "new",
                "geomorph_grid": 1,
                "clutter": 0.0,
                "geomorph_categories": custom_pack,
            })
            custom_geomorph_count = custom_result["counts"]["geomorphs"]
            assert custom_geomorph_count == 1, custom_result["warnings"]
            assert custom_result["pieces"][0]["asset_path"].startswith(
                os.path.splitext(os.path.basename(archives["custom_tiles"]))[0]
                + "/")

        assert small_tile_count or custom_geomorph_count, (
            "No real Custom Tiles assets were recognized by either generator "
            "mode (small tiles or Core geomorphs).")

        categories = classify_geomorph_assets(library.assets,
                                              feet_per_square=5)
        core = categories["core"]
        overlays = categories["overlays"]
        symbols = categories["symbols"]
        assert core, "No 100x100 Core modules were detected across the real packs."
        assert symbols, "No high-resolution Symbols assets were detected."
        symbols_archive_prefix = (
            os.path.splitext(os.path.basename(archives["symbols"]))[0] + "/")
        assert any(item["path"].startswith(symbols_archive_prefix)
                   for item in symbols), (
            "The Symbols set did not contribute any assets to the generator.")
        assert 250 <= categories["pixels_per_square"] <= 350, (
            "Expected approximately 300 source pixels per five-foot square; "
            f"detected {categories['pixels_per_square']:.1f}.")
        paired_core = [item for item in core if item["id"] in overlays]
        overlay_candidates = [asset.path for asset in library.assets
                              if asset.is_overlay]
        if overlay_candidates and not paired_core:
            core_examples = [asset.path for asset in library.assets
                             if "core" in f"{asset.folder}/{asset.name}".casefold()][:5]
            raise AssertionError(
                f"Found {categories['unpaired_overlay_count']} overlay assets "
                "but could not pair any with a Core; examples: "
                f"overlays={overlay_candidates[:5]}; cores={core_examples}.")
        generated_categories = dict(categories)
        if paired_core:
            generated_categories["core"] = paired_core
            generated_categories["overlays"] = {
                item["id"]: overlays[item["id"]] for item in paired_core}
        else:
            # The published pack may have no overlay artwork. Exercise the
            # base geomorph generator in that case; optional layers stay empty.
            generated_categories["overlays"] = {}

        result = generate_geomorphs({
            "seed": 601006,
            "cell_size": 70,
            "region": (0, 0, 59, 59),
            "mode": "new",
            "geomorph_grid": 3,
            "clutter": 1.0,
            "geomorph_categories": generated_categories,
        })
        base_pieces = [piece for piece in result["pieces"]
                       if piece["layer_name"] == "Geomorphs"]
        overlay_pieces = [piece for piece in result["pieces"]
                          if piece["layer_name"] == "Overlays"]
        symbol_pieces = [piece for piece in result["pieces"]
                         if piece["layer_name"] == "Symbols"]
        assert len(base_pieces) == 9, result["counts"]
        expected_overlays = 9 if paired_core else 0
        assert len(overlay_pieces) == expected_overlays, result["counts"]
        assert result["connected"] is None  # prebuilt layouts aren't BFS-tested

        by_origin = {(piece["x"], piece["y"]): piece
                     for piece in base_pieces}
        assert len(by_origin) == 9, "Module placement created duplicate slots."
        for overlay in overlay_pieces:
            base = by_origin[(overlay["x"], overlay["y"])]
            assert overlay["rotation"] == base["rotation"]
            assert overlay["scale"] == base["scale"]

        map_cells = 60
        symbol_fits = any(
            max(item["w"] / item["pixels_per_square"],
                item["h"] / item["pixels_per_square"]) <= map_cells
            for item in symbols if item["pixels_per_square"] > 0)
        if symbol_fits:
            assert symbol_pieces, "Fitting Symbols assets were not placed."

        print(
            "Real-pack integration passed: "
            f"geomorph ZIP={import_counts['geomorphs']} PNGs, "
            f"custom ZIP={import_counts['custom_tiles']} PNGs, "
            f"Symbols ZIP={import_counts['symbols']} PNGs; "
            f"{len(core)} compatible Core modules; "
            f"{sum(map(len, overlays.values()))} paired overlay variants; "
            f"{categories['unpaired_overlay_count']} unmatched overlays; "
            f"{len(symbols)} Symbols; "
            f"{categories['pixels_per_square']:.1f} px/square; "
            f"3x3 generated ({len(symbol_pieces)} symbols placed); "
            f"Custom Tiles used by small/core generator: "
            f"{small_tile_count}/{custom_geomorph_count}.")


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
