"""MU/TH/UR terminal export: terminal markers on the map -> a short code for Tabletop Simulator.

The GM hand-places Terminal markers in rooms. On export, every marker is matched to the
room it sits in (a hand-drawn gameplay zone, or the Geomorph room under it), checked, and
packed into a short letters-and-digits code. The MU/TH/UR terminal script in Tabletop
Simulator carries the same room catalog (``core/muthur_catalog.json``) and decodes the code
back into its rooms.

Code layout (version 1), most significant bit first, base32 (Crockford) with a 10-bit check:
    version 3 | archetype 6 | floors-1 4 | terminals-1 5 | seed 12
    per terminal: other-archetype 1 [archetype 6] | room ceil(log2 rooms) | copy>1 1 [copy-2 3]
                  | floor-1 ceil(log2 floors) | state 2
No Qt imports.
"""
from __future__ import annotations

import base64
import io
import json
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parent / "muthur_catalog.json"
VERSION = 1
MAX_TERMINALS = 26          # the terminal holds 26 rooms (A..Z)
MAX_FLOORS = 16
MAX_COPY = 9
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
STATES = ("Normal", "Power out", "Quarantine", "Lockdown")
NORMAL, POWER_OUT, QUARANTINE, LOCKDOWN = range(4)
TERMINAL_NAME = "MU/TH/UR terminal"

_CATALOG = None


def catalog() -> dict:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return _CATALOG


def room_bits(arch_index: int) -> int:
    n = len(catalog()["archetypes"][arch_index]["rooms"])
    return max(1, math.ceil(math.log2(n)))


def floor_bits(floors: int) -> int:
    return 0 if floors <= 1 else math.ceil(math.log2(floors))


# --------------------------------------------------------------------------
# Closest catalog room for a free-form name (hand-drawn zones, ship tiles, custom archetypes)
# --------------------------------------------------------------------------
_STOP = {"and", "the", "of", "a", "an", "room", "area", "section", "module", "block", "blocks", "bay", "bays"}


_SYN = {"armoury": "armory", "med": "medical", "medbay": "medical", "sickbay": "medical",
        "labs": "lab", "laboratory": "lab", "laboratories": "lab", "quarter": "quarters",
        "cafeteria": "mess", "canteen": "mess", "galley": "mess", "hab": "habitat",
        "comm": "comms", "communications": "comms", "radio": "comms", "reactor": "power",
        "generator": "power", "hangar": "hangar", "dock": "docking", "jail": "cell", "prison": "cell"}


def _words(text) -> set:
    out = set()
    for w in re.findall(r"[a-z0-9]+", str(text).lower()):
        w = _SYN.get(w, w)
        if w in _STOP:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and w != "comms" else w)
    return out


def _overlap(want: set, have: set) -> float:
    """Shared words, with half credit for words sharing their first five letters."""
    hit = len(want & have)
    for w in want - have:
        if len(w) >= 5 and any(h[:5] == w[:5] for h in have):
            hit += 0.5
    return hit / len(want | have)


def closest(name: str, tags=(), prefer_arch: int | None = None, allowed_only=False):
    """(archetype index, room index) of the catalog room nearest to ``name``.

    Exact names win, then shared words, then shared function tags; rooms of
    ``prefer_arch`` win ties. Returns ``None`` when nothing at all matches."""
    want, low, tags = _words(name), str(name).strip().lower(), set(tags or ())
    best, best_score = None, 0.0
    for ai, a in enumerate(catalog()["archetypes"]):
        for ri, r in enumerate(a["rooms"]):
            if allowed_only and not r["allowed"]:
                continue
            have = _words(r["name"])
            score = 0.0
            if r["name"].lower() == low:
                score += 10
            if want and have:
                score += 4 * _overlap(want, have)
            if tags and r["tags"]:
                score += 2 * len(tags & set(r["tags"])) / len(tags | set(r["tags"]))
            if any(w in r["pool"].lower() for w in want):
                score += 0.5
            if score > 0 and ai == prefer_arch:
                score += 0.25
            if score > best_score:
                best, best_score = (ai, ri), score
    return best


