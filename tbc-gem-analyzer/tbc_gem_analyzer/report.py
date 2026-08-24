"""Result ranking and report output (console + markdown + CSV)."""
from __future__ import annotations

import csv
import os

from .dbutil import Database, SLOT_NAMES
from .gems import GemCombo, meta_condition_str


def rank_results(combos, boss_results, bosses):
    """boss_results: {combo_index: {boss_key: {'dps':..,'stdev':..}}}
    Returns list of dicts sorted by duration-weighted average DPS."""
    rows = []
    total_time = sum(b.duration for b in bosses) or 1
    for ci, per_boss in boss_results.items():
        avg = sum(r["dps"] for r in per_boss.values()) / max(len(per_boss), 1)
        weighted = sum(per_boss[b.key]["dps"] * b.duration for b in bosses
                       if b.key in per_boss) / total_time
        per_raid = {}
        for b in bosses:
            if b.key in per_boss:
                per_raid.setdefault(b.raid, []).append(per_boss[b.key]["dps"])
        rows.append({
            "combo": combos[ci],
            "combo_index": ci,
            "avg_dps": avg,
            "weighted_dps": weighted,
            "per_boss": per_boss,
            "per_raid": {r: sum(v) / len(v) for r, v in per_raid.items()},
        })
    rows.sort(key=lambda r: r["weighted_dps"], reverse=True)
    return rows


def describe_combo(combo: GemCombo, db: Database, gear) -> list:
    lines = []
    for opt in combo.options:
        item = db.item(gear.slots[opt.item_slot].id)
        gems = ", ".join(db.gem(g).name if db.gem(g) else str(g) for g in opt.gems)
        bonus = "bonus ✓" if opt.bonus_active else "bonus ✗"
        lines.append(f"{SLOT_NAMES[opt.item_slot]:>10} ({item.name}): {gems}  [{bonus}]")
    return lines


def print_report(rows, combos, db, gear, bosses, meta_gem, current_index, out_dir):
    baseline = next((r for r in rows if r["combo_index"] == current_index), None)
    base_dps = baseline["weighted_dps"] if baseline else None

    print("\n" + "=" * 78)
    print("GEM COMBO RANKING (duration-weighted average DPS across selected bosses)")
    if meta_gem:
        print(f"Meta gem (kept): {meta_gem.name} — requires {meta_condition_str(meta_gem.id)}")
    print("=" * 78)
    for i, row in enumerate(rows, 1):
        c = row["combo"]
        tag = "  <== CURRENT GEMS" if row["combo_index"] == current_index else ""
        delta = ""
        if base_dps is not None and row["combo_index"] != current_index:
            d = row["weighted_dps"] - base_dps
            delta = f"  ({'+' if d >= 0 else ''}{d:.1f} vs current)"
        print(f"\n#{i}  {c.label:<18} weighted {row['weighted_dps']:8.1f} dps"
              f"   mean {row['avg_dps']:8.1f} dps{delta}{tag}")
        raid_str = "   ".join(f"{r}: {v:7.1f}" for r, v in sorted(row["per_raid"].items()))
        print(f"    per raid: {raid_str}")
        if i <= 3 or row["combo_index"] == current_index:
            for line in describe_combo(c, db, gear):
                print("      " + line)

    best = rows[0]
    print("\n" + "-" * 78)
    print("BEST SETUP:", best["combo"].label)
    for line in describe_combo(best["combo"], db, gear):
        print("  " + line)
    jc = [db.gem(g) for o in best["combo"].options for g in o.gems
          if db.gem(g) and db.gem(g).is_jc_gem]
    if jc:
        print(f"  Jewelcrafting gem used: {jc[0].name}")
    print("-" * 78)

    write_markdown(rows, combos, db, gear, bosses, meta_gem, current_index,
                   os.path.join(out_dir, "gem_report.md"))
    write_csv(rows, bosses, os.path.join(out_dir, "gem_report.csv"))
    print(f"\nReports written: {out_dir}/gem_report.md, {out_dir}/gem_report.csv")


def write_markdown(rows, combos, db, gear, bosses, meta_gem, current_index, path):
    with open(path, "w") as f:
        f.write("# TBC Rogue Gem Analyzer Report\n\n")
        if meta_gem:
            f.write(f"Meta gem (kept): **{meta_gem.name}** — requires "
                    f"{meta_condition_str(meta_gem.id)}\n\n")
        f.write("## Ranking (duration-weighted average DPS)\n\n")
        f.write("| # | Combo | Weighted DPS | Mean DPS | " +
                " | ".join(sorted({b.raid for b in bosses})) + " |\n")
        f.write("|---|-------|-------------:|---------:|" +
                "---:|" * len({b.raid for b in bosses}) + "\n")
        for i, row in enumerate(rows, 1):
            tag = " **(current)**" if row["combo_index"] == current_index else ""
            raids = sorted({b.raid for b in bosses})
            raid_cells = " | ".join(f"{row['per_raid'].get(r, 0):.1f}" for r in raids)
            f.write(f"| {i} | {row['combo'].label}{tag} | "
                    f"{row['weighted_dps']:.1f} | {row['avg_dps']:.1f} | {raid_cells} |\n")

        f.write("\n## Per-boss DPS\n\n")
        f.write("| Boss | Raid | Fight len | " +
                " | ".join(r["combo"].label for r in rows) + " |\n")
        f.write("|------|------|-----------|" + "---:|" * len(rows) + "\n")
        for b in bosses:
            cells = " | ".join(
                f"{r['per_boss'][b.key]['dps']:.1f}" if b.key in r["per_boss"] else "-"
                for r in rows)
            f.write(f"| {b.name} | {b.raid} | {b.duration}s | {cells} |\n")

        f.write("\n## Gem details (top 3 + current)\n\n")
        shown = [r for i, r in enumerate(rows) if i < 3 or r["combo_index"] == current_index]
        for r in shown:
            tag = " (current gems)" if r["combo_index"] == current_index else ""
            f.write(f"### {r['combo'].label}{tag} — {r['weighted_dps']:.1f} weighted DPS\n\n")
            for line in describe_combo(r["combo"], db, gear):
                f.write(f"- {line}\n")
            f.write("\n")


def write_csv(rows, bosses, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "combo", "weighted_dps", "mean_dps"] +
                   [b.key for b in bosses])
        for i, row in enumerate(rows, 1):
            w.writerow([i, row["combo"].label,
                        f"{row['weighted_dps']:.1f}", f"{row['avg_dps']:.1f}"] +
                       [f"{row['per_boss'][b.key]['dps']:.1f}"
                        if b.key in row["per_boss"] else "" for b in bosses])
