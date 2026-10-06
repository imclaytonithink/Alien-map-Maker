"""Pure-Python checks for non-destructive raster-label patch nodes."""
import json

from core.project import Piece, Project


def main():
    project = Project(name="Raster label replacement")
    source = Piece(name="Map tile", asset_path="images/room.png")
    patch = Piece(
        name="Cover old label", is_patch=True, patch_color="#4f3928",
        patch_opacity=0.72, x=40, y=60, w=180, h=38, snap=False)
    replacement = Piece(is_text=True, name="New label", text="Docking Bay 3")
    project.levels[0].pieces.extend([source, patch, replacement])

    saved = json.loads(json.dumps(project.to_dict()))
    assert saved["version"] >= 6
    restored = Project.from_dict(saved)
    nodes = restored.levels[0].pieces
    assert not nodes[0].is_patch and not nodes[0].is_text
    assert nodes[1].is_patch
    assert nodes[1].patch_color == "#4f3928"
    assert nodes[1].patch_opacity == 0.72
    assert (nodes[1].x, nodes[1].y, nodes[1].w, nodes[1].h) == (40, 60, 180, 38)
    assert nodes[2].is_text and nodes[2].text == "Docking Bay 3"

    # Older image/text nodes are not accidentally reclassified as patch nodes.
    legacy = Piece.from_dict({"name": "Old image", "asset_path": "map.png"})
    assert not legacy.is_patch
    assert legacy.patch_color == "#10141c"
    assert legacy.patch_opacity == 1.0
    clamped = Piece.from_dict({"is_patch": True, "patch_opacity": 3})
    assert clamped.is_patch and clamped.patch_opacity == 1.0
    print("Batch 4 non-destructive patch serialization checks passed.")


if __name__ == "__main__":
    main()
