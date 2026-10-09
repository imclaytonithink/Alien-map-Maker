"""Function tags for library images (medical, cargo, hangar...), taken from the geomorph data.

The geomorph tile manifest already says what each tile is for, and the symbols
manifest knows each furniture category. Matching is by file name, the same way
the generator finds tiles in the library, so it works for any pack that keeps the
original file names and does nothing for other images.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "geomorph" / "data"
MIN_WEIGHT = 0.5                      # tile tags weaker than this are guesses, not functions

# friendly extra words so "low berth" or "sick bay" find the right tag
ALIASES = {
    "crew_quarters": ["crew quarters", "bunks", "barracks"], "staterooms": ["stateroom", "cabin", "cabins"],
    "vehicle_bay": ["vehicle bay", "garage", "motor pool"], "lowberth": ["low berth", "cryo", "hypersleep"],
    "medical": ["medbay", "sick bay", "infirmary", "hospital"], "recreation": ["rec", "lounge", "bar"],
    "hydroponics": ["farm", "greenhouse", "garden"], "weapons": ["guns", "gunnery", "turret"],
    "engineering": ["engine", "reactor"], "escape": ["escape pod", "lifeboat"], "fresher": ["bathroom", "shower", "toilet"],
    "armory": ["armoury"], "brig": ["cell", "cells", "prison"], "docking": ["dock"], "airlock": ["lock"],
}

_INDEX: dict | None = None


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", os.path.basename(name.replace("\\", "/")).casefold()).strip()


def _build() -> dict:
    index: dict[str, tuple] = {}
    try:
        tiles = json.loads((DATA / "tile_manifest.json").read_text(encoding="utf-8")).get("tiles", [])
    except (OSError, ValueError):
        tiles = []
    for t in tiles:
        tags = sorted((k for k, w in (t.get("tags") or {}).items() if w >= MIN_WEIGHT))
        if t.get("image") and tags:
            index[_norm(t["image"])] = tuple(tags)
    try:
        symbols = json.loads((DATA / "symbols_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        symbols = []
    for row in symbols:
        try:
            rel, cat = row[0], row[1]
        except (IndexError, TypeError):
            continue
        index.setdefault(_norm(rel), (str(cat).casefold(),))
    return index


def function_tags(path_or_name: str) -> tuple:
    """Function tags for one library image (empty when it is not a known tile or symbol)."""
    global _INDEX
    if _INDEX is None:
        _INDEX = _build()
    return _INDEX.get(_norm(path_or_name), ())


def display_words(tag: str) -> list:
    """Words a person might type to mean ``tag`` (for search matching and the filter list)."""
    return [tag.replace("_", " "), *ALIASES.get(tag, [])]


def label(tag: str) -> str:
    return tag.replace("_", " ").capitalize()
