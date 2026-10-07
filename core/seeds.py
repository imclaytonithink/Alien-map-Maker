"""Generation seeds: long, random, and shareable.

A seed is any text. All-digit text is used as that number; anything else (for
example ``hangar-7``) is hashed to a stable number, so the same words always
rebuild the same map. New seeds are 12 random digits.
"""
from __future__ import annotations

import hashlib
import secrets

SEED_DIGITS = 12
MAX_SEED_LENGTH = 40


def new_seed() -> str:
    """A fresh random 12-digit seed (never starts with 0, so it stays 12 long)."""
    low = 10 ** (SEED_DIGITS - 1)
    return str(low + secrets.randbelow(9 * low))


def clean_seed(text) -> str:
    """Trim a user-entered seed; empty input becomes a new random seed."""
    value = str(text if text is not None else "").strip()[:MAX_SEED_LENGTH]
    return value or new_seed()


def coerce_seed(seed) -> int:
    """Deterministic integer for ``random.Random`` from an int or any text."""
    if isinstance(seed, bool):
        return int(seed)
    if isinstance(seed, int):
        return seed
    text = str(seed).strip()
    if text.isdigit():
        return int(text)
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")
