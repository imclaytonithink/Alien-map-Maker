"""Pure-Python checks for Batch 1 project tint persistence and migration."""
import json

from core.project import Piece, Project


def main():
    project = Project(name="Color test")
    project.tint_color = "#4477aa"
    project.tint_strength = 0.35

    inherited = Piece(name="inherits")
    overridden = Piece(name="override", tint_mode="override",
                       tint_color="#ff8800", tint_strength=0.65)
    original = Piece(name="original", tint_mode="original",
                     tint_color="#ffffff", tint_strength=1.0)
    project.levels[0].pieces.extend([inherited, overridden, original])

    roundtrip = Project.from_dict(json.loads(json.dumps(project.to_dict())))
    assert roundtrip.tint_color == "#4477aa"
    assert roundtrip.tint_strength == 0.35
    restored = roundtrip.levels[0].pieces
    assert [piece.tint_mode for piece in restored] == [
        "inherit", "override", "original"]
    assert restored[1].tint_color == "#ff8800"
    assert restored[2].tint_strength == 1.0

    # Pre-Batch-1 projects carried tint_color/strength but no tint_mode.
    legacy = Piece(name="legacy", tint_color="#22aa66",
                   tint_strength=0.4).to_dict()
    legacy.pop("tint_mode")
    assert Piece.from_dict(legacy).tint_mode == "override"

    legacy_uncolored = Piece(name="legacy plain").to_dict()
    legacy_uncolored.pop("tint_mode")
    assert Piece.from_dict(legacy_uncolored).tint_mode == "inherit"

    invalid_mode = Piece(name="invalid", tint_mode="unknown")
    assert Piece.from_dict(invalid_mode.to_dict()).tint_mode == "inherit"
    print("Batch 1 project tint serialization and migration checks passed.")


if __name__ == "__main__":
    main()
