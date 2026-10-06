"""Pure-Python checks for Batch 2 border and gameplay-zone persistence."""
import json

from core.project import Piece, Project, ZoneRegion


def main():
    project = Project(name="Borders and zones")
    project.border_color = "#2f88cc"
    project.border_opacity = 0.42
    project.border_width = 3.5
    project.node_border_shape = "alpha"
    project.show_node_borders = True
    project.export_node_borders = True
    project.show_zones = False
    project.export_zones = False

    node = Piece(name="Outlined node", border_mode="override",
                 border_shape="bounds", border_color="#ffcc00",
                 border_opacity=0.7,
                 border_edges=[True, False, True, False])
    project.levels[0].pieces.append(node)
    zone = ZoneRegion(
        name="Airlock", points=[(10, 20), (110, 20), (110, 80), (10, 80)],
        border_mode="override", border_color="#dd3344", border_opacity=0.6,
        edge_visible=[True, False, True, True])
    project.levels[0].zones.append(zone)

    serialized = json.loads(json.dumps(project.to_dict()))
    assert serialized["version"] >= 4
    roundtrip = Project.from_dict(serialized)
    assert roundtrip.border_color == "#2f88cc"
    assert roundtrip.border_opacity == 0.42
    assert roundtrip.border_width == 3.5
    assert roundtrip.node_border_shape == "alpha"
    assert roundtrip.show_node_borders and roundtrip.export_node_borders
    assert not roundtrip.show_zones and not roundtrip.export_zones

    restored_node = roundtrip.levels[0].pieces[0]
    assert restored_node.border_mode == "override"
    assert restored_node.border_shape == "bounds"
    assert restored_node.border_edges == [True, False, True, False]
    restored_zone = roundtrip.levels[0].zones[0]
    assert restored_zone.name == "Airlock"
    assert restored_zone.points == [(10.0, 20.0), (110.0, 20.0),
                                   (110.0, 80.0), (10.0, 80.0)]
    assert restored_zone.edge_visible == [True, False, True, True]
    assert restored_zone.contains(50, 50)
    assert not restored_zone.contains(200, 50)

    # Old project files load with the new project defaults and no gameplay zones.
    legacy_project = Project.from_dict({"version": 3, "levels": [{"pieces": [
        {"name": "Old node", "tint_color": "#22aa66", "tint_strength": 0.4}
    ]}]})
    assert legacy_project.border_color == "#69b7f5"
    assert legacy_project.show_node_borders is False
    assert legacy_project.export_zones is True
    assert legacy_project.levels[0].zones == []
    old_node = legacy_project.levels[0].pieces[0]
    assert old_node.tint_mode == "override"
    assert old_node.border_mode == "inherit"
    assert old_node.border_edges == [True] * 4

    invalid = ZoneRegion(points=[(0, 0), (1, 0), (1, 1)],
                         border_mode="bad", border_opacity=2,
                         edge_visible=[False])
    assert invalid.border_mode == "inherit"
    assert invalid.border_opacity == 1.0
    assert invalid.edge_visible == [False, True, True]
    print("Batch 2 border and gameplay-zone serialization checks passed.")


if __name__ == "__main__":
    main()
