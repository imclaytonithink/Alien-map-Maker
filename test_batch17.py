"""Pure-Python checks: cut-out geometry, sections and clone windows, level
backdrops, door-mode stamps, backdrop textures in project bundles, and floor
textures uploaded from a file into the library's Backdrops folder."""
from __future__ import annotations

import json
import math
import os
import struct
import tempfile
import zipfile

from core import cutouts
from core.asset_manager import AssetLibrary
from core.bundle import export_project_bundle
from core.project import BACKDROP_FOLDER, Level, Piece, Project
from core.stamps import (asset_slot, edge_placement, is_edge_slot, looks_like_door,
                         node_slot, slots_from_json, slots_to_json, with_edge)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


def check_polygons():
    assert cutouts.clean_polygon([[0, 0], [1, 0]]) is None, "needs three points"
    assert cutouts.clean_polygon([[0, 0], [1, 0], [2, 0]]) is None, "zero area"
    assert cutouts.clean_polygon([[0, 0], ["x", 1], [1, 1]]) is None
    assert cutouts.clean_polygon([[0, 0], [float("nan"), 1], [1, 1]]) is None
    poly = cutouts.clean_polygon([[0, 0], [0, 0], [1, 0], [1, 1], [0, 1], [0, 0]])
    assert poly == [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], poly
    assert cutouts.clean_polygon([[-50, 0], [1, 0], [1, 1]])[0][0] == -2.0, "clamped"
    big = [[math.cos(i / 1000 * 6.283), math.sin(i / 1000 * 6.283)] for i in range(1000)]
    assert len(cutouts.clean_polygon(big)) == cutouts.MAX_POINTS
    assert cutouts.clean_polygons("nope") == []
    assert len(cutouts.clean_polygons([poly, "bad", poly])) == 2

    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert close(cutouts.polygon_area(square), 100.0)
    assert cutouts.point_in_polygon(5, 5, square)
    assert not cutouts.point_in_polygon(15, 5, square)
    # winding rule: a loop drawn twice still counts as inside
    twice = square + square
    assert cutouts.point_in_polygon(5, 5, twice)
    assert cutouts.polygon_overlaps_box(square, (5, 5, 20, 20))
    assert cutouts.polygon_overlaps_box(square, (2, 2, 3, 3)), "box inside polygon"
    assert cutouts.polygon_overlaps_box([(4, -5), (6, -5), (6, 15), (4, 15)], (0, 0, 10, 10))
    assert not cutouts.polygon_overlaps_box(square, (11, 11, 20, 20))
    assert cutouts.polygon_covers_box(square, (1, 1, 9, 9))
    assert not cutouts.polygon_covers_box(square, (1, 1, 11, 9))
    notch = [(0, 0), (10, 0), (10, 10), (6, 10), (5, 4), (4, 10), (0, 10)]
    assert not cutouts.polygon_covers_box(notch, (1, 1, 9, 9)), "a notch reaches inside"

    rect = cutouts.rect_polygon(10, 20, 0, 5)
    assert rect == [(0, 5), (10, 5), (10, 20), (0, 20)]
    ellipse = cutouts.ellipse_polygon(0, 0, 20, 10, segments=64)
    assert len(ellipse) == 64
    assert all(close(((x - 10) / 10) ** 2 + ((y - 5) / 5) ** 2, 1.0) for x, y in ellipse)
    wobbly = [(i, 0.01 * (i % 2)) for i in range(50)] + [(49, 10), (0, 10)]
    simple = cutouts.simplify_path(wobbly, 0.5)
    assert len(simple) < 8 and simple[0] == (0.0, 0.0), simple
    print("cut-out polygons ok")


