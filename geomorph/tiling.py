"""Tile choice: map zones onto tiles with rotation, mirroring and edge fit."""
from __future__ import annotations

from .placement import LevelGrid, Orientation, orientations


class TilePicker:
    """Scores tiles for a zone's tags and picks one that fits its neighbours."""

    def __init__(self, registry, rng):
        self.reg = registry
        self.rng = rng
        self.used: dict = {}

    # -- tag scoring ---------------------------------------------------
    def tag_score(self, tile, tags) -> float:
        if not tags:
            return 0.0
        w = [tile.tags.get(t, 0.0) for t in tags]
        best = max(w)
        if best <= 0:
            return 0.0
        return best + 0.25 * (sum(w) - best) / max(1, len(tags) - 1) if len(tags) > 1 else best

    def pool(self, tile_type, w, h, tags, minimum=0.5, exclude=()):
        out = []
        for t in self.reg.tiles.values():
            if t.type != tile_type or {t.w, t.h} != {w, h} or t.id in exclude:
                continue
            s = self.tag_score(t, tags)
            if s >= minimum:
                out.append((s, t))
        return out

    def gap(self, tile_type, w, h, tags) -> bool:
        return not self.pool(tile_type, w, h, tags)

    # -- selection -----------------------------------------------------
    def choose(self, grid: LevelGrid, x, y, w, h, tags, tile_type="standard", prefer_open=(),
               fallback_tags=("multipurpose", "cargo", "recreation"), fixed=None, reuse_penalty=0.35,
               allowed_orients=None, topn=6, strict_fit=False, reject=None):
        """Pick ``(tile, orientation, fit)`` for the box at (x, y), or ``None``.

        ``prefer_open`` lists sides (N/E/S/W) that should carry doors (links to
        neighbours that do not touch, e.g. walkways).
        """
        if fixed is not None:
            cands = [(1.0, fixed)]
        else:
            cands = self.pool(tile_type, w, h, tags)
            if not cands:
                cands = self.pool(tile_type, w, h, list(fallback_tags), minimum=0.3)
            if not cands:
                cands = [(0.1, t) for t in self.reg.tiles.values()
                         if t.type == tile_type and {t.w, t.h} == {w, h}]
        if reject is not None:
            kept = [(s_, t_) for s_, t_ in cands if not reject(t_)]
            if not kept:                 # rule is strict: widen to any tile of this size that obeys it
                kept = [(0.1, t_) for t_ in self.reg.tiles.values()
                        if t_.type == tile_type and {t_.w, t_.h} == {w, h} and not reject(t_)]
            cands = kept or cands
        scored = []
        for ts, tile in cands:
            best = None
            for o in orientations(tile):
                if (o.w, o.h) != (w, h):
                    continue
                if allowed_orients is not None and not allowed_orients(tile, o):
                    continue
                f = grid.fit(x, y, o, tile.type)
                if not f.ok:
                    continue
                sc = f.score() + 0.6 * sum(1 for side in prefer_open
                                           if any(c == 1 for c in o.cls(side)))
                if strict_fit and not f.perfect:
                    continue
                jitter = self.rng.random() * 0.4
                if best is None or sc + jitter > best[0]:
                    best = (sc + jitter, o, f)
            if best is None:
                continue
            total = ts * 3.0 + best[0] - reuse_penalty * self.used.get(tile.id, 0)
            scored.append((total, tile, best[1], best[2]))
        if not scored:
            return None
        scored.sort(key=lambda r: -r[0])
        top = scored[:topn]
        # weighted pick among the best few: variety without leaving the good fits
        lowest = top[-1][0]
        weights = [max(0.05, r[0] - lowest + 0.5) for r in top]
        total, tile, o, f = self.rng.choices(top, weights=weights)[0]
        self.used[tile.id] = self.used.get(tile.id, 0) + 1
        return tile, o, f
