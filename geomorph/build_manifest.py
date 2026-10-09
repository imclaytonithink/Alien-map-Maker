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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tiles_dir")
    ap.add_argument("--out")
    ap.add_argument("--workers", type=int)
    a = ap.parse_args(argv)
    build(a.tiles_dir, a.out, a.workers)


if __name__ == "__main__":
    main()
