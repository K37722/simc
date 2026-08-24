#!/usr/bin/env python3
"""Prints (and writes) the per-boss consumables / poison / build cheatsheet.

Everything is derived from the same data the simulator uses (bosses.py +
runner.py), so this always matches what the analyzer actually sims.

Usage: python3 cheatsheet.py [--boss-config FILE] [--out BOSS_CHEATSHEET.md]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tbc_gem_analyzer.bosses import BOSSES, apply_boss_config
from tbc_gem_analyzer.runner import MURDER_MOB_TYPES

CONSTANT_LINE = ("Every boss: Windfury main hand (no MH imbue), Spicy Hot Talbuk, "
                 "Haste Potion, Thistle Tea, Super Sapper Charge, Glyph/armor "
                 "consumables as usual.")


def rows(bosses):
    for b in bosses:
        murder = b.mob_type in MURDER_MOB_TYPES
        demon = b.mob_type == "MobTypeDemon"
        yield {
            "boss": b.name,
            "raid": b.raid,
            "type": b.mob_type.replace("MobType", ""),
            "build": "A (Murder)" if murder else "B (no Murder)",
            "elixirs": ("Elixir of Demonslaying + Elixir of Major Fortitude"
                        if demon else "Flask of Relentless Assault"),
            "oh": ("Adamantite Sharpening Stone" if b.poison_immune
                   else "Deadly Poison"),
        }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--boss-config", default=None)
    p.add_argument("--out", default="BOSS_CHEATSHEET.md")
    args = p.parse_args()
    if args.boss_config:
        apply_boss_config(args.boss_config)

    header = f"| Boss | Raid | Type | Build | Battle elixir / flask | Off-hand |"
    sep = "|---|---|---|---|---|---|"
    lines = ["# Per-boss consumables, poison and build (TBC rogue)", "",
             CONSTANT_LINE, "",
             "Build A = your Murder spec (Murder works vs Humanoid / Giant / "
             "Beast / Dragonkin). Build B = your dual-spec without Murder "
             "(Demons, Elementals, Undead, Mechanical).", "",
             header, sep]
    for r in rows(BOSSES):
        lines.append(f"| {r['boss']} | {r['raid']} | {r['type']} | {r['build']} "
                     f"| {r['elixirs']} | {r['oh']} |")
    lines += ["",
              "Poison immunities are configurable per boss (see --boss-config, "
              "key `poison_immune`); defaults flag Mechanical bosses and Rage "
              "Winterchill. Adjust to your realm's behavior if a boss resists "
              "poison application."]
    text = "\n".join(lines) + "\n"
    print(text)
    with open(args.out, "w") as f:
        f.write(text)
    print(f"written: {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
