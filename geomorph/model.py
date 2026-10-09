"""Shared data model for the generation pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field

TIER = {"public": 0, "staff": 1, "restricted": 2, "secure": 3, "containment": 4}


@dataclass
class ZoneInst:
    id: str                      # unique, e.g. "cells#2"
    base: str                    # archetype zone id
    name: str
    tags: list
    access: str = "staff"
    level: object = "any"        # any | top | bottom | int
    position: str = "any"
    checkpoint: bool = False
    filler: str = ""             # filler kind used when no tile exists / filler_only
    filler_only: bool = False
    prefer: list = field(default_factory=list)      # base ids
    forbid: list = field(default_factory=list)
    entrance: bool = False
    height: int = 1              # 2/3 = double/triple height (void above)
    required: bool = True
    text: str = ""

    @property
    def tier(self):
        return TIER.get(self.access, 1)


@dataclass
class Slot:
    idx: int
    level: int
    x: int
    y: int
    w: int = 20
    h: int = 20
    kind: str = "standard"       # standard | filler
    pos: str = "any"             # core | edge | corner
    role: str = ""               # "" | "vertical" | "headframe" | "hub" | ...
    zone: ZoneInst | None = None
    face: tuple = ()             # directions links leave from (campus/hub), e.g. ("E","S")
    reserved: str = ""           # "void" when open above a tall room below
    placed: object = None        # placement.Placed once a tile is chosen
    fixed_tile: object = None    # force a tile (vertical cores, the two floors of a tall room)
    pair: int = -1               # other floor of a tall room: slot index (lower <-> upper)

    @property
    def cx(self): return self.x + self.w / 2
    @property
    def cy(self): return self.y + self.h / 2


@dataclass
class Link:
    a: int                       # slot idx
    b: int
    kind: str = "contact"        # contact | walkway | tunnel | road | stairs | lift | shaft
    open: bool = True            # connection exists (a door/corridor)
    native: int = 0              # matched native doors
    patched: bool = False        # door/corridor added by the engine
    sealed: bool = False         # native doors present but closed off (access rules)
    squares: tuple = ()          # (x, y) of the connection (for drawing/markers)


@dataclass
class Layout:
    levels: int
    level_names: list
    slots: list
    links: list = field(default_factory=list)
    filler: list = field(default_factory=list)       # procedural pieces (dicts, see filler.py)
    vertical: list = field(default_factory=list)     # vertical structures
    volumes: list = field(default_factory=list)      # special volumes
    markers: list = field(default_factory=list)      # utility shafts etc.
    entrance: dict = field(default_factory=dict)
    bounds: tuple = (0, 0, 0, 0)
    topology: str = ""
    notes: list = field(default_factory=list)
    gate: dict = field(default_factory=dict)


@dataclass
class Result:
    """Everything one generation produced (before export)."""
    kind: str                                  # "ship" | "site"
    meta: dict
    grids: list                                # [LevelGrid]
    zones: dict                                # zone id -> ZoneInst
    layout: "Layout | None" = None
    links: list = field(default_factory=list)  # Link objects (logical connections)
    markers: list = field(default_factory=list)
    overlays: dict = field(default_factory=dict)
    key: list = field(default_factory=list)
    text: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    gaps: dict = field(default_factory=dict)
    section: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    registry: object = None
