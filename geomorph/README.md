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
AF09 is skipped: its wing art is not in the Custom Tiles pack, and the A107–A116 "Transition … for AF09" pieces are not wings on their own, so they are left out too.
Custom Tiles pack: `python -m geomorph --tiles <main pack> manifest --extra <custom pack>`.
Aerofins are always a mirrored pair. One bridge on the nose, one engineering on the tail (centre line).
Hull sides always face outward. Secure zones are only entered through a checkpoint. Stairs/lifts sit at the same X/Y on every level.

## Smart grouping
Related rooms are generated close together: medical with labs and the morgue, cargo with loading bays and docks, dining
with the galley, quarters with freshers, engineering with fuel and workshops, security with the brig and armory, and
so on (and rooms of one function cluster). `data/affinity.json` holds the groups and the pairs that should stay apart
(waste away from the galley, engines away from cabins); add your own. The pull works over distance across the whole
building (a level change counts as 25 squares), not just touching neighbours. Strength: the *Room grouping* slider or
`--grouping 0..1`.

## Symbols (furniture, machinery, cargo...)
`--decor` (or the Symbols box in the dialog) furnishes open rooms from the Symbols pack. The pack draws at 60 px
per foot (300 px per grid square) with transparent padding, so each symbol's real size is its measured opaque box;
nothing is ever stretched. Only enclosed room floor from `data/tile_floor.json` is used, so corridors stay clear and
items go against the walls (tables in the middle). `data/symbol_kits.json` is the per-room kit (a ward gets medical beds, counters and consoles; a mess gets tables, chairs and a counter; a cell
gets bunks and brig fixtures; cargo gets crates; hydroponics gets plants) and the room's assigned function decides, never the odd
thing a multipurpose tile contains. Stair/lift cores are never furnished. "Something bad happened" (`--incident struggle|ransacked|overrun`, `--incident-where all|overlay|random`)
displaces and rotates items, adds debris, and for *overrun* barricaded doors, scorch/acid burns, resin and drag marks.
Rebuild the symbol sizes with `symbols.build(<Symbols folder>)`.

## Archetypes
Each `data/archetypes/*.json` is one site type; add a file (or use the editor) and it appears, no code change.

Credits: Tiles by Robert Pearce (Pearce Design Studio, LLC), CC BY-NC 4.0; PNGs by Eric Smith / RPG Mobius.
Non-commercial, unofficial fan tool. Traveller is a trademark of Far Future Enterprises.


## Tall rooms

A double-height room is built from the pack's own **Lower / Upper tile pairs** (Engineering 145/146, Arboretum 111/112,
Hangar 115/116, Lobby 225/226, Construction Deck 139/140, Xboat deck 220-1/220-2, Fighter Hangar 106-1/106-3). The lower
tile sits on its level and the matching upper tile sits on the level above, on the same square and facing, joined by a
stairs link; each floor is a room of its own in the key. Which pair is used follows the room's function tags, or name the
tile number in an archetype's `special_volumes` entry with `"pair_tiles": ["225"]`. A room with no matching pair falls
back to a single tile with a railed, dimmed overlook above it. Three-storey rooms keep an overlook on the middle level.


## Checking, comparing and teaching the generator

* **Map check** (`quality.py`): a 0-100 score from unreachable rooms, dead-end corridors, known issues, zones with no
  tile, how much furnishable floor got furniture, and tile variety, with the plain sentence "N dead ends, M unreachable
  rooms, X% furnished". Re-rolls recompute it and show what changed ("Score 88 -> 93, dead ends 2 -> 1"); **Undo** goes back
  to the exact previous layout, furniture and key (or the previous map and seed after "New seed").
* **Best of 6**: six candidates from seeds derived from the current one, ranked by score plus your taste; click one to use it.
* **Taste** (`learning.py`): the thumbs buttons (and, gently, maps you place or export) vote on the tiles in a map. A tile's
  net taste is (up - down) / (up + down + 3), added as a small bonus or penalty when tiles are picked. It never bends
  a rule. The votes are in `preferences.json` in the generator's user folder; **Reset** clears them, and the checkbox
  switches learning off.
* **Outbreaks** (`decor.spread_plan`): with "Spreading from a starting room" the worst damage (resin, burns, a nest) is at the
  source, it thins out with every door away up to the chosen reach, barricades stand on doors facing the source and drag
  marks lead toward it; the GM gets a marker and notes. Choose the starting room (entrance, a medical room, a lab…).
* **Atmosphere** (`atmosphere.py`): with the power out a room goes dark and round emergency lamps, flat against the walls,
  light what they can see (walls and furniture cast shadow, nothing leaks outside the building); lockdown shows a red shutter
  on every door (GM view only); quarantine gets a hazard border. The same overlay is added to the canvas on its own layers.
* **Exports for Tabletop Simulator**: "Tabletop Sim (sharp 100px/sq, in sections)" writes a big map as grid-aligned PNG
  sections (A1, A2, B1…) no bigger than the limit you choose (default 4096 px), with a `sections.txt` that says how they fit.

## Lights

* Each emergency lamp casts light as rays against the walls in the tile art (walls are the solid, dark bands), so a pool fills a
  hallway **wall to wall**, is brightest at the fixture, feathers out to its reach and stops at walls. Pools are cached, so
  redrawing is quick.
* **Colours**: choose the emergency light and the fixture colour in the Lights box ("Red" resets them). They are saved in the
  options (`atmosphere: {light, fixture}`) and used in the preview, the exports and when the map is placed on the canvas.
* **Your own lights**: press "Place lights", then click the preview: a *wall light* snaps to the nearest wall (found from the
  tile art), a *ceiling light* goes where you click. Set the reach, brightness and colours first; right-click a light to
  remove it, or turn "Place lights" off and drag a light to move it (it re-snaps to the wall; Undo puts it back). They work in any tile (even one that is not dark), light only what they can see, are part of the saved layout,
  undo and the canvas, and are removed if a re-roll replaces the tile they were on.
