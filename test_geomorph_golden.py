"""Regression check: a fixed set of seeds must keep producing the same maps.

Each case is fingerprinted (every placed tile with its position, orientation and
zone, plus counts of filler, symbols, key entries and issues). A change that
alters a map shows up here with the case name and what moved. When the change is
intended, refresh the snapshot:

    python test_geomorph_golden.py --update

With the tile pack available (GEOMORPH_TILES) add ``--sheet DIR`` to also write
a PNG of every case, for a quick look at what changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from geomorph import pipeline
from geomorph.registry import Registry

GOLDEN = Path(__file__).resolve().parent / "geomorph" / "tests_data" / "golden.json"
DECOR = {"enabled": True, "density": 0.5, "incident": "none", "where": "all", "exterior": True}

CASES = {
    "ship merchant 1000": dict(kind="ship", seed="g1", tonnage=1000, ship_type="Merchant", decor=DECOR),
    "ship scout planned": dict(kind="ship", seed="g2", tonnage=400, ship_type="Scout"),
    "ship military parts": dict(kind="ship", seed="g3", tonnage=2500, ship_type="Military",
                                parts={"counts": {"escape": 2, "weapons": 4}}, decor=DECOR),
    "ship liner random": dict(kind="ship", seed="g4", tonnage=4000, ship_type="Luxury Liner", mode="random"),
    "ship research selective": dict(kind="ship", seed="g5", tonnage=2000, ship_type="Research", mode="selective"),
    "ship movie": dict(kind="ship", seed="g6", tonnage=3000, mode="movie"),
    "site colony": dict(kind="site", seed="g7", archetype="Frontier colony outpost", scale="medium",
                        environment="breathable", decor=DECOR),
    "site mine": dict(kind="site", seed="g8", archetype="Deep mine", scale="medium",
                      overlays=["power_failure", "threat"], decor=DECOR),
    "site prison": dict(kind="site", seed="g9", archetype="Prison / penal colony", scale="large",
                        overlays=["lockdown"], decor=dict(DECOR, incident="struggle")),
    "site station": dict(kind="site", seed="g10", archetype="Space station (ring / spindle / cylinder / modular)",
                         scale="medium"),
    "site lab": dict(kind="site", seed="g11", archetype="Research facility", scale="small",
                     overlays=["quarantine", "secrets"], decor=DECOR),
    "site farm": dict(kind="site", seed="g12", archetype="Agricultural / hydroponics colony", scale="medium",
                      environment="breathable", decor=DECOR),
}


def fingerprint(res) -> dict:
    tiles = []
    for g in res.grids:
        for p in sorted(g.placed, key=lambda q: (q.y, q.x, q.tile.id)):
            tiles.append([g.index, p.tile.id, p.x, p.y, p.o.rot, bool(p.o.mirror), p.zone])
    blob = json.dumps([tiles, [[f["kind"], f["x"], f["y"], f["w"], f["h"]] for g in res.grids for f in g.filler],
                       [[d.get("sym"), d.get("level"), round(d.get("cx", 0), 2), round(d.get("cy", 0), 2)]
                        for d in (getattr(res, "decor", None) or [])]], sort_keys=True)
    return {"hash": hashlib.sha1(blob.encode()).hexdigest()[:16], "tiles": len(tiles),
            "levels": len(res.grids), "filler": sum(len(g.filler) for g in res.grids),
            "decor": len(getattr(res, "decor", None) or []), "key": len(res.key), "issues": len(res.issues)}


def build_all(reg):
    out = {}
    for name, opts in CASES.items():
        out[name] = fingerprint(pipeline.generate(reg, dict(opts)))
    return out


def write_sheet(reg, directory):
    from geomorph import render
    images = render.TileImages(reg.tiles_dir)
    images.symbols_dir = os.environ.get("GEOMORPH_SYMBOLS")
    Path(directory).mkdir(parents=True, exist_ok=True)
    for name, opts in CASES.items():
        res = pipeline.generate(reg, dict(opts))
        im = render.render_level(res, 0, images, pps=10, gm=True)
        im.convert("RGB").save(Path(directory) / (name.replace(" ", "_") + ".png"))


def main(argv) -> int:
    reg = Registry.load(tiles_dir=os.environ.get("GEOMORPH_TILES"))
    got = build_all(reg)
    if "--sheet" in argv:
        write_sheet(reg, argv[argv.index("--sheet") + 1])
    if "--update" in argv or not GOLDEN.exists():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True), encoding="utf-8")
        print(f"snapshot written: {len(got)} cases")
        return 0
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    bad = []
    for name in sorted(set(got) | set(want)):
        if got.get(name) != want.get(name):
            a, b = want.get(name) or {}, got.get(name) or {}
            moved = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k) and k != "hash"}
            bad.append(f"  {name}: {moved or 'same counts, tiles/positions differ'}")
    if bad:
        print("Maps changed for fixed seeds:\n" + "\n".join(bad) +
              "\nIf this is intended, run: python test_geomorph_golden.py --update")
        return 1
    print(f"golden maps ok ({len(got)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
