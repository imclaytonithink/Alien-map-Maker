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
pick *New map* or open a *Recent* map. Creating a map lets you choose its width,
height, and grid-square pixel size; these can also be changed later in the
canvas properties or from **View → Canvas size**. No placeholder art ships in
the library (older builds copied demo images into the store; those untouched
copies are removed automatically). Choose a persistent app-wide appearance: **Dark**,
**Light**, or **Alien / MU-TH-UR**. The Alien appearance includes optional
scanlines and Green / Amber / Red accents. Press **ESC** any time
to bring the menu back up — it has Save, Open, PNG/PDF/Tabletop
Simulator/project-pack export, recent-map management, appearance, and auto-save
settings.

## Building the .exe (Windows)
1. Install Python 3.10+ from python.org (tick "Add Python to PATH").
2. Run `build.bat` (double-click). It downloads the three high-resolution asset
   ZIPs from the project's GitHub release (tag `Released`; the release must
   stay published for the download to work), strips unsupported files while
   preserving their folders, and bundles only supported raster assets plus the
   starter art. You get a **folder build**:
   - `dist\SceneBoard\SceneBoard.exe` — run this. Keep the whole `SceneBoard`
     folder together (make a shortcut to the EXE if you like).
   - `dist\SceneBoard-Windows.zip` — the same folder, zipped for sharing. Unzip it
     anywhere and run `SceneBoard.exe` inside.

   A folder build starts quickly because nothing has to be unpacked. If you
   prefer one single file, run `build.bat onefile` for the older
   `dist\SceneBoard.exe`; it unpacks everything (about 280 MB) into a temporary
   folder every time it starts, so it opens more slowly.
3. The first launch installs the bundled high-resolution packs into the user's
   persistent app-data asset store. This one-time setup can take a few minutes;
   later launches reuse the installed copies.
4. Optional: confirm the build really carries every asset with
   `venv\Scripts\python verify_exe_assets.py --exe dist\SceneBoard\SceneBoard.exe
   --zip dist\SceneBoard-Windows.zip --icon ui\icons\SceneBoard.ico
   --source-dir "%TEMP%\SceneBoard-highres-packs" --filtered-dir
   "%TEMP%\SceneBoard-supported-asset-packs"`. It checks that every image in
   the release ZIPs survived filtering, that each pack is bundled byte-for-byte
   (in the folder and in the zip) and that the EXE carries the app icon; add
   `--launch` to also start the app and verify the first-launch install. The
   **Windows EXE assets** GitHub Actions workflow runs this on a clean Windows
   runner — use *Run workflow* to re-check at any time.

The app icon (a folded map with a grid and a location pin) is drawn in code in
`ui/app_icon.py`; `python make_icon.py` rewrites `ui/icons/SceneBoard.ico`
(16–256 px) after a design change.

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
  those paths; overlapping smart views add counts for core modules and modular
  tile pieces, rooms, floors, walls, corridors, doors, controls, engineering,
  medical/science,
  furniture, storage, vehicles/aircraft, weapons/security, food/galley,
  loose props, organic remains, landscaping/vegetation, overlays, lighting,
  hazards, fire/smoke, and symbols. Unmatched art stays visible under **Other /
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
  nodes' edges/centers, the canvas centerlines and placed guides; the nearest
  target wins, with red smart-guide lines showing node-to-node alignment.
  Per-node "Snap to grid" checkbox can disable grid snapping for one node;
  hold **Alt** while dragging to place a node freely with no snapping at all.
- **Yellow handle** = rotate (hold **Shift** for 15°). **Right/Middle-drag**
  pans; **wheel** zooms.
- Multi-select: **Shift-click** or drag a marquee. Then align / distribute /
  **Glue** (group) / tint / delete / duplicate / copy-paste together. Grouped
  nodes are picked together by a click, marquee or lasso; **Ctrl+click** picks
  a single member. Copies of a group form their own group. Glue and unglue can
  be undone.
