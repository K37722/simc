"""Gear import parsers.

Supported formats (only *currently equipped* gear is used):
  * SeventyUpgrades (seventyupgrades.com) JSON export
  * WoWSims Exporter (WSE) addon JSON export

Both are normalized into an EquippedGear: a 17-entry slot array of ItemSpec
(matching the wowsims EquipmentSpec slot order).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .dbutil import Database, ITEM_TYPE_TO_SLOTS, NUM_ITEM_SLOTS, SLOT_NAMES

# SeventyUpgrades slot names -> ItemSlot index (names are normalized by
# lowercasing and stripping spaces/underscores/hyphens first).
SLOT_NAME_TO_INDEX = {
    "head": 0, "neck": 1, "shoulder": 2, "shoulders": 2, "back": 3, "cloak": 3,
    "chest": 4, "wrist": 5, "wrists": 5, "hands": 6, "waist": 7, "legs": 8,
    "feet": 9, "finger1": 10, "finger2": 11, "ring1": 10, "ring2": 11,
    "trinket1": 12, "trinket2": 13, "mainhand": 14, "offhand": 15,
    "ranged": 16, "finger": 10, "trinket": 12,
}
IGNORED_SLOTS = {"tabard", "shirt", "body"}

RACE_NAMES = {
    "human": "RaceHuman", "dwarf": "RaceDwarf", "gnome": "RaceGnome",
    "nightelf": "RaceNightElf", "night elf": "RaceNightElf",
    "draenei": "RaceDraenei", "orc": "RaceOrc", "troll": "RaceTroll",
    "undead": "RaceUndead", "forsaken": "RaceUndead",
    "bloodelf": "RaceBloodElf", "blood elf": "RaceBloodElf",
    "tauren": "RaceTauren",
}


@dataclass
class ItemSpec:
    id: int
    enchant: int = 0
    gems: list = field(default_factory=list)
    random_suffix: int = 0

    def to_proto_json(self) -> dict:
        d = {"id": self.id}
        if self.random_suffix:
            d["randomSuffix"] = self.random_suffix
        if self.enchant:
            d["enchant"] = self.enchant
        if self.gems:
            d["gems"] = self.gems
        return d


@dataclass
class EquippedGear:
    slots: list  # list[ItemSpec | None], length 17
    race: str = "RaceHuman"
    talents: str = ""
    professions: list = field(default_factory=list)  # profession name strings
    char_name: str = "Rogue"

    def to_equipment_json(self) -> dict:
        return {"items": [s.to_proto_json() if s else {} for s in self.slots]}

    def describe(self, db: Database) -> str:
        lines = []
        for i, spec in enumerate(self.slots):
            if not spec:
                continue
            item = db.item(spec.id)
            name = item.name if item else f"Unknown item {spec.id}"
            gems = ", ".join(
                (db.gem(g).name if db.gem(g) else f"gem {g}") for g in spec.gems if g)
            lines.append(f"  {SLOT_NAMES[i]:>10}: {name}" + (f" [{gems}]" if gems else ""))
        return "\n".join(lines)


class ImportError_(Exception):
    pass


def load_gear(path: str, db: Database) -> EquippedGear:
    """Auto-detect the export format and parse it."""
    with open(path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ImportError_(f"{path} is not valid JSON: {e}")

    if isinstance(data, dict) and "gear" in data and "class" in data:
        return parse_wse(data, db)
    if isinstance(data, dict) and "character" in data and "items" in data:
        return parse_seventyupgrades(data, db)
    raise ImportError_(
        "Unrecognized export format. Expected a SeventyUpgrades JSON export "
        "(has 'character' + 'items') or a WoWSims Exporter addon export "
        "(has 'class' + 'gear').")


def _norm_race(s: str) -> str:
    key = (s or "").strip().lower().replace("_", "").replace("-", " ")
    key2 = key.replace(" ", "")
    return RACE_NAMES.get(key, RACE_NAMES.get(key2, "RaceHuman"))


def _place_in_slots(slots, item_spec: ItemSpec, db: Database, slot_hint=None):
    """Place an item into the first free eligible slot."""
    candidates = None
    if slot_hint is not None:
        candidates = [slot_hint]
    else:
        item = db.item(item_spec.id)
        if item:
            candidates = ITEM_TYPE_TO_SLOTS.get(item.type)
    if not candidates:
        return False
    for idx in candidates:
        if slots[idx] is None:
            slots[idx] = item_spec
            return True
    return False


def _norm_slot(name: str) -> str:
    return (name or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")


def talents_to_string(talents_json: list, db: Database) -> str:
    """Convert a SeventyUpgrades talent list [{spellId, rank, ...}] into a
    wowhead-style talent string, using the wowsims rogue talent tree data."""
    tree_path = os.path.join(db.sim_repo, "ui", "core", "talents", "trees", "rogue.json")
    with open(tree_path) as f:
        trees = json.load(f)

    # spellId (any rank) -> (tree index, order index within tree)
    spell_to_pos = {}
    tree_sizes = []
    for ti, tree in enumerate(trees):
        talents = sorted(tree["talents"],
                         key=lambda t: (t["location"]["rowIdx"], t["location"]["colIdx"]))
        tree_sizes.append(len(talents))
        for oi, talent in enumerate(talents):
            for rank_idx, sid in enumerate(talent.get("spellIds", [])):
                spell_to_pos[sid] = (ti, oi, rank_idx + 1)

    points = [[0] * n for n in tree_sizes]
    for t in talents_json or []:
        sid = t.get("spellId")
        pos = spell_to_pos.get(sid)
        if pos is None:
            print(f"  warning: unknown talent spellId {sid} ({t.get('name')}); skipped")
            continue
        ti, oi, rank_from_spell = pos
        points[ti][oi] = int(t.get("rank") or rank_from_spell)

    tree_strs = ["".join(map(str, p)).rstrip("0") for p in points]
    while tree_strs and not tree_strs[-1]:
        tree_strs.pop()
    return "-".join(tree_strs)


def parse_seventyupgrades(data: dict, db: Database) -> EquippedGear:
    char = data.get("character") or {}
    game_class = (char.get("gameClass") or "").lower()
    if game_class and game_class != "rogue":
        raise ImportError_(f"This analyzer only supports rogues (export is a {game_class}).")

    slots = [None] * NUM_ITEM_SLOTS
    skipped = []
    for item_json in data.get("items", []):
        item_id = item_json.get("id")
        if not item_id:
            continue
        slot_name = _norm_slot(item_json.get("slot"))
        if slot_name in IGNORED_SLOTS:
            continue
        spec = ItemSpec(id=item_id)
        enchant = item_json.get("enchant")
        if isinstance(enchant, dict) and enchant.get("id"):
            spec.enchant = enchant["id"]
        for gem_json in item_json.get("gems") or []:
            if isinstance(gem_json, dict) and gem_json.get("id"):
                spec.gems.append(gem_json["id"])
            elif isinstance(gem_json, int):
                spec.gems.append(gem_json)
        slot_hint = None
        if slot_name in SLOT_NAME_TO_INDEX:
            slot_hint = SLOT_NAME_TO_INDEX[slot_name]
            if slots[slot_hint] is not None:  # e.g. two rings both named "finger"
                slot_hint = None
        if not _place_in_slots(slots, spec, db, slot_hint):
            skipped.append(f"{item_json.get('name') or item_id}")

    if skipped:
        print(f"  warning: skipped items not in the sim database: {skipped}")

    talents = ""
    if data.get("talents"):
        try:
            talents = talents_to_string(data["talents"], db)
        except Exception as e:
            print(f"  warning: could not convert talents ({e}); using defaults")

    return EquippedGear(
        slots=slots,
        race=_norm_race(char.get("race") or ""),
        talents=talents,
        professions=[],
        char_name=char.get("name") or "Rogue",
    )


def parse_wse(data: dict, db: Database) -> EquippedGear:
    if (data.get("class") or "").strip().lower() not in ("rogue", ""):
        raise ImportError_(
            f"This analyzer only supports rogues (export is a {data.get('class')}).")

    gear = data.get("gear") or {}
    items = gear.get("items") or []
    slots = [None] * NUM_ITEM_SLOTS
    skipped = []

    # WSE exports the items array already in EquipmentSpec slot order,
    # including null entries for empty slots.
    positional = len(items) in (NUM_ITEM_SLOTS, NUM_ITEM_SLOTS + 1)
    for i, item_json in enumerate(items):
        if not item_json:
            continue
        item_id = item_json.get("id")
        if not item_id:
            continue
        spec = ItemSpec(
            id=item_id,
            enchant=item_json.get("enchant") or 0,
            gems=[g or 0 for g in (item_json.get("gems") or [])],
            random_suffix=item_json.get("randomSuffix") or item_json.get("random_suffix") or 0,
        )
        while spec.gems and spec.gems[-1] == 0:
            spec.gems.pop()
        placed = False
        if positional and i < NUM_ITEM_SLOTS and slots[i] is None:
            slots[i] = spec
            placed = True
        if not placed and not _place_in_slots(slots, spec, db):
            skipped.append(item_id)

    if skipped:
        print(f"  warning: skipped items not in the sim database: {skipped}")

    profs = []
    for p in data.get("professions") or []:
        if isinstance(p, dict):
            profs.append((p.get("name") or "").strip())
        elif isinstance(p, str):
            profs.append(p.strip())

    return EquippedGear(
        slots=slots,
        race=_norm_race(data.get("race") or ""),
        talents=(data.get("talents") or "").strip(),
        professions=[p for p in profs if p],
        char_name=data.get("name") or "Rogue",
    )
