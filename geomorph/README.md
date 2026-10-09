# Geomorph generator (ships and sites)

Builds connected, keyed deck plans from the Starship Geomorphs 2.0 tiles.
Open it from **Tools → Geomorph Generator…**, or use the CLI:

    python -m geomorph --tiles /path/to/pack generate --kind ship --ship-type Military --tonnage 2000 --seed 7
    python -m geomorph --tiles /path/to/pack generate --archetype "Deep mine" --scale large --overlays threat,secrets

Other commands: `archetypes`, `validate`, `tags`, `gaps`, `review`, `manifest`.

## Setup
The tile images are not in Git. Import the pack ZIP into the library (placing on the canvas uses it),
and/or point the dialog at the extracted folder (preview/export) or set `GEOMORPH_TILES`.
`data/tile_manifest.json` already holds each tile's function tags (from the tile names) and edge/door data
(detected from the images). Rebuild with `python -m geomorph --tiles <pack> manifest`.

## Edges
Detected per square as wall / door / open. 8 tiles are flagged low-confidence (`python -m geomorph review`);
fix them with the **Edge editor** (click squares). Corrections go in `edge_overrides.json` and win over detection.
Standard tiles are used for buildings (edge tiles read as ship hull only). Add your own tiles with `geomorph/custom.py`.

## Rules
Wings: one Port + one Starboard tile with the same number/variant/colour, mirrored about the centre line.
Nose/tail: exactly one each on the centre line (bridge on the nose, engineering on the tail), optionally a 100'->50' transition between hull and nose.
Fuel-scoop wings need a fuel nose or tail. The pack's [Mirror] file is used instead of a flipped copy so labels read correctly.
Launch bays, barbettes/turrets, escape pods and fuel scoops always come as mirrored port/starboard pairs; maneuver/jump drive rooms only at the stern.
AF09 wings are skipped until their root-transition geometry is modelled.
Custom Tiles pack: `python -m geomorph --tiles <main pack> manifest --extra <custom pack>`.
Aerofins are always a mirrored pair. One bridge on the nose, one engineering on the tail (centre line).
Hull sides always face outward. Secure zones are only entered through a checkpoint. Stairs/lifts sit at the same X/Y on every level.

## Symbols (furniture, machinery, cargo...)
`--decor` (or the Symbols box in the dialog) furnishes open rooms from the Symbols pack. The pack draws at 60 px
per foot (300 px per grid square) with transparent padding, so each symbol's real size is its measured opaque box;
nothing is ever stretched. Only enclosed room floor from `data/tile_floor.json` is used, so corridors stay clear and
items go against the walls (tables in the middle). `data/symbol_map.json` says which folders furnish which room
function. "Something bad happened" (`--incident struggle|ransacked|overrun`, `--incident-where all|overlay|random`)
displaces and rotates items, adds debris, and for *overrun* barricaded doors, scorch/acid burns, resin and drag marks.
Rebuild the symbol sizes with `symbols.build(<Symbols folder>)`.

## Archetypes
Each `data/archetypes/*.json` is one site type; add a file (or use the editor) and it appears, no code change.

Credits: Tiles by Robert Pearce (Pearce Design Studio, LLC), CC BY-NC 4.0; PNGs by Eric Smith / RPG Mobius.
Non-commercial, unofficial fan tool. Traveller is a trademark of Far Future Enterprises.