def arch_index(name: str) -> int | None:
    for i, a in enumerate(catalog()["archetypes"]):
        if a["name"].lower() == str(name).lower():
            return i
    return None


def resolve_room(info: dict):
    """Catalog (archetype, room) for a Geomorph room record (``geomorph.canvas_export.room_record``)."""
    ai = arch_index(info.get("arch", ""))
    if ai is not None:
        for ri, r in enumerate(catalog()["archetypes"][ai]["rooms"]):
            if r["id"] == info.get("base"):
                return ai, ri
    return closest(info.get("name", ""), info.get("tags", ()), prefer_arch=ai)


# --------------------------------------------------------------------------
# Code: bits <-> base32
# --------------------------------------------------------------------------
class _Bits:
    def __init__(self, bits=None):
        self.bits = list(bits or [])
        self.pos = 0

    def put(self, value: int, width: int):
        if width <= 0:
            return
        if value < 0 or value >= (1 << width):
            raise ValueError(f"{value} does not fit in {width} bits")
        for i in range(width - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def get(self, width: int) -> int:
        v = 0
        for _ in range(width):
            if self.pos >= len(self.bits):
                raise ValueError("code is too short")
            v = v * 2 + self.bits[self.pos]
            self.pos += 1
        return v


def _check(symbols) -> int:
    c = 0
    for v in symbols:
        c = (c * 37 + v + 1) % 1021
    return c


def format_code(raw: str) -> str:
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def normalize(code: str) -> str:
    s = str(code).upper().replace("O", "0").replace("I", "1").replace("L", "1")
    return "".join(ch for ch in s if ch in ALPHABET)


@dataclass
class Terminal:
    arch: int
    room: int
    copy: int = 1
    floor: int = 1
    state: int = NORMAL

    @property
    def record(self) -> dict:
        return catalog()["archetypes"][self.arch]["rooms"][self.room]

    @property
    def name(self) -> str:
        n = self.record["name"]
        return f"{n} {self.copy}" if self.copy > 1 else n


@dataclass
class Spec:
    arch: int
    floors: int = 1
    seed: int = 0
    terminals: list = field(default_factory=list)


def encode(spec: Spec) -> str:
    t = spec.terminals
    if not 1 <= len(t) <= MAX_TERMINALS:
        raise ValueError(f"a code holds 1 to {MAX_TERMINALS} terminals")
    if not 1 <= spec.floors <= MAX_FLOORS:
        raise ValueError(f"a code holds 1 to {MAX_FLOORS} floors")
    b = _Bits()
    b.put(VERSION, 3)
    b.put(spec.arch, 6)
    b.put(spec.floors - 1, 4)
    b.put(len(t) - 1, 5)
    b.put(spec.seed % 4096, 12)
    fb = floor_bits(spec.floors)
    for term in t:
        if term.arch == spec.arch:
            b.put(0, 1)
        else:
            b.put(1, 1)
            b.put(term.arch, 6)
        b.put(term.room, room_bits(term.arch))
        copy = max(1, min(MAX_COPY, term.copy))
        if copy == 1:
            b.put(0, 1)
        else:
            b.put(1, 1)
            b.put(copy - 2, 3)
        if not 1 <= term.floor <= spec.floors:
            raise ValueError("terminal floor out of range")
        b.put(term.floor - 1, fb)
        b.put(term.state, 2)
    while len(b.bits) % 5:
        b.bits.append(0)
    symbols = [int("".join(map(str, b.bits[i:i + 5])), 2) for i in range(0, len(b.bits), 5)]
    c = _check(symbols)
    symbols += [c // 32, c % 32]
    return format_code("".join(ALPHABET[s] for s in symbols))


def decode(code: str) -> Spec:
    s = normalize(code)
    if len(s) < 8:
        raise ValueError("code is too short")
    symbols = [ALPHABET.index(ch) for ch in s]
    data, chk = symbols[:-2], symbols[-2] * 32 + symbols[-1]
    if _check(data) != chk:
        raise ValueError("code check failed - a character is wrong or missing")
    bits = []
    for v in data:
        bits += [(v >> i) & 1 for i in range(4, -1, -1)]
    b = _Bits(bits)
    if b.get(3) != VERSION:
        raise ValueError("this code was made by a newer SceneBoard")
    cat = catalog()["archetypes"]
    spec = Spec(arch=b.get(6), floors=b.get(4) + 1)
    count = b.get(5) + 1
    spec.seed = b.get(12)
    if spec.arch >= len(cat):
        raise ValueError("unknown site type in code")
    fb = floor_bits(spec.floors)
    for _ in range(count):
        a = b.get(6) if b.get(1) else spec.arch
        if a >= len(cat):
            raise ValueError("unknown site type in code")
        r = b.get(room_bits(a))
        if r >= len(cat[a]["rooms"]):
            raise ValueError("unknown room in code")
        copy = b.get(3) + 2 if b.get(1) else 1
        floor = b.get(fb) + 1
        spec.terminals.append(Terminal(a, r, copy, floor, b.get(2)))
    return spec


# --------------------------------------------------------------------------
# Map side: markers, rooms, checks
# --------------------------------------------------------------------------
def _in_tile(piece, x, y) -> bool:
    info = piece.room
    cx, cy = piece.center
    cx, cy = cx + info.get("ox", 0.0), cy + info.get("oy", 0.0)
    w, h = info.get("fw", 0), info.get("fh", 0)
    turn = round(((piece.rotation - info.get("rot", 0.0)) % 360) / 90) % 2
    if turn:
        w, h = h, w
    return abs(x - cx) <= w / 2 and abs(y - cy) <= h / 2


def _zone_name(zone) -> str:
    name = (zone.label or "").strip() or (zone.name or "").strip()
    return "" if name.lower() in ("zone", "") else name


@dataclass
class Found:
    """One marker after it was matched to a room."""
    piece: object
    level_index: int
    floor: int = 0
    terminal: Terminal | None = None
    source: str = ""          # "zone" | "geomorph"
    source_name: str = ""     # the name on the map
    room_key: str = ""        # identity of the physical room (two markers may not share one)
    states: list = field(default_factory=list)
    allowed: bool = True
    error: str = ""


@dataclass
class Scan:
    found: list
    floors: int
    floor_names: list
    errors: list
    notes: list


def terminal_pieces(level):
    return [p for p in level.pieces if getattr(p, "is_terminal", False)]


def scan(project, zones_first=True) -> Scan:
    """Match every terminal marker to its room. Floors are the levels that hold anything."""
    used = [i for i, lv in enumerate(project.levels) if lv.pieces or lv.zones]
    floor_of = {li: n + 1 for n, li in enumerate(used)}
    found, errors, notes = [], [], []
    copies = {}
    for li, level in enumerate(project.levels):
        for p in terminal_pieces(level):
            x, y = p.center
            f = Found(piece=p, level_index=li, floor=floor_of.get(li, 1))
            zone = next((z for z in level.zones if z.contains(x, y) and _zone_name(z)), None)
            tile = next((t for t in reversed(level.paint_order()) if getattr(t, "room", None)
                         and _in_tile(t, x, y)), None)
            if zone is not None and (zones_first or tile is None):
                f.source, f.source_name, f.room_key = "zone", _zone_name(zone), "z:" + zone.id
                hit = closest(f.source_name)
                f.states = []
            elif tile is not None:
                info = tile.room
                f.source, f.source_name = "geomorph", info.get("name", "")
                f.room_key = f"g:{li}:{info.get('zone') or tile.id}"
                if info.get("arch") == "Ship":
                    f.room_key = f"g:{li}:{tile.id}"
                hit = resolve_room(info)
                f.states = list(info.get("states", []))
            else:
                f.error = "is not inside a room (draw a gameplay zone around it, or place it on a Geomorph room)"
                found.append(f)
                continue
            if hit is None:
                f.error = f"is in '{f.source_name}', which matches no terminal room"
                found.append(f)
                continue
            ai, ri = hit
            rec = catalog()["archetypes"][ai]["rooms"][ri]
            f.allowed = bool(rec["allowed"])
            state = LOCKDOWN if "lockdown" in f.states else QUARANTINE if "quarantine" in f.states \
                else POWER_OUT if "power_failure" in f.states else NORMAL
            chosen = (p.room or {}).get("state")              # the GM's pick in the export dialog
            if isinstance(chosen, int) and 0 <= chosen < len(STATES):
                state = chosen
            f.terminal = Terminal(ai, ri, 1, f.floor, state)
            found.append(f)
    # copy numbers: Geomorph zones carry theirs ("habitat#2"); other rooms count up per name
    seen_keys = {}
    for f in found:
        if f.terminal is None:
            continue
        if f.room_key in seen_keys:
            continue
        m = re.search(r"#(\d+)$", f.room_key)
        key = (f.terminal.arch, f.terminal.room)
        if m and f.source == "geomorph":
            f.terminal.copy = int(m.group(1))
        else:
            copies[key] = copies.get(key, 0) + 1
            f.terminal.copy = copies[key]
        seen_keys[f.room_key] = f
    taken = set()
    for f in seen_keys.values():          # two different rooms never share a terminal name on one floor
        t = f.terminal
        while (t.arch, t.room, t.copy, t.floor) in taken and t.copy < MAX_COPY:
            t.copy += 1
        taken.add((t.arch, t.room, t.copy, t.floor))
    for f in found:                       # markers sharing a room (an error) show that room's name
        if f.terminal is not None and f.room_key in seen_keys and seen_keys[f.room_key] is not f:
            f.terminal.copy = seen_keys[f.room_key].terminal.copy
    names = [project.levels[li].name for li in used]
    return Scan(found=found, floors=max(1, len(used)), floor_names=names, errors=errors, notes=notes)


def check(sc: Scan, expected: int) -> list:
    """Every reason the code can't be given yet (an empty list means it can)."""
    errors = []
    markers = len(sc.found)
    if markers == 0:
        errors.append("There are no MU/TH/UR terminal markers on the map. Place one in each room that has a terminal.")
    if expected != markers:
        errors.append(f"You said the map has {expected} terminal(s), but {markers} marker(s) are placed.")
    if markers > MAX_TERMINALS:
        errors.append(f"The terminal holds at most {MAX_TERMINALS} rooms; {markers} markers are placed.")
    if sc.floors > MAX_FLOORS:
        errors.append(f"A code holds at most {MAX_FLOORS} floors; this map has {sc.floors}.")
    seen = {}
    for f in sc.found:
        where = f"Terminal on {_floor_label(sc, f)}"
        if f.error:
            errors.append(f"{where} {f.error}.")
            continue
        if not f.allowed:
            errors.append(f"{where} is in '{f.source_name}' ({f.terminal.record['name']}), which is not allowed a terminal.")
        if f.room_key in seen:
            errors.append(f"Two terminals are in the same room ('{f.source_name}' on {_floor_label(sc, f)}).")
        seen[f.room_key] = f
    ok = [f for f in sc.found if f.terminal is not None]
    if ok and (sum(1 for f in ok if f.terminal.state == NORMAL) < 1
               or sum(1 for f in ok if f.terminal.state in (NORMAL, POWER_OUT)) < 2):
        errors.append("The terminal needs at least two rooms players can use: one for the END and one that starts "
                      "Normal to hold its clues. Set more rooms to Normal (or Power out).")
    for floor in range(1, sc.floors + 1):
        on = [f for f in sc.found if f.terminal is not None and f.floor == floor]
        for state, label in ((QUARANTINE, "quarantined"), (LOCKDOWN, "locked-down")):
            n = sum(1 for f in on if f.terminal.state == state)
            if n > 1:
                errors.append(f"{sc.floor_names[floor - 1] if floor <= len(sc.floor_names) else 'Floor ' + str(floor)}"
                              f" has {n} {label} terminal rooms; at most 1 per floor.")
    return errors


def limit_states(sc: Scan) -> list:
    """Keep at most one quarantined and one locked-down room per floor (extras go back to normal)."""
    notes = []
    for floor in range(1, sc.floors + 1):
        for state, label in ((QUARANTINE, "quarantine"), (LOCKDOWN, "lockdown")):
            hits = [f for f in sc.found if f.terminal is not None and f.floor == floor and f.terminal.state == state]
            hits.sort(key=lambda f: "state" not in (f.piece.room or {}))     # the GM's own picks are kept
            for f in hits[1:]:
                if "state" in (f.piece.room or {}):
                    continue                                                # check() reports these
                f.terminal.state = NORMAL
                notes.append(f"{f.source_name}: the map's {label} was dropped (only one per floor).")
    return notes


def _floor_label(sc: Scan, f: Found) -> str:
    return sc.floor_names[f.floor - 1] if 0 < f.floor <= len(sc.floor_names) else f"floor {f.floor}"


def build_spec(sc: Scan, seed: int | None = None) -> Spec:
    terms = [f.terminal for f in sorted(sc.found, key=lambda f: (f.floor, f.piece.y, f.piece.x)) if f.terminal]
    counts = {}
    for t in terms:
        counts[t.arch] = counts.get(t.arch, 0) + 1
    arch = max(counts, key=lambda a: (counts[a], -a)) if counts else 0
    if seed is None:
        seed = random.randrange(4096)
    return Spec(arch=arch, floors=sc.floors, seed=seed, terminals=terms)


# --------------------------------------------------------------------------
# The marker itself
# --------------------------------------------------------------------------
_ICON = None


def icon_png_b64(size: int = 256) -> str:
    """A green CRT terminal on a dark plate: the marker players see on the map."""
    global _ICON
    if _ICON is not None:
        return _ICON
    from PIL import Image, ImageDraw
    s = size
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    green, dim, plate = (80, 255, 110, 255), (20, 90, 34, 255), (10, 18, 12, 235)
    d.rounded_rectangle((4, 4, s - 5, s - 5), radius=s // 8, fill=plate, outline=green, width=max(3, s // 40))
    d.rounded_rectangle((s * .18, s * .16, s * .82, s * .60), radius=s // 20, fill=(4, 30, 10, 255),
                        outline=green, width=max(3, s // 48))
    for i, w in enumerate((0.40, 0.28, 0.34)):
        y = s * (.25 + i * .095)
        d.rectangle((s * .26, y, s * (.26 + w), y + s * .04), fill=green)
    d.rectangle((s * .26, s * .515, s * .32, s * .555), fill=green)            # cursor
    d.polygon([(s * .30, s * .66), (s * .70, s * .66), (s * .80, s * .82), (s * .20, s * .82)],
              fill=dim, outline=green)
    for c in range(5):
        x = s * (.29 + c * .085)
        d.rectangle((x, s * .70, x + s * .055, s * .75), fill=green)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    _ICON = base64.b64encode(buf.getvalue()).decode("ascii")
    return _ICON


def make_marker(x: float, y: float, cell: float, layer: str = ""):
    """A terminal marker node (an embedded picture) centered on (x, y), one square wide."""
    from core.project import Piece
    size = 256
    scale = cell / size
    return Piece(name=TERMINAL_NAME, embedded=icon_png_b64(size), w=size, h=size, scale=scale,
                 x=x - size * scale / 2, y=y - size * scale / 2, snap=False, layer=layer, is_terminal=True)
