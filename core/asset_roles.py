"""Generator roles: one clear job per asset.

The library's smart categories (core/asset_taxonomy.py) are overlapping *tags*
for browsing. The generator needs something stricter: each asset gets exactly
one **role** saying what it is for, so a generation strategy can pull the right
pool (full deck plans, small rooms, empty rooms, ship parts, loose interior
parts, ...). Roles are worked out from the file name, folder, name-coded size
(for example ``[100x100]`` is feet), image pixel size and the library tags, and
can always be overridden by hand. Overrides are saved with the asset store.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from core import generator
from core.asset_taxonomy import _ROOM_WORDS, classify_asset_categories

# (role id, short label, what it is, how the generator uses it)
ROLE_DEFS = (
    ("deck_plan", "Deck plans & large modules",
     "Full-size decks of 100 ft or more on a side with interiors drawn in.",
     "Main building blocks when assembling a map."),
    ("room", "Room modules",
     "Self-contained rooms around 50 ft (quarters, labs, stations...).",
     "Tiled together to build new zones, or mixed with deck plans."),
    ("empty_room", "Empty rooms",
     "Blank shells or empty variants of rooms and decks.",
     "Fillers between rooms, or rooms to furnish with interior parts."),
    ("ship_part", "Ship parts & hull sections",
     "Noses, port/starboard sections, transitions, wings and other hull pieces.",
     "Placed along the edges of an assembly as the ship's outer shell."),
    ("corridor", "Corridors & connectors",
     "Corridors, passages, junctions and connecting pieces.",
     "Links rooms together."),
    ("modular_piece", "Build-it pieces (corners, edges, ends)",
     "Corner, edge, end and base-plate parts of the modular tile sets.",
     "Not used automatically; for hand building."),
    ("floor_tile", "Floor tiles",
     "Small floor, deck, grating or room-fill tiles (about one square).",
     "Fills room interiors in tile-by-tile mode."),
    ("wall_tile", "Wall tiles",
     "Small wall, bulkhead or hull tiles.",
     "Outlines rooms in tile-by-tile mode."),
    ("door", "Doors & hatches",
     "Doors, hatches and airlocks.",
     "Placed in walls where rooms connect."),
    ("interior_part", "Interior parts & props",
     "Furniture, consoles, machinery, crates and other small interior items.",
     "Furnishes rooms."),
    ("overlay", "Overlays & effects",
     "Transparent overlay variants, glows, spills and fire/smoke.",
     "Optional layer on top of a module or a floor."),
    ("symbol", "Symbols & markers",
     "Tokens, icons and map symbols.",
     "Optional scatter on top of the map."),
    ("terrain", "Terrain & vegetation",
     "Landscaping, plants and outdoor ground.",
     "Outdoor decoration."),
    ("other", "Unsorted",
     "Nothing recognisable in the name, folder or size.",
     "Ignored by the generator until you give it a role."),
)
ROLE_IDS = tuple(role[0] for role in ROLE_DEFS)
ROLE_LABELS = {role[0]: role[1] for role in ROLE_DEFS}
ROLE_ABOUT = {role[0]: role[2] for role in ROLE_DEFS}
ROLE_USE = {role[0]: role[3] for role in ROLE_DEFS}

OVERRIDES_FILE = ".asset_roles.json"

_BLANK_WORDS = ("blank", "empty", "vacant", "bare", "unfurnished")
_SHIP_WORDS = ("nose", "starboard", "fuselage", "hull", "nacelle", "wing",
               "wings", "tail", "bow", "stern", "prow", "transition", "pylon",
               "engine pod")
_PORT_CONTEXT = ("concorde", "shuttle", "valkyrie", "boeing", "lockheed",
                 "gunnery", "xb 70", "aircraft", "ship")
_CORRIDOR_WORDS = ("corridor", "hallway", "passage", "passageway", "tunnel",
                   "junction", "intersection", "connector", "gangway")
# 'passage' alone would also match Traveller's "High Passage" ticket class,
# so that match is vetoed when the phrase is present (see classify_role).
_CORRIDOR_EXCEPTIONS = {"passage": ("high passage",)}
_DOOR_WORDS = ("door", "hatch", "airlock", "portal", "gate", "iris valve")
_MODULAR_FOLDER_WORDS = {"corner", "corners", "edge", "edges", "end", "ends",
                         "baseplate", "baseplates", "endcap"}
_PART_TAGS = {"controls", "engineering", "medical", "furniture", "storage",
              "vehicles", "weapons", "food", "loose_props", "organic"}
_LANDSCAPE_TAGS = {"landscaping"}

# Room words that only the generator roles care about (the taxonomy's
# browsing tags stay untouched): Mobius pack folder/room vocabulary.
_ROLE_ROOM_EXTRA = ("armory", "armoury", "brig", "security", "medical",
                    "bunkroom", "dock", "food service")

# Whole-object tokens: a complete craft (or stand-alone equipment sprite
# like a solar panel) is a map symbol, not room content. Matched as head
# nouns (start of the name after the code/size), plural-safe.
_VEHICLE_HEADS = ("air raft", "airraft", "air-raft", "fighter", "tram car",
                  "tram engine", "drop capsule", "work pod", "liftbot",
                  "grav bike", "grav tank", "g carrier", "gcarrier", "atv",
                  "mech", "saucer", "runabout", "gunboat", "dropship",
                  "starship", "xboat", "escape pod", "lifeboat", "pinnace",
                  "solar panel")
# Vehicles that count as symbols even at full sheet scale outside the
# Symbols pack (the Misc tram sprites are [100x50]).
_BIG_VEHICLE_HEADS = ("tram car", "tram engine")

# Equipment-bay heads: in the Symbols pack these name room-scale strips
# ('Engineering 112 [60x100]') rather than loose props.
_EQUIPMENT_HEADS = ("cargo", "weaponry", "sensors", "engineering", "machinery",
                    "fuel", "battery", "computer", "storage", "utility")

# Fixed interior fittings drawn as individual pieces in the Symbols pack.
_FIXTURE_WORDS = ("window", "stairs", "stairway", "railing", "catwalk",
                  "elevator", "robot", "robotics", "sensor probe", "banana",
                  "counter", "piano")

# Furniture compounds: the first word would otherwise read as a room word
# ("Medical" bed, "Bridge" console, "Gym" treadmill...).
_FURNITURE_COMPOUNDS = ("medical bed", "bridge console", "gaming table",
                        "gym treadmill", "exercise bike", "slot machine",
                        "washer dryer", "desk keyboard", "banana for scale")


@dataclass(frozen=True)
class RoleInfo:
    role: str
    confidence: str          # "high" | "medium" | "low" | "set by you"
    reason: str
    auto_role: str           # what the rules chose, even if overridden
    overridden: bool = False


def _words(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", re.sub(
        r"([a-z0-9])([A-Z])", r"\1 \2", text).casefold()))


def _plural(term: str) -> str:
    """Naive plural so 'Corridors 001' still matches the word 'corridor'."""
    if term.endswith("y") and len(term) > 1 and term[-2] not in "aeiou":
        return term[:-1] + "ies"
    if term.endswith(("s", "x", "z", "ch", "sh")):
        return term + "es"
    return term + "s"


def _has_any(words: tuple[str, ...], terms) -> str | None:
    joined = " " + " ".join(words) + " "
    for term in terms:
        for form in (term, _plural(term)):
            if f" {form} " in joined:
                return term
    return None


def _words_outside_parens(stem: str) -> tuple[str, ...]:
    return _words(re.sub(r"\([^)]*\)", " ", stem))


def _head_words(words: tuple[str, ...]) -> tuple[str, ...]:
    """Words of the name's head noun: skip a leading code ('TR01', 'E700'),
    [WxH] size, bare numbers and 'Misc 033'-style labels so
    'TR01 [100x50] Tram Car (Cargo)' -> ('tram', 'car')."""
    out = list(words)
    while out:
        first = out[0]
        if re.fullmatch(r"\d+x\d+|\d+", first):
            out.pop(0)
            continue
        if re.fullmatch(r"[a-z]{0,2}\d{2,4}", first):
            out.pop(0)
            continue
        if (first in ("misc", "miscellaneous") and len(out) > 1
                and out[1].isdigit()):
            out.pop(0)
            out.pop(0)
            continue
        break
    return tuple(out)


def _starts_with_any(head: tuple[str, ...], terms) -> str | None:
    joined = " ".join(head) + " "
    for term in terms:
        for form in (term, _plural(term)):
            if joined.startswith(form + " "):
                return term
    return None


def _vetoed(term: str, words: tuple[str, ...]) -> bool:
    """True when a corridor-word match is a known false positive ('High
    Passage' is a Traveller ticket class, not a corridor)."""
    joined = " " + " ".join(words) + " "
    return any(f" {phrase} " in joined
               for phrase in _CORRIDOR_EXCEPTIONS.get(term, ()))


def feet_longest_side(asset) -> int:
    """Longest side in feet when the name codes a size AND the image is a
    high-resolution deck (so ``[100x100]`` means feet, not pixels)."""
    size = generator._asset_size(asset)
    if not size:
        return 0
    pixels = max(getattr(asset, "width", 0), getattr(asset, "height", 0))
    if pixels and pixels < 700:
        return 0                # a small image named like "room_100x100": pixels
    return max(size)


def name_code(asset) -> tuple[str, int] | None:
    """('E', 101) for names such as 'E101 [100x100] ...'; ('', 501) for '501 ...'."""
    match = re.match(r"^\s*([a-z]{0,2})(\d{2,4})\b", os.path.splitext(asset.name)[0],
                    re.IGNORECASE)
    return (match.group(1).lower(), int(match.group(2))) if match else None


def classify_role(asset, tags: set[str] | None = None) -> RoleInfo:
    """Choose the generator role for one asset from its name, folder and size."""
    tags = tags or set()
    stem = os.path.splitext(asset.name)[0]
    name_words = _words(stem)
    folder_words = _words(getattr(asset, "folder", ""))
    words = name_words + folder_words
    outside_words = _words_outside_parens(stem)     # feature lists live in (...)
    longest = feet_longest_side(asset)
    code = name_code(asset)
    prefix = code[0] if code else ""
    pixels = max(getattr(asset, "width", 0), getattr(asset, "height", 0))
    small_image = bool(pixels) and pixels < 700

    def result(role, confidence, reason):
        return RoleInfo(role, confidence, reason, role)

    def room_word_in(ws):
        joined = " " + " ".join(ws) + " "
        for source in (_ROOM_WORDS, _ROLE_ROOM_EXTRA):
            for term in source:
                for form in (term, _plural(term)):
                    if f" {form} " in joined:
                        return term
        return None

    room_word = room_word_in(outside_words)
    head = _head_words(outside_words)

    # -- overlays first: they pair with a module and are never a module
    if generator._is_geomorph_overlay(asset) or getattr(asset, "is_overlay", False):
        return result("overlay", "high", "Marked as an overlay in its name or folder")

    in_symbols = generator._is_symbol_pack_asset(asset)

    # -- landscaping
    if (tags & _LANDSCAPE_TAGS) and not room_word:
        return result("terrain", "high", "Landscaping / vegetation keywords")

    # -- A/AF-series hull sections: 'A101 Nose', 'AF07 Gray, Port (...)'
    shipish = _has_any(outside_words, _SHIP_WORDS) or _has_any(
        outside_words, ("port",))
    if prefix in ("a", "af") and shipish:
        return result("ship_part", "high", "A/AF-series hull section")

    # -- empty rooms / blank shells. 'Empty' inside a longer bracket like
    # '(Empty Bays)' is a feature list, not a blank marker; a bare '(Empty)'
    # suffix is one.
    blank = _has_any(outside_words, _BLANK_WORDS)
    bare_variant = re.search(r"\(\s*(blank|empty|vacant)\s*\)", stem, re.IGNORECASE)
    big_named_deck = longest >= 150 and prefix != "b"   # Megamorph-scale
    if ((blank or bare_variant) and not big_named_deck
            and (longest >= 50 or (not longest and not small_image))):
        return result("empty_room", "high",
                      f"Name contains '{blank or bare_variant.group(1)}'")
    if prefix == "b" and code and longest >= 50:
        return result("empty_room", "medium", "Code prefix B (blank decks)")

    # -- small craft trees: the craft themselves are map symbols, their
    # 'Modules, ...' folders hold interior rooms
    folder_parts = [p.strip() for p in re.split(r"[\\/]", getattr(asset, "folder", ""))]
    sc = next((i for i, p in enumerate(folder_parts)
               if p.casefold() == "small craft"), None)
    if sc is not None:
        tail = folder_parts[sc + 1:]
        if any(p.casefold().startswith("modules") for p in tail):
            return result("room", "medium", "Small-craft interior module")
        if _has_any(name_words, ("hex background", "square background")):
            return result("modular_piece", "low",
                          "Grid background plate - worth a look")
        return result("symbol", "medium", "Complete small craft / vehicle")

    # -- full-size pattern & legend sheets at pack roots
    if _has_any(name_words, ("checkerboard",)) and longest >= 50:
        return result("other", "low", "Full-size pattern sheet - worth a look")
    if _has_any(name_words, ("iris valve",)) and longest >= 50:
        return result("door", "low", "Full-size valve sheet - worth a look")
    if _has_any(name_words, ("symbols abbreviations",)):
        return result("symbol", "low", "Legend sheet - worth a look")

    # -- tram bits: stations and depots are rooms, the cars are symbols
    if _has_any(outside_words, ("tram station",)) and longest <= 100:
        return result("room", "medium", "Tram station")
    if (room_word and _has_any(outside_words, ("tram",)) and longest <= 100
            and not _has_any(outside_words, ("tram track",))):
        return result("room", "medium", "Tram-room module")
    if _starts_with_any(head, _BIG_VEHICLE_HEADS):
        return result("symbol", "medium", "Vehicle sprite")

    # -- V0xx vertical-circulation modules (elevators link decks like corridors)
    if prefix == "v" and code and code[1] < 100:
        return result("corridor", "high", "V-series vertical circulation")

    # -- modular build-it pieces (corner / edge / end / base plate folders)
    if _MODULAR_FOLDER_WORDS & set(folder_words):
        return result("modular_piece", "medium",
                      "Corner / edge / end / base-plate part")

    # -- symbol pack contents
    if in_symbols and longest < 50:
        if any("iconographic" in p.casefold()
               for p in folder_parts):
            return result("symbol", "high", "Iconographic sheet item")
        if blank or bare_variant:
            return result("empty_room", "high",
                          f"Name contains '{blank or bare_variant.group(1)}'")
        door = _has_any(name_words, _DOOR_WORDS)
        if door:
            return result("door", "high", f"Name contains '{door}'")
        corridor = _has_any(words, _CORRIDOR_WORDS)
        if corridor and not _vetoed(corridor, words):
            return result("corridor", "high", f"Name or folder says '{corridor}'")
        vehicle = _starts_with_any(head, _VEHICLE_HEADS)
        if vehicle:
            return result("symbol", "medium", "Vehicle token")
        fixture = _has_any(name_words, _FIXTURE_WORDS)
        if fixture:
            return result("interior_part", "medium",
                          f"Interior fitting ('{fixture}')")
        furniture = _has_any(head, _FURNITURE_COMPOUNDS)
        if furniture:
            return result("interior_part", "medium",
                          f"Furniture ('{furniture}')")
        if room_word:
            return result("room", "medium", "Room layout inside the Symbols pack")
        if tags & _PART_TAGS:
            return result("interior_part", "medium",
                          "Equipment or furniture in the Symbols pack")
        return result("symbol", "high", "Item from the Symbols pack")

    # -- room-scale equipment strips in the Symbols pack ('Engineering 112
    # [60x100]'), too big for the branch above but not whole decks
    if (in_symbols and longest >= 50 and not room_word
            and _starts_with_any(head, _EQUIPMENT_HEADS)):
        return result("room", "medium", "Equipment bay strip in the Symbols pack")

    # -- corridors
    corridor = _has_any(words, _CORRIDOR_WORDS)
    if corridor and not _vetoed(corridor, words) and (
            longest <= 100 or corridor == "gangway"):
        return result("corridor", "high", f"Name or folder says '{corridor}'")

    # -- doors
    door = _has_any(name_words, _DOOR_WORDS)
    if door and (longest <= 25 or small_image):
        return result("door", "high", f"Name contains '{door}'")

    # -- ship exterior parts
    ship = _has_any(name_words, _SHIP_WORDS)
    port = _has_any(name_words, ("port",))
    if ship and (longest >= 25 or not longest or prefix == "a"):
        return result("ship_part", "high" if prefix == "a" or longest else "medium",
                      f"Hull/ship keyword '{ship}'")
    if port and (prefix == "a" or _has_any(words, _PORT_CONTEXT)):
        return result("ship_part", "high" if prefix == "a" else "medium",
                      "Port/starboard hull section")

    # -- by size: the high-resolution decks and rooms
    if longest >= 100:
        reason = f"{longest} ft deck" + (" with room words" if room_word else "")
        return result("deck_plan", "high" if (room_word or code) else "medium", reason)
    if longest >= 50:
        return result("room", "high" if (room_word or code) else "medium",
                      f"{longest} ft module" + (" with room words" if room_word else ""))

    # -- untitled but coded high-resolution modules ('E700.png') still
    # belong to their series
    if code and pixels >= 700:
        role = {"a": "ship_part", "b": "empty_room"}.get(prefix, "room")
        return result(role, "low",
                      f"Untitled {prefix.upper() or 'numbered'} module "
                      "(high-resolution image) - worth a look")

    # -- small tiles and parts
    category = generator._category(asset.name)
    if category == "floor" and not (tags & _PART_TAGS):
        return result("floor_tile", "medium", "Floor / deck keyword on a small image")
    if category == "wall":
        return result("wall_tile", "medium", "Wall / bulkhead keyword on a small image")
    if category == "door":
        return result("door", "medium", "Door keyword on a small image")
    if category == "corridor":
        return result("corridor", "medium", "Corridor keyword on a small image")
    if category == "hazard":
        return result("overlay", "medium", "Hazard / spill keyword")
    if category in ("wall_fixture", "floor_fixture") or tags & _PART_TAGS:
        return result("interior_part", "medium", "Furniture / equipment keyword")
    if room_word:
        return result("room", "low", "Room keyword on a small image")
    return result("other", "low", "No recognisable keyword or size")


def classify_roles(assets, overrides: dict | None = None,
                   tags_by_path: dict | None = None) -> dict:
    """{asset path: RoleInfo} for every asset, honouring manual overrides.
    ``tags_by_path`` may be passed to reuse an existing taxonomy pass."""
    assets = list(assets)
    overrides = overrides or {}
    if tags_by_path is None:
        tags_by_path = classify_asset_categories(assets)
    out = {}
    for asset in assets:
        auto = classify_role(asset, tags_by_path.get(asset.path, set()))
        forced = overrides.get(asset.path)
        if forced in ROLE_IDS:
            out[asset.path] = RoleInfo(forced, "set by you",
                                       f"You set this to {ROLE_LABELS[forced]}",
                                       auto.role, True)
        else:
            out[asset.path] = auto
    return out


def role_counts(roles: dict) -> dict:
    counts = {role_id: 0 for role_id in ROLE_IDS}
    for info in roles.values():
        counts[info.role] = counts.get(info.role, 0) + 1
    return counts


# -- persistence ------------------------------------------------------------
def load_overrides(store_root: str) -> dict:
    try:
        with open(os.path.join(store_root, OVERRIDES_FILE), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if v in ROLE_IDS} \
        if isinstance(data, dict) else {}


def save_overrides(store_root: str, overrides: dict) -> bool:
    if not store_root:
        return False
    path = os.path.join(store_root, OVERRIDES_FILE)
    try:
        os.makedirs(store_root, exist_ok=True)
        if not overrides:
            if os.path.exists(path):
                os.remove(path)
            return True
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(overrides, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
        return True
    except OSError:
        return False
