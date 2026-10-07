"""Batch 15: placed guides and grid coordinates — model and pure helpers."""
from __future__ import annotations

import json

from core.guides import (column_label, describe_position, format_amount,
                         grid_counts, label_step, layout_positions, nearest,
                         row_label)
from core.project import DEFAULT_GUIDE_COLOR, Guide, Level, Project, new_project


def check_model():
    project = new_project()
    level = project.levels[0]
    assert level.guides == []
    first = level.add_guide("v", 210.0)
    assert first is not None and first.axis == "v" and first.pos == 210.0
    assert level.add_guide("v", 210.0) is None, "duplicates are ignored"
    assert level.add_guide("h", 210.0) is not None, "same position, other axis is fine"
    assert level.add_guide("x", 5.0) is None and level.add_guide("v", float("nan")) is None
    assert level.guide_positions("v") == [210.0] and level.guide_positions("h") == [210.0]
    assert level.find_guide(first.id) is first and level.has_guide("v", 210.0)
    assert level.remove_guide(first.id) and not level.remove_guide(first.id)
    assert level.guide_positions("v") == []

    # defaults: guides shown, snapping on, unlocked, magenta, coordinates on the
    # rails; every editor aid is left out of exports unless asked for
    assert project.show_guides and project.snap_to_guides and not project.lock_guides
    assert project.guide_color == DEFAULT_GUIDE_COLOR == "#ff2bd6"
    assert project.show_coordinates
    assert not (project.export_guides or project.export_centerlines
                or project.export_coordinates)


def check_round_trip():
    project = new_project()
    project.add_level("Deck 2")
    project.levels[0].add_guide("v", 350.0)
    project.levels[0].add_guide("h", 122.5)
    project.levels[1].add_guide("v", 70.0)
    project.lock_guides = True
    project.snap_to_guides = False
    project.guide_color = "#00e5ff"
    project.guide_opacity = 0.5
    project.show_coordinates = False
    project.export_guides = project.export_centerlines = project.export_coordinates = True
    data = json.loads(json.dumps(project.to_dict()))
    assert data["version"] == 9
    restored = Project.from_dict(data)
    assert [(g.axis, g.pos) for g in restored.levels[0].guides] == [("v", 350.0), ("h", 122.5)]
    assert [(g.axis, g.pos) for g in restored.levels[1].guides] == [("v", 70.0)]
    assert [g.id for g in restored.levels[0].guides] == [g.id for g in project.levels[0].guides]
    assert restored.lock_guides and not restored.snap_to_guides
    assert restored.guide_color == "#00e5ff" and restored.guide_opacity == 0.5
    assert not restored.show_coordinates
    assert restored.export_guides and restored.export_centerlines and restored.export_coordinates

    # undo/redo restores through restore_from(), which copies every saved key
    target = new_project()
    target.restore_from(restored)
    assert len(target.levels) == 2 and target.levels[1].guides[0].pos == 70.0
    assert target.lock_guides and target.guide_color == "#00e5ff"


def check_old_and_damaged_files():
    legacy = new_project().to_dict()
    legacy["version"] = 7
    for key in ("show_guides", "snap_to_guides", "lock_guides", "guide_color",
                "guide_opacity", "show_coordinates", "export_guides",
                "export_centerlines", "export_coordinates"):
        legacy.pop(key)
    for level in legacy["levels"]:
        level.pop("guides")
    old = Project.from_dict(legacy)
    assert old.levels[0].guides == [] and old.show_guides and old.show_coordinates
    assert not old.export_centerlines, "old maps no longer bake centerlines into exports"

    damaged = new_project().to_dict()
    damaged.update(guide_color="magenta", guide_opacity="loud")
    damaged["levels"][0]["guides"] = [
        {"axis": "diagonal", "pos": "inf"}, "not a guide", {"pos": 12}, {"axis": "h", "pos": "7.5"}]
    fixed = Project.from_dict(damaged)
    assert fixed.guide_color == DEFAULT_GUIDE_COLOR and fixed.guide_opacity == 0.9
    assert [(g.axis, g.pos) for g in fixed.levels[0].guides] == [("v", 0.0), ("v", 12.0),
                                                                  ("h", 7.5)]
    assert all(g.id for g in fixed.levels[0].guides)
    assert Project.from_dict({**damaged, "guide_opacity": 9}).guide_opacity == 1.0
    assert Guide.from_dict({"axis": "h", "pos": 3, "id": "abc"}).id == "abc"
    assert Level.from_dict({"name": "x"}).guides == []


def check_helpers():
    assert [column_label(i) for i in (0, 1, 25, 26, 27, 51, 52, 701, 702)] == \
        ["A", "B", "Z", "AA", "AB", "AZ", "BA", "ZZ", "AAA"]
    assert column_label(-1) == "" and row_label(0) == "1" and row_label(159) == "160"
    assert grid_counts(2100, 2100, 70) == (30, 30)
    assert grid_counts(2101, 700, 70) == (31, 10), "a partial last square still gets a label"
    assert label_step(70, 14) == 1 and label_step(10, 14) == 2 and label_step(3, 14) == 10
    assert layout_positions(2100, 70, every=5) == [350.0, 700.0, 1050.0, 1400.0, 1750.0]
    assert layout_positions(2100, 70, every=30) == [], "no guides on the map edges"
    assert layout_positions(2100, 70, margin=2, center=True) == [140.0, 1050.0, 1960.0]
    assert layout_positions(700, 70, every=5, center=True) == [350.0]
    assert layout_positions(700, 70, margin=6) == [], "margins wider than half the map are ignored"
    assert format_amount(14.0) == "14" and format_amount(6.5) == "6.5"
    assert format_amount(2.3333) == "2.33"
    assert describe_position("v", 980, 70, 5) == "x 14 sq · 70 ft"
    assert describe_position("h", 455, 70, 5) == "y 6.5 sq · 32.5 ft"
    assert describe_position("v", 35, 70, 10) == "x 0.5 sq · 5 ft"
    assert nearest(103, [100, 110], 5) == 100 and nearest(120, [100, 110], 5) is None
    assert nearest(105, [100, 110], 5) == 110, "ties go to the later candidate"


def main():
    check_model()
    check_round_trip()
    check_old_and_damaged_files()
    check_helpers()
    print("Batch 15 guide and grid-coordinate model checks passed.")


if __name__ == "__main__":
    main()
