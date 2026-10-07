"""Generator roles: names from the real RPG Mobius packs land in the right role."""
from __future__ import annotations

import os
import tempfile

from core.asset_manager import Asset
from core.asset_roles import (ROLE_IDS, classify_roles,
                              load_overrides, role_counts, save_overrides)
from core.project import parse_size_from_name


def make(name, folder="Pack/100x100 Core", pixels=None, overlay=False):
    size = parse_size_from_name(name)
    if pixels is None:
        pixels = (max(size) * 72 - 1) if size else 600
    w = pixels if not size else pixels
    h = pixels if not size or size[0] == size[1] else int(pixels * size[1] / size[0])
    return Asset(path=f"{folder}/{name}", name=name, folder=folder, size=size,
                 is_overlay=overlay, width=w, height=h)


CASES = [
    ("E101 [100x100] Tractor Beam Control.png", "Pack/100x100 Core", "deck_plan"),
    ("E757 [100x100] Reactor Hall, 37 Consoles.png", "Pack/100x100 Core", "deck_plan"),
    ("M101 [200x100] Tractor Beam Bay.png", "Pack/200x100 Mega", "deck_plan"),
    ("M006 [200x100] Flight Deck - Vertical.png", "Pack/200x100 Mega", "deck_plan"),
    ("B004 [200x100] Blank.png", "Pack/200x100 Mega", "empty_room"),
    ("M007 [200x100] Cargo Deck Empty.png", "Pack/200x100 Mega", "empty_room"),
    ("A123 [100x50] Nose, Fuel, 100' to 50' Transition Corridor.png", "Pack/Aero", "ship_part"),
    ("A124 [100x200] Port (Concorde).png", "Pack/Aero", "ship_part"),
    ("A126 [100x200] Starboard (XB-70 Valkyrie).png", "Pack/Aero", "ship_part"),
    ("A139 [50x200] Port (4x Gunnery).png", "Pack/Aero", "ship_part"),
    ("A129 [40x140] Transition to 50' for AF09.png", "Pack/Aero", "ship_part"),
    ("E758 [50x50] 12 Stations, Lounge.png", "Pack/50x50 Core", "room"),
    ("501 [50x50] Fuel Deck (Intake Scoop).png", "Pack/50x50 Core", "room"),
    ("E800 [50x50] .png", "Pack/Custom Tiles/Corner", "modular_piece"),
    ("Stateroom x12.png", "Pack/Symbols/Rooms", "room"),
    ("Prison Cells x10, Dbl Stateroom, Galley, Medical.png", "Pack/Symbols", "room"),
    ("Cryopod.png", "Pack/Symbols/Medical", "interior_part"),
    ("Checkerboard.png", "Pack/Symbols", "symbol"),
    ("Fern.png", "Pack/Symbols/Landscaping", "terrain"),
    ("E101 [100x100] Tractor Beam Control [Overlay].png", "Pack/100x100 Core/Overlays", "overlay"),
    ("room_100x100.png", "floors", "floor_tile"),
    ("wall_25x50.png", "walls", "wall_tile"),
    ("corridor_40x120.png", "floors", "corridor"),
    ("keyboard_40x15.png", "props", "interior_part"),
    ("computer_10x50.png", "props", "interior_part"),
    ("Mystery.png", "misc", "other"),
]


def main():
    wrong = []
    for name, folder, expected in CASES:
        pixels = None
        if "x" in name.lower() and not name.startswith(("room_", "wall_", "corridor_",
                                                        "keyboard_", "computer_")):
            pass
        asset = make(name, folder, pixels)
        if folder in ("floors", "walls", "props"):
            size = parse_size_from_name(name)
            asset.width, asset.height = (size or (64, 64))
        info = classify_roles([asset])[asset.path]     # tags come from the library taxonomy
        assert info.role in ROLE_IDS
        assert info.reason and info.confidence
        if info.role != expected:
            wrong.append((name, expected, info.role, info.reason))
    assert not wrong, "\n".join(map(str, wrong))

    # overrides win, are reported, and round-trip through the store folder
    a = make("Mystery.png", "misc")
    b = make("E101 [100x100] Tractor Beam Control.png")
    roles = classify_roles([a, b], {a.path: "room", b.path: "not-a-role"})
    assert roles[a.path].role == "room" and roles[a.path].overridden
    assert roles[a.path].auto_role == "other" and roles[a.path].confidence == "set by you"
    assert roles[b.path].role == "deck_plan" and not roles[b.path].overridden
    counts = role_counts(roles)
    assert counts["room"] == 1 and counts["deck_plan"] == 1
    with tempfile.TemporaryDirectory() as store:
        assert save_overrides(store, {a.path: "room"})
        assert load_overrides(store) == {a.path: "room"}
        assert save_overrides(store, {})
        assert load_overrides(store) == {}
    print("Batch 12 asset-role checks passed.")


if __name__ == "__main__":
    main()
