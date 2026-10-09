"""Data-driven site archetypes: load, validate, and save custom ones.

An archetype is one JSON file in ``data/archetypes/`` (or in the user's own
folder). Adding a brand-new archetype needs only a new file — no code change.

Schema (all keys documented in ``geomorph/README.md``)::

    name, group, topology, scales{small|medium|large: {...}},
    environments[], zones[{id,name,tags[],min,max,required,level,position,access,
                           prefer_adjacent[],forbid_adjacent[],checkpoint,filler,...}],
    vertical{stairs,lifts[],shafts[],special_volumes[]}, routes{route_type:[access...]},
    condition_default, text{description[],notes[],hooks[{type,text}]}, overlays[]
"""
from __future__ import annotations

import json
from pathlib import Path

from . import DATA_DIR

TOPOLOGIES = ("stacked", "campus", "street", "branching", "hub", "spine", "ring", "ship", "wreck")
ACCESS_LEVELS = ("public", "staff", "restricted", "secure", "containment")
ROUTE_TYPES = ("staff", "visitor", "prisoner", "specimen", "cargo", "emergency")
ENVIRONMENTS = ("breathable", "hostile", "vacuum", "underground", "orbital")
SCALES = ("small", "medium", "large")
OVERLAYS = ("lockdown", "power_failure", "breach", "quarantine", "salvage", "battle", "threat", "secrets")
POSITIONS = ("any", "core", "edge", "corner")
TOPO_SCALE_KEYS = {"stacked": ("cols", "rows", "levels"), "campus": ("buildings",), "street": ("blocks",),
                   "branching": ("levels", "chambers"), "hub": ("modules",), "spine": ("modules",),
                   "ring": ("modules",), "ship": ("tonnage",), "wreck": ("tonnage",)}


class ArchetypeError(ValueError):
    pass


def validate(a: dict, source="archetype") -> list:
    """Return a list of problems (empty when the definition is usable)."""
    errs = []
    def err(msg): errs.append(f"{source}: {msg}")
    if not isinstance(a, dict):
        return [f"{source}: not an object"]
    for key in ("name", "group", "topology", "zones", "scales"):
        if key not in a:
            err(f"missing '{key}'")
    if errs:
        return errs
    if a["topology"] not in TOPOLOGIES:
        err(f"unknown topology '{a['topology']}' (use one of {', '.join(TOPOLOGIES)})")
    for s in a["scales"]:
        if s not in SCALES:
            err(f"unknown scale '{s}'")
    for s in SCALES:
        if s not in a["scales"]:
            err(f"scale '{s}' missing")
            continue
        for k in TOPO_SCALE_KEYS.get(a["topology"], ()):
            if k not in a["scales"][s]:
                err(f"scale '{s}' needs '{k}' for topology {a['topology']}")
    ids = set()
    zones = a["zones"]
    if not zones:
        err("no zones")
    for z in zones:
        zid = z.get("id")
        if not zid:
            err("zone without id")
            continue
        if zid in ids:
            err(f"duplicate zone id '{zid}'")
        ids.add(zid)
        if not z.get("name"):
            err(f"zone '{zid}' has no name")
        if not isinstance(z.get("tags", []), list) or (not z.get("tags") and not z.get("filler")):
            err(f"zone '{zid}' needs tags (or a filler kind)")
        if z.get("access", "staff") not in ACCESS_LEVELS:
            err(f"zone '{zid}' bad access '{z.get('access')}'")
        if z.get("position", "any") not in POSITIONS:
            err(f"zone '{zid}' bad position '{z.get('position')}'")
        mn, mx = z.get("min", 1), z.get("max", 1)
        if mn < 0 or mx < mn:
            err(f"zone '{zid}' needs 0 <= min <= max")
        lvl = z.get("level", "any")
        if lvl not in ("any", "top", "bottom") and not isinstance(lvl, int):
            err(f"zone '{zid}' bad level '{lvl}'")
    for z in zones:
        for key in ("prefer_adjacent", "forbid_adjacent"):
            for ref in z.get(key, []):
                if ref not in ids:
                    err(f"zone '{z.get('id')}' {key} refers to unknown zone '{ref}'")
    for rt, allowed in (a.get("routes") or {}).items():
        if rt not in ROUTE_TYPES:
            err(f"unknown route type '{rt}'")
        for acc in allowed:
            if acc not in ACCESS_LEVELS:
                err(f"route '{rt}' lists unknown access '{acc}'")
    for env in a.get("environments", []):
        if env not in ENVIRONMENTS:
            err(f"unknown environment '{env}'")
    for ov in a.get("overlays", []):
        if ov not in OVERLAYS:
            err(f"unknown overlay '{ov}'")
    ent = a.get("entrance_zone")
    if ent and ent not in ids:
        err(f"entrance_zone '{ent}' is not a zone")
    for z in zones:                     # secure/containment need a checkpoint somewhere
        pass
    if any(z.get("access") in ("secure", "containment") for z in zones) and \
            not any(z.get("checkpoint") for z in zones):
        err("has secure/containment zones but no zone with \"checkpoint\": true")
    return errs


