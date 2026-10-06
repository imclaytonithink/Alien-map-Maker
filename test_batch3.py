"""Pure-Python checks for Batch 3 editable-text persistence and migration."""
import json

from core.project import Piece, Project


def main():
    text = Piece(
        name="Editable sign", is_text=True, text="Docking bay\nAuthorized crew only",
        font_family="Arial", font_size=30, font_bold=True, font_italic=True,
        font_underline=True, text_halign="left", text_valign="top",
        text_padding=12, text_auto_size=False,
        text_background_color="#18242e", text_background_opacity=0.64,
        text_color="#f3e7c2", w=320, h=120)
    project = Project(name="Text persistence")
    project.levels[0].pieces.append(text)

    serialized = json.loads(json.dumps(project.to_dict()))
    assert serialized["version"] >= 5
    loaded = Project.from_dict(serialized)
    restored = loaded.levels[0].pieces[0]
    assert restored.is_text
    assert restored.text == "Docking bay\nAuthorized crew only"
    assert restored.font_italic and restored.font_underline
    assert restored.text_halign == "left"
    assert restored.text_valign == "top"
    assert restored.text_padding == 12
    assert restored.text_auto_size is False
    assert restored.text_background_color == "#18242e"
    assert restored.text_background_opacity == 0.64
    assert (restored.w, restored.h) == (320, 120)

    # Version 4 and earlier text nodes receive rich-text defaults; image nodes
    # remain distinct objects with is_text=False.
    old_text = Piece.from_dict({"is_text": True, "text": "Old sign"})
    assert old_text.is_text
    assert old_text.text_halign == "center"
    assert old_text.text_valign == "center"
    assert old_text.text_auto_size is True
    assert old_text.text_background_color == ""
    image = Piece.from_dict({"asset_path": "room.png", "name": "Room"})
    assert not image.is_text

    invalid = Piece.from_dict({
        "is_text": True, "text_halign": "diagonal", "text_valign": "sideways",
        "text_padding": 500, "text_background_opacity": -1,
    })
    assert invalid.text_halign == "center"
    assert invalid.text_valign == "center"
    assert invalid.text_padding == 100
    assert invalid.text_background_opacity == 0.0
    print("Batch 3 editable-text serialization and migration checks passed.")


if __name__ == "__main__":
    main()
