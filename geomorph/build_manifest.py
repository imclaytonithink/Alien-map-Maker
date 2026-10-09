"""Build ``data/tile_manifest.json`` from an extracted tile pack.

    python -m geomorph.build_manifest /path/to/pack [--out manifest.json]

The pack folder is the extracted ``RPG-Mobius-Geomorphs-Geomorphs-High-Res-*``
ZIP (folders ``100x100 Core``, ``100x50 Edge``, ``50x50 Corner``, ``100x100 End``,
``200x100 Megamorph``). Images are analysed in parallel; this takes a few
minutes once and never has to run again.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import DATA_DIR
from .edges import REVIEW_CONF, analyse_image
from .placement import hull_sides
from .registry import Registry, scan_tiles


def _job(args):
    path, w, h = args
    try:
        return analyse_image(path, w, h)
    except Exception as exc:                        # corrupt image: flag, don't die
        return {"error": str(exc)}


def build(tiles_dir, out=None, workers=None, progress=True):
    tiles_dir = Path(tiles_dir)
    tiles = scan_tiles(tiles_dir)
    jobs = [(str(tiles_dir / t.image), t.w, t.h) for t in tiles]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i, (t, res) in enumerate(zip(tiles, pool.map(_job, jobs, chunksize=2)), 1):
            if "error" in res:
                t.review = True
            else:
                t.edges = res
                hull = hull_sides(t)
                conns = [e["conf"] for side, e in res.items() if side not in hull] or [1.0]
                t.review = min(conns) < REVIEW_CONF
            t.px = (0, 0)
            if progress and i % 50 == 0:
                print(f"  {i}/{len(tiles)}", file=sys.stderr, flush=True)
    reg = Registry(tiles, tiles_dir)
    out = Path(out) if out else DATA_DIR / "tile_manifest.json"
    reg.save(out)
    flagged = sum(t.review for t in tiles)
    print(f"{len(tiles)} tiles -> {out} ({flagged} flagged for edge review)")
    return reg


def add_custom_pack(manifest=None, dirs=(), workers=None):
    """Add wings, noses/tails and transitions from the extracted Custom Tiles pack (and the AF wings
    that live in the main pack's Misc folder) to an existing manifest. Safe to re-run."""
    from .registry import scan_noses, scan_wings
    manifest = Path(manifest) if manifest else DATA_DIR / "tile_manifest.json"
    reg = Registry.load(manifest)
    for k in [k for k, t in reg.tiles.items() if t.type in ("wing", "trans") or (t.type == "end" and t.w == 10)]:
        reg.tiles.pop(k)
    new = []
    for d in dirs:
        new += scan_wings(d) + scan_noses(d)
    todo = [t for t in new if t.type in ("end", "trans")]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        res = list(pool.map(_job, [(str(Path(d) / t.image), t.w, t.h) for t in todo
                                   for d in [next(x for x in dirs if (Path(x) / t.image).exists())]], chunksize=4))
    for t, r in zip(todo, res):
        if "error" in r:
            t.review = True
        else:
            t.edges = r
    for t in new:
        reg.add(t)
    reg.save(manifest)
    print(f"{len(new)} custom-pack tiles added ({sum(t.type == 'wing' for t in new)} wings, "
          f"{sum(t.type == 'trans' for t in new)} transitions, {sum(t.type == 'end' for t in new)} nose/tail ends)")
    return reg


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tiles_dir")
    ap.add_argument("--out")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--extra", nargs="*", help="extracted Custom Tiles pack folder(s): add wings/noses/transitions to the manifest")
    a = ap.parse_args(argv)
    if a.extra:
        add_custom_pack(a.out, [a.tiles_dir] + a.extra, a.workers)
    else:
        build(a.tiles_dir, a.out, a.workers)


if __name__ == "__main__":
    main()
