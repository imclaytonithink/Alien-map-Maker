"""Batch 16 (no Qt): mirror and grid copies, stamp keys, rolling backups,
relinking missing images, app-data files, layer export flags and choosing a
map's asset folder."""
from __future__ import annotations

import json
import os
import tempfile

from core.backups import BACKUP_SLOTS, backup_folder, list_backups, rotate_backup
from core.project import (Layer, Level, Piece, Project, choose_asset_store,
                          new_project)
from core.relink import match_score, relink_plan
from core.stamps import (SLOT_COUNT, StampError, asset_slot, node_slot, slot_label,
                         slots_from_json, slots_to_json)
from core.transforms import (grid_offsets, mirror_piece_data, on_mirror_line,
                             remap_groups, visual_center)
from core.userfiles import (load_path_list, map_base_name, recent_file_path,
                            safe_file_stem, save_path_list, thumbnail_path)


def close(a, b, eps=1e-6):
    return abs(a - b) <= eps


def check_mirror():
    image = Piece(asset_path="a.png", x=100, y=40, w=60, h=20, scale=2.0,
                  rotation=30, flip_h=False).to_dict()
    copy = mirror_piece_data(image, "v", 500)
    cx, cy = visual_center(image)
    mx, my = visual_center(copy)
    assert close(mx, 1000 - cx) and close(my, cy)
    assert close(copy["rotation"], 330) and copy["flip_h"] is True
    assert copy["flip_v"] is False and copy["id"] != image["id"]
    assert copy["w"] == 60 and copy["scale"] == 2.0          # size is untouched
    back = mirror_piece_data(copy, "v", 500)                  # mirroring twice
    for key in ("x", "y", "rotation", "flip_h", "flip_v"):
        assert close(float(back[key]), float(image[key])), key

    horizontal = mirror_piece_data(image, "h", 300)
    hx, hy = visual_center(horizontal)
    assert close(hx, cx) and close(hy, 600 - cy)
    assert horizontal["flip_v"] is True and horizontal["flip_h"] is False

    text = Piece(is_text=True, text="CARGO", x=10, y=10, w=80, h=20,
                 rotation=10).to_dict()
    mirrored_text = mirror_piece_data(text, "v", 100)
    assert mirrored_text["flip_h"] is False, "text must stay readable"
    assert close(mirrored_text["rotation"], 350)
    bar = Piece(is_scale_bar=True, x=0, y=0, w=50, h=10).to_dict()
    assert mirror_piece_data(bar, "h", 50)["flip_v"] is False

    line = Piece(is_connector=True, x=0, y=0, w=40, h=20).to_dict()
    assert mirror_piece_data(line, "v", 100)["flip_h"] is True, "arrows do flip"
    upright = mirror_piece_data(Piece(x=0, y=0, w=10, h=10).to_dict(), "v", 50)
    assert upright["rotation"] == 0.0

    centered = Piece(x=90, y=0, w=20, h=10).to_dict()          # center x = 100
    assert on_mirror_line(centered, "v", 100)
    assert not on_mirror_line(centered, "v", 120)
    assert on_mirror_line(centered, "h", 5) and not on_mirror_line(centered, "h", 50)
    try:
        mirror_piece_data(image, "x", 1)
        raise AssertionError("an unknown axis must be rejected")
    except ValueError:
        pass


def check_grid_copies():
    offsets = grid_offsets(2, 3, 10.0, 7.0)
    assert len(offsets) == 5 and (0, 0) not in offsets
    assert offsets[:2] == [(10.0, 0.0), (20.0, 0.0)] and offsets[2] == (0.0, 7.0)
    assert grid_offsets(1, 1, 5, 5) == []
    assert len(grid_offsets(0, -3, 5, 5)) == 0                # clamped to 1 x 1

    batch = [{"group_id": "g1"}, {"group_id": "g1"}, {"group_id": "g2"},
             {"group_id": ""}, {}]
    remap_groups(batch)
    assert batch[0]["group_id"] == batch[1]["group_id"] not in ("", "g1")
    assert batch[2]["group_id"] not in ("", "g2", batch[0]["group_id"])
    assert batch[3]["group_id"] == "" and "group_id" not in batch[4]


