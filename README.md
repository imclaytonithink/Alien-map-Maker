# SceneBoard — The Map-Making Studio & Generator

**Bring your assets. Generate a map. Refine every detail.**

A general-purpose desktop tool for arranging PNG assets into maps and scenes.
Snap nodes to a grid, rotate/flip, add overlays and text, tint/recolor images,
manage levels, and export to PNG or PDF. Asset-driven procedural generation
builds maps from imported small tiles or prebuilt geomorph modules. The app has
neutral **Dark** and **Light** themes, plus an optional **Alien / MU-TH-UR 6000**
theme inspired by the original CRT-style ship computer.

Built with **Python + PyQt6**. Packaged to a standalone `.exe` via PyInstaller.

---

## Running from source (any OS with Python)
```bash
pip install -r requirements.txt
python main.py
```
On first launch the **project menu** opens over the editor (blurred backdrop):
pick *New map*, open a *Recent* map, or start from a *Template*. The tracked
starter art in `sample_assets/` is bundled with the Windows executable and
seeded into the library on first launch. Choose a persistent app-wide
appearance: **Dark**, **Light**, or **Alien / MU-TH-UR**.
The Alien appearance includes optional scanlines and boot text, and Green /
Amber / Red accents. Press **ESC** any time to bring the menu back up — it has
Save, Open, PNG/PDF/Tabletop Simulator/project-pack export, recent-map management,
templates, appearance, and auto-save settings.

## Building the .exe (Windows)
1. Install Python 3.10+ from python.org (tick "Add Python to PATH").
2. Run `build.bat` (double-click). It downloads the three high-resolution asset
   ZIPs from the project's GitHub release, strips unsupported files while
   preserving their folders, and bundles only supported raster assets plus the
   starter art. Your `.exe` appears at `dist/SceneBoard.exe`.
3. The first launch installs the bundled high-resolution packs into the user's
   persistent app-data asset store. This one-time setup can take a few minutes;
   later launches reuse the installed copies.

The source ZIPs total about 239 MB before filtering; unsupported documents and
other non-image files are not bundled. The extracted image library needs
additional disk space. `curl.exe` and an internet connection are required while
building. PyInstaller can't cross-compile from Linux/macOS, so build the `.exe`
on Windows.

## Testing
Install `requirements.txt`, then run `python run_tests.py --require-gui`.
This executes the pure-Python checks, renders the demo, and runs the Qt GUI
smoke tests using the offscreen platform. The GitHub Actions workflow runs this
same required-GUI test command. A separate `real-pack-integration` job
retrieves the three published high-resolution geomorph, custom-tile, and
Symbols ZIPs into the runner's temporary directory (never into Git), imports
their actual contents, checks Core/Overlay pairing when variants exist,
exercises Custom Tiles through its applicable generator path, verifies Symbols
recognition, and assembles a seeded 3x3 map. If Qt runtime libraries are
unavailable, plain `python run_tests.py` still runs the pure-Python checks and
clearly reports skipped GUI checks.

---

## How to use

### 1. Bring your pictures into the tool (stays forever)
- **Import Folder**, **Import File**, or **Import ZIP** (top of the library
  panel) copies supported images into the app's **internal asset store** —
  shown at the bottom of the panel (`Store: …`) with an **Open** button and a
  **?** help button. In the packaged `.exe`, this store is placed in the
  persistent per-user app-data folder, not PyInstaller's temporary unpack
  directory; use **Open** to see its exact location. Folder imports keep the
  selected folder name as well as
  its subfolders, so selecting a `100x100 Core` or `Symbols` folder directly
  does not discard the names the generator needs. ZIP imports preserve folders,
  normalize Windows-style archive paths, skip non-images, and keep each archive
  in its own group so similarly named files never overwrite each other. The
  original ZIP is never changed. The library's expandable folder tree preserves
  those paths; overlapping smart views add counts for core modules, rooms,
  floors, walls, corridors, doors, controls, engineering, medical/science,
  furniture, storage, vehicles/small craft, weapons/security, food/galley,
  loose props, organic remains, overlays, lighting, hazards, fire/smoke, and
  symbols. Unmatched art stays visible under **Other /
  Unclassified**. These are browse filters only—assets are not moved or copied
  again—and folder siblings can be reordered with ▲▼. For geomorph assembly, import
  the matching high-res Geomorphs / Custom Tiles and Symbols archives; folder
  names are preserved so Core modules, paired overlays, and Symbols can be
  recognized. After importing, the library clears old filters so the assets are
  visible immediately; drag or double-click one to put it on the canvas. Large
  libraries load thumbnails only as they enter view, rather than decoding every
  full-size PNG at once. The checked-in starter art is a small demo collection.
  The high-resolution Geomorphs / Custom Tiles / Symbols ZIPs are stored as
  GitHub Release assets rather than in Git; the Windows build filters each ZIP
  to supported image files while preserving folders, then bundles and installs
  them on first launch. Running from source? **Import ZIP** applies the same
  supported-file filter when adding those packs manually.
