"""Multi-label asset tags for the hand-building library browser.

Folder paths remain the authoritative file tree. These categories are virtual
views, so one image may appear under more than one useful tag without moving or
duplicating its stored file.
"""
from __future__ import annotations

import re

from core import generator


SMART_CATEGORY_TREE = (
    ("Map Structure", (
        ("core_modules", "Core Modules (50x50 / 100x100)"),
        ("rooms", "Rooms & Floorplans"),
        ("floors", "Floors & Surfaces"),
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
        ("loose_props", "Loose Props"),
        ("organic", "Bodies & Organic Remains"),
    )),
    ("Effects & Reference", (
        ("geomorph_overlays", "Core Overlay Variants"),
        ("overlays", "Overlays & Visual Effects"),
        ("lighting", "Lighting & Glow"),
        ("hazards", "Hazards & Contamination"),
        ("fire_smoke", "Fire & Smoke"),
        ("symbols", "Symbols & Markers"),
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
)
_ENGINEERING_WORDS = (
    "reactor", "valve", "gauge", "engine", "machinery", "machine",
    "power", "generator", "turbine", "pump", "cable", "pipe", "vent",
    "conduit", "boiler", "motor",
)
_MEDICAL_WORDS = (
    "medical", "medbay", "medkit", "clinic", "infirmary", "laboratory",
    "science", "lab", "cryo", "cryopod", "tube", "specimen", "surgery",
    "microscope", "autopsy", "biological",
)
_FURNITURE_WORDS = (
    "furniture", "bed", "table", "chair", "stool", "desk", "bench",
    "couch", "bunk", "seat", "seating",
)
_STORAGE_WORDS = (
    "storage", "crate", "locker", "barrel", "box", "pallet", "canister",
    "shelf", "rack", "cabinet", "container", "cargo", "supply",
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
_LIGHT_WORDS = ("glow", "light", "lamp", "illumination", "flare")
_LOOSE_PROP_WORDS = (
    "prop", "props", "object", "objects", "decoration", "decor",
    "furnishing", "equipment", "miscellaneous",
)
_ROOM_WORDS = (
    "room", "pod", "laboratory", "lab", "module", "geomorph",
    "floorplan", "floor plan", "floor_plan", "deckplan", "deck plan",
    "deck_plan",
)
_FLOOR_SURFACE_WORDS = (
    "floor", "deck", "deckplate", "tile", "grating", "carpet", "ground",
    "plate", "mat",
)


def _text(asset) -> str:
    """Search meaningful path components without treating a pack title as a tag."""
    components = []
    folder = getattr(asset, "folder", "").replace("\\", "/")
    for component in folder.split("/"):
        normalized = " ".join(re.findall(r"[a-z0-9]+", component.casefold()))
        # The bundled release/archive names contain words such as "Custom
        # Tiles" that describe the pack, not every image inside it.
        if ("rpg mobius" in normalized or normalized in {
                "geomorphs", "custom tiles", "high res", "highres"}):
            continue
        components.append(re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", component))
    components.append(re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", asset.name))
    return "/".join(components).casefold()


def _has(text: str, terms) -> bool:
    words = re.findall(r"[a-z0-9]+", text.casefold())
    word_set = set(words)
    for term in terms:
        term_words = re.findall(r"[a-z0-9]+", term.casefold())
        if not term_words:
            continue
        if len(term_words) > 1:
            width = len(term_words)
            if any(words[index:index + width] == term_words
                   for index in range(len(words) - width + 1)):
                return True
            continue
        word = term_words[0]
        plural_forms = {word + "s", word + "es"}
        if word.endswith("y"):
            plural_forms.add(word[:-1] + "ies")
        if word in word_set or word_set.intersection(plural_forms):
            return True
        # Permit common suffixes such as "flooring" and "lighting" without
        # substring matches inside unrelated words (for example, "mat" in
        # "automated").
        if len(word) >= 5 and any(token.startswith(word) for token in words):
            return True
    return False


def classify_asset_categories(assets) -> dict[str, set[str]]:
    """Map each asset path to zero or more useful hand-building categories.

    Tags combine recognized geomorph/symbol-pack structure with filename
    keywords. The taxonomy is intentionally multi-label: an overlay may also be
    a hazard, and an image remains available in the original folder tree.
    """
    result: dict[str, set[str]] = {}
    for asset in assets:
        text = _text(asset)
        tags: set[str] = set()

        is_geomorph_overlay = generator._is_geomorph_overlay(asset)
        if (generator._is_large_core_geomorph(asset)
                and not is_geomorph_overlay):
            tags.add("core_modules")
        if is_geomorph_overlay:
            tags.add("overlays")
            if "core" in text:
                tags.add("geomorph_overlays")
        if generator._is_symbol_pack_asset(asset):
            tags.add("symbols")

        # Map construction and surface assets.
        if _has(text, _ROOM_WORDS):
            tags.add("rooms")
        if _has(text, generator.KEYWORDS["floor"]):
            if _has(text, _FLOOR_SURFACE_WORDS) or "rooms" not in tags:
                tags.add("floors")
        if _has(text, generator.KEYWORDS["wall"]):
            tags.add("walls")
        if _has(text, generator.KEYWORDS["corridor"]):
            tags.add("corridors")
        if _has(text, generator.KEYWORDS["door"]):
            tags.add("doors")

        # Equipment and dressing. Use semantic folder names as well as
        # filenames, rather than requiring every manual-use asset to match the
        # generator's much narrower placement classifier.
        if _has(text, _CONTROL_WORDS):
            tags.add("controls")
        if _has(text, _ENGINEERING_WORDS):
            tags.add("engineering")
        if _has(text, _MEDICAL_WORDS):
            tags.add("medical")
        if _has(text, _FURNITURE_WORDS):
            tags.add("furniture")
        if _has(text, _STORAGE_WORDS):
            tags.add("storage")
        if _has(text, _ORGANIC_WORDS):
            tags.add("organic")

        if (_has(text, generator.KEYWORDS["wall_fixture"])
                and not tags.intersection({"controls", "engineering", "medical"})):
            tags.add("controls")
        if (_has(text, generator.KEYWORDS["floor_fixture"])
                and not tags.intersection(
                    {"furniture", "storage", "medical", "organic"})):
            tags.add("loose_props")
        if _has(text, _LOOSE_PROP_WORDS):
            tags.add("loose_props")

        if _has(text, generator.KEYWORDS["hazard"]) or _has(text, _HAZARD_WORDS):
            tags.add("hazards")
        if _has(text, _FIRE_WORDS):
            tags.add("fire_smoke")
        if _has(text, _LIGHT_WORDS):
            tags.add("lighting")
        if getattr(asset, "is_overlay", False):
            tags.add("overlays")

        if not tags:
            tags.add("other")
        result[asset.path] = tags
    return result
