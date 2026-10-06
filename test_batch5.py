"""Pure-Python checks for RPG Map Packs and composition-node migrations."""
from __future__ import annotations

import json
import os
import tempfile
import zipfile

from core.bundle import (
    BUNDLE_FORMAT, BundleError, export_project_bundle, read_bundle_manifest,
    unpack_project_bundle,
)
from core.project import Piece, Project, ZoneRegion


def main():
    with tempfile.TemporaryDirectory() as temp:
        store = os.path.join(temp, "source-assets")
        os.makedirs(os.path.join(store, "rooms"))
        with open(os.path.join(store, "rooms", "floor.png"), "wb") as fh:
            fh.write(b"test image payload")

        project = Project(name="Portable map", asset_store=store)
        image = Piece(name="Floor", asset_path="rooms/floor.png",
                      crop_rect=[0.1, 0.2, 0.9, 0.8])
        scale = Piece(name="Scale", is_scale_bar=True, scale_distance=25,
                      scale_units="ft", scale_caption="25 feet", w=240, h=44)
        connector = Piece(name="Transition", is_connector=True,
                          connector_label="To level 2", connector_arrow=False)
        missing = Piece(name="Missing source", asset_path="lost/ghost.png")
        zone = ZoneRegion(name="Cryo bay", label="CRYO", show_label=True,
                          show_id=True, points=[(0, 0), (10, 0), (10, 10)])
        project.levels[0].pieces.extend([image, scale, connector, missing])
        project.levels[0].zones.append(zone)

        pack = os.path.join(temp, "portable.rpgpack")
        export_project_bundle(project, pack, include_renders=False)
        with zipfile.ZipFile(pack) as archive:
            members = set(archive.namelist())
            assert "project.bmap" in members
            assert "assets/rooms/floor.png" in members
            assert "manifest.json" in members
            saved = json.loads(archive.read("project.bmap"))
            assert saved["asset_store"] == "assets"
            assert saved["levels"][0]["pieces"][0]["asset_path"] == "rooms/floor.png"
            assert saved["levels"][0]["pieces"][0]["crop_rect"] == [0.1, 0.2, 0.9, 0.8]
            assert saved["levels"][0]["pieces"][3]["asset_path"] == ""
        manifest = read_bundle_manifest(pack)
        assert manifest["missing_assets"] == ["ghost.png"]

        extracted_project = unpack_project_bundle(pack, os.path.join(temp, "imports"))
        with open(extracted_project, encoding="utf-8") as fh:
            restored = Project.from_dict(json.load(fh))
        restored.asset_store = os.path.abspath(os.path.join(
            os.path.dirname(extracted_project), restored.asset_store))
        assert os.path.isfile(restored.resolve_asset("rooms/floor.png"))
        nodes = restored.levels[0].pieces
        assert nodes[1].is_scale_bar and nodes[1].scale_distance == 25
        assert nodes[2].is_connector and nodes[2].connector_label == "To level 2"
        assert restored.levels[0].zones[0].label == "CRYO"
        assert restored.levels[0].zones[0].show_id
        assert restored.to_dict()["version"] == 7

        malicious = os.path.join(temp, "bad.rpgpack")
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr("manifest.json", json.dumps({
                "format": BUNDLE_FORMAT, "format_version": 1}))
            archive.writestr("project.bmap", "{}")
            archive.writestr("../escape.txt", "no")
        try:
            unpack_project_bundle(malicious, os.path.join(temp, "bad-import"))
        except BundleError:
            pass
        else:
            raise AssertionError("archive traversal was not rejected")

    crop = Piece.from_dict({"crop_rect": [-1, 0.25, 2, 0.75]})
    assert crop.crop_rect == [0.0, 0.25, 1.0, 0.75]
    invalid_crop = Piece.from_dict({"crop_rect": [0.8, 0.2, 0.1, 0.9]})
    assert invalid_crop.crop_rect == [0.0, 0.0, 1.0, 1.0]
    legacy = Piece.from_dict({"name": "Legacy"})
    assert legacy.crop_rect == [0.0, 0.0, 1.0, 1.0]
    assert not legacy.is_scale_bar and not legacy.is_connector
    print("Batch 5 portable-pack and composition-node checks passed.")


if __name__ == "__main__":
    main()