def check_frames():
    piece = Piece(asset_path="tile.png", x=100, y=50, w=200, h=100, scale=1.5,
                  rotation=30, flip_h=True, crop_rect=[0.1, 0.2, 0.9, 0.8])
    for u, v in ((0.1, 0.2), (0.5, 0.5), (0.9, 0.8), (0.33, 0.61)):
        wx, wy = cutouts.source_to_world(piece, u, v)
        back = cutouts.world_to_source(piece, wx, wy)
        assert close(back[0], u) and close(back[1], v), (u, v, back)
    # the crop's center sits on the node's center
    cx, cy = piece.center
    u, v = cutouts.world_to_source(piece, cx, cy)
    assert close(u, 0.5) and close(v, 0.5)
    # a node at 0° / no flip: world fractions map straight through
    plain = Piece(asset_path="a.png", x=0, y=0, w=100, h=100)
    assert cutouts.world_to_source(plain, 25, 75) == (0.25, 0.75)
    du, dv = cutouts.world_offset_to_source(plain, 10, -20)
    assert close(du, 0.1) and close(dv, -0.2)
    mirrored = Piece(asset_path="a.png", x=0, y=0, w=100, h=100, flip_h=True)
    assert close(cutouts.world_to_source(mirrored, 25, 75)[0], 0.75)
    print("source/world frames ok")


def check_sections():
    data = Piece(asset_path="tile.png", name="Tile", x=0, y=0, w=100, h=100,
                 group_id="g1").to_dict()
    square = [[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4]]
    assert cutouts.add_cutout(data, square) and len(data["cutouts"]) == 1
    assert not cutouts.add_cutout(data, [[2, 2], [2.5, 2], [2.5, 2.5]]), "outside the picture"

    part = cutouts.section_data(data, [[0.5, 0.5], [0.7, 0.5], [0.7, 0.9], [0.5, 0.9]])
    assert part is not None and part["id"] != data["id"]
    assert close(part["w"], 20) and close(part["h"], 40)
    assert close(part["x"], 50) and close(part["y"], 50), (part["x"], part["y"])
    assert part["crop_rect"] == [0.5, 0.5, 0.7, 0.9]
    assert len(part["clip_shapes"]) == 1 and part["cutouts"] == [], "far hole not copied"
    assert part["name"] == "Tile (part)" and part["group_id"] == ""
    near = cutouts.section_data(data, [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]])
    assert len(near["cutouts"]) == 1, "a hole inside the part stays a hole"
    # a part of a part keeps both shapes
    nested = cutouts.section_data(part, [[0.55, 0.55], [0.65, 0.55], [0.65, 0.65]])
    assert len(nested["clip_shapes"]) == 2
    assert cutouts.section_data(data, [[3, 3], [4, 3], [4, 4]]) is None

    # rotated and flipped node: the part lands exactly where it was cut
    turned = Piece(asset_path="tile.png", x=300, y=200, w=120, h=80, rotation=90,
                   flip_v=True, scale=2.0).to_dict()
    box = [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]]
    part = cutouts.section_data(turned, box)
    want = cutouts.source_to_world(turned, 0.5, 0.5)
    got = Piece.from_dict(part).center
    assert close(got[0], want[0]) and close(got[1], want[1]), (got, want)
    assert part["rotation"] == 90 and part["flip_v"] and part["scale"] == 2.0

    kept = cutouts.keep_only(data, [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]])
    assert kept["id"] == data["id"] and kept["group_id"] == "g1"
    assert kept["crop_rect"] == [0.0, 0.0, 0.5, 0.5] and close(kept["w"], 50)

    moved = cutouts.clone_window({"crop_rect": [0.1, 0.1, 0.3, 0.3]}, 0.5, -0.5)
    assert all(close(a, b) for a, b in zip(moved["crop_rect"], [0.6, 0.0, 0.8, 0.2])), \
        "stops at the picture's edge"
    assert cutouts.covers(data, [[-1, -1], [2, -1], [2, 2], [-1, 2]])
    assert not cutouts.covers(data, square)
    print("sections and clone windows ok")


def check_piece_model():
    raw = Piece(asset_path="a.png", w=100, h=100).to_dict()
    raw["cutouts"] = [[[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]], "junk"]
    raw["clip_shapes"] = "junk"
    raw["clone_home"] = [0.2, 0.2, 0.1, 0.1]
    piece = Piece.from_dict(raw)
    assert len(piece.cutouts) == 1 and piece.clip_shapes == [] and piece.clone_home == []
    assert not piece.hit_test(50, 50), "clicks in a hole fall through"
    assert piece.hit_test(10, 10)
    shaped = Piece(asset_path="a.png", w=100, h=100,
                   clip_shapes=[[[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]]])
    assert shaped.hit_test(20, 20) and not shaped.hit_test(80, 80)
    text = Piece(is_text=True, text="x", w=50, h=20)
    assert not cutouts.is_image(text) and cutouts.is_image(piece)
    again = Piece.from_dict(json.loads(json.dumps(piece.to_dict())))
    assert again.cutouts == piece.cutouts
    good = Piece.from_dict({**raw, "clone_home": [0.1, 0.1, 0.3, 0.4]})
    assert good.clone_home == [0.1, 0.1, 0.3, 0.4]
    # an older build only knows its own fields and simply ignores the new ones
    old_fields = set(Piece.__dataclass_fields__) - {"cutouts", "clip_shapes", "clone_home"}
    legacy = {key: value for key, value in piece.to_dict().items() if key in old_fields}
    assert Piece(**legacy).w == 100
    print("piece model ok")


