"""Multi-label asset tags for the hand-building library browser.

Folder paths remain the authoritative file tree. These categories are virtual
views, so one image may appear under more than one useful tag without moving or
duplicating its stored file.
"""
from __future__ import annotations

from functools import lru_cache
import re

from core import generator


SMART_CATEGORY_TREE = (
    ("Map Structure", (
        ("core_modules", "Core Modules (50x50 / 100x100)"),
        ("modular_pieces", "Modular Pieces & Build-It Tiles"),
        ("rooms", "Rooms, Facilities & Floorplans"),
        ("floors", "Floors, Decks & Walkways"),
        ("walls", "Walls & Bulkheads"),
        ("corridors", "Corridors & Passages"),
        ("doors", "Doors, Hatches & Airlocks"),
    )),
    ("Equipment & Props", (
        ("controls", "Controls, Consoles & Terminals"),
        ("engineering", "Engineering & Machinery"),
        ("medical", "Medical, Science & Cryo"),
        ("furniture", "Furniture & Bedding"),
        ("storage", "Storage & Cargo"),
        ("vehicles", "Vehicles & Small Craft"),
        ("weapons", "Weapons & Security"),
        ("food", "Food, Galley & Mess"),
        ("loose_props", "Loose Props"),
        ("organic", "Bodies & Organic Remains"),
    )),
    ("Environment & Terrain", (
        ("landscaping", "Landscaping & Vegetation"),
    )),
    ("Effects & Reference", (
        ("geomorph_overlays", "Core Overlay Variants"),
        ("overlays", "Overlays & Visual Effects"),
        ("lighting", "Lighting & Glow"),
        ("hazards", "Hazards & Contamination"),
        ("fire_smoke", "Fire & Smoke"),
        ("symbols", "Symbols, Tokens & Markers"),
        ("other", "Other / Unclassified"),
    )),
)

CATEGORY_LABELS = {
    category_id: label
    for _group, categories in SMART_CATEGORY_TREE
    for category_id, label in categories
}
CATEGORY_GROUPS = {
    group: tuple(category_id for category_id, _label in categories)
    for group, categories in SMART_CATEGORY_TREE
}


_CONTROL_WORDS = (
    "terminal", "computer", "console", "screen", "monitor", "display",
    "panel", "switch", "controls", "control", "keypad", "keyboard",
    "intercom", "interface", "radio", "communications", "antenna",
    "bridge", "gunnery", "sensor control", "fire control", "cic",
    "station", "stellar cartography",
)
_ENGINEERING_WORDS = (
    "reactor", "valve", "gauge", "engine", "machinery", "machine",
    "power", "generator", "turbine", "pump", "cable", "pipe", "vent",
    "conduit", "boiler", "motor", "battery", "fuel", "sensor", "utility",
    "incinerator", "compactor", "solar panel", "solar panels", "transporter",
)
_MEDICAL_WORDS = (
    "medical", "medbay", "medkit", "clinic", "infirmary", "laboratory",
    "science", "lab", "cryo", "cryopod", "tube", "specimen", "surgery",
    "microscope", "autopsy", "biological", "sickbay", "low berth",
)
_FURNITURE_WORDS = (
    "furniture", "bed", "bedding", "table", "chair", "stool", "desk",
    "bench", "couch", "sofa", "bunk", "berth", "seat", "seating",
)
_STORAGE_WORDS = (
    "storage", "crate", "locker", "barrel", "box", "pallet", "canister",
    "shelf", "rack", "cabinet", "container", "cargo", "supply", "hold",
)
_VEHICLE_WORDS = (
    "vehicle", "small craft", "air raft", "air-raft", "fighter", "shuttle",
    "ship's boat", "runabout", "dropship", "drop capsule", "grav bike",
    "grav tank", "atv", "mech", "aircraft", "airplane", "aeroplane",
    "spacecraft", "spaceship", "spaceplane", "orbiter", "concorde",
    "boeing", "xb 70", "valkyrie", "launch bay", "hangar", "helipad",
    "landing pad",
)
_WEAPON_WORDS = (
    "weapon", "gunnery", "gun", "missile", "cannon", "laser", "armory",
    "armoury", "security", "brig", "combat", "turret", "fire control",
    "barbette", "shooting range", "target range",
)
_FOOD_WORDS = (
    "galley", "mess", "dining", "food", "kitchen", "restaurant",
    "cafeteria", "pantry", "beverage", "canteen", "bar",
)
_LANDSCAPING_WORDS = (
    "landscape", "landscaping", "vegetation", "flora", "foliage", "tree",
    "shrub", "bush", "grass", "flower", "garden", "fern", "moss", "vine",
    "ivy", "cactus", "cacti", "fungus", "fungi", "mushroom", "orchid",
    "palm", "hedge", "plant life", "potted plant",
)
_ORGANIC_WORDS = (
    "corpse", "body", "bodies", "skeleton", "remains", "creature",
    "organism", "alien", "specimen",
)
_HAZARD_WORDS = (
    "spill", "hazard", "blood", "acid", "warning", "fire", "smoke",
    "biohazard", "slime", "toxic", "contamination", "radiation",
)
_FIRE_WORDS = ("fire", "smoke", "flame", "burning", "burn")
_LIGHT_WORDS = (
    "glow", "light", "lamp", "illumination", "flare", "lighting",
    "light fixture", "light source", "lightbulb",
)
# "Misc" is deliberately not a loose-prop signal: the reviewed Symbols
# directory uses it for full rooms, corridors, landscaping, and machinery.
_LOOSE_PROP_WORDS = (
    "prop", "props", "object", "objects", "decoration", "decor", "loose",
    "furnishing",
)
_ROOM_WORDS = (
    "room", "pod", "laboratory", "lab", "module", "geomorph", "bridge",
    "stateroom", "suite", "quarters", "barracks", "office", "lounge",
    "galley", "mess", "hangar", "arboretum", "classroom", "briefing",
    "conference", "gym", "court", "pool", "retail", "shop", "repair area",
    "holodeck", "holo deck", "holopit", "holo pit", "holosuite", "holo suite",
    "shooting range", "target range", "stellar cartography", "transporter",
    "laundry", "incinerator", "trash compactor", "promenade", "casino", "bar",
    "bay", "floorplan", "floor plan", "floor_plan", "deckplan", "deck plan",
    "deck_plan", "escape pod", "empty room", "fresher", "low berth",
    "animal pen", "launch area",
)
_FLOOR_SURFACE_WORDS = (
    "floor", "deck", "deckplate", "tile", "grating", "carpet", "ground",
    "plate", "mat", "catwalk", "walkway", "platform", "baseplate",
    "base plate", "ramp", "stairway", "stairs", "landing pad", "helipad",
)
_CORRIDOR_WORDS = (
    "corridor", "hallway", "passage", "high passage", "passageway", "tunnel",
)


