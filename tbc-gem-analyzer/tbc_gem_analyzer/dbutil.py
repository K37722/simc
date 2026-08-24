"""Database helpers: loads the wowsims/tbc-new item/gem/enchant database (db.json).

The wowsims database is the single source of truth for item sockets, socket
bonuses, gem colors/stats and enchant stats, so the analyzer never needs its
own hardcoded item data.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# --- Stat enum indices (proto/common.proto Stat enum) ---
STAT_STRENGTH = 0
STAT_AGILITY = 1
STAT_STAMINA = 2
STAT_INTELLECT = 3
STAT_SPELL_HIT = 12
STAT_SPELL_CRIT = 13
STAT_SPELL_HASTE = 14
STAT_ATTACK_POWER = 17
STAT_RANGED_ATTACK_POWER = 18
STAT_MELEE_HIT = 20
STAT_MELEE_CRIT = 21
STAT_MELEE_HASTE = 22
STAT_ARMOR_PENETRATION = 23
STAT_EXPERTISE = 24
STAT_ARMOR = 31
STAT_HEALTH = 33
NUM_STATS = 42

STAT_NAMES = {
    STAT_STRENGTH: "Str",
    STAT_AGILITY: "Agi",
    STAT_STAMINA: "Sta",
    STAT_ATTACK_POWER: "AP",
    STAT_RANGED_ATTACK_POWER: "RAP",
    STAT_MELEE_HIT: "Hit",
    STAT_MELEE_CRIT: "Crit",
    STAT_MELEE_HASTE: "Haste",
    STAT_ARMOR_PENETRATION: "ArP",
    STAT_EXPERTISE: "Exp",
}

# Stats that matter for a DPS rogue (used to filter gem candidates and to
# describe stat deltas). Stamina is tracked but weighted ~0.
ROGUE_USEFUL_STATS = (
    STAT_AGILITY,
    STAT_STRENGTH,
    STAT_ATTACK_POWER,
    STAT_MELEE_HIT,
    STAT_MELEE_CRIT,
    STAT_MELEE_HASTE,
    STAT_ARMOR_PENETRATION,
    STAT_EXPERTISE,
)
# Any value in these indices marks a gem as useless/caster-only for a rogue.
ROGUE_JUNK_STATS = (
    STAT_INTELLECT,
    4, 5, 6, 7, 8, 9, 10, 11,  # healing/spell damage
    STAT_SPELL_HIT, STAT_SPELL_CRIT, STAT_SPELL_HASTE, 15, 16,  # caster secondaries, spirit
    25, 26, 27, 28, 29, 30,  # tank secondaries
    35,  # mp5
)

# --- GemColor enum (proto/common.proto) ---
COLOR_UNKNOWN = 0
COLOR_META = 1
COLOR_RED = 2
COLOR_BLUE = 3
COLOR_YELLOW = 4
COLOR_GREEN = 5
COLOR_ORANGE = 6
COLOR_PURPLE = 7
COLOR_PRISMATIC = 8

COLOR_NAMES = {
    COLOR_META: "Meta", COLOR_RED: "Red", COLOR_BLUE: "Blue",
    COLOR_YELLOW: "Yellow", COLOR_GREEN: "Green", COLOR_ORANGE: "Orange",
    COLOR_PURPLE: "Purple", COLOR_PRISMATIC: "Prismatic",
}

# Which socket colors a gem color "matches" for socket-bonus purposes.
COLOR_MATCHES = {
    COLOR_RED: {COLOR_RED},
    COLOR_BLUE: {COLOR_BLUE},
    COLOR_YELLOW: {COLOR_YELLOW},
    COLOR_GREEN: {COLOR_BLUE, COLOR_YELLOW},
    COLOR_ORANGE: {COLOR_RED, COLOR_YELLOW},
    COLOR_PURPLE: {COLOR_RED, COLOR_BLUE},
    COLOR_PRISMATIC: {COLOR_RED, COLOR_BLUE, COLOR_YELLOW},
    COLOR_META: {COLOR_META},
}

# Counts toward meta-gem color requirements (a gem can count as several colors).
COLOR_COUNTS_AS = {
    COLOR_RED: (1, 0, 0),
    COLOR_YELLOW: (0, 1, 0),
    COLOR_BLUE: (0, 0, 1),
    COLOR_ORANGE: (1, 1, 0),
    COLOR_PURPLE: (1, 0, 1),
    COLOR_GREEN: (0, 1, 1),
    COLOR_PRISMATIC: (1, 1, 1),
}

PROFESSION_JEWELCRAFTING = 7

# ItemType enum -> ItemSlot enum index (proto/common.proto)
ITEM_TYPE_TO_SLOTS = {
    1: [0],        # Head
    2: [1],        # Neck
    3: [2],        # Shoulder
    4: [3],        # Back
    5: [4],        # Chest
    6: [5],        # Wrist
    7: [6],        # Hands
    8: [7],        # Waist
    9: [8],        # Legs
    10: [9],       # Feet
    11: [10, 11],  # Finger
    12: [12, 13],  # Trinket
    13: [14, 15],  # Weapon
    14: [16],      # Ranged
}
NUM_ITEM_SLOTS = 17
SLOT_NAMES = [
    "Head", "Neck", "Shoulder", "Back", "Chest", "Wrist", "Hands", "Waist",
    "Legs", "Feet", "Finger 1", "Finger 2", "Trinket 1", "Trinket 2",
    "Main Hand", "Off Hand", "Ranged",
]


@dataclass
class Gem:
    id: int
    name: str
    color: int
    stats: list
    phase: int = 1
    quality: int = 3
    unique: bool = False
    required_profession: int = 0

    @property
    def is_meta(self) -> bool:
        return self.color == COLOR_META

    @property
    def is_jc_gem(self) -> bool:
        return self.required_profession == PROFESSION_JEWELCRAFTING and self.unique

    def stat(self, idx: int) -> float:
        return self.stats[idx] if idx < len(self.stats) else 0.0

    def counts_as(self):
        return COLOR_COUNTS_AS.get(self.color, (0, 0, 0))

    def matches_socket(self, socket_color: int) -> bool:
        return socket_color in COLOR_MATCHES.get(self.color, set())

    def __str__(self):
        parts = [f"{int(v)} {STAT_NAMES.get(i, f'stat{i}')}"
                 for i, v in enumerate(self.stats) if v]
        return f"{self.name} ({'+'.join(parts) or 'no stats'})"


@dataclass
class Item:
    id: int
    name: str
    type: int
    gem_sockets: list = field(default_factory=list)
    socket_bonus: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    phase: int = 1

    @property
    def colored_socket_indices(self):
        """Socket indices that are not the meta socket."""
        return [i for i, c in enumerate(self.gem_sockets) if c != COLOR_META]

    @property
    def meta_socket_index(self):
        for i, c in enumerate(self.gem_sockets):
            if c == COLOR_META:
                return i
        return None


class Database:
    def __init__(self, sim_repo: str):
        self.sim_repo = sim_repo
        path = os.path.join(sim_repo, "assets", "database", "db.json")
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"wowsims database not found at {path}. Pass --sim-repo pointing at a "
                "wowsims/tbc-new checkout (see setup.sh).")
        with open(path) as f:
            raw = json.load(f)

        self.items: dict[int, Item] = {}
        for it in raw.get("items", []):
            stats = {}
            scaling = (it.get("scalingOptions") or {}).get("0") or {}
            for k, v in (scaling.get("stats") or {}).items():
                stats[int(k)] = v
            self.items[it["id"]] = Item(
                id=it["id"], name=it.get("name", f"Item {it['id']}"),
                type=it.get("type", 0),
                gem_sockets=it.get("gemSockets", []) or [],
                socket_bonus=it.get("socketBonus", []) or [],
                stats=stats, phase=it.get("phase", 1),
            )

        self.gems: dict[int, Gem] = {}
        for g in raw.get("gems", []):
            self.gems[g["id"]] = Gem(
                id=g["id"], name=g.get("name", f"Gem {g['id']}"),
                color=g.get("color", 0), stats=g.get("stats", []),
                phase=g.get("phase", 1), quality=g.get("quality", 0),
                unique=g.get("unique", False),
                required_profession=g.get("requiredProfession", 0),
            )

        self.enchants = {}
        for e in raw.get("enchants", []):
            self.enchants[e.get("effectId")] = e

    def gem(self, gem_id: int) -> Gem | None:
        return self.gems.get(gem_id)

    def item(self, item_id: int) -> Item | None:
        return self.items.get(item_id)