def check_backdrops():
    level = Level()
    data = level.to_dict()
    assert (data["backdrop"], data["backdrop_texture"], data["backdrop_tile"],
            data["backdrop_opacity"]) == ("color", "", 0.0, 1.0)
    data.update(backdrop="texture", backdrop_texture="floors/grate.png",
                backdrop_tile=2.5, backdrop_opacity=0.4, background="#223344")
    copy = Level.from_dict(data)
    assert (copy.backdrop, copy.backdrop_texture, copy.backdrop_tile,
            copy.backdrop_opacity, copy.background) == (
        "texture", "floors/grate.png", 2.5, 0.4, "#223344")
    bad = Level.from_dict({**data, "backdrop": "wallpaper", "backdrop_tile": "x",
                           "backdrop_opacity": 7, "backdrop_texture": 5,
                           "background": ""})
    assert (bad.backdrop, bad.backdrop_tile, bad.backdrop_opacity,
            bad.backdrop_texture, bad.background) == ("color", 0.0, 1.0, "", "#10141c")
    legacy = Level.from_dict({"name": "Old", "background": "#000000"})
    assert legacy.backdrop == "color" and legacy.background == "#000000"

    project = Project()
    project.levels = [copy, Level(name="Plain", backdrop_texture="unused.png")]
    assert project.referenced_assets() == {"floors/grate.png": 1}, "only textures in use"
    assert project.relink_assets({"floors/grate.png": "pack/floors/grate.png"}) == 1
    assert copy.backdrop_texture == "pack/floors/grate.png"
    restored = Project.from_dict(json.loads(json.dumps(project.to_dict())))
    assert restored.levels[0].backdrop_texture == "pack/floors/grate.png"
    print("level backdrops ok")


def check_bundle_texture():
    with tempfile.TemporaryDirectory() as temp:
        store = os.path.join(temp, "store")
        os.makedirs(os.path.join(store, "floors"))
        with open(os.path.join(store, "floors", "grate.png"), "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\nfake")
        project = Project(asset_store=store)
        project.levels[0].backdrop = "texture"
        project.levels[0].backdrop_texture = "floors/grate.png"
        out = export_project_bundle(project, os.path.join(temp, "map.rpgpack"),
                                    include_renders=False)
        with zipfile.ZipFile(out) as archive:
            names = archive.namelist()
            saved = json.loads(archive.read("project.bmap" if "project.bmap" in names
                                            else [n for n in names
                                                  if n.endswith(".bmap")][0]))
        assert "assets/floors/grate.png" in names, names
        assert saved["levels"][0]["backdrop_texture"] == "floors/grate.png"
    print("backdrop texture travels in project bundles ok")