def check_stamps():
    slot = asset_slot("Symbols/Door [5x5].png")
    assert slot["kind"] == "asset" and slot["name"] == "Door [5x5]"
    try:
        asset_slot("")
        raise AssertionError("an empty asset path must be refused")
    except StampError:
        pass
    piece = Piece(asset_path="props/crate.png", name="Crate", x=5, y=6, w=70, h=70,
                  rotation=90, tint_mode="override", tint_color="#ff0000",
                  tint_strength=0.4, locked=True, group_id="g", layer="L")
    pinned = node_slot(piece.to_dict())
    template = pinned["template"]
    assert pinned["kind"] == "node" and pinned["name"] == "Crate"
    for key in ("id", "x", "y", "z", "layer", "group_id", "locked"):
        assert key not in template, key
    assert template["rotation"] == 90 and template["tint_color"] == "#ff0000"
    try:
        node_slot(Piece(embedded="aGVsbG8=", w=5, h=5).to_dict())
        raise AssertionError("embedded images can't live on the hotbar")
    except StampError as exc:
        assert "library" in str(exc)
    text = node_slot(Piece(is_text=True, text="Airlock\nB deck").to_dict())
    assert slot_label(text) == "Text: Airlock"

    slots = [None] * SLOT_COUNT
    slots[0], slots[4] = slot, pinned
    restored = slots_from_json(slots_to_json(slots))
    assert len(restored) == SLOT_COUNT
    assert restored[0] == slot and restored[4]["template"]["rotation"] == 90
    assert restored[1] is None
    junk = json.dumps([{"kind": "bogus"}, 5, {"kind": "asset"},
                       {"kind": "node", "template": {"embedded": "xx"}}] + [None] * 20)
    assert slots_from_json(junk) == [None] * SLOT_COUNT
    assert slots_from_json("not json") == [None] * SLOT_COUNT
    assert slot_label(None) == ""


def check_backups():
    with tempfile.TemporaryDirectory() as temp:
        map_path = os.path.join(temp, "maps", "Ship.bmap")
        os.makedirs(os.path.dirname(map_path))
        folder = backup_folder(os.path.join(temp, "backups"), map_path)
        assert os.path.basename(folder).startswith("Ship-")
        other = backup_folder(os.path.join(temp, "backups"),
                              os.path.join(temp, "elsewhere", "Ship.bmap"))
        assert other != folder, "same name in another folder gets its own backups"
        assert rotate_backup(map_path, folder) is None, "nothing on disk yet"

        written = []
        for version in range(1, 7):                    # six auto-save cycles
            with open(map_path, "w", encoding="utf-8") as handle:
                handle.write(f"version {version}")
            written.append(rotate_backup(map_path, folder, now=1000 + version))
        backups = list_backups(folder)
        assert len(backups) == BACKUP_SLOTS == 4
        contents = [open(b["path"], encoding="utf-8").read() for b in backups]
        assert contents == ["version 6", "version 5", "version 4", "version 3"], contents
        assert written[4] == written[0], "the 5th cycle replaced the oldest slot"
        assert rotate_backup(map_path, folder, now=2000) is None, \
            "an unchanged map is not backed up twice"
        with open(os.path.join(folder, "source.txt"), encoding="utf-8") as note:
            assert note.read().strip() == os.path.abspath(map_path)
        files = sorted(name for name in os.listdir(folder) if name.endswith(".bmap"))
        assert files == ["backup-1.bmap", "backup-2.bmap", "backup-3.bmap",
                         "backup-4.bmap"], files


def check_relink():
    known = ["Geomorphs (2)/Core/Room A.png", "Symbols/Door.png",
             "Pack1/Tiles/Floor.png", "Pack2/Tiles/Floor.png",
             "Pack3/Other/Floor.png"]
    assert match_score("Geomorphs/Core/Room A.png", known[0]) == 2
    plan, ambiguous, not_found = relink_plan(
        ["Geomorphs/Core/room a.PNG", "Old/Symbols/Door.png", "X/Tiles/Floor.png",
         "Pack2/Tiles/Floor.png", "Nowhere/Vent.png"], known)
    assert plan["Geomorphs/Core/room a.PNG"] == known[0], plan
    assert plan["Old/Symbols/Door.png"] == "Symbols/Door.png"
    assert plan["Pack2/Tiles/Floor.png"] == "Pack2/Tiles/Floor.png"
    assert ambiguous == ["X/Tiles/Floor.png"], "two equally good matches stay alone"
    assert not_found == ["Nowhere/Vent.png"]


