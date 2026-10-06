"""Pure-Python checks for the high-resolution geomorph generator mode."""
from __future__ import annotations

from core.asset_manager import Asset
from core.generator import (classify_assets, classify_geomorph_assets,
                            generate, generate_geomorphs)


def make_asset(path, name, folder, size, width, height, is_overlay=False):
    return Asset(path=path, name=name, folder=folder, size=size,
                 width=width, height=height, is_overlay=is_overlay)


def main():
    base = make_asset(
        "Geomorphs, Symbols, & Small Craft/100x100 Core/E101 [100x100] Tractor Beam Control.png",
        "E101 [100x100] Tractor Beam Control.png",
        "Geomorphs, Symbols, & Small Craft/100x100 Core", (100, 100), 7199, 7199)
    overlay = make_asset(
        "Geomorphs, Symbols, & Small Craft/100x100 Core/E101 [Overlay] [100x100] Tractor Beam Control.png",
        "E101 [Overlay] [100x100] Tractor Beam Control.png",
        "Geomorphs, Symbols, & Small Craft/100x100 Core", (100, 100), 7199, 7199, True)
    standard_base = make_asset(
        "Standard Geomorphs/100x100 Core/E101 [100x100] Tractor Beam Control.png",
        "E101 [100x100] Tractor Beam Control.png",
        "Standard Geomorphs/100x100 Core", (100, 100), 1439, 1439)
    standard_overlay = make_asset(
        "Standard Geomorphs/100x100 Core/E101 [Overlay] [100x100] Tractor Beam Control.png",
        "E101 [Overlay] [100x100] Tractor Beam Control.png",
        "Standard Geomorphs/100x100 Core", (100, 100), 1439, 1439, True)
    symbol = make_asset(
        "Geomorphs, Symbols, & Small Craft/Symbols/Battery/Battery 001 [20x20].png",
        "Battery 001 [20x20].png",
        "Geomorphs, Symbols, & Small Craft/Symbols/Battery",
        (20, 20), 1800, 1800)
    standard_symbol = make_asset(
        "Standard Symbols/Battery/Battery 001 [20x20].png",
        "Battery 001 [20x20].png", "Standard Symbols/Battery",
        (20, 20), 360, 360)
    archive_root_symbol = make_asset(
        "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal/Root asset [10x10].png",
        "Root asset [10x10].png",
        "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal",
        (10, 10), 900, 900)
    floor = make_asset(
        "Sample/floors/room_100x100.png", "room_100x100.png",
        "Sample/floors", (100, 100), 600, 600)

    detected = classify_geomorph_assets(
        [base, overlay, standard_base, standard_overlay, symbol,
         standard_symbol, archive_root_symbol, floor])
    assert len(detected["core"]) == 1
    assert detected["core"][0]["path"] == base.path
    assert detected["core"][0]["core_w"] == 20
    assert detected["core"][0]["core_h"] == 20
    assert detected["core"][0]["border_cells"] == 2
    assert detected["pixels_per_square"] > 299
    assert list(detected["overlays"]) == [detected["core"][0]["id"]]
    assert len(detected["overlays"][detected["core"][0]["id"]]) == 1
    assert detected["overlays"][detected["core"][0]["id"]][0]["path"] == overlay.path
    assert len(detected["symbols"]) == 2
    assert detected["symbols"][0]["w"] == symbol.width
    assert any(asset["path"] == archive_root_symbol.path
               for asset in detected["symbols"])
    assert detected["unpaired_overlay_count"] == 1

    # Whole Core tiles and the Symbols package are not misread as single-cell
    # floor/wall tiles, while the independent small-tile generator is intact.
    small_tiles = classify_assets(
        [base, overlay, standard_base, standard_overlay, symbol,
         standard_symbol, archive_root_symbol, floor])
    assert len(small_tiles["floor"]) == 1
    assert all(not pool for category, pool in small_tiles.items()
               if category != "floor")
    tile_result = generate({
        "seed": 9, "cell_size": 70, "region": (0, 0, 29, 29),
        "setting": "Starship", "layout": "Grid", "clutter": 0.2,
        "rooms": 6, "categories": small_tiles,
    })
    assert tile_result["counts"]["pieces"] > 0
    assert tile_result["connected"]

    result = generate_geomorphs({
        "seed": 23,
        "cell_size": 70,
        "region": (0, 0, 59, 59),
        "mode": "new",
        "geomorph_grid": 3,
        "clutter": 1.0,
        "geomorph_categories": detected,
    })
    base_pieces = [piece for piece in result["pieces"]
                   if piece["layer_name"] == "Geomorphs"]
    overlay_pieces = [piece for piece in result["pieces"]
                      if piece["layer_name"] == "Overlays"]
    symbol_pieces = [piece for piece in result["pieces"]
                     if piece["layer_name"] == "Symbols"]
    assert len(base_pieces) == 9
    assert len(overlay_pieces) == 9
    assert result["counts"]["geomorphs"] == 9
    assert result["counts"]["overlays"] == 9
    assert result["counts"]["symbols"] == len(symbol_pieces) > 0
    assert result["connected"] is None  # prebuilt-module paths aren't BFS-tested
    assert not result["warnings"]

    # Core art's transparent 2-square margin starts outside each core, while
    # neighboring 20-square cores still meet on the exact 20-square pitch.
    expected_origins = {-2 * 70, (20 - 2) * 70, (40 - 2) * 70}
    assert {round(piece["x"]) for piece in base_pieces} == expected_origins
    assert {round(piece["y"]) for piece in base_pieces} == expected_origins
    for piece in base_pieces + overlay_pieces:
        assert piece["rotation"] in (0, 90, 180, 270)
        assert abs(piece["w"] * piece["scale"] - 24 * 70) < 1
    base_by_pos = {(piece["x"], piece["y"]): piece for piece in base_pieces}
    for piece in overlay_pieces:
        base_piece = base_by_pos[(piece["x"], piece["y"])]
        assert piece["rotation"] == base_piece["rotation"]
        assert piece["scale"] == base_piece["scale"]

    # Fill-area mode places only whole modules that fit and reports the
    # reduced grid rather than cropping or distorting the source art.
    partial = generate_geomorphs({
        "seed": 5,
        "cell_size": 70,
        "region": (0, 0, 19, 39),
        "mode": "area",
        "geomorph_grid": 3,
        "clutter": 0,
        "geomorph_categories": detected,
    })
    assert partial["counts"]["geomorphs"] == 2
    assert partial["layout"] == "1x2 grid"
    assert partial["warnings"]

    print("Batch 7 geomorph assembly checks passed.")


if __name__ == "__main__":
    main()
