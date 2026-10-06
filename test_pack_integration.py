"""Integration test for the three high-resolution Mobius ZIP packs.

The packs stay outside Git. CI downloads them to a temporary directory and
passes that directory to this script so the real filenames and image headers
exercise import, classification, pairing, and both applicable generation paths.
"""
from __future__ import annotations

import argparse
import os
import tempfile

from core.asset_manager import AssetLibrary
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs-dir", required=True,
                        help="directory containing the downloaded ZIP archives")
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

        geomorph_pack = classify_geomorph_assets(pack_assets["geomorphs"])
        custom_pack = classify_geomorph_assets(pack_assets["custom_tiles"])
        symbols_pack = classify_geomorph_assets(pack_assets["symbols"])
        assert geomorph_pack["core"], (
            "The real Geomorphs ZIP yielded no compatible 100x100 Core modules.")
        assert geomorph_pack["overlays"], (
            "The real Geomorphs ZIP yielded no filename-paired overlays.")
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
        assert overlays, "No [Overlay] assets paired with a Core module."
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
        assert paired_core, "No Core/Overlay filename pairs were found."
        generated_categories = dict(categories)
        generated_categories["core"] = paired_core
        generated_categories["overlays"] = {
            item["id"]: overlays[item["id"]] for item in paired_core}

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
        assert len(overlay_pieces) == 9, result["counts"]
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
            f"{len(symbols)} Symbols; "
            f"{categories['pixels_per_square']:.1f} px/square; "
            f"3x3 generated ({len(symbol_pieces)} symbols placed); "
            f"Custom Tiles used by small/core generator: "
            f"{small_tile_count}/{custom_geomorph_count}.")


if __name__ == "__main__":
    main()
