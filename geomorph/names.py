"""Name generator driven by editable word tables (data/themes/*.json)."""
from __future__ import annotations

import json
import random

from . import DATA_DIR

DEFAULT_THEME = "alien_corporate_frontier"


def themes() -> dict:
    out = {}
    for f in sorted((DATA_DIR / "themes").glob("*.json")):
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        out[f.stem] = d
    return out


def theme_labels() -> dict:
    return {k: v.get("label", k) for k, v in themes().items()}


def load_theme(theme=None) -> dict:
    t = themes()
    return t.get(theme or DEFAULT_THEME) or t[DEFAULT_THEME] if t else {}


def make_name(table: str, rng: random.Random, theme=None) -> str:
    """Name from table 'facilities'|'colonies'|'corporations'|'mines'|'stations'|'ships'."""
    th = load_theme(theme)
    t = th.get(table) or th.get("facilities") or {}
    corp = corp_name(rng, theme)
    pattern = rng.choice(t.get("patterns", ["{prefix}"]))
    vals = {"prefix": rng.choice(t.get("prefix", ["Site"])), "suffix": rng.choice(t.get("suffix", ["Facility"])),
            "number": rng.randint(2, 99), "corp": corp, "first": "", "second": ""}
    try:
        return pattern.format(**vals)
    except KeyError:
        return vals["prefix"]


def corp_name(rng: random.Random, theme=None) -> str:
    th = load_theme(theme)
    t = th.get("corporations") or {}
    pattern = rng.choice(t.get("patterns", ["{first} {second}"]))
    return pattern.format(first=rng.choice(t.get("first", ["Acme"])), second=rng.choice(t.get("second", ["Corp"])))