def check_userfiles():
    assert safe_file_stem('Deck B: "Cargo"/Hold?') == "Deck B_ _Cargo__Hold_"
    assert safe_file_stem("   ") == "map" and safe_file_stem("CON") == "_CON"
    assert safe_file_stem("nul.txt") == "_nul.txt" and safe_file_stem("trail. ") == "trail"
    assert map_base_name("Untitled Map", None) == "Untitled Map"
    assert map_base_name("ignored", os.path.join("x", "Hope's Last Day.bmap")) == \
        "Hope's Last Day"
    with tempfile.TemporaryDirectory() as temp:
        thumb = thumbnail_path(temp, os.path.join(temp, "maps", "a.bmap"))
        assert os.path.dirname(thumb) == os.path.join(temp, "thumbnails")
        assert thumb == thumbnail_path(temp, os.path.join(temp, "maps", ".", "a.bmap"))
        assert thumb != thumbnail_path(temp, os.path.join(temp, "other", "a.bmap"))
        recent = recent_file_path(os.path.join(temp, "appdata"))
        assert load_path_list(recent) == []
        items = [f"/maps/{n}.bmap" for n in range(20)] + ["/maps/0.bmap", 5, ""]
        assert save_path_list(items, recent)
        loaded = load_path_list(recent)
        assert len(loaded) == 12 and loaded[0] == "/maps/0.bmap"
        with open(recent, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        assert load_path_list(recent) == []


def check_layers_and_store():
    project = new_project()
    level = project.levels[0]
    level.layers[1].export = False
    data = project.to_dict()
    assert data["version"] == 9
    assert data["levels"][0]["layers"][1]["export"] is False
    again = Project.from_dict(json.loads(json.dumps(data)))
    assert [l.export for l in again.levels[0].layers] == [True, False, True, True]
    legacy = Level.from_dict({"layers": [{"id": "a", "name": "Old", "visible": False,
                                          "unknown": 1}]})
    assert legacy.layers[0].export is True and legacy.layers[0].visible is False
    odd = Layer.from_dict({"opacity": "bad", "color": "red", "export": 0})
    assert odd.opacity == 1.0 and odd.color == "" and odd.export is False

    with tempfile.TemporaryDirectory() as temp:
        mine, default = os.path.join(temp, "mine"), os.path.join(temp, "default")
        for store, names in ((mine, ["a.png"]), (default, ["a.png", "b.png"])):
            os.makedirs(store)
            for name in names:
                open(os.path.join(store, name), "wb").close()
        gone = os.path.join(temp, "other-pc", "asset_store")
        assert choose_asset_store(gone, default, ["a.png"]) == default
        assert choose_asset_store("", default, ["a.png"]) == default
        assert choose_asset_store(mine, default, ["a.png"]) == mine
        assert choose_asset_store(mine, default, []) == mine
        assert choose_asset_store(mine, default, ["a.png", "b.png"]) == default
        assert choose_asset_store(default, default, ["zzz.png"]) == default

        project.asset_store = mine
        level.pieces = [Piece(asset_path="a.png"), Piece(asset_path="b.png"),
                        Piece(asset_path="b.png"), Piece(asset_path="gone.png"),
                        Piece(asset_path="x.png", embedded="aGk="),
                        Piece(is_text=True, asset_path="t.png"), Piece()]
        assert project.referenced_assets() == {"a.png": 1, "b.png": 2, "gone.png": 1}
        assert project.missing_assets() == {"b.png": 2, "gone.png": 1}
        project.fallback_asset_stores = [default]
        assert project.resolve_asset("b.png") == os.path.abspath(
            os.path.join(default, "b.png"))
        assert project.missing_assets() == {"gone.png": 1}
        assert "fallback_asset_stores" not in project.to_dict(), "runtime only"
        assert project.relink_assets({"gone.png": "a.png", "x.png": "b.png"}) == 1
        assert project.missing_assets() == {}
        assert level.pieces[4].asset_path == "x.png", "embedded nodes are not relinked"


def main():
    check_mirror()
    check_grid_copies()
    check_stamps()
    check_backups()
    check_relink()
    check_userfiles()
    check_layers_and_store()
    print("Batch 16 mirror, grid copy, stamp, backup, relink and asset-folder checks passed.")


if __name__ == "__main__":
    main()