- **Custom single PNGs** dragged straight onto the canvas are *embedded*
  inside the saved `.bmap` file, so that map is portable on its own.

### 2. Browse & preview
- Search by name or size (e.g. `40x120`).
- Hover an asset ~0.6s for a pop-out preview; **right-click → View larger…**
  for a full preview with an *Add to canvas* button.
- Drag an asset onto the canvas, or double-click to drop at center.
- While you drag, the canvas lights up the **exact landing cell(s)** under
  the incoming node (calibrated to where it will snap, not the raw cursor).
- Thumbnail size slider on the left.

### 3. Build the map
- **Left-drag** a node to move. Dragging moves the whole selection as one
  rigid group — no more accidental deformation.
- **Snapping** (on by default): nodes snap to the grid *and* to neighboring
  nodes' edges/centers; the nearest target wins, with red smart-guide lines
  showing node-to-node alignment. Per-node "Snap to grid" checkbox can
  disable it for one node.
- **Yellow handle** = rotate (hold **Shift** for 15°). **Right/Middle-drag**
  pans; **wheel** zooms.
- Multi-select: **Shift-click** or drag a marquee. Then align / distribute /
  **Glue** (group) / tint / delete / duplicate / copy-paste together.
- The **quick toolbar** (top-right of canvas) rotates/flips, raises/lowers,
  locks, copies, duplicates, deletes the selection.
- The main toolbar can be customized from **View → Customize toolbar…** (or
  its gear button): choose which tools to show, drag-and-hold a tool to reorder
  it, and use **Reset to default** if needed. Your layout is saved for the user.
- **Project-wide tint** applies to image nodes set to *Inherit project*. Each
  node can instead use its own *Override* tint or *Original / no tint*.
  Both project and node tints preserve transparent pixels and appear in exports.
- **Editable text nodes** support multiline text, font family/size, bold/italic/
  underline, horizontal and vertical alignment, auto-fit or a fixed text box,
  padding, text color, and a translucent fill behind the label. Text formatting
  and content are undoable. Text nodes are separate from image nodes: lettering
  already present in an imported PNG or other image is rasterized into that
  image and is not directly editable. To replace a baked-in label, use the
  **Patch** toolbar tool (or Tools menu) and drag a cover rectangle. In the
  patch properties, sample a nearby clean map color, adjust fill opacity, then
  add an editable text node above the patch. Both overlays remain separate
  nodes; the source image is never rewritten. All color dialogs include
  **Pick from map…**; grid, guides, selection handles, and editor-only outlines
  are excluded from the sample.
- **Node outlines & gameplay zones** are independent: node outlines can follow
  image bounds or the PNG alpha silhouette, while rectangular and polygonal
  gameplay zones have their own editable geometry. Use the **Zones** panel or
  toolbar: drag to draw a rectangle; click polygon vertices, then double-click,
  press Enter, or right-click to finish. Select a zone in the panel or on its
  edge, drag its handles to reshape, and drag an edge/body to move it. The
  project border color/opacity can be overridden per node or zone; hide
  individual bounds sides or zone segments. Zone labels and short IDs can be
  shown on the canvas and in exports. Canvas visibility and PNG/PDF export
  visibility are separate options.
- **Composition tools** (toolbar or Tools menu): stamp copies of a selected
  node; crop an image non-destructively (with a reset-crop control); measure
  with a ruler and Shift-to-grid snap; draw a static scale bar; add labeled
  connection / transition markers, and lasso-select nodes by their centers.
  **Select similar** selects related nodes and **Copy Style** applies formatting.
  Replace a node's image while preserving its placement and transforms. Escape
  exits an active tool. Static scale bars keep their saved pixel length and
  label when grid calibration changes.
- **Grid**: square grid with opacity slider, solid/dashed/dotted style, major
  lines every N cells, adjustable cell size & "square = 5ft/10ft".
- **Arrow keys** nudge 1px; **Shift+arrows** move exactly one square.