- **Selecting and the clipboard**: **Ctrl+A** selects everything you could
  click (locked nodes and hidden or locked layers are skipped),
  **Ctrl+Shift+I** inverts the selection and **Ctrl+Shift+A** deselects.
  **Edit → Select**, the node right-click menu (*Select → Everything on this
  layer*) and the Layers panel's right-click menu select a whole layer.
  **Ctrl+C / Ctrl+X / Ctrl+V / Ctrl+D** copy, cut, paste and duplicate (text
  boxes and the library keep their own copy and paste). Right-click the map →
  **Paste here** drops the clipboard where you clicked.
- **Arrange**: right-click → *Arrange* → **Bring to front**, *Bring forward*,
  *Send backward* or **Send to back** (also in the Edit menu). Forward and
  backward now step past the next overlapping node every time.
- **Send to level**: right-click → *Send to level* → **Move to** / **Copy to**
  another level, or *Copy to → Every other level*. Nodes keep their exact
  position and go on the layer with the same name there — handy for lift
  shafts, ladders and hull pieces that repeat on every deck.
- **Swap image**: select node(s) on the map, right-click a library image →
  *Swap the selected node(s) on the map to this*. They keep their place,
  rotation, flips, layer, tint and cut-outs; the size follows the new picture
  (a 100x100 ft tile stays 20x20 squares). **[** and **]** step a selected tile
  through the other images in its library folder (holding the key is one undo
  step). *Swap every copy of the selected node's image to this* changes every
  copy on every level. The node right-click menu has the same under *Swap
  image* (plus the old *Replace with a file from disk*).
- **Cut out part of an image** (right-click an image → *Cut out part of the
  image*, Tools menu, the Inspector's *Cut out part…* button, or the **Cut
  Out** toolbar tool): pick a **Rectangle**, **Ellipse** (Shift = circle),
  **Lasso** (freehand) or **Polygon** (click the corners; double-click, Enter
  or click the first corner to close) on the bar at the top of the canvas, and
  draw the area. *Snap to grid* puts rectangle, ellipse and polygon corners on
  grid lines (Alt = free), so a doorway is exactly one or two squares wide. The
  area shows as marching ants; drag it or use the arrow keys to move it. Then:
  - **Delete** (or Backspace) hides that part of the image — the tile or the
    backdrop underneath shows through, and clicks there go to whatever is below;
  - **Ctrl+X** cuts it and **Ctrl+C** copies it; **Ctrl+V** pastes it as a new
    node (right-click → *Paste here* puts it where you click), so you can move a
    console or a room section anywhere, even to another level;
  - **New node** cuts it out into its own node right where it was, ready to drag;
  - **Keep only** hides everything else.
  Right-click inside the area for the same choices. The tool works on the
  selected image(s); with nothing selected it takes the image under the area,
  and **Ctrl+click** adds or removes images — select two tiles to open a
  doorway across the seam between them in one go. Everything is
  non-destructive: the image file is never changed, undo works, and
  *Restore cut-out areas* (right-click) or the Inspector's *Restore cut-outs*
  brings the picture back. A pasted part's *Reset crop* turns it back into the
  whole picture. Esc clears the area, a second Esc puts the tool away.
- **Clone patch** (right-click an image, Tools menu or the **Clone** toolbar
  tool): drag a box over a label printed on a tile, then point at clean floor —
  a dashed box shows what will be copied — and click. The patch is a piece of
  the same picture laid right over the label, so floor plates, grating and
  stains match. The source moves in whole squares so repeating floor patterns
  line up (Alt = free). The tool stays on for the next label; Esc finishes.
  Select a patch → *Pick a new clone source…* to choose a different spot later.