def fake_png(path, width=32, height=32, extra=b""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
                     struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00" + extra)
    return path


def check_backdrop_upload():
    with tempfile.TemporaryDirectory() as temp:
        store = os.path.join(temp, "store")
        outside = os.path.join(temp, "Pictures")
        for rel in ("a.png", "zz/b.png", "Floors/grate.png", "Backdrops/sand.png",
                    "Backdrops/sub/deep.png"):
            fake_png(os.path.join(store, *rel.split("/")))
        library = AssetLibrary()
        library.scan(store)
        revision = library._scan_revision

        steel = fake_png(os.path.join(outside, "steel floor.png"), 512, 256)
        rel = library.import_into_folder(steel, BACKDROP_FOLDER)
        assert rel == "Backdrops/steel floor.png", rel
        assert os.path.isfile(os.path.join(store, "Backdrops", "steel floor.png"))
        asset = library.get(rel)
        assert asset and asset.folder == "Backdrops" and (asset.width, asset.height) == (512, 256)
        assert library._scan_revision > revision, "groups and counts refresh"
        # listed where a full scan lists it (folder by folder, names sorted)
        order = [a.path for a in library.assets]
        library.scan(store)
        assert [a.path for a in library.assets] == order, order

        # the same picture again reuses the copy; another picture never overwrites it
        assert library.import_into_folder(steel, BACKDROP_FOLDER) == rel
        other = fake_png(os.path.join(outside, "elsewhere", "steel floor.png"), 64, 64)
        assert library.import_into_folder(other, BACKDROP_FOLDER) == "Backdrops/steel floor_1.png"
        assert library.import_into_folder(other, BACKDROP_FOLDER) == "Backdrops/steel floor_1.png"
        assert sorted(os.listdir(os.path.join(store, "Backdrops"))) == \
            ["sand.png", "steel floor.png", "steel floor_1.png", "sub"]
        # a library image is used where it is, not copied
        inside = os.path.join(store, "Floors", "grate.png")
        assert library.import_into_folder(inside, BACKDROP_FOLDER) == "Floors/grate.png"
        assert not os.path.exists(os.path.join(store, "Backdrops", "grate.png"))
        assert library.store_path_of(os.path.join(temp, "store-other.png")) is None
        assert library.store_path_of(store) is None
        # not pictures (or not there) → nothing happens
        notes = os.path.join(outside, "notes.txt")
        with open(notes, "w", encoding="utf-8") as handle:
            handle.write("hi")
        assert library.import_into_folder(notes, BACKDROP_FOLDER) is None
        assert library.import_into_folder(os.path.join(outside, "gone.png"), BACKDROP_FOLDER) is None
        assert AssetLibrary().import_into_folder(steel, BACKDROP_FOLDER) is None, "no store"

        # uploaded textures stay exactly where they were put: the store's own
        # folders are the only organisation the app offers
        assert library.get(rel).folder == BACKDROP_FOLDER
        assert BACKDROP_FOLDER in library.groups()
    print("backdrop textures uploaded from a file ok")


def check_door_stamps():
    slot = asset_slot("doors/door_1sq.png", "Door", edge=True)
    assert is_edge_slot(slot) and not is_edge_slot(with_edge(slot, False))
    node = node_slot(Piece(asset_path="x.png", w=10, h=10).to_dict(), edge=True)
    restored = slots_from_json(slots_to_json([slot, node, with_edge(slot, False)]))
    assert [is_edge_slot(s) for s in restored[:3]] == [True, True, False]
    assert not is_edge_slot(slots_from_json(json.dumps([{
        "kind": "asset", "asset_path": "a.png", "name": "a"}]))[0]), "old slots: off"
    assert looks_like_door("Airlock_Hatch_02") and not looks_like_door("crate")

    cell = 70
    # a one-square door (long side horizontal) near the line y = 140
    cx, cy, angle = edge_placement(100, 145, cell, 70, 14, 0)
    assert (cx, cy, angle) == (105.0, 140.0, 0.0), (cx, cy, angle)
    assert edge_placement(100, 135, cell, 70, 14, 0)[2] == 180.0, "faces the pointer"
    # near a vertical line it turns to run along it
    assert edge_placement(138, 100, cell, 70, 14, 0) == (140.0, 105.0, 90.0)
    assert edge_placement(144, 100, cell, 70, 14, 0) == (140.0, 105.0, 270.0)
    # two squares long: centered on a grid crossing, so both ends meet the grid
    assert edge_placement(100, 145, cell, 140, 14, 0)[:2] == (70.0, 140.0)
    # pinned standing up (long side vertical): turned flat on a horizontal line
    assert edge_placement(100, 145, cell, 14, 70, 0)[2] == 270.0
    assert edge_placement(138, 100, cell, 14, 70, 0) == (140.0, 105.0, 0.0)
    print("door-mode stamps ok")


def main():
    check_polygons()
    check_frames()
    check_sections()
    check_piece_model()
    check_backdrops()
    check_bundle_texture()
    check_backdrop_upload()
    check_door_stamps()
    print("ALL BATCH 17 CHECKS PASSED")


if __name__ == "__main__":
    main()
