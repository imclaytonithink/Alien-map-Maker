"""Generator roles, part 2: naming patterns distilled from a full manual
review of the RPG Mobius high-res packs (see role_summary.md)."""
from __future__ import annotations

from core.asset_manager import Asset
from core.asset_roles import ROLE_IDS, classify_roles
from core.project import parse_size_from_name


def make(name, folder="Pack/Symbols/Misc", pixels=None):
    # the real packs code sizes in the name *or* the folder ('100x50 Edge')
    size = parse_size_from_name(name) or parse_size_from_name(folder)
    if pixels is None:
        pixels = (max(size) * 72 - 1) if size else 600
    if not size:
        w = h = pixels
    else:
        scale = pixels / max(size)
        w = max(1, int(size[0] * scale))
        h = max(1, int(size[1] * scale))
    return Asset(path=f"{folder}/{name}", name=name, folder=folder, size=size,
                 is_overlay=False, width=w, height=h)


CASES = [
    # -- Small Craft trees: the craft are map symbols, their 'Modules, ...'
    #    folders hold interior rooms, and the grid plates are build-it pieces
    ("30-dTon Saucer.png", "Pack/Small Craft", "symbol"),
    ("Cargo 2.png", "Pack/Small Craft/60-dTon Runabout/Loadouts", "symbol"),
    ("Center Transporter Podium.png",
     "Pack/Small Craft/60-dTon Runabout/Modules, Mid", "room"),
    ("Port Armory.png", "Pack/Small Craft/60-dTon Runabout/Modules, Mid", "room"),
    ("Hex Background (180dpi).png", "Pack/Small Craft/60-dTon Runabout",
     "modular_piece"),
    # -- bracketed feature lists are not blank markers; a bare '(Empty)' is
    ("708 (2) Bridge & Cargo Bay (8 Stations, Empty Cargo Holds, Briefing).png",
     "Pack/100x100 End", "modular_piece"),
    ("306 Cargo Bay (Empty) - 10-dTon Runabouts (Engineering, Tech Stations).png",
     "Pack/100x50 Edge", "empty_room"),
    ("A101 Nose (Blank).png", "Pack/Custom Tiles/Misc", "ship_part"),
    ("B301 [100x50] Transition Corridor.png", "Pack/Custom Tiles/Misc",
     "empty_room"),
    # -- corner/edge/end folders beat ship words; 'Corner' in a name no
    #    longer demotes 100 ft core decks
    ("E579 [50x50] Starboard, Fuel.png", "Pack/Custom Tiles/Corner",
     "modular_piece"),
    ("E118 [100x100] Corner Space Lounge, Staterooms.png", "Pack/100x100 Core",
     "deck_plan"),
    # -- Symbols pack: doors, corridors (plurals!), vehicles, fittings, rooms
    ("Door 001 [10x30].png", "Pack/Symbols/Misc", "door"),
    ("Iris Valves 001 [5x5].png", "Pack/Symbols/Misc", "door"),
    ("Extendable Airlock 001 [5x10].png", "Pack/Symbols/Misc", "door"),
    ("Corridors 001 [20x20].png", "Pack/Symbols/Misc", "corridor"),
    ("Air-Raft 001 [20x20].png", "Pack/Symbols/Dock, Small Craft", "symbol"),
    ("Drop Capsule 001 [5x5].png", "Pack/Symbols/Drop Capsule", "symbol"),
    ("Work Pod 001 [5x10].png", "Pack/Symbols/Misc", "symbol"),
    ("Misc 033 Solar Panels [10x20].png", "Pack/Symbols/Misc", "symbol"),
    ("Ship's Locker 002 [5x5] Biohazard.png",
     "Pack/Symbols/Ship's Locker/Iconographic", "symbol"),
    ("Window 001 [0x5].png", "Pack/Symbols/Misc", "interior_part"),
    ("Stairs 001 [10x20].png", "Pack/Symbols/Misc", "interior_part"),
    ("Elevator 001 [20x20].png", "Pack/Symbols/Misc", "interior_part"),
    ("10' Railing [20x20] Corner.png", "Pack/Symbols/Catwalk Railing",
     "interior_part"),
    ("Medical Bed 001 [4x6].png", "Pack/Symbols/Furniture, Consoles, & Equipment",
     "interior_part"),
    ("Azhanti Style Bridge Console 001 [10x10].png", "Pack/Symbols/Bridge",
     "interior_part"),
    ("Counter 001 [2x15].png", "Pack/Symbols/Furniture, Consoles, & Equipment",
     "interior_part"),
    ("Piano [5x6].png", "Pack/Symbols/Furniture, Consoles, & Equipment",
     "interior_part"),
    ("Brig 001 [20x20].png", "Pack/Symbols/Brig & Security", "room"),
    ("Security 001 [20x20].png", "Pack/Symbols/Brig & Security", "room"),
    ("Armory 001 [10x20].png", "Pack/Symbols/Misc", "room"),
    ("Medical 047 [5x20] Decontamination.png", "Pack/Symbols/Lab", "room"),
    ("Food Service 001 [10x20].png", "Pack/Symbols/Galley & Mess", "room"),
    ("Empty Room 001 [5x5].png", "Pack/Symbols/Empty Room", "empty_room"),
    ("Dock 002 [15x50] 10-dTon Launch.png", "Pack/Symbols/Dock, Small Craft",
     "room"),
    ("Dock 018 [20x100] 50-dTon Launch.png", "Pack/Symbols/Dock, Small Craft",
     "deck_plan"),
    ("Engineering 112 [60x100].png", "Pack/Symbols/Engineering", "room"),
    ("Machinery 168 [9x100].png", "Pack/Symbols/Machinery", "room"),
    # -- full-size pattern & legend sheets at pack roots
    ("100x100 Checkerboard.png", "Pack", "other"),
    ("100x100 Iris Valves.png", "Pack/Symbols", "door"),
    ("Symbols & Abbreviations.png", "Pack/Symbols", "symbol"),
    # -- tram suite: stations and depots are rooms, cars are symbols,
    #    tracks are deck surfaces
    ("TR03 [100x50] (1) Tram Station - Cargo Platform.png", "Pack/Misc", "room"),
    ("TR01 [100x50] (1) Tram Station - Empty.png", "Pack/Misc", "empty_room"),
    ("TR05 [100x50] Tram Car (Passenger Standing).png", "Pack/Misc", "symbol"),
    ("TR01 [100x50] Tram Engine (Right with Cargo).png", "Pack/Misc", "symbol"),
    ("TR08 [100x50] Tram Repair Area.png", "Pack/Misc", "room"),
    ("TR09 [100x50] Tram Track & Engineering.png", "Pack/Misc", "deck_plan"),
    # -- V0xx elevators link decks like corridors; V1xx are rooms
    ("V05 [50x10] Elevator.png", "Pack/Misc", "corridor"),
    ("V101 [10x50] Lounge.png", "Pack/Misc", "room"),
    # -- 'High Passage' is a Traveller ticket class, not a corridor
    ("187 [100x100] High Passage Deck (4x Escape Pods, Galley, Lounge).png",
     "Pack/100x100 Core", "deck_plan"),
    # -- gangways are corridors even at full sheet length
    ("CB01 [100x50] Connecting Gangway.png", "Pack/Misc", "corridor"),
    ("CB06 [200x100] Connecting Gangway.png", "Pack/Misc", "corridor"),
]