### 4. Generate a map (Tools → Generate Map…)
- Choose between two distinct **Generator** modes:
  - **Tile-by-tile** classifies smaller floor, wall, corridor, door, fixture,
    and hazard assets. Starship / Colony base / Research lab settings and the
    Random / Corridors / Grid / Organic layouts shape the room network. Props
    are placed on their matching surfaces. Small-tile maps are BFS-verified
    for connectivity, with isolated areas automatically repaired.
  - **Geomorph assembly** arranges imported 100x100 Core deck-plan modules.
    A module's 20x20-square playable core is placed on a 20-square pitch; the
    surrounding two-square transparent gutter is preserved so adjacent walls
    align. Source dimensions determine scale (the high-resolution pack is
    300 pixels per five-foot square). Modules rotate as complete pieces, and
    matching `[Overlay]` images inherit their base module's placement, scale,
    and rotation. Optional Symbols assets are size-scaled and scattered on a
    separate editable layer.
- **Geomorph layout** offers 2x2, 3x3, or 4x4 modules. At the default five-foot
  grid, a 3x3 assembly covers 60x60 squares; a new map expands the default
  30x30 canvas as needed. The Overlay / symbol density slider controls optional
  dressing.
- **Output**: *New level* or *Fill selected area* (select nodes first). Area
  fills place only whole modules that fit the selection bounds.
- **Seed** makes generated results reproducible/shareable; **Regenerate** rolls
  a new seed and replaces the previous result for that output mode.

### 5. Floors / Levels
- Tab bar above the canvas: add / remove / rename / reorder floors.
- **Reference floor overlay**: ghost the floor above/below (adjustable opacity)
  so you can align things between levels.
- **Layers** (right panel): per-level layers with show/hide, lock, opacity,
  reorder, and an *active layer* that new nodes go to.

### 6. Export
- **Export PNG…**: current level or all levels, size presets (Foundry, Roll20,
  Tabletop Simulator, Print 2×/4×), transparent background, and separate
  exported-grid on/off, color, and opacity controls.
- **Export for Tabletop Simulator…**: a direct entry point to an opaque PNG
  export, with 1024/2048/3072px longest-edge presets that preserve aspect
  ratio. Import the PNG in TTS as a **Custom Board**; the preset defaults to
  2048px and lets you choose whether to bake in the grid. Gameplay-zone
  borders/labels are static artwork in the PNG; use TTS's native zone tools
  separately when you need interactive in-game behavior such as Fog of War.
- **Export PDF…**: choose the current level or all levels (one page per level),
  with the same sizing, transparency, and grid/color/opacity controls as PNG.
- **Export project bundle / PNG pack…**: creates a portable `.rpgpack` ZIP with
  the editable project, its referenced source assets, a manifest, and a PNG
  render of every level (longest edge capped at 2048 px). Open a `.rpgpack`
  from File → Open to extract a local working copy; saving that copy does not
  overwrite the original pack.

### 7. Save / Open
- Projects are `.bmap` JSON (node placements and outlines, gameplay zones and
  labels, tint overlays, editable text, non-destructive crops and patches,
  connection markers, static scale bars, embedded custom images, levels, and
  layers). A thumbnail
  is saved next to the file for the menu.
- **Auto-save** is on by default (every 5 minutes; choose Off / 1 / 5 / 10
  minutes from File or the ESC menu). Named projects are saved in place.
  Unsaved maps get a recovery copy, with a restore prompt on the next launch.
- **Undo/Redo** (Ctrl+Z / Ctrl+Y) with named history; **Ctrl+S** save,
  **Ctrl+O** open, **Ctrl+N** new.

### Keyboard reference
| Key | Action |
|---|---|
| **Esc** | System menu (save / open / quit / theme / recents) |
| Arrows | Nudge 1px (Shift = one grid square) |
| Delete | Delete selection |
| R | Rotate selection 90° |
| Ctrl+Z / Ctrl+Y | Undo / Redo |
| Ctrl+S / Ctrl+O / Ctrl+N | Save / Open / New |

---

## Project layout
```
core/   project (data model) · history (undo/redo) · asset_manager (internal
        store) · exporter (PNG/PDF) · bundle (portable RPG Map Packs) ·
        render (shared piece drawing) · generator (tile layouts + geomorph assembly)
ui/     main_window · canvas · library · properties · layers_panel · zones_panel ·
        menu_overlay (ESC project menu) · generator_dialog · theme (Dark/Light/Alien) ·
        export_dialog
sample_assets/   demo pieces
```