- **Mirror copy**: ship decks are mostly symmetrical, so select one side and
  right-click a guide (or the map's dashed center line) → *Mirror selection
  across this guide*. The node right-click menu and **Edit → Mirror copy** list
  the center lines and every guide on the level; there is also a **Mirror**
  toolbar tool. Copies are flipped and placed on the other side (text and scale
  bars stay readable); nodes centered on the line are left as they are.
- **Duplicate as grid** (**Ctrl+Shift+D**, Edit menu or right-click): repeat the
  selection as one block in rows and columns with a gap in squares — cryo pods,
  bunks, lockers, crates. The dialog shows the size of the result and warns
  when it would run past the map edge.
- **Stamp hotbar (keys 1–9)**: right-click a library asset or a node →
  *Pin to stamp key*, or drag an asset onto a slot. The hotbar appears at the
  bottom of the canvas once something is pinned. Press the number (or click
  the slot), see a faint preview under the pointer, and click to place copies;
  press the number again, **Esc** or right-click to stop. Pinned library assets
  are placed like a library drop; pinned nodes keep their size, rotation, flips,
  tint and crop. The keys are remembered across maps (View → Stamp hotbar
  hides it).
  **Door mode** (right-click a slot → *Door mode*): each copy sits centered on
  the nearest grid line and turns to run along it — horizontal walls get a
  horizontal door, vertical walls a vertical one — with its ends on grid
  points (a door two squares long is centered on a grid crossing). The door's
  bottom side faces the side of the wall your pointer is on, so a swing arc
  follows the pointer. Drag along a wall to fill it, one door per segment;
  clicking a spot that already has the same door does nothing. Assets named or
  sorted as doors, hatches, airlocks or vents start out in door mode; a small
  wall-and-door badge marks those slots.
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
  image and is not directly editable. To hide a baked-in label on a textured
  floor, use **Clone patch** (below). For a flat cover, use the
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
- **Guides**: thin magenta lines for lining things up, separate from the grid.
  Thin **guide rails** run along the inside edges of the canvas view (with grid
  ticks). Drag from the left or right rail for a vertical guide, or from the
  top or bottom rail for a horizontal one; a readout shows the position (e.g.
  `x 14 sq · 70 ft`), and guides snap to grid lines, node edges/centers and the
  map's middle while you drag (**Alt** = free). Drag a guide back onto any rail
  to remove it, double-click it to type an exact position, or press **Esc**
  mid-drag to cancel. Nodes snap to guides when moving, resizing and dropping
  from the library, and the ruler, scale bar, connector, crop, stamp, zone and
  patch tools snap their points to them too (Shift still means grid). Guides
  belong to each level, are saved with the map and are undoable. Right-click
  a guide or rail, or use **View → Guides**, to show/hide (Ctrl+;), toggle
  snapping (Ctrl+Shift+;), lock guides against accidental drags (Ctrl+Alt+;),
  copy guides to all levels, clear them, pick their color, add guides around
  a selection (also on the node right-click menu) or open **Guide layout…**
  (a guide every N squares, margin guides, center guides).
- **Grid coordinates**: column letters (A, B … Z, AA …) and row numbers along
  the rails, so you can call out squares like `F7`. Toggle them from
  **View → Guides → Grid coordinates on rails**, the rail right-click menu or
  the canvas properties; hide the rails entirely with **View → Guide rails**.
- **Arrow keys** nudge 1px; **Shift+arrows** move exactly one square.

### 4. Generate a map (Tools → Generate Map…)
One window, one set of options, top to bottom:
1. **What to build**
   - *Assemble a map from rooms and decks* packs full deck plans, 50 ft rooms
     and empty rooms into a map of the size you choose. *Mixed sizes* fills the
     area with whatever fits (50x50, 100x100, 200x100, 100x200…); *Uniform* uses
     one size in a tidy grid. Optional hull parts (nose + matching
     port/starboard) are added around the edge. Matching `[Overlay]` images
     inherit their base module's placement, scale and rotation, and Symbols can
     be scattered on a separate layer.
   - *Build rooms and corridors from small tiles* is the classic floor / wall /
     door builder (Starship / Colony base / Research lab, Random / Corridors /
     Grid / Organic layouts, BFS-verified connectivity).
   - *Furnish existing rooms* scatters interior parts inside the selected
     nodes, or in every empty room on the level, with a wall margin and density.
2. **Which assets to use** — tick the roles that feed it, with usable counts.
3. **Size and placement** — a new level (size presets up to 160 x 160 squares,
   or custom) or the area of the selected nodes. The canvas grows if the map
   needs more room.
4. **Style** — only the options that apply to the chosen strategy are shown.
5. **Seed** — 12 random digits by default, or type any numbers/words. **🎲**
   rolls a new random seed, **Copy** copies it, and **Regenerate** rolls a new
   seed and replaces the previous result (tick *Keep this seed* to rebuild with
   the same one). The same seed, settings and assets always give the same map.

**Asset sorting.** Every asset has exactly one generator *role*: deck plan,
room, empty room, ship part, corridor, build-it piece, floor tile, wall tile,
door, interior part, overlay, symbol, terrain or unsorted. Roles come from the
file name, folder, name-coded size (`[100x100]` is feet) and image size.
Browse them under **Generator roles** in the library tree, right-click assets
(select several) → *Set generator role*, or open **Check asset sorting…**
(also in the library ☰ menu) to filter, review "Needs a look", and reassign in
bulk. Your choices (★) are saved with the asset store and always win.

### 5. Floors / Levels
- Tab bar above the canvas: add / remove / rename / reorder floors.
- **Backdrop** (per level; Node tab with nothing selected, right-click the empty
  map → *Backdrop…*, or **View → Backdrop**): **Solid color**, **Floor
  texture** — an image tiled under everything, with a tile size in squares and
  a strength that fades it into the color — or **None (transparent)**. Press
  **Upload an image…** to use a picture from your computer (PNG, JPG, WEBP, BMP
  or TIFF): a copy goes into the library's **Backdrops** folder, so the map
  keeps finding it even if the original moves, picking the same file again
  reuses that copy, and the map generator leaves that folder alone. Or pick a
  library image and press *Use the highlighted library image*, or right-click
  a library image → *Use as backdrop*. **None** is shown as a checkerboard on the
  canvas and kept transparent in PNG exports. Packs whose tiles have
  transparent surroundings sit on the backdrop, and it shows through cut-out
  holes. *Use this backdrop on every level* copies it to the other levels.
  Backdrop textures count as images the map uses: missing ones are reported,
  and project bundles carry them.
- **Reference floor overlay**: ghost the floor above/below (adjustable opacity)
  so you can align things between levels.
- **Layers** (right panel): per-level layers with an **eye** button to show or
  hide the layer, a **picture** button that keeps a layer on the canvas but out
  of PNG/PDF exports (for images you trace over, notes or work in progress —
  the export dialog lists such layers), a padlock, solo, opacity, reorder, and
  an *active layer* that new nodes go to.

### 6. Export
- **Export PNG…**: current level or all levels, size presets (Foundry, Roll20,
  Tabletop Simulator, Print 2×/4×), the level's backdrop (or *Leave out the
  backdrop* for a transparent PNG; a level whose backdrop is *None* is
  transparent anyway), and separate exported-grid on/off, color, and opacity
  controls. Cropped nodes and pasted parts export at full sharpness (they
  used to be decoded at the size of the cropped part, which blurred them).