# names that need a hand-set pixel size (no [WxH] in the name)
PIXELS = {
    "E700.png": 4199,
    "A800.png": 4199,
    "30-dTon Saucer.png": 600,
}

UNTITLED = [
    # untitled but coded high-resolution modules still follow their series
    ("E700.png", "Pack/Bridge, Pointed Nose, Air-Raft, Medical, Gunnery x2",
     "room"),
    ("A800.png", "Pack/Custom Tiles/Misc", "ship_part"),
]


def _check(name, folder, expected):
    asset = make(name, folder, pixels=PIXELS.get(name))
    info = classify_roles([asset])[asset.path]
    assert info.role in ROLE_IDS and info.reason and info.confidence
    if info.role != expected:
        return (name, expected, info.role, info.reason)
    return None


def main():
    wrong = [r for r in (_check(*case) for case in CASES + UNTITLED) if r]
    assert not wrong, "\n".join(map(str, wrong))

    # low-confidence results are exactly the ones a human should glance at
    asset = make("100x100 Checkerboard.png", "Pack")
    info = classify_roles([asset])[asset.path]
    assert info.confidence == "low" and "worth a look" in info.reason
    asset = make("E700.png", "Pack/Bridge", pixels=4199)
    info = classify_roles([asset])[asset.path]
    assert info.confidence == "low" and info.role == "room"

    print("Batch 14 asset-role checks passed.")


if __name__ == "__main__":
    main()
