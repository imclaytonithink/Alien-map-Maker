# MU-TH-UR 6000 — Sci-Fi Battlemap Builder

A desktop app for assembling PNG map pieces into sci-fi battlemaps: snap them
to a grid, rotate/flip, drop free overlays, tint/recolor, add text labels,
manage floors/levels, overlay a reference floor, and export to PNG or PDF.

Built with **Python + PyQt6**. Packaged to a standalone `.exe` via PyInstaller.

---

## Running from source (any OS with Python)
```bash
pip install -r requirements.txt
python main.py
```
On first launch the **system menu** opens over the editor (blurred backdrop):
pick *New map*, open a *Recent* map, or start from a *Template*, and choose
the theme (Green / Amber / Red + scanline/boot toggles). Press **ESC** any
time to bring the menu back up — it has Save, Open, Export, Quit, recent-map
management (open / remove / delete from disk), templates, and the theme
picker.

## Building the .exe (Windows)
1. Install Python 3.10+ from python.org (tick "Add Python to PATH").
2. Run `build.bat` (double-click). Your `.exe` appears at `dist/BattlemapBuilder.exe`.

> PyInstaller can't cross-compile from Linux/macOS, so build the `.exe` on Windows.

---

## How to use

### 1. Bring your pictures into the tool (stays forever)
- **Import Folder** / **Import File** (top of the library panel) copies your
  PNGs into the app's **internal asset store** — shown at the bottom of the
  panel (`Store: …`) with an **Open** button and a **?** help button that
  explains exactly where files live. They're auto-grouped by their source
  subfolder, auto-tagged by type/size, and you can reorder groups with ▲▼.
- **Custom single PNGs** dragged straight onto the canvas are *embedded*
  inside the saved `.bmap` file, so that map is portable on its own.

### 2. Browse & preview
- Search by name or size (e.g. `40x120`).
- Hover an asset ~0.6s for a pop-out preview; **right-click → View larger…**
  for a full preview with an *Add to canvas* button.
- Drag an asset onto the canvas, or double-click to drop at center.
- While you drag, the canvas lights up the **exact landing cell(s)** under
  the incoming piece (calibrated to where it will snap, not the raw cursor).
- Thumbnail size slider on the left.

### 3. Build the map
- **Left-drag** a piece to move. Dragging moves the whole selection as one
  rigid group — no more accidental deformation.
- **Snapping** (on by default): pieces snap to the grid *and* to neighboring
  pieces' edges/centers; the nearest target wins, with red smart-guide lines
  showing piece-to-piece alignment. Per-piece "Snap to grid" checkbox can
  disable it for a single piece.
- **Yellow handle** = rotate (hold **Shift** for 15°). **Right/Middle-drag**
  pans; **wheel** zooms.
- Multi-select: **Shift-click** or drag a marquee. Then align / distribute /
  **Glue** (group) / tint / delete / duplicate / copy-paste together.
- The **quick toolbar** (top-right of canvas) rotates/flips, raises/lowers,
  locks, copies, duplicates, deletes the selection.
- **Tint** a piece (or whole selection) with a color picker + strength slider.
- **Text** tool adds labels with font/size/bold/color (color picker included).
- **Grid**: square grid with opacity slider, solid/dashed/dotted style, major
  lines every N cells, adjustable cell size & "square = 5ft/10ft".
- **Arrow keys** nudge 1px; **Shift+arrows** move exactly one square.

### 4. Generate a map (Tools → Generate Map…)
- **Settings**: Starship (corridor spine, compact rooms), Colony base (big
  organic rooms, lots of floor clutter), Research lab (tidy grid, more
  hazards), or Random.
- **Layouts**: Random / Corridors / Grid / Organic.
- **Output**: *New level* or *Fill selected area* (select pieces first).
- Auto-classifies your tiles from filenames into floors, walls, corridors,
  doors, wall fixtures (terminals…) and floor fixtures (crates…) — preview
  panel shows the pools before you generate. Fixtures land only on the right
  surfaces: never a computer on the floor, never a wall in mid-air.
- Every room is connected by a spanning corridor, **BFS-verified** — no
  sealed areas (auto-repairs isolated pockets and tells you).
- **Seed** makes maps reproducible/shareable; **Regenerate** rolls a new seed
  and *replaces* the previous result instead of piling up levels.

### 5. Floors / Levels
- Tab bar above the canvas: add / remove / rename / reorder floors.
- **Reference floor overlay**: ghost the floor above/below (adjustable opacity)
  so you can align things between levels.
- **Layers** (right panel): per-level layers with show/hide, lock, opacity,
  reorder, and an *active layer* that new pieces go to.

### 6. Export
- **Export PNG…**: current level or all levels, size presets (Foundry, Roll20,
  **Tabletop Simulator**, Print 2×/4×), transparent background, and a separate
  **grid on/off + color + opacity** for the exported image.
- **Export PDF…**: one page per level.

### 7. Save / Open
- Projects are `.bmap` JSON (placements, tints, text, embedded custom images,
  layers, theme). A thumbnail is saved next to the file for the menu.
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
        store) · exporter (PNG/PDF) · render (shared piece drawing) ·
        generator (procedural maps, settings + BFS validation)
ui/     main_window · canvas · library · properties · layers_panel ·
        menu_overlay (ESC system menu) · generator_dialog · theme (MUTHER look) ·
        export_dialog
sample_assets/   demo pieces
```