def load_file(path) -> dict:
    path = Path(path)
    with open(path, encoding="utf-8") as fh:
        a = json.load(fh)
    errs = validate(a, path.name)
    if errs:
        raise ArchetypeError("; ".join(errs))
    a.setdefault("_file", str(path))
    return a


def archetype_dirs(extra=None):
    dirs = [DATA_DIR / "archetypes"]
    user = DATA_DIR.parent.parent / "user_archetypes"
    if user.is_dir():
        dirs.append(user)
    for d in (extra or []):
        dirs.append(Path(d))
    return dirs


def load_all(extra=None, strict=False) -> dict:
    """name -> archetype for every valid ``*.json`` in the archetype folders."""
    out, problems = {}, []
    for d in archetype_dirs(extra):
        for f in sorted(d.glob("*.json")):
            try:
                a = load_file(f)
                out[a["name"]] = a
            except (ArchetypeError, json.JSONDecodeError) as exc:
                problems.append(f"{f.name}: {exc}")
    if problems and strict:
        raise ArchetypeError("\n".join(problems))
    out["_problems"] = problems
    return out


def list_names(extra=None):
    d = load_all(extra)
    d.pop("_problems", None)
    return sorted(d)


def save_custom(a: dict, directory) -> Path:
    """Save an archetype made in the UI editor; refuses invalid definitions."""
    errs = validate(a, a.get("name", "custom"))
    if errs:
        raise ArchetypeError("; ".join(errs))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fn = "".join(c if c.isalnum() else "_" for c in a["name"].lower()).strip("_") + ".json"
    path = directory / fn
    path.write_text(json.dumps(a, indent=1), encoding="utf-8")
    return path


def blank_archetype(name="New archetype") -> dict:
    """Starting point for the 'Other / custom' editor."""
    return {"name": name, "group": "Custom", "topology": "stacked",
            "scales": {"small": {"cols": 2, "rows": 1, "levels": 2},
                       "medium": {"cols": 2, "rows": 2, "levels": 3},
                       "large": {"cols": 3, "rows": 2, "levels": 4}},
            "environments": ["breathable"],
            "zones": [{"id": "main", "name": "Main hall", "tags": ["multipurpose"], "min": 1, "max": 3,
                       "required": True, "level": "any", "position": "any", "access": "public",
                       "prefer_adjacent": [], "forbid_adjacent": []}],
            "routes": {"staff": ["public", "staff"], "visitor": ["public"]},
            "condition_default": "Average", "kind": "site", "name_table": "facilities",
            "vertical": {"stairs": True, "lifts": ["staff"], "shafts": [], "special_volumes": []},
            "text": {}, "overlays": ["lockdown", "power_failure", "threat", "secrets"],
            "entrance_zone": "main"}