- **Export for Tabletop Simulator…**: a direct entry point to an opaque PNG
  export, with 1024/2048/3072px longest-edge presets that preserve aspect
  ratio. Import the PNG in TTS as a **Custom Board**; the preset defaults to
  2048px and lets you choose whether to bake in the grid. Boards are always
  opaque: a transparent backdrop is filled with the level's color. Gameplay-zone
  borders/labels are static artwork in the PNG; use TTS's native zone tools
  separately when you need interactive in-game behavior such as Fog of War.
- **Export PDF…**: choose the current level or all levels (one page per level),
  with the same sizing, transparency, and grid/color/opacity controls as PNG.
- Editor aids are left out of exports unless you ask for them. PNG and PDF
  exports have separate **Include canvas centerlines**, **Include guides** and
  **Include grid coordinates** checkboxes (all off by default and remembered
  per map). Coordinates add a labeled border exactly one square wide on every
  side, so a virtual-tabletop grid still lines up, offset by one square.
- **Export project bundle / PNG pack…**: creates a portable `.rpgpack` ZIP with
  the editable project, its referenced source assets, a manifest, and a PNG
  render of every level (longest edge capped at 2048 px). Open a `.rpgpack`
  from File → Open to extract a local working copy; saving that copy does not
  overwrite the original pack.

