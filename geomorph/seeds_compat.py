"""Seed handling identical to ``core.seeds.coerce_seed`` (so the package runs standalone)."""
from __future__ import annotations

import hashlib


def coerce_seed(seed) -> int:
    if isinstance(seed, bool):
        return int(seed)
    if isinstance(seed, int):
        return seed
    text = str(seed).strip()
    if text.isdigit():
        return int(text)
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
