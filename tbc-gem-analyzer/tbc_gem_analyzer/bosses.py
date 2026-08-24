"""Boss encounter profiles for SSC / TK / Hyjal / BT.

Bosses are modeled as level-73 raid targets with per-boss fight durations
(typical anniversary-era kill times), mob types and execute-phase splits.
Durations can be overridden with a JSON file (--boss-config): a mapping of
boss key -> {"duration": sec, "variation": sec}.

Boss armor uses the standard 7685 raid-boss value from the wowsims default
raid target; sunder/faerie fire/expose armor handling happens inside the sim.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

MOB_HUMANOID = "MobTypeHumanoid"
MOB_DEMON = "MobTypeDemon"
MOB_UNDEAD = "MobTypeUndead"
MOB_GIANT = "MobTypeGiant"
MOB_ELEMENTAL = "MobTypeElemental"
MOB_MECHANICAL = "MobTypeMechanical"
MOB_BEAST = "MobTypeBeast"

BOSS_ARMOR = 7685.0
BOSS_HEALTH = 6_070_400.0


@dataclass
class Boss:
    key: str
    name: str
    raid: str
    duration: int          # typical kill time in seconds
    variation: int         # +/- seconds
    mob_type: str = MOB_HUMANOID
    armor: float = BOSS_ARMOR
    notes: str = ""


BOSSES = [
    # --- Serpentshrine Cavern ---
    Boss("hydross", "Hydross the Unstable", "SSC", 150, 25, MOB_ELEMENTAL),
    Boss("lurker", "The Lurker Below", "SSC", 210, 30, MOB_BEAST,
         notes="includes add phase downtime in the duration"),
    Boss("leotheras", "Leotheras the Blind", "SSC", 200, 30, MOB_DEMON,
         notes="whirlwind/demon phases modeled as longer fight"),
    Boss("karathress", "Fathom-Lord Karathress", "SSC", 160, 25, MOB_HUMANOID),
    Boss("morogrim", "Morogrim Tidewalker", "SSC", 200, 30, MOB_GIANT),
    Boss("vashj", "Lady Vashj", "SSC", 300, 40, MOB_HUMANOID,
         notes="phase 2 target-switching not modeled"),
    # --- Tempest Keep ---
    Boss("alar", "Al'ar", "TK", 240, 35, MOB_ELEMENTAL),
    Boss("voidreaver", "Void Reaver", "TK", 180, 25, MOB_MECHANICAL),
    Boss("solarian", "High Astromancer Solarian", "TK", 160, 25, MOB_HUMANOID),
    Boss("kaelthas", "Kael'thas Sunstrider", "TK", 360, 45, MOB_HUMANOID,
         notes="long fight; advisor/weapon phases not modeled"),
    # --- Mount Hyjal ---
    Boss("winterchill", "Rage Winterchill", "Hyjal", 130, 20, MOB_UNDEAD),
    Boss("anetheron", "Anetheron", "Hyjal", 150, 25, MOB_DEMON),
    Boss("kazrogal", "Kaz'rogal", "Hyjal", 130, 20, MOB_DEMON),
    Boss("azgalor", "Azgalor", "Hyjal", 160, 25, MOB_DEMON),
    Boss("archimonde", "Archimonde", "Hyjal", 240, 35, MOB_DEMON),
    # --- Black Temple ---
    Boss("najentus", "High Warlord Naj'entus", "BT", 150, 25, MOB_HUMANOID),
    Boss("supremus", "Supremus", "BT", 170, 25, MOB_DEMON,
         notes="phase-2 kiting not modeled"),
    Boss("akama", "Shade of Akama", "BT", 90, 15, MOB_HUMANOID),
    Boss("teron", "Teron Gorefiend", "BT", 160, 25, MOB_UNDEAD),
    Boss("bloodboil", "Gurtogg Bloodboil", "BT", 240, 35, MOB_HUMANOID),
    Boss("ros", "Reliquary of Souls", "BT", 220, 30, MOB_UNDEAD,
         notes="phase transitions not modeled"),
    Boss("shahraz", "Mother Shahraz", "BT", 180, 25, MOB_DEMON),
    Boss("council", "Illidari Council", "BT", 300, 40, MOB_HUMANOID),
    Boss("illidan", "Illidan Stormrage", "BT", 360, 45, MOB_DEMON,
         notes="demon/flame phases not modeled"),
]

BOSSES_BY_KEY = {b.key: b for b in BOSSES}
RAIDS = ("SSC", "TK", "Hyjal", "BT")


def select_bosses(spec: str | None) -> list[Boss]:
    """spec: comma-separated raid names and/or boss keys; None = all."""
    if not spec:
        return list(BOSSES)
    out, seen = [], set()
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        tl = tok.lower()
        matched = [b for b in BOSSES if b.raid.lower() == tl] or \
                  ([BOSSES_BY_KEY[tl]] if tl in BOSSES_BY_KEY else [])
        if not matched:
            raise ValueError(
                f"Unknown raid/boss '{tok}'. Raids: {', '.join(RAIDS)}. "
                f"Bosses: {', '.join(sorted(BOSSES_BY_KEY))}")
        for b in matched:
            if b.key not in seen:
                seen.add(b.key)
                out.append(b)
    return out


def apply_boss_config(path: str):
    with open(path) as f:
        cfg = json.load(f)
    for key, over in cfg.items():
        b = BOSSES_BY_KEY.get(key)
        if not b:
            raise ValueError(f"Unknown boss key in {path}: {key}")
        if "duration" in over:
            b.duration = int(over["duration"])
        if "variation" in over:
            b.variation = int(over["variation"])
        if "armor" in over:
            b.armor = float(over["armor"])


def encounter_json(boss: Boss) -> dict:
    stats = [0.0] * 42
    stats[17] = 320.0          # attack power
    stats[31] = boss.armor     # armor
    stats[33] = BOSS_HEALTH    # health
    return {
        "duration": boss.duration,
        "durationVariation": boss.variation,
        "executeProportion20": 0.2,
        "executeProportion25": 0.25,
        "executeProportion35": 0.35,
        "targets": [{
            "id": 31146,
            "name": boss.name,
            "level": 73,
            "mobType": boss.mob_type,
            "stats": stats,
            "minBaseDamage": 6000,
            "damageSpread": 0.4,
            "swingSpeed": 2,
            "parryHaste": True,
            "tankIndex": -1,
        }],
    }


REFERENCE_BOSS = Boss("reference", "Reference Target", "-", 190, 30, MOB_DEMON)