### 7. Save / Open
- Projects are `.bmap` JSON (node placements and outlines, gameplay zones and
  labels, tint overlays, editable text, non-destructive crops and patches,
  connection markers, static scale bars, embedded custom images, levels, and
  layers). Maps that use this app's own asset library save it as "the app's
  library" rather than a folder path, so a map opens with its images on any PC
  where the same packs are installed. If a map's saved asset folder isn't on
  this computer, it uses this app's library instead.
- **Missing images** are named on the canvas (red dashed outline) and listed
  when a map opens; **File → Find missing images…** relinks them by file name
  (undoable) after you import the pack they came from. PNG/PDF export warns
  before writing grey boxes for images it can't find.
- The recent-maps list, map previews and backups live in the per-user app-data
  folder, so the EXE remembers recent maps between launches and nothing is
  written next to your maps (older versions saved a `<map>.png` preview there,
  which could overwrite an export with the same name).
- Open, save, import and export dialogs start in the folder you used last
  (not the folder the program started from); exports are named after the map
  (and level).
- **Auto-save** is on by default (every 5 minutes; choose Off / 1 / 5 / 10
  minutes from File or the ESC menu). Named projects are saved in place, and
  before each auto-save the version on disk goes into one of **4 rolling
  backups** (the oldest is replaced each cycle). **File → Restore from
  backup…** loads one as an undoable change; nothing is written until you
  save. Unsaved maps get a recovery copy, with a restore prompt on the next
  launch.
- Closing, **New** or **Open** with unsaved changes asks **Save / Don't Save /
  Cancel**.
- **Undo/Redo** (Ctrl+Z / Ctrl+Y) with named history; **Ctrl+S** save,
  **Ctrl+O** open, **Ctrl+N** new.

### Layout, layers and history
- Drag the splitters to resize the Library / canvas / Inspector; the layout is
  remembered. **View → Reset panel layout** restores the default.
- **Layers panel:** double-click a name to rename, drag rows to reorder,
  right-click for color labels, solo, duplicate and delete, and use the filter
  box on long lists. **S** solos a layer for editing only (never saved or exported).
- Continuous edits (sliders, spin boxes, held arrow keys) collapse into one
  undo step; snapshots are stored compressed with embedded images shared.
- The canvas only paints pieces inside the visible area. The map edge is drawn
  as a bold frame with a hatched, dimmed pasteboard outside it (off-canvas nodes
  are kept but clipped in exports).
- Align and Distribute never leave nodes overlapping unless **Allow overlap** is
  ticked (Edit menu or the multi-select panel); colliding nodes are stacked
  instead. Moving and resizing snap any edge to grid lines and to neighbors'
  sides, corners and centers.
- Library tiles named in feet (e.g. `[100x100]`) are scaled to your square size
  on placement, so a 100x100 ft tile is 20x20 squares. New maps default to
  60x60 squares. Library previews are cached on disk after the first decode.

### Workspace, menus and right-click
- The screen is kept clear for the canvas: the toolbar shows only New / Open /
  Save / Export / Undo / Redo / Generate by default; everything else is in the
  menus, the right-click menu and the command palette (Ctrl+Shift+P).
- **View** toggles every bar and panel (Toolbar F4, Status bar, Level tabs,
  Minimap F5, Library F2, Inspector F3, floating node buttons), has
  **Workspace → Standard / Minimal / Canvas only**, and **Ctrl+\\** hides
  everything for a clean canvas (press again to restore). Choices are remembered.
- The **inspector** (Node / Layers / Zones / History) can be dragged as narrow
  as 220 px — wider with a bigger **Text size** — and stays readable: when a
  section gets tight its labels move above the fields, long buttons, checkboxes
  and file names wrap onto more lines, and rows of buttons continue on the next
  line, so nothing is cut off.
