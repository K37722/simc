#!/usr/bin/env python3
"""TBC Anniversary rogue gem analyzer.

Finds the optimal gem setup for your currently equipped gear by running real
WoWSims (wowsims/tbc-new) simulations against the SSC / TK / Hyjal / BT bosses.

Rules honored:
  * The meta gem currently in your helm is kept, and every candidate combo
    must satisfy its color-activation requirement.
  * At most one unique-equipped epic Jewelcrafting gem (Crimson Sun etc.).
  * Hit-rating tradeoffs are resolved by simulation: the optimizer produces
    the best combo for every achievable amount of gem hit rating and lets the
    sims decide (yellow-hit soft caps fall out naturally).

Usage:
  python3 analyze.py --import my_export.json [--sim-repo ~/wowsims/tbc-new]
                     [--bosses SSC,TK] [--iterations 5000] [--top 8]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tbc_gem_analyzer.bosses import (REFERENCE_BOSS, apply_boss_config,
                                     select_bosses)
from tbc_gem_analyzer.dbutil import (Database, ROGUE_USEFUL_STATS,
                                     STAT_MELEE_HIT, STAT_STAMINA, SLOT_NAMES)
from tbc_gem_analyzer.gems import (GemCombo, ItemOption, meta_condition_str,
                                   optimize_combos, select_candidate_gems)
from tbc_gem_analyzer.importers import load_gear
from tbc_gem_analyzer.report import print_report, rank_results
from tbc_gem_analyzer.runner import SimRunner, equipment_with_combo

DEFAULT_SIM_REPO = os.environ.get(
    "TBC_SIM_REPO", os.path.expanduser("~/wowsims/tbc-new"))

EP_DELTAS = {  # stat index -> delta size used for weight measurement
    1: 40,    # Agility
    0: 40,    # Strength
    2: 60,    # Stamina (expected ~0)
    17: 80,   # Attack Power
    20: 40,   # Hit Rating
    21: 40,   # Crit Rating
    22: 40,   # Haste Rating
    23: 120,  # Armor Pen
    24: 20,   # Expertise
}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--import", dest="import_file", required=True,
                   help="SeventyUpgrades or WSE addon JSON export")
    p.add_argument("--sim-repo", default=DEFAULT_SIM_REPO,
                   help="path to wowsims/tbc-new checkout (default: %(default)s)")
    p.add_argument("--bosses", default=None,
                   help="comma-separated raids/bosses (SSC,TK,Hyjal,BT or boss keys); "
                        "default: all four raids")
    p.add_argument("--boss-config", default=None,
                   help="JSON file overriding per-boss durations/armor")
    p.add_argument("--phase", type=int, default=None,
                   help="max content phase for candidate gems (default: the current "
                        "TBC Anniversary phase, auto-detected from the wowsims repo)")
    p.add_argument("--iterations", type=int, default=4000,
                   help="iterations per final boss sim (default %(default)s)")
    p.add_argument("--screen-iterations", type=int, default=2500,
                   help="iterations for EP/screening sims (default %(default)s)")
    p.add_argument("--top", type=int, default=8,
                   help="number of finalist combos simmed on every boss (default %(default)s)")
    p.add_argument("--jc", choices=["on", "off"], default="on",
                   help="allow the unique epic Jewelcrafting gem (default on)")
    p.add_argument("--no-honor-gems", action="store_true",
                   help="exclude the unique-equipped honor vendor gems (Bold "
                        "Ornate Ruby, Inscribed Ornate Topaz, ...)")
    p.add_argument("--dungeon-gems", action="store_true",
                   help="also consider unique-equipped heroic dungeon drop gems "
                        "(Glinting/Pristine Fire Opal, Shifting Tanzanite, ...)")
    p.add_argument("--talents", default=None,
                   help="override talent string (your Murder/main build)")
    p.add_argument("--talents-alt", default=None,
                   help="second (dual-spec) talent string without Murder, used "
                        "automatically vs Demons/Elementals/Undead/Mechanical "
                        "bosses where Murder does nothing; if omitted it is "
                        "auto-derived by moving the Murder points into flex "
                        "talents (Imp Eviscerate > Vile Poisons > Imp Poisons)")
    p.add_argument("--no-talents-alt", action="store_true",
                   help="use the same (Murder) build on every boss")
    p.add_argument("--armor-debuff", choices=["sunder", "iea"], default="iea",
                   help="armor debuff on the boss: 'iea' = you are the Improved "
                        "Expose Armor rogue (default), 'sunder' = warrior sunders")
    p.add_argument("--no-demonslaying", action="store_true",
                   help="keep the flask on demon bosses instead of switching to "
                        "Elixir of Demonslaying + Elixir of Major Fortitude")
    p.add_argument("--apl", default=None, help="override rotation APL json file")
    p.add_argument("--settings", default=None,
                   help="JSON overriding consumables/raidBuffs/partyBuffs/"
                        "individualBuffs/debuffs")
    p.add_argument("--rare-gems", action="store_true",
                   help="also consider rare-quality gems (default: epic gems only "
                        "plus the JC gems)")
    p.add_argument("--workers", type=int, default=3,
                   help="concurrent sim processes (default %(default)s)")
    p.add_argument("--out-dir", default=".", help="report output directory")
    return p.parse_args()


def current_combo_from_gear(gear, db) -> GemCombo:
    """Represent the currently-equipped gems as a combo (for comparison)."""
    options = []
    for slot, spec in enumerate(gear.slots):
        if not spec:
            continue
        item = db.item(spec.id)
        if not item or not item.colored_socket_indices:
            continue
        gems = list(spec.gems) + [0] * len(item.gem_sockets)
        chosen, hit, counts = [], 0, [0, 0, 0]
        matched = True
        for sock_idx in item.colored_socket_indices:
            gid = gems[sock_idx] if sock_idx < len(spec.gems) else 0
            chosen.append(gid)
            g = db.gem(gid) if gid else None
            if g:
                hit += int(g.stat(STAT_MELEE_HIT))
                for i, c in enumerate(g.counts_as()):
                    counts[i] += c
                if not g.matches_socket(item.gem_sockets[sock_idx]):
                    matched = False
            else:
                matched = False
        meta_idx = item.meta_socket_index
        if meta_idx is not None:
            meta_ok = meta_idx < len(spec.gems) and spec.gems[meta_idx] != 0
            matched = matched and meta_ok
        options.append(ItemOption(
            item_slot=slot, gems=tuple(chosen), ep=0.0, hit=hit,
            counts=tuple(counts), jc=0, bonus_active=matched))
    return GemCombo(label="current gems", options=options)


def detect_current_phase(sim_repo: str) -> int:
    """Read CURRENT_PHASE from the wowsims UI constants (fallback: phase 2)."""
    import re
    path = os.path.join(sim_repo, "ui", "core", "constants", "other.ts")
    try:
        with open(path) as f:
            m = re.search(r"CURRENT_PHASE\s*:\s*Phase\s*=\s*Phase\.Phase(\d+)", f.read())
        if m:
            return int(m.group(1))
    except OSError:
        pass
    return 2


def main():
    args = parse_args()
    db = Database(args.sim_repo)
    if args.phase is None:
        args.phase = detect_current_phase(args.sim_repo)
        print(f"Gem pool: phase <= {args.phase} (current TBC Anniversary phase; "
              f"override with --phase)")
    if args.boss_config:
        apply_boss_config(args.boss_config)
    bosses = select_bosses(args.bosses)

    print(f"Loading gear from {args.import_file} ...")
    gear = load_gear(args.import_file, db)
    print(f"Character: {gear.char_name} ({gear.race.replace('Race', '')})")
    print(gear.describe(db))

    # --- meta gem ---
    meta_gem = None
    for spec in filter(None, gear.slots):
        item = db.item(spec.id)
        if not item:
            continue
        meta_idx = item.meta_socket_index
        if meta_idx is not None and meta_idx < len(spec.gems) and spec.gems[meta_idx]:
            meta_gem = db.gem(spec.gems[meta_idx])
    if meta_gem:
        print(f"\nMeta gem (kept as-is): {meta_gem.name} "
              f"[requires {meta_condition_str(meta_gem.id)}]")
    else:
        print("\nwarning: no meta gem equipped - combos are unconstrained by meta colors")

    # --- JC gems ---
    allow_jc = args.jc == "on"
    if allow_jc and gear.professions and \
            not any(p.lower() == "jewelcrafting" for p in gear.professions):
        print("note: export lists professions without Jewelcrafting; disabling JC gems "
              "(force with --jc on and no professions in export)")
        allow_jc = False

    candidates, special_pool = select_candidate_gems(
        db, max_phase=args.phase, allow_jc=allow_jc,
        min_quality=3 if args.rare_gems else 4,
        honor_gems=not args.no_honor_gems, dungeon_gems=args.dungeon_gems)
    if not args.rare_gems:
        # epic-quality cut can remove entire colors pre-phase-3; backfill rares
        have_colors = {g.color for g in candidates}
        rare, _ = select_candidate_gems(db, args.phase, False, min_quality=3,
                                        honor_gems=False)
        candidates += [g for g in rare if g.color not in have_colors]
    print(f"\nCandidate gems ({len(candidates)} regular, "
          f"{len(special_pool)} unique-equipped):")
    for g in sorted(candidates + special_pool, key=lambda g: g.color):
        from tbc_gem_analyzer.dbutil import COLOR_NAMES, HONOR_UNIQUE_GEM_IDS
        tag = ("  (JC unique)" if g.is_jc_gem else
               "  (honor vendor, unique)" if g.id in HONOR_UNIQUE_GEM_IDS else
               "  (unique)" if g.unique else "")
        print(f"  [{COLOR_NAMES.get(g.color, '?'):>6}] {g}{tag}")

    socketed = []
    for slot, spec in enumerate(gear.slots):
        if not spec:
            continue
        item = db.item(spec.id)
        if item and item.colored_socket_indices:
            socketed.append((slot, item))
    n_sockets = sum(len(i.colored_socket_indices) for _, i in socketed)
    print(f"\n{len(socketed)} items with {n_sockets} colored sockets to optimize")

    talents_alt = args.talents_alt
    if talents_alt is None and not args.no_talents_alt:
        from tbc_gem_analyzer.talents import derive_no_murder_build
        talents_alt = derive_no_murder_build(
            args.talents or gear.talents or "", args.sim_repo)
    runner = SimRunner(args.sim_repo, gear, apl_path=args.apl,
                       talents=args.talents, settings_path=args.settings,
                       talents_alt=None if args.no_talents_alt else talents_alt,
                       demonslaying=not args.no_demonslaying,
                       armor_debuff=args.armor_debuff)
    print(f"Talents: {runner.talents}")
    if runner.talents_alt:
        print(f"Alt build (non-Murder bosses): {runner.talents_alt}")
    print(f"Armor debuff: {'Improved Expose Armor' if args.armor_debuff == 'iea' else 'Sunder Armor'}"
          f" | Demonslaying elixir on demons: {not args.no_demonslaying}"
          f" | Windfury MH, Deadly Poison OH (Adamantite Sharpening Stone on "
          f"poison-immune bosses)")

    # --- Stage 1: EP weights from delta sims on the reference target ---
    print(f"\nStage 1: measuring stat weights "
          f"({len(EP_DELTAS) + 1} sims x {args.screen_iterations} iterations) ...")
    base_eq = gear.to_equipment_json()
    reqs = [runner.build_request(base_eq, REFERENCE_BOSS, args.screen_iterations)]
    stat_order = list(EP_DELTAS)
    for stat in stat_order:
        bonus = [0.0] * 42
        bonus[stat] = float(EP_DELTAS[stat])
        reqs.append(runner.build_request(base_eq, REFERENCE_BOSS,
                                         args.screen_iterations, bonus_stats=bonus))
    results = runner.run_many(reqs, workers=args.workers,
                              progress=lambda d, t: print(f"  {d}/{t}", end="\r"))
    base_dps = results[0]["dps"]
    ep = {}
    for stat, res in zip(stat_order, results[1:]):
        ep[stat] = max(0.0, (res["dps"] - base_dps) / EP_DELTAS[stat])
    from tbc_gem_analyzer.dbutil import STAT_NAMES
    print("  weights (dps per point): " +
          ", ".join(f"{STAT_NAMES.get(s, s)}={ep[s]:.3f}" for s in stat_order))

    # --- Stage 2: optimize combos per gem-hit-rating total ---
    print("\nStage 2: enumerating optimal combos per hit-rating bracket ...")
    combos = optimize_combos(socketed, ep, meta_gem.id if meta_gem else None,
                             candidates, special_pool,
                             meta_socket_gem_matches=meta_gem is not None)
    current = current_combo_from_gear(gear, db)

    # Deduplicate combos that are the same set of gems in different sockets
    # (identical stats -> identical DPS); prefer the current gemming when tied.
    def signature(c):
        return (tuple(sorted(g for o in c.options for g in o.gems)),
                tuple(sorted(o.item_slot for o in c.options if o.bonus_active)))

    cur_sig = signature(current)
    deduped, seen = [], set()
    for c in combos:
        sig = signature(c)
        if sig == cur_sig or sig in seen:
            continue
        seen.add(sig)
        deduped.append(c)
    combos = deduped
    combos.append(current)
    current_index = len(combos) - 1
    print(f"  {len(combos) - 1} distinct hit brackets + current gems")
    for c in combos[:-1]:
        jc_used = sum(o.jc for o in c.options)
        print(f"    {c.label:<16} EP {c.ep:7.1f}  colors r/y/b={c.counts}  "
              f"JC={'yes' if jc_used else 'no'}")

    # --- Stage 3: screening sims to pick finalists ---
    print(f"\nStage 3: screening {len(combos)} combos on the reference target ...")
    reqs = [runner.build_request(equipment_with_combo(gear, db, c),
                                 REFERENCE_BOSS, args.screen_iterations)
            for c in combos]
    results = runner.run_many(reqs, workers=args.workers,
                              progress=lambda d, t: print(f"  {d}/{t}", end="\r"))
    scored = sorted(range(len(combos)), key=lambda i: results[i]["dps"], reverse=True)
    for i in scored:
        print(f"  {combos[i].label:<16} {results[i]['dps']:8.1f} dps")
    finalists = scored[:args.top]
    if current_index not in finalists:
        finalists.append(current_index)

    # --- Stage 4: full sims on every selected boss ---
    total = len(finalists) * len(bosses)
    print(f"\nStage 4: full sims - {len(finalists)} combos x {len(bosses)} bosses "
          f"({total} sims x {args.iterations} iterations) ...")
    reqs, keys = [], []
    for ci in finalists:
        eq = equipment_with_combo(gear, db, combos[ci])
        for b in bosses:
            reqs.append(runner.build_request(eq, b, args.iterations))
            keys.append((ci, b.key))
    results = runner.run_many(reqs, workers=args.workers,
                              progress=lambda d, t: print(f"  {d}/{t}", end="\r"))
    boss_results = {}
    for (ci, bkey), res in zip(keys, results):
        boss_results.setdefault(ci, {})[bkey] = res

    rows = rank_results(combos, boss_results, bosses)
    print_report(rows, combos, db, gear, bosses, meta_gem, current_index,
                 args.out_dir)


if __name__ == "__main__":
    main()
