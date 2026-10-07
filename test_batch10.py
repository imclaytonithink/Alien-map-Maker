"""Regression tests for the hand-building library's multi-label taxonomy."""
from __future__ import annotations

from core.asset_manager import Asset
from core.asset_taxonomy import (
    CATEGORY_GROUPS,
    CATEGORY_LABELS,
    SMART_CATEGORY_TREE,
    classify_asset_categories,
)


def make_asset(path: str, *, size=None, pixels=(1280, 1280), overlay=False) -> Asset:
    name = path.rsplit("/", 1)[-1]
    folder = path.rsplit("/", 1)[0] if "/" in path else "."
    return Asset(path=path, name=name, folder=folder, size=size,
                 is_overlay=overlay, width=pixels[0], height=pixels[1])


def main():
    assets = [
        make_asset(
            "RPG-Mobius-Geomorphs-Custom-Tiles-High-Res-Teal/100x100 Core/"
            "E101 [100x100] Module.png",
            size=(100, 100), pixels=(7199, 7199)),
        make_asset(
            "RPG-Mobius-Geomorphs-Custom-Tiles-High-Res-Teal/100x100 Core/"
            "Overlays/E101 [100x100] Module [Overlay].png",
            size=(100, 100), pixels=(7199, 7199), overlay=True),
        make_asset(
            "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal/Symbols/"
            "Medical/Medkit icon.png"),
        make_asset("starter/Rooms/room_floor_20x20.png", size=(20, 20)),
        make_asset("starter/Walls/bulkhead_20x20.png", size=(20, 20)),
        make_asset("starter/Corridors/hallway_20x20.png", size=(20, 20)),
        make_asset("starter/Doors/airlock_20x20.png", size=(20, 20)),
        make_asset("starter/Equipment/terminal_console.png"),
        make_asset("starter/Engineering/reactor_valve.png"),
        make_asset("starter/Furnishing/bunk_bed.png"),
        make_asset("starter/Cargo/storage_crate.png"),
        make_asset("starter/Organic/corpse_body.png"),
        make_asset("starter/Hazards/acid_spill.png"),
        make_asset("starter/Effects/fire_and_smoke.png"),
        make_asset("starter/Effects/glow_overlay.png", overlay=True),
        make_asset("starter/Uncategorized/strange_unrecognized_art.png"),
        make_asset("hand-build/Rooms/E201.png"),
        make_asset("hand-build/Furniture/E202.png"),
        make_asset("hand-build/Medical/E203.png"),
        make_asset("hand-build/Storage/E204.png"),
        make_asset("hand-build/Props/E205.png"),
        make_asset("real-pack/Core/Fire Control CIC.png"),
        make_asset("real-pack/Mess Halls/Galley & Mess Halls.png"),
        make_asset("real-pack/Small Craft/Grav Fighter.png"),
        make_asset("real-pack/Weaponry/Laser Cannon.png"),
        make_asset("real-pack/Core/E102 [100x100] Perimeter Catwalk.png",
                   size=(100, 100), pixels=(7199, 7199)),
        make_asset("real-pack/Bridge/Lightning Class Cruiser.png"),
        make_asset("real-pack/50x50 Build It/E800 [50x50].png",
                   size=(50, 50), pixels=(3600, 3600)),
        make_asset("real-pack/100x50 Edge/E410 [100x50] Holodeck.png",
                   size=(100, 50)),
        make_asset("real-pack/100x50 Edge/E442 Promenade - Bar.png",
                   size=(100, 50)),
        make_asset("real-pack/100x50 Edge/E451 Dorsal Barbettes.png",
                   size=(100, 50)),
        make_asset("real-pack/200x100 Megamorph/M107 Landing Pad or Helipad.png",
                   size=(200, 100)),
        make_asset("real-pack/Misc/A132 [150x300] Port (Concorde).png"),
        make_asset("real-pack/Misc/A133 [150x300] Port (Space Shuttle).png"),
        make_asset("real-pack/Misc/A134 [150x300] Port (XB-70 Valkyrie).png"),
        make_asset("real-pack/Misc/A136 [150x300] Port (Boeing 2707-300).png"),
        make_asset(
            "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal/Misc/Landscaping/"
            "Landscaping 001 [7x7].png"),
        # Manually reviewed Symbols sheets 015-016 contain full room modules,
        # structural tiles, and terrain art within the generic Misc directory.
        make_asset(
            "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal/Misc/"
            "Airlock 001 [10x10].png", size=(10, 10)),
        make_asset(
            "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal/Misc/"
            "Corridors 001 [20x20].png", size=(20, 20)),
        make_asset(
            "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal/Misc/"
            "Misc 008 Shooting Range [10x20].png", size=(10, 20)),
        make_asset(
            "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal/Misc/"
            "Misc 030 Sensor Holo Pit [15x15].png", size=(15, 15)),
        make_asset(
            "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal/Misc/"
            "Misc 020 Stellar Cartography [50x50].png", size=(50, 50)),
        make_asset(
            "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal/Misc/"
            "Misc 001 Incinerator [25x45].png", size=(25, 45)),
        make_asset(
            "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal/Misc/"
            "Misc 005 Trash Compactor [25x45].png", size=(25, 45)),
        make_asset(
            "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal/Misc/"
            "Misc 033 Solar Panels [10x20].png", size=(10, 20)),
        make_asset(
            "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal/Misc/"
            "Misc 034 Laundry [10x10].png", size=(10, 10)),
    ]

    tags = classify_asset_categories(assets)
    assert set(tags) == {asset.path for asset in assets}

    def has(filename: str, category: str) -> bool:
        path = next(asset.path for asset in assets
                    if asset.name == filename)
        return category in tags[path]

    assert has("E101 [100x100] Module.png", "core_modules")
    assert has("E101 [100x100] Module [Overlay].png", "overlays")
    assert has("E101 [100x100] Module [Overlay].png", "geomorph_overlays")
    assert not has("E101 [100x100] Module [Overlay].png", "core_modules")
    assert has("Medkit icon.png", "symbols")
    assert has("room_floor_20x20.png", "rooms")
    assert has("room_floor_20x20.png", "floors")
    assert has("bulkhead_20x20.png", "walls")
    assert has("hallway_20x20.png", "corridors")
    assert has("airlock_20x20.png", "doors")
    assert has("terminal_console.png", "controls")
    assert has("reactor_valve.png", "engineering")
    assert has("bunk_bed.png", "furniture")
    assert has("storage_crate.png", "storage")
    assert has("corpse_body.png", "organic")
    assert has("acid_spill.png", "hazards")
    assert has("fire_and_smoke.png", "fire_smoke")
    assert has("glow_overlay.png", "lighting")
    assert has("glow_overlay.png", "overlays")
    assert has("strange_unrecognized_art.png", "other")
    assert has("E201.png", "rooms")
    assert has("E202.png", "furniture")
    assert has("E203.png", "medical")
    assert has("E204.png", "storage")
    assert has("E205.png", "loose_props")
    assert has("Fire Control CIC.png", "controls")
    assert has("Fire Control CIC.png", "weapons")
    assert not has("Fire Control CIC.png", "hazards")
    assert not has("Fire Control CIC.png", "fire_smoke")
    assert has("Galley & Mess Halls.png", "rooms")
    assert has("Galley & Mess Halls.png", "food")
    assert not has("Galley & Mess Halls.png", "corridors")
    assert has("Grav Fighter.png", "vehicles")
    assert has("Laser Cannon.png", "weapons")
    assert has("E102 [100x100] Perimeter Catwalk.png", "floors")
    assert not has("Lightning Class Cruiser.png", "lighting")
    assert has("E800 [50x50].png", "modular_pieces")
    assert not has("E800 [50x50].png", "other")
    assert has("E410 [100x50] Holodeck.png", "rooms")
    assert has("E442 Promenade - Bar.png", "rooms")
    assert has("E442 Promenade - Bar.png", "food")
    assert has("E451 Dorsal Barbettes.png", "weapons")
    assert has("M107 Landing Pad or Helipad.png", "vehicles")
    assert has("M107 Landing Pad or Helipad.png", "floors")
    assert has("M107 Landing Pad or Helipad.png", "modular_pieces")
    # Manual review of the full packs found real aircraft stored under Misc;
    # visual category coverage must not depend on the original folder name.
    assert has("A132 [150x300] Port (Concorde).png", "vehicles")
    assert has("A133 [150x300] Port (Space Shuttle).png", "vehicles")
    assert has("A134 [150x300] Port (XB-70 Valkyrie).png", "vehicles")
    assert has("A136 [150x300] Port (Boeing 2707-300).png", "vehicles")
    assert has("Landscaping 001 [7x7].png", "landscaping")
    assert has("Landscaping 001 [7x7].png", "symbols")
    assert not has("Landscaping 001 [7x7].png", "loose_props")
    assert has("Airlock 001 [10x10].png", "doors")
    assert not has("Airlock 001 [10x10].png", "loose_props")
    assert has("Corridors 001 [20x20].png", "corridors")
    assert not has("Corridors 001 [20x20].png", "loose_props")
    assert has("Misc 008 Shooting Range [10x20].png", "weapons")
    assert has("Misc 008 Shooting Range [10x20].png", "rooms")
    assert not has("Misc 008 Shooting Range [10x20].png", "loose_props")
    assert has("Misc 030 Sensor Holo Pit [15x15].png", "rooms")
    assert has("Misc 020 Stellar Cartography [50x50].png", "rooms")
    assert has("Misc 020 Stellar Cartography [50x50].png", "controls")
    assert has("Misc 001 Incinerator [25x45].png", "rooms")
    assert has("Misc 001 Incinerator [25x45].png", "engineering")
    assert has("Misc 005 Trash Compactor [25x45].png", "rooms")
    assert has("Misc 005 Trash Compactor [25x45].png", "engineering")
    assert has("Misc 033 Solar Panels [10x20].png", "engineering")
    assert has("Misc 034 Laundry [10x10].png", "rooms")
    assert all("loose_props" not in tags[asset.path]
               for asset in assets if "/Misc/" in asset.path)

    # Pack/archive titles are not semantic tags for every contained asset.
    core_tags = tags[assets[0].path]
    assert "floors" not in core_tags
    assert "other" not in core_tags

    # Every virtual category has a display label and belongs to exactly one
    # expandable group; the taxonomy remains navigation, not file movement.
    category_ids = [category_id for _group, categories in SMART_CATEGORY_TREE
                    for category_id, _label in categories]
    assert len(category_ids) == len(set(category_ids))
    assert set(category_ids) == set(CATEGORY_LABELS)
    assert set(category_ids) == {
        category_id for category_ids in CATEGORY_GROUPS.values()
        for category_id in category_ids
    }

    print("Batch 10 library taxonomy checks passed.")


if __name__ == "__main__":
    main()