- Levels: right-click a tab (add, rename, move, delete) or **Edit → Levels**.
  Layers: right-click the list. Library: the **☰** button next to search holds
  import, collections, thumbnail size (presets up to 360 px, a custom slider, or
  Ctrl+wheel over the list), the folder-tree toggle and store tools.
- Rotation snaps to 15° stops (hold **Shift** to always step, **Alt** for free).
- **Centerlines** (canvas properties → grid) draw dashed amber lines through
  the middle of the map; nodes snap to them, and they only appear in exports
  when **Include canvas centerlines** is ticked. New nodes are **auto-tightened** to their visible
  pixels (a non-destructive crop, so transparent margins no longer spoil grid
  snapping); use Edit → Tighten selected, or turn auto-tighten off in the Edit menu.

### Keyboard reference
| Key | Action |
|---|---|
| **Esc** | System menu (save / open / quit / theme / recents) |
| Arrows | Nudge 1px (Shift = one grid square) |
| Delete | Delete selection |
| R | Rotate selection 90° |
| Ctrl+Z / Ctrl+Y | Undo / Redo |
| Ctrl+S / Ctrl+O / Ctrl+N | Save / Open / New |
| Ctrl+Shift+P | Command palette (search every menu command) |
| F2 / F3 / F4 / F5 / Ctrl+\\ | Library / Inspector / Toolbar / Minimap / everything |
| Right-click (no drag) | Context menu; right-drag still pans |
| Drag a handle | Resize: corners keep proportions, **Shift** frees them; edges stretch one axis; **Alt** resizes from the center |
| Ctrl+T | Free transform: corners are free (Shift locks), Enter applies, Esc cancels; the whole session is one undo step |
| Drag from a canvas edge rail | New guide (left/right rail = vertical, top/bottom = horizontal); drag a guide back onto a rail to remove it |
| Ctrl+; / Ctrl+Shift+; / Ctrl+Alt+; | Show guides / Snap to guides / Lock guides |
| Alt (while dragging) | Move a node or guide with no snapping |
| 1 – 9 | Pick up that stamp key; click to place copies (same key, Esc or right-click stops) |
| Ctrl+Shift+D | Duplicate as grid |
| Ctrl+click | Pick one member of a group |
| Ctrl+A / Ctrl+Shift+A / Ctrl+Shift+I | Select all / Deselect / Invert selection |
| Ctrl+C / Ctrl+X / Ctrl+V / Ctrl+D | Copy / Cut / Paste / Duplicate (in the cut-out tool: the selected area) |
| [ / ] | Swap the selected image to the previous / next image in its library folder |
| Cut-out tool: Delete or Backspace | Hide the selected area (Backspace also removes the last polygon corner) |
| Cut-out tool: Shift / Alt / Ctrl+click | Square or circle / no grid snapping / add or remove an image |
| Cut-out tool: Esc | Clear the area; press again to put the tool away |

---

## Project layout
```
core/   project (data model) · history (undo/redo) · asset_manager (internal
        store) · exporter (PNG/PDF) · bundle (portable RPG Map Packs) ·
        render (shared piece drawing) · generator (tile layouts + geomorph assembly) ·
        guides · transforms (mirror / grid copies) · stamps (hotbar keys) ·
        backups (rolling auto-save backups) · relink (missing images) ·
        userfiles (recent maps, previews, file names) · cutouts (cut-out
        holes, pasted parts, clone patches)
ui/     main_window · canvas · library · properties · layers_panel · zones_panel ·
        menu_overlay (ESC project menu) · generator_dialog · theme (Dark/Light/Alien) ·
        export_dialog · stamp_bar · tool_dialogs · glyphs · app_icon ·
        canvas_tools (selection basics, swapping, cut-out tool, clone patch) ·
        cutout_bar
sample_fixtures.py   demo images for tests, generated on demand (generate_samples.py)
```