def _text(asset) -> str:
    """Search meaningful path components without treating pack titles as tags."""
    components = []
    folder = getattr(asset, "folder", "").replace("\\", "/")
    for component in folder.split("/"):
        normalized = " ".join(re.findall(r"[a-z0-9]+", component.casefold()))
        # The bundled release/archive names contain words such as "Custom
        # Tiles" that describe the pack, not every image inside it. This one
        # combined Symbols folder is likewise too broad to tag each item as
        # loose furniture; use the filename and more specific semantic folders.
        if ("rpg mobius" in normalized or normalized in {
                "geomorphs", "custom tiles", "high res", "highres",
                "furniture consoles equipment"}):
            continue
        components.append(re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", component))
    components.append(re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", asset.name))
    return "/".join(components).casefold()


def _is_modular_piece(asset) -> bool:
    """Recognize the actual Geomorphs/Custom Tiles component directories."""
    component_names = {"core", "corner", "edge", "end", "megamorph",
                       "baseplate", "baseplates"}
    folder = getattr(asset, "folder", "").replace("\\", "/")
    for component in folder.split("/"):
        words = set(re.findall(r"[a-z0-9]+", component.casefold()))
        if words.intersection(component_names) or {"build", "it"}.issubset(words):
            return True
    return False


@lru_cache(maxsize=256)
def _prepare_terms(terms: tuple[str, ...]):
    """Tokenize each taxonomy vocabulary once instead of once per asset."""
    prepared = []
    for term in terms:
        term_words = tuple(re.findall(r"[a-z0-9]+", term.casefold()))
        if not term_words:
            continue
        if len(term_words) > 1:
            prepared.append((term_words, "", frozenset(), False))
            continue
        word = term_words[0]
        plural_forms = {word + "s", word + "es"}
        if word.endswith("y"):
            plural_forms.add(word[:-1] + "ies")
        prepared.append(((), word, frozenset(plural_forms), len(word) >= 5))
    return tuple(prepared)


def _token_prefixes(words):
    prefixes = set()
    prefixes_without_lightning = set()
    for token in set(words):
        for length in range(5, len(token) + 1):
            prefix = token[:length]
            prefixes.add(prefix)
            if not token.startswith("lightning"):
                prefixes_without_lightning.add(prefix)
    return prefixes, prefixes_without_lightning


def _has_words(words, word_set, terms, prefixes=None,
               prefixes_without_lightning=None) -> bool:
    if not isinstance(words, tuple):
        words = tuple(words)
    if prefixes is None or prefixes_without_lightning is None:
        prefixes, prefixes_without_lightning = _token_prefixes(words)
    for term_words, word, plural_forms, allow_suffix in _prepare_terms(tuple(terms)):
        if term_words:
            width = len(term_words)
            if any(words[index:index + width] == term_words
                   for index in range(len(words) - width + 1)):
                return True
            continue
        if word in word_set or any(form in word_set for form in plural_forms):
            return True
        # Permit common suffixes such as "flooring" and "lighting" without
        # substring matches inside unrelated words (for example, "mat" in
        # "automated"). "Lightning" is a separate sci-fi name, not a light.
        matching_prefixes = (prefixes_without_lightning if word == "light"
                             else prefixes)
        if allow_suffix and word in matching_prefixes:
            return True
    return False


def _has(text: str, terms) -> bool:
    words = tuple(re.findall(r"[a-z0-9]+", text.casefold()))
    prefixes, prefixes_without_lightning = _token_prefixes(words)
    return _has_words(words, set(words), terms, prefixes,
                      prefixes_without_lightning)


def classify_asset_categories(assets) -> dict[str, set[str]]:
    """Map each asset path to zero or more useful hand-building categories.

    Tags combine recognized geomorph/symbol-pack structure with folder and
    filename keywords. The taxonomy is intentionally multi-label: an overlay
    may also be a hazard, and every image remains available in the original
    folder tree even if it has no useful smart tag.
    """
    result: dict[str, set[str]] = {}
    for asset in assets:
        text = _text(asset)
        words = tuple(re.findall(r"[a-z0-9]+", text))
        word_set = set(words)
        prefixes, prefixes_without_lightning = _token_prefixes(words)
        has = lambda terms: _has_words(
            words, word_set, terms, prefixes, prefixes_without_lightning)
        tags: set[str] = set()

        is_geomorph_overlay = generator._is_geomorph_overlay(asset)
        if (generator._is_large_core_geomorph(asset)
                and not is_geomorph_overlay):
            tags.add("core_modules")
        if _is_modular_piece(asset):
            tags.add("modular_pieces")
        if is_geomorph_overlay:
            tags.add("overlays")
            if "core" in text:
                tags.add("geomorph_overlays")
        if generator._is_symbol_pack_asset(asset):
            tags.add("symbols")

        # Map construction and surface assets.
        if has(_ROOM_WORDS):
            tags.add("rooms")
        if (has(generator.KEYWORDS["floor"])
                or has(_FLOOR_SURFACE_WORDS)):
            if has(_FLOOR_SURFACE_WORDS) or "rooms" not in tags:
                tags.add("floors")
        if has(generator.KEYWORDS["wall"]):
            tags.add("walls")
        if has(_CORRIDOR_WORDS):
            tags.add("corridors")
        if has(generator.KEYWORDS["door"]):
            tags.add("doors")

        # Equipment and dressing. Use semantic folder names as well as
        # filenames, rather than requiring every manual-use asset to match the
        # generator's much narrower placement classifier.
        if has(_CONTROL_WORDS):
            tags.add("controls")
        if has(_ENGINEERING_WORDS):
            tags.add("engineering")
        if has(_MEDICAL_WORDS):
            tags.add("medical")
        if has(_FURNITURE_WORDS):
            tags.add("furniture")
        if has(_STORAGE_WORDS):
            tags.add("storage")
        if has(_VEHICLE_WORDS):
            tags.add("vehicles")
        if has(_WEAPON_WORDS):
            tags.add("weapons")
        if has(_FOOD_WORDS):
            tags.add("food")
        if has(_LANDSCAPING_WORDS):
            tags.add("landscaping")
        if has(_ORGANIC_WORDS):
            tags.add("organic")

        if (has(generator.KEYWORDS["wall_fixture"])
                and not tags.intersection({"controls", "engineering", "medical"})):
            tags.add("controls")
        if (has(generator.KEYWORDS["floor_fixture"])
                and not tags.intersection(
                    {"furniture", "storage", "medical", "organic"})):
            tags.add("loose_props")
        if has(_LOOSE_PROP_WORDS):
            tags.add("loose_props")

        fire_control = has(("fire control", "fire-control"))
        if ((has(generator.KEYWORDS["hazard"]) or
             has(_HAZARD_WORDS)) and not fire_control):
            tags.add("hazards")
        if has(_FIRE_WORDS) and not fire_control:
            tags.add("fire_smoke")
        if has(_LIGHT_WORDS):
            tags.add("lighting")
        if getattr(asset, "is_overlay", False):
            tags.add("overlays")

        if not tags:
            tags.add("other")
        result[asset.path] = tags
    return result
