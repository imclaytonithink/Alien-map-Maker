"""Find missing library images again by file name (no Qt imports).

When a map's images are not where it expects them (the library was imported
into differently named folders, or a pack was re-imported), each missing path
is matched with library images of the same file name. The candidate sharing
the most trailing folder names wins; ties are left alone rather than guessed.
"""
from __future__ import annotations


def _parts(path: str) -> list[str]:
    return [part for part in str(path).replace("\\", "/").casefold().split("/") if part]


def match_score(missing: str, candidate: str) -> int:
    """How many trailing path parts (file name first) two paths share."""
    a, b = _parts(missing), _parts(candidate)
    score = 0
    while score < min(len(a), len(b)) and a[-1 - score] == b[-1 - score]:
        score += 1
    return score


def relink_plan(missing, known_paths):
    """Return ``(plan, ambiguous, not_found)``: ``plan`` maps each missing path
    that has one clear match to that library path."""
    by_name: dict[str, list[str]] = {}
    for path in known_paths:
        parts = _parts(path)
        if parts:
            by_name.setdefault(parts[-1], []).append(path)
    plan: dict[str, str] = {}
    ambiguous: list[str] = []
    not_found: list[str] = []
    for path in missing:
        parts = _parts(path)
        candidates = by_name.get(parts[-1], []) if parts else []
        if not candidates:
            not_found.append(path)
            continue
        scored = sorted(((match_score(path, candidate), candidate)
                         for candidate in candidates), reverse=True)
        best = scored[0][0]
        top = [candidate for score, candidate in scored if score == best]
        if len(top) == 1:
            plan[path] = top[0]
        else:
            ambiguous.append(path)
    return plan, ambiguous, not_found
