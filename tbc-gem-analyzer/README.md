# TBC Rogue Gem Analyzer

Finds the **optimal gem setup for your currently equipped gear** by running real
[WoWSims TBC (Anniversary)](https://github.com/wowsims/tbc-new) simulations
against the Serpentshrine Cavern, Tempest Keep, Mount Hyjal and Black Temple
bosses, and ranking every candidate gem combination by simulated DPS.

## What it does

1. **Imports your currently equipped gear** from either:
   - a **SeventyUpgrades** (sixtyupgrades.com/tbc) JSON export, or
   - a **WoWSims Exporter (WSE)** addon JSON export.

   Race and talents are imported too (SeventyUpgrades talent lists are
   converted to a talent string automatically).

2. **Enumerates gem combinations** for every colored socket on your gear, with
   the rules you actually play by:
   - the **meta gem currently in your helm is kept**, and every candidate combo
     must satisfy its **color activation requirement** (e.g. Relentless
     Earthstorm Diamond needs 2 Red / 2 Yellow / 2 Blue);
   - at most **one unique-equipped epic Jewelcrafting gem** (Crimson Sun,
     Lionseye, Shadowsong Amethyst, ...) is used, and only if you are a
     Jewelcrafter;
   - socket bonuses are weighed automatically - the optimizer decides per item
     whether matching the socket colors or ignoring the bonus is better;
   - **hit rating is handled by simulation, not by static weights**: the
     optimizer produces the best possible combo for *every achievable amount of
     gem hit rating* and lets the sims decide where your soft caps really are.

3. **Simulates** the finalists on each boss with realistic per-boss fight
   durations, then ranks combos by **duration-weighted average DPS** across the
   bosses you selected. The current gemming is always simmed too, so the report
   shows exactly how much DPS each change is worth.

## Setup

```bash
# one-time: clone + build the WoWSims TBC simulator (needs go >= 1.24, protoc, git)
./setup.sh
```

The simulator is built from `wowsims/tbc-new` with its item/gem database
embedded, so results match the wowsims.com TBC Anniversary sim exactly.

## Usage

```bash
# everything (all 24 bosses in SSC/TK/Hyjal/BT):
python3 analyze.py --import my_export.json

# only the raids you care about this week:
python3 analyze.py --import my_export.json --bosses SSC,TK

# specific bosses, more precision:
python3 analyze.py --import my_export.json --bosses vashj,kaelthas --iterations 10000
```

Useful options:

| Option | Default | Meaning |
|---|---|---|
| `--sim-repo PATH` | `~/wowsims/tbc-new` | wowsims/tbc-new checkout (or set `TBC_SIM_REPO`) |
| `--bosses LIST` | all | comma-separated raids (`SSC,TK,Hyjal,BT`) and/or boss keys (`vashj`, `illidan`, ...) |
| `--iterations N` | 4000 | iterations per final boss sim |
| `--top K` | 8 | finalist combos simmed on every boss |
| `--phase N` | auto | max gem content phase; auto-detected from the wowsims repo's current TBC Anniversary phase (phase 3+ unlocks Sunwell-tier epic gems like Rigid Lionseye) |
| `--jc on\|off` | on | allow the unique epic Jewelcrafting gem |
| `--rare-gems` | off | also consider rare-quality gems everywhere |
| `--talents STR` | imported | override the talent string |
| `--apl FILE` | wowsims swords APL | override the rotation |
| `--settings FILE` | - | JSON overriding consumables / raid buffs / debuffs |
| `--boss-config FILE` | - | JSON overriding per-boss durations/armor |

Reports are written to `gem_report.md` and `gem_report.csv` (see `--out-dir`).

## Boss modeling

Each boss is simmed as a level 73 raid target with a typical anniversary-era
kill time (e.g. Hydross 150s ± 25s, Lady Vashj 300s ± 40s, Illidan 360s ± 45s)
and the correct mob type. Kill times matter: short fights favor front-loaded
cooldown alignment, long fights favor sustained stats - which is why the tool
ranks by duration-weighted DPS across the bosses you select. Override any
duration with `--boss-config`:

```json
{ "vashj": { "duration": 270, "variation": 30 }, "illidan": { "duration": 400 } }
```

Fight gimmicks (add phases, target swaps, forced downtime) are not scripted;
they affect all gem combos roughly equally, so rankings are unaffected.

## Assumptions / defaults

- Combat swords APL from wowsims (`ui/rogue/dps/apls/swords.apl.json`), full
  raid buffs (Bloodlust, totems, improved Battle Shout/Might/Kings, Windfury,
  drums), standard debuffs (Sunder, Improved Expose Weakness, Blood Frenzy,
  JotC, Faerie Fire, ...), flask/food/haste potions/thistle tea. All of it can
  be overridden with `--settings`.
- Only currently equipped items are considered - the tool changes gems, never
  gear.
- SeventyUpgrades exports do not include professions; the JC gem is allowed by
  default (`--jc off` if you are not a Jewelcrafter). WSE exports include
  professions, which are respected automatically.

## Example

`examples/latmann_seventyupgrades.json` is a real SeventyUpgrades export; try:

```bash
python3 analyze.py --import examples/latmann_seventyupgrades.json --bosses SSC
```
