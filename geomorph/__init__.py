"""Starship Geomorphs 2.0 map generator — ships *and* sites.

Pure Python (no Qt), so every stage can be unit-tested headlessly.

Pipeline (see ``pipeline.generate``)::

    Program -> Zone graph -> Topology -> Placement -> Scoring/repair
            -> Dressing -> Text -> Export

The tile pack itself is *not* in Git. Point ``GEOMORPH_TILES`` (or the
``tiles_dir`` option) at the extracted pack folder; see ``geomorph/README.md``.
"""
from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"

CREDITS = (
    "Tiles: Starship Geomorphs 2.0 by Robert Pearce (Pearce Design Studio, LLC), "
    "licensed CC BY-NC 4.0; PNG renderings by Eric Smith / RPG Mobius. "
    "Unofficial, private, non-commercial fan tool. Traveller is a trademark of "
    "Far Future Enterprises. Generated content is original."
)
