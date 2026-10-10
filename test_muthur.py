"""MU/TH/UR terminal export: catalog, code round trip, marker-to-room matching and checks."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import muthur as M
from core.project import Level, Piece, Project, ZoneRegion


def test_catalog_is_frozen_and_complete():
    cat = M.catalog()
    assert cat["version"] == 1
    names = [a["name"] for a in cat["archetypes"]]
    assert names[0] == "Ship" and len(names) == 27, names
    arch_dir = Path(__file__).resolve().parent / "geomorph" / "data" / "archetypes"
    on_disk = {json.loads(p.read_text(encoding="utf-8"))["name"] for p in arch_dir.glob("*.json")}
    assert on_disk <= set(names), on_disk - set(names)
    for a in cat["archetypes"]:
        assert 1 <= len(a["rooms"]) <= 64
        assert 0 <= a["setting"] < len(cat["settings"])
        for r in a["rooms"]:
            assert r["pool"] and r["name"]
    town = cat["archetypes"][M.arch_index("Frontier colony outpost")]
    by_name = {r["name"]: r for r in town["rooms"]}
    assert by_name["Landing pad"]["allowed"] is True           # the GM allowed it
    deep = cat["archetypes"][M.arch_index("Deep mine")]
    assert not {r["name"]: r for r in deep["rooms"]}["Ore chambers"]["allowed"]


def test_code_round_trip():
    rng = random.Random(5)
    cat = M.catalog()["archetypes"]
    for _ in range(400):
        floors = rng.randint(1, 16)
        arch = rng.randrange(len(cat))
        terms = []
        for _ in range(rng.randint(1, 26)):
            a = arch if rng.random() < 0.8 else rng.randrange(len(cat))
            terms.append(M.Terminal(a, rng.randrange(len(cat[a]["rooms"])), rng.randint(1, 9),
                                    rng.randint(1, floors), rng.randrange(4)))
        spec = M.Spec(arch, floors, rng.randrange(4096), terms)
        code = M.encode(spec)
        back = M.decode(code.lower().replace("-", " "))
        assert (back.arch, back.floors, back.seed) == (spec.arch, spec.floors, spec.seed)
        assert [vars(t) for t in back.terminals] == [vars(t) for t in spec.terminals]


def test_code_is_short_and_catches_typos():
    a = M.arch_index("Frontier colony outpost")
    spec = M.Spec(a, 2, 77, [M.Terminal(a, r, 1, 1 + r % 2, r % 4 if r < 4 else 0) for r in range(6)])
    code = M.encode(spec)
    assert len(M.normalize(code)) <= 20, code          # six terminals on two floors
    raw = M.normalize(code)
    for i in range(len(raw)):
        for ch in M.ALPHABET:
            if ch == raw[i]:
                continue
            bad = raw[:i] + ch + raw[i + 1:]
            try:
                M.decode(bad)
            except ValueError:
                continue
            raise AssertionError(f"typo not caught: {bad}")
    try:
        M.decode(raw[:-1])
    except ValueError:
        pass
    else:
        raise AssertionError("missing character not caught")


def test_closest_names():
    cat = M.catalog()["archetypes"]

    def name(hit):
        return cat[hit[0]]["rooms"][hit[1]]["name"]
    assert name(M.closest("Medical clinic")) == "Medical clinic"
    assert name(M.closest("Ripley's med bay", prefer_arch=M.arch_index("Frontier colony outpost"))) in (
        "Medical clinic", "Medical", "Med-bay")
    assert "armory" in name(M.closest("THE ARMOURY")).lower() or "armory" in name(M.closest("Armory")).lower()
    assert M.closest("zzzz qqq") is None


def _project():
    proj = Project()
    cell = proj.cell_size
    lv = proj.levels[0]
    lv.name = "Deck 1"
    tile = Piece(name="tile", w=20 * 300, h=20 * 300, scale=cell / 300, x=0, y=0)
    tile.room = {"arch": "Frontier colony outpost", "zone": "habitat#2", "base": "habitat",
                 "name": "Habitat module", "tags": ["staterooms"], "access": "public",
                 "states": ["quarantine"], "fw": 20 * cell, "fh": 20 * cell, "rot": 0.0}
    lv.add(tile)
    lv.add(M.make_marker(5 * cell, 5 * cell, cell))
    lv2 = proj.add_level("Deck 2")
    lv2.zones.append(ZoneRegion(name="Zone", label="Comms shack",
                                points=[(0, 0), (10 * cell, 0), (10 * cell, 10 * cell), (0, 10 * cell)]))
    lv2.add(M.make_marker(2 * cell, 2 * cell, cell))
    return proj


def test_scan_and_check():
    proj = _project()
    sc = M.scan(proj)
    assert sc.floors == 2 and sc.floor_names == ["Deck 1", "Deck 2"]
    f1, f2 = sc.found
    assert f1.source == "geomorph" and f1.terminal.name == "Habitat module 2" and f1.floor == 1
    assert f1.terminal.state == M.QUARANTINE
    assert f2.source == "zone" and f2.terminal.name == "Comms shack" and f2.floor == 2
    assert any("at least two rooms" in e for e in M.check(sc, 2)), "a quarantined room can't be used"
    f1.terminal.state = M.POWER_OUT
    assert M.check(sc, 2) == []
    assert any("You said" in e for e in M.check(sc, 3))
    spec = M.build_spec(sc, seed=9)
    back = M.decode(M.encode(spec))
    assert [t.name for t in back.terminals] == ["Habitat module 2", "Comms shack"]
    assert [t.floor for t in back.terminals] == [1, 2]
    # outside any room, two in one room, a room that may not have one
    proj.levels[0].add(M.make_marker(5.5 * proj.cell_size, 5 * proj.cell_size, proj.cell_size))
    proj.levels[1].add(M.make_marker(40 * proj.cell_size, 40 * proj.cell_size, proj.cell_size))
    errs = M.check(M.scan(proj), 4)
    assert any("same room" in e for e in errs), errs
    assert any("not inside a room" in e for e in errs), errs
    proj2 = _project()
    proj2.levels[1].zones[0].label = "Ore chambers"
    errs = M.check(M.scan(proj2), 2)
    assert any("not allowed" in e for e in errs), errs


def test_state_limits_per_floor():
    proj = _project()
    cell = proj.cell_size
    lv = proj.levels[0]
    t2 = Piece(name="tile", w=6000, h=6000, scale=cell / 300, x=20 * cell, y=0)
    t2.room = dict(lv.pieces[0].room, zone="habitat#3")
    lv.add(t2)
    lv.add(M.make_marker(25 * cell, 5 * cell, cell))
    sc = M.scan(proj)
    assert any("at most 1 per floor" in e for e in M.check(sc, 3))
    notes = M.limit_states(sc)
    assert len(notes) == 1 and not any("per floor" in e for e in M.check(sc, 3))


def test_needs_two_usable_rooms():
    proj = _project()
    sc = M.scan(proj)
    for f in sc.found:
        f.terminal.state = M.QUARANTINE if f.floor == 1 else M.LOCKDOWN
    assert any("at least two rooms" in e for e in M.check(sc, 2))
    sc.found[0].terminal.state = M.POWER_OUT
    assert any("at least two rooms" in e for e in M.check(sc, 2))
    sc.found[1].terminal.state = M.NORMAL
    assert M.check(sc, 2) == []


def test_marker_survives_save_and_load():
    proj = _project()
    data = Project.from_dict(json.loads(json.dumps(proj.to_dict())))
    marks = [p for lv in data.levels for p in lv.pieces if p.is_terminal]
    assert len(marks) == 2 and marks[0].embedded
    tiles = [p for p in data.levels[0].pieces if p.room]
    assert tiles and tiles[0].room["zone"] == "habitat#2"


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)


if __name__ == "__main__":
    main()
