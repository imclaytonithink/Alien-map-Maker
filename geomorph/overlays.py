"""Craft / vehicle overlays ('internal ships'): optional art drawn over a tile.

Many tiles have ``[Overlay]`` images (an air-raft, escape pods, fighters in a hangar, a launch...). They are off
by default and can be switched on per category, in the preview, in exports and on the canvas.
"""
from __future__ import annotations

import re

CATEGORIES = {
    "escape": ("Escape pods", r"escape pod|life ?boat"),
    "raft": ("Air-rafts and small vehicles", r"air.?raft|atv|grav bike|grav.?tank|wheeled|mech\b|mechs"),
    "fighters": ("Fighters", r"fighter|valkyrie|dragonfly|manta|rampart|drone"),
    "launches": ("Launches, shuttles and boats", r"launch|shuttle|gunboat|ship.?s boat|boat|dropship|runabout|gig|cutter"),
    "turrets": ("Gun turrets", r"turret|gunnery|barbette"),
}


def categorize(name: str) -> str:
    n = name.lower()
    for key, (_label, rx) in CATEGORIES.items():
        if re.search(rx, n):
            return key
    return "other"


def selected_overlays(tile, craft) -> list:
    """Relative image paths of ``tile``'s overlays whose category is in ``craft`` (None/empty = none)."""
    if not craft:
        return []
    want = set(craft)
    mirrored = tile.id.endswith("m") or bool(tile.mirror_of)
    out = []
    for rel in tile.overlays:
        low = rel.lower()
        if ("[mirror]" in low) != mirrored:
            continue
        cat = categorize(rel.rsplit("/", 1)[-1])
        if cat in want or ("other" in want and cat == "other"):
            out.append(rel)
    return out
