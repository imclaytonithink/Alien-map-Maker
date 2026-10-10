"""Write ``core/muthur_catalog.json``: the frozen room list shared with the MU/TH/UR terminal.

The terminal code (see ``core/muthur.py``) points into this list by position, and the
Tabletop Simulator script carries an identical copy, so entries must never be reordered
or removed once released. To add rooms, append them to the end of an archetype (or append
a new archetype) and bump nothing: older codes keep decoding the same.

    python build_muthur_catalog.py            # rebuild from geomorph/data/archetypes
    python build_muthur_catalog.py --lua out  # also write the Lua table for the terminal
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARCH_DIR = ROOT / "geomorph" / "data" / "archetypes"
OUT = ROOT / "core" / "muthur_catalog.json"

# Terminal settings (the TTS script's RT table, in its order, 1-based there).
SETTINGS = ["SPACESHIP", "COLONY", "RESEARCH LAB (WEYLAND-YUTANI)", "MINING OPERATION",
            "SPACE STATION", "MILITARY BASE (USCM)", "PRISON", "DERELICT (ABANDONED SHIP)"]

ARCH_SETTING = {
    "Abandoned / \"ghost\" station": 7, "Agricultural / hydroponics colony": 1,
    "Archaeological / dig site": 2, "Asteroid mining station": 3,
    "Black site / secret corporate lab": 2, "Bunker / shelter": 5, "Company town": 1,
    "Crashed or half-buried wreck": 7, "Deep mine": 3, "Derelict ship": 7,
    "Domed or underground town": 1, "Factory / shipyard / ship-breaking yard": 3,
    "Frontier colony outpost": 1, "Fuel depot / waystation / relay": 4,
    "Military base / garrison": 5, "Observatory / sensor array / listening post": 2,
    "Orbital / gas-giant refinery": 4, "Prison / penal colony": 6,
    "Quarantine station / cryo (hypersleep) vault": 2, "Research facility": 2,
    "Spaceport / starport": 1, "Space station (ring / spindle / cylinder / modular)": 4,
    "Surface mining operation": 3, "Terraforming / atmosphere processor": 1,
    "Transit / tram hub": 4, "Xeno-biology containment lab": 2,
}

# The terminal room type whose systems a room uses: first matching name keyword wins,
# then the room's first function tag.
NAME_POOL = [
    ("containment cells", "SPECIMEN CONTAINMENT"), ("specimen", "SPECIMEN CONTAINMENT"),
    ("quarantine", "SPECIMEN CONTAINMENT"), ("holding", "CELL BLOCK"), ("cell block", "CELL BLOCK"),
    ("lockdown control", "SECURITY OFFICE"), ("monitoring station", "SECURITY OFFICE"),
    ("autopsy", "MORGUE"), ("armory", "ARMORY"), ("air plant", "LIFE SUPPORT HUB"),
    ("water & air", "LIFE SUPPORT HUB"), ("water plant", "LIFE SUPPORT HUB"),
    ("pump room", "LIFE SUPPORT HUB"), ("ventilation", "LIFE SUPPORT HUB"),
    ("safety systems", "LIFE SUPPORT HUB"), ("reactor", "REACTOR ROOM"), ("cooling plant", "REACTOR ROOM"),
    ("refuge", "STORAGE"), ("emergency shelter", "STORAGE"), ("cryo", "CRYO VAULT"),
    ("secure storage", "DATA VAULT"), ("day room", "ASSEMBLY HALL"), ("infirmary", "INFIRMARY"),
    ("mechanical", "ENGINEERING"), ("incinerator", "RECYCLING PLANT"), ("scrap", "RECYCLING PLANT"),
    ("ore", "ORE PROCESSING"), ("tank", "STORAGE DEPOT"), ("intake & processing", "ORE PROCESSING"),
    ("finds lab", "SCIENCE LAB"), ("excavation", "DRILL CONTROL"), ("dish", "COMMS TOWER"),
    ("comms shack", "COMMS TOWER"), ("school", "ASSEMBLY HALL"), ("gatehouse", "SECURITY OFFICE"),
    ("customs", "SECURITY OFFICE"), ("passenger terminal", "ASSEMBLY HALL"), ("hotels", "MESS HALL"),
    ("bar & rec", "MESS HALL"), ("plaza", "ASSEMBLY HALL"), ("landing pad", "DROPSHIP BAY"),
    ("blast door", "AIRLOCK"), ("escape routes", "AIRLOCK"), ("gates / airlocks", "AIRLOCK"),
    ("decoy front", "ADMIN OFFICE"), ("badge-only", "SECURITY OFFICE"), ("secure labs", "BIO-RESEARCH LAB"),
    ("laboratories", "BIO-RESEARCH LAB"), ("main shaft", "MINE ELEVATOR"), ("head-frame", "MINE ELEVATOR"),
    ("production floor", "FOUNDRY"), ("gantry", "DOCKING RING"), ("observation", "OBSERVATION DECK"),
    ("platforms", "STORAGE"), ("decon", "MEDICAL BAY"), ("bunk", "BUNKHOUSE"), ("barracks", "BARRACKS"),
    ("motor pool", "MOTOR POOL"), ("vehicle", "VEHICLE GARAGE"), ("garage", "VEHICLE GARAGE"),
    ("hydroponics", "HYDROPONICS BAY"), ("habitat", "CREW QUARTERS"), ("housing", "CREW QUARTERS"),
    ("living quarters", "CREW QUARTERS"), ("camp", "CREW QUARTERS"), ("docking", "DOCKING RING"),
    ("hangar", "DROPSHIP BAY"), ("command", "COMMAND CENTER"), ("control", "COMMAND CENTER"),
    ("civic hall", "ADMIN OFFICE"), ("security", "SECURITY OFFICE"), ("checkpoint", "SECURITY OFFICE"),
    ("admin", "ADMIN OFFICE"), ("office", "ADMIN OFFICE"), ("power", "POWER CONTROL"),
    ("stores", "STORAGE"), ("storage", "STORAGE"), ("depot", "STORAGE"),
]
TAG_POOL = {
    "bridge": "COMMAND CENTER", "office": "ADMIN OFFICE", "comms": "COMMS CENTER", "sensors": "COMMS CENTER",
    "engineering": "ENGINEERING", "power": "POWER CONTROL", "drive": "ENGINE ROOM", "fuel": "STORAGE DEPOT",
    "scoop": "ENGINEERING", "cargo": "CARGO BAY", "hangar": "DROPSHIP BAY", "vehicle_bay": "VEHICLE GARAGE",
    "docking": "DOCKING RING", "airlock": "AIRLOCK", "weapons": "WEAPONS LOCKER", "armory": "ARMORY",
    "staterooms": "CREW QUARTERS", "crew_quarters": "CREW QUARTERS", "barracks": "BARRACKS",
    "passenger": "HABITAT RING", "galley": "MESS HALL", "recreation": "ASSEMBLY HALL",
    "concourse": "ASSEMBLY HALL", "medical": "MEDICAL BAY", "morgue": "MORGUE", "lowberth": "CRYO VAULT",
    "lab": "SCIENCE LAB", "hydroponics": "HYDROPONICS BAY", "security": "SECURITY OFFICE", "brig": "CELL BLOCK",
    "workshop": "MAINTENANCE BAY", "waste": "RECYCLING PLANT", "water": "LIFE SUPPORT HUB",
    "refinery": "ORE PROCESSING", "escape": "AIRLOCK", "vertical": "MINE ELEVATOR",
    "interstitial": "MAINTENANCE BAY", "fresher": "CREW QUARTERS", "multipurpose": "STORAGE",
}
# Ship rooms: one per function tag (ship tiles have no fixed room list).
SHIP_ROOMS = [
    ("bridge", "Bridge", "staff", "BRIDGE"), ("engineering", "Engineering", "restricted", "ENGINEERING DECK"),
    ("power", "Power plant", "restricted", "POWER CONTROL"), ("drive", "Drive room", "restricted", "ENGINE ROOM"),
    ("fuel", "Fuel tanks", "restricted", "STORAGE DEPOT"), ("scoop", "Fuel scoops", "restricted", "ENGINEERING"),
    ("cargo", "Cargo hold", "staff", "CARGO HOLD"), ("hangar", "Hangar", "staff", "DROPSHIP BAY"),
    ("vehicle_bay", "Vehicle bay", "staff", "VEHICLE GARAGE"), ("docking", "Docking port", "staff", "DOCKING RING"),
    ("airlock", "Airlock", "staff", "AIRLOCK"), ("weapons", "Gunnery", "restricted", "WEAPONS LOCKER"),
    ("armory", "Armory", "secure", "ARMORY"), ("staterooms", "Staterooms", "public", "CREW QUARTERS"),
    ("crew_quarters", "Crew quarters", "public", "CREW QUARTERS"), ("barracks", "Barracks", "staff", "BARRACKS"),
    ("passenger", "Passenger deck", "public", "HABITAT RING"), ("galley", "Galley", "public", "MESS HALL"),
    ("recreation", "Lounge", "public", "ASSEMBLY HALL"), ("concourse", "Concourse", "public", "ASSEMBLY HALL"),
    ("medical", "Med-bay", "staff", "MED-BAY"), ("morgue", "Morgue", "restricted", "MORGUE"),
    ("lowberth", "Hypersleep chamber", "staff", "HYPERSLEEP CHAMBER"), ("lab", "Laboratory", "restricted", "SCIENCE LAB"),
    ("hydroponics", "Hydroponics", "staff", "HYDROPONICS BAY"), ("security", "Security", "staff", "SECURITY OFFICE"),
    ("brig", "Brig", "secure", "BRIG"), ("office", "Office", "staff", "ADMIN OFFICE"),
    ("workshop", "Workshop", "staff", "MAINTENANCE BAY"), ("waste", "Waste recycling", "staff", "RECYCLING PLANT"),
    ("water", "Water tanks", "staff", "LIFE SUPPORT HUB"), ("refinery", "Refinery", "restricted", "ORE PROCESSING"),
    ("sensors", "Sensors", "staff", "COMMS CENTER"), ("comms", "Comms center", "staff", "COMMS CENTER"),
    ("escape", "Escape pods", "public", "AIRLOCK"), ("vertical", "Lift", "staff", "MINE ELEVATOR"),
    ("interstitial", "Crawlspace", "staff", "MAINTENANCE BAY"), ("fresher", "Washroom", "public", "CREW QUARTERS"),
    ("multipurpose", "Multipurpose bay", "staff", "STORAGE"),
]
# Defaults reviewed by the GM (the "MU/TH/UR Terminal Room List" document).
NOT_ALLOWED = {
    ("Company town", "Plaza"), ("Domed or underground town", "Plaza / market"),
    ("Transit / tram hub", "Platforms"), ("Archaeological / dig site", "Excavation area"),
    ("Black site / secret corporate lab", "Escape routes"), ("Surface mining operation", "Pit access"),
    ("Asteroid mining station", "Docking arms"), ("Deep mine", "Main shaft & lifts"),
    ("Deep mine", "Ore chambers"), ("Deep mine", "Surface head-frame"),
}
SHIP_NOT_ALLOWED = {"fuel", "scoop", "escape", "interstitial", "fresher", "multipurpose"}


def pool_for(name, tags):
    low = name.lower()
    for key, pool in NAME_POOL:
        if re.search(r"\b" + re.escape(key), low):
            return pool
    for t in tags:
        if t in TAG_POOL:
            return TAG_POOL[t]
    return "STORAGE"


def build():
    archetypes = [{"name": "Ship", "setting": 0, "rooms": [
        {"id": tag, "name": name, "tags": [tag], "access": access, "pool": pool, "allowed": tag not in SHIP_NOT_ALLOWED}
        for tag, name, access, pool in SHIP_ROOMS]}]
    for path in sorted(ARCH_DIR.glob("*.json")):
        a = json.loads(path.read_text(encoding="utf-8"))
        rooms = []
        for z in a["zones"]:
            rooms.append({"id": z["id"], "name": z["name"], "tags": list(z.get("tags", [])),
                          "access": z.get("access", "staff"), "pool": pool_for(z["name"], z.get("tags", [])),
                          "allowed": (a["name"], z["name"]) not in NOT_ALLOWED})
        archetypes.append({"name": a["name"], "setting": ARCH_SETTING.get(a["name"], 1), "rooms": rooms})
    return {"version": 1, "settings": SETTINGS, "archetypes": archetypes}


def lua_table(cat) -> str:
    """The catalog as a compact Lua table literal for the terminal script."""
    def q(s):
        return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
    acc = {"public": 0, "staff": 1, "restricted": 2, "secure": 3, "containment": 4}
    lines = ["MCAT={"]
    for a in cat["archetypes"]:
        rooms = ",".join("{" + ",".join([q(r["name"]), q(r["pool"]), str(acc.get(r["access"], 1))]) + "}"
                         for r in a["rooms"])
        lines.append("{n=" + q(a["name"]) + ",s=" + str(a["setting"] + 1) + ",r={" + rooms + "}},")
    lines.append("}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lua", help="also write the terminal's Lua catalog table to this file")
    args = ap.parse_args()
    cat = build()
    if OUT.exists():
        old = json.loads(OUT.read_text(encoding="utf-8"))
        for i, a in enumerate(old["archetypes"]):
            new = cat["archetypes"][i] if i < len(cat["archetypes"]) else None
            if new is None or new["name"] != a["name"] or \
                    [r["name"] for r in new["rooms"][:len(a["rooms"])]] != [r["name"] for r in a["rooms"]]:
                raise SystemExit(f"Refusing to reorder released catalog entries ({a['name']}). Append instead.")
    OUT.write_text(json.dumps(cat, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({sum(len(a['rooms']) for a in cat['archetypes'])} rooms)")
    if args.lua:
        Path(args.lua).write_text(lua_table(cat) + "\n", encoding="utf-8")
        print(f"wrote {args.lua}")


if __name__ == "__main__":
    main()
