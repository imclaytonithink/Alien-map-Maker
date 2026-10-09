"""Program: an archetype plus options becomes the list of zone instances."""
from __future__ import annotations

from .model import ZoneInst

GENERAL = {"id": "general", "name": "General storage", "tags": ["cargo", "multipurpose"],
           "access": "staff", "required": False}
# spare rooms get varied everyday uses (not just storage)
GENERAL_VARIANTS = [("Storage", ["cargo", "multipurpose"], "staff"), ("Offices", ["office"], "staff"),
                    ("Workshop", ["workshop"], "staff"), ("Common room", ["recreation", "galley"], "public"),
                    ("Cabins", ["staterooms", "crew_quarters"], "public"), ("Maintenance", ["workshop", "engineering"], "staff")]


def general_zone(n: int, rng=None) -> dict:
    name, tags, access = GENERAL_VARIANTS[(n - 1) % len(GENERAL_VARIANTS)]
    return {"id": "general", "name": name, "tags": list(tags), "access": access, "required": False}


def zone_from_spec(z: dict, n: int, entrance_base: str, specials=None) -> ZoneInst:
    sv = {s.get("zone"): s for s in (specials or [])}
    inst_id = f"{z['id']}#{n}"
    height = int(sv.get(z["id"], {}).get("height", z.get("height", 1)) or 1)
    return ZoneInst(
        id=inst_id, base=z["id"], name=z["name"] if n == 1 else f"{z['name']} {n}",
        tags=list(z.get("tags", [])), access=z.get("access", "staff"), level=z.get("level", "any"),
        position=z.get("position", "any"), checkpoint=bool(z.get("checkpoint")),
        filler=z.get("filler", ""), filler_only=bool(z.get("filler_only")),
        prefer=list(z.get("prefer_adjacent", [])), forbid=list(z.get("forbid_adjacent", [])),
        entrance=(z["id"] == entrance_base) or bool(z.get("entrance_zone")),
        height=height, required=bool(z.get("required", True)), text=z.get("text", ""))


def required_count(arch: dict) -> int:
    """Zone instances every Planned layout must contain (sum of minimums)."""
    return sum(max(int(z.get("min", 1)), 0) if z.get("required", True) else 0 for z in arch["zones"])


def make_program(arch: dict, rng, n_slots: int, specials_count: int = 0):
    """Return ``(zones, shortfall)``.

    ``n_slots`` is the number of slots that need a zone. Required minimums are
    always included; ``shortfall`` > 0 means the layout is too small and must
    grow. Optional and spare capacity fills the rest by remaining headroom.
    """
    entrance = arch.get("entrance_zone", "")
    sv = (arch.get("vertical") or {}).get("special_volumes", [])
    counts = {}
    for z in arch["zones"]:
        mn = int(z.get("min", 1)) if z.get("required", True) else int(z.get("min", 0))
        counts[z["id"]] = max(mn, 0)
    total = sum(counts.values())
    shortfall = max(0, total - n_slots)
    if shortfall:
        n_slots = total
    by_id = {z["id"]: z for z in arch["zones"]}
    # optional zones first (variety), then spare capacity of everything
    while total < n_slots:
        cands = [z for z in arch["zones"] if counts[z["id"]] < int(z.get("max", 1))]
        if not cands:
            break
        optional = [z for z in cands if counts[z["id"]] == 0]
        pool = optional if optional and rng.random() < 0.7 else cands
        weights = [max(1, int(z.get("max", 1)) - counts[z["id"]]) for z in pool]
        pick = rng.choices(pool, weights=weights)[0]
        counts[pick["id"]] += 1
        total += 1
    zones = []
    for z in arch["zones"]:
        for n in range(1, counts[z["id"]] + 1):
            zones.append(zone_from_spec(z, n, entrance, sv))
    spare = n_slots - len(zones)
    for n in range(1, spare + 1):                     # still room: general-purpose rooms
        zones.append(zone_from_spec(general_zone(n), n, entrance, sv))
    if entrance and not any(zn.entrance for zn in zones) and zones:
        zones[0].entrance = True
    return zones, shortfall
