"""Command line: ``python -m geomorph <command>`` (see ``--help``)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import archetype as archmod
from . import exporter, names, pipeline, render, reports
from .registry import Registry


def _tiles_dir(a):
    d = a.tiles or os.environ.get("GEOMORPH_TILES")
    if d and not Path(d).is_dir():
        sys.exit(f"tile pack folder not found: {d}")
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m geomorph", description="Starship Geomorphs ship and site generator")
    ap.add_argument("--tiles", help="extracted tile pack folder (or set GEOMORPH_TILES)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="generate a ship or a site and export it")
    g.add_argument("--kind", choices=("ship", "site"), default="site")
    g.add_argument("--archetype", default="Research facility")
    g.add_argument("--scale", choices=archmod.SCALES, default="medium")
    g.add_argument("--env", choices=archmod.ENVIRONMENTS)
    g.add_argument("--condition")
    g.add_argument("--mode", choices=("planned", "random", "selective", "movie"), default="planned")
    g.add_argument("--ship-type", default="Merchant")
    g.add_argument("--tonnage", type=int, default=1000)
    g.add_argument("--no-symmetry", action="store_true")
    g.add_argument("--no-fins", action="store_true")
    g.add_argument("--orientation", choices=("N", "E", "S", "W"), default="N")
    g.add_argument("--seed", default="1")
    g.add_argument("--theme", default=names.DEFAULT_THEME)
    g.add_argument("--overlays", default="", help="comma list: lockdown,power_failure,breach,quarantine,salvage,battle,threat,secrets")
    g.add_argument("--intensity", type=float, default=0.5)
    g.add_argument("--out", default="geomorph_out")
    g.add_argument("--pps", type=int, default=16)
    sub.add_parser("archetypes", help="list the archetypes (and any invalid files)")
    sub.add_parser("validate", help="validate every archetype file")
    sub.add_parser("tags", help="print the tile function-tag table")
    sub.add_parser("gaps", help="per-archetype zones that have no suitable tile")
    sub.add_parser("review", help="tiles whose edge detection needs a human check")
    m = sub.add_parser("manifest", help="rebuild the tile manifest from the pack (slow, one time)")
    m.add_argument("--out")
    a = ap.parse_args(argv)

    if a.cmd == "archetypes":
        A = archmod.load_all()
        for p in A.pop("_problems"):
            print("INVALID", p)
        for n in sorted(A):
            print(f"{A[n]['group']:10} {n}  [{A[n]['topology']}]")
        return 0
    if a.cmd == "validate":
        A = archmod.load_all()
        problems = A.pop("_problems")
        print(f"{len(A)} valid archetypes, {len(problems)} invalid")
        for p in problems:
            print(" ", p)
        return 1 if problems else 0
    if a.cmd == "manifest":
        from .build_manifest import build
        d = _tiles_dir(a) or sys.exit("give --tiles /path/to/pack")
        build(d, a.out)
        return 0
    reg = Registry.load(tiles_dir=_tiles_dir(a))
    if a.cmd == "tags":
        print(reports.tag_table_report(reg))
        return 0
    if a.cmd == "gaps":
        for name, gaps in reports.all_gaps(reg).items():
            print(f"{name}: {len(gaps)} gap(s)")
            for gp in gaps:
                print(f"   - {gp['name']}: {gp['reason']}")
        return 0
    if a.cmd == "review":
        for r in reports.edge_review_list(reg):
            print(r["id"], r["type"], r["title"], r["sides"])
        return 0
    if a.cmd == "generate":
        opts = {"kind": a.kind, "archetype": a.archetype, "scale": a.scale, "environment": a.env,
                "condition": a.condition, "mode": a.mode, "ship_type": a.ship_type, "tonnage": a.tonnage,
                "symmetric": not a.no_symmetry, "fins": not a.no_fins, "orientation": a.orientation,
                "seed": a.seed, "theme": a.theme, "overlays": [o for o in a.overlays.split(",") if o],
                "intensity": a.intensity}
        res = pipeline.generate(reg, opts)
        print(f"{res.meta['name']}: {sum(len(g.placed) for g in res.grids)} tiles, {len(res.grids)} level(s)")
        for i in res.issues:
            print("  issue:", i)
        if reg.tiles_dir is None:
            Path(a.out).mkdir(exist_ok=True)
            exporter.save_layout(res, Path(a.out) / "layout_gm.json")
            print("no tile pack given: wrote the layout JSON only")
            return 0
        files = exporter.export_all(res, a.out, render.TileImages(reg.tiles_dir), pps=a.pps)
        print("\n".join(files))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
