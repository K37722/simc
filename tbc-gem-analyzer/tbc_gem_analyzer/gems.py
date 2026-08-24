"""Gem candidate selection, meta-gem rules and gem-combo optimization.

The optimizer works in three stages (driven from analyze.py):
  1. EP weights for the relevant stats are measured with small delta-sims.
  2. A dynamic program finds, for every achievable amount of "gem hit rating",
     the max-EP valid gemming (meta condition satisfied, <=1 unique JC gem).
     This produces a small set of finalist combos that cover the whole
     hit-rating curve, so hit caps/soft caps are resolved by real simulation
     rather than by static weights.
  3. The finalists are simmed for real on every selected boss.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .dbutil import (
    COLOR_BLUE, COLOR_META, COLOR_NAMES, COLOR_RED, COLOR_YELLOW,
    Database, Gem, Item, ROGUE_JUNK_STATS, ROGUE_USEFUL_STATS,
    STAT_MELEE_HIT, STAT_STAMINA, NUM_STATS, SLOT_NAMES,
)

# --- Meta gem activation conditions (ui/core/proto_utils/gems.ts) ---
# gem id -> (min_red, min_yellow, min_blue) or ('cmp', greater_color, lesser_color)
META_CONDITIONS = {
    25899: (2, 2, 2),   # Brutal Earthstorm Diamond
    34220: (0, 0, 2),   # Chaotic Skyfire Diamond
    25890: (2, 2, 2),   # Destructive Skyfire Diamond
    35503: (3, 0, 0),   # Ember Skyfire Diamond
    35501: (0, 1, 2),   # Eternal Earthstorm Diamond
    32641: (0, 3, 0),   # Imbued Unstable Diamond
    25901: (2, 2, 2),   # Insightful Earthstorm Diamond
    25896: (0, 0, 3),   # Powerful Earthstorm Diamond
    32409: (2, 2, 2),   # Relentless Earthstorm Diamond
    25894: (1, 2, 0),   # Swift Skyfire Diamond
    28557: (1, 2, 0),   # Swift Starfire Diamond
    28556: (1, 2, 0),   # Swift Windfire Diamond
    25898: (0, 0, 5),   # Tenacious Earthstorm Diamond
    32410: (2, 2, 2),   # Thundering Skyfire Diamond
    25897: ("cmp", COLOR_RED, COLOR_BLUE),      # Bracing Earthstorm Diamond
    25895: ("cmp", COLOR_RED, COLOR_YELLOW),    # Enigmatic Skyfire Diamond
    25893: ("cmp", COLOR_BLUE, COLOR_YELLOW),   # Mystical Skyfire Diamond
    32640: ("cmp", COLOR_BLUE, COLOR_YELLOW),   # Potent Unstable Diamond
}


def meta_condition_met(meta_gem_id: int, r: int, y: int, b: int) -> bool:
    cond = META_CONDITIONS.get(meta_gem_id)
    if cond is None:
        return True
    if cond[0] == "cmp":
        counts = {COLOR_RED: r, COLOR_YELLOW: y, COLOR_BLUE: b}
        return counts[cond[1]] > counts[cond[2]]
    return r >= cond[0] and y >= cond[1] and b >= cond[2]


def meta_condition_str(meta_gem_id: int) -> str:
    cond = META_CONDITIONS.get(meta_gem_id)
    if cond is None:
        return "always active"
    if cond[0] == "cmp":
        return f"more {COLOR_NAMES[cond[1]]} than {COLOR_NAMES[cond[2]]} gems"
    parts = []
    for n, color in zip(cond, ("Red", "Yellow", "Blue")):
        if n:
            parts.append(f">={n} {color}")
    return ", ".join(parts)


def useful_value(gem_stats, ep: dict) -> float:
    return sum(ep.get(i, 0.0) * v for i, v in enumerate(gem_stats) if v)


def select_candidate_gems(db: Database, max_phase: int, allow_jc: bool,
                          min_quality: int = 3, include_stamina: bool = True):
    """Pick the gem candidate pool for a DPS rogue.

    Returns (candidates, jc_candidates). Dominated gems (same color, all
    useful stats <= another gem's) are dropped.
    """
    useful = set(ROGUE_USEFUL_STATS) | ({STAT_STAMINA} if include_stamina else set())
    pool, jc_pool = [], []
    for g in db.gems.values():
        if g.is_meta or g.phase > max_phase:
            continue
        if any(g.stat(i) > 0 for i in ROGUE_JUNK_STATS):
            continue
        if not any(g.stat(i) > 0 for i in useful):
            continue
        if g.required_profession and not g.is_jc_gem:
            continue
        if g.is_jc_gem:
            if allow_jc:
                jc_pool.append(g)
            continue
        if g.unique:
            continue  # keep the combinatorics simple: skip other unique gems
        if g.quality < min_quality:
            continue
        pool.append(g)

    def dominated(a: Gem, pool_):
        for b in pool_:
            if b.id == a.id or b.color != a.color:
                continue
            a_vals = [a.stat(i) for i in useful]
            b_vals = [b.stat(i) for i in useful]
            if all(bv >= av for av, bv in zip(a_vals, b_vals)) and sum(b_vals) > sum(a_vals):
                return True
        return False

    pool = [g for g in pool if not dominated(g, pool)]
    jc_pool = [g for g in jc_pool if not dominated(g, jc_pool)]
    return pool, jc_pool


@dataclass
class ItemOption:
    """One way to gem a single item's colored sockets."""
    item_slot: int
    gems: tuple            # gem ids, aligned with the item's colored sockets
    ep: float              # EP of gems + socket bonus (if active)
    hit: int               # hit rating contributed (gems + active socket bonus)
    counts: tuple          # (red, yellow, blue) counting toward meta condition
    jc: int                # number of unique JC gems used (0/1)
    bonus_active: bool


@dataclass
class GemCombo:
    label: str
    options: list          # list[ItemOption]
    ep: float = 0.0
    hit: int = 0
    counts: tuple = (0, 0, 0)
    jc_gem: Gem | None = None

    def gems_for_slot(self, slot: int):
        for o in self.options:
            if o.item_slot == slot:
                return o
        return None


def _cap_counts(counts, caps):
    return tuple(min(c, m) for c, m in zip(counts, caps))


def build_item_options(item: Item, slot: int, candidates, jc_candidates,
                       ep: dict, meta_socket_ok: bool):
    """Enumerate Pareto-optimal gemmings for one item's colored sockets.

    meta_socket_ok: whether a meta gem is socketed in this item's meta socket
    (only relevant for the item that has one; the socket bonus requires every
    socket, including the meta socket, to be matched).
    """
    colored = item.colored_socket_indices
    if not colored:
        return []
    needs_meta = item.meta_socket_index is not None
    pools = []
    for _ in colored:
        pools.append(candidates + jc_candidates)

    bonus_ep = useful_value(item.socket_bonus, ep)
    bonus_hit = 0
    if len(item.socket_bonus) > STAT_MELEE_HIT:
        bonus_hit = int(item.socket_bonus[STAT_MELEE_HIT])

    best = {}
    for combo in itertools.product(*pools):
        jc = sum(1 for g in combo if g.is_jc_gem)
        if jc > 1:
            continue
        matched = all(g.matches_socket(item.gem_sockets[s])
                      for g, s in zip(combo, colored))
        bonus_active = matched and (not needs_meta or meta_socket_ok)
        gem_ep = sum(useful_value(g.stats, ep) for g in combo)
        gem_hit = sum(int(g.stat(STAT_MELEE_HIT)) for g in combo)
        opt_ep = gem_ep + (bonus_ep if bonus_active else 0.0)
        opt_hit = gem_hit + (bonus_hit if bonus_active else 0)
        counts = tuple(sum(x) for x in zip(*(g.counts_as() for g in combo)))
        key = (opt_hit, counts, jc)
        prev = best.get(key)
        if prev is None or opt_ep > prev.ep:
            best[key] = ItemOption(
                item_slot=slot, gems=tuple(g.id for g in combo), ep=opt_ep,
                hit=opt_hit, counts=counts, jc=jc, bonus_active=bonus_active)

    # Pareto prune: drop options beaten on EP by another option with the same
    # jc usage, same hit, and >= color counts.
    opts = list(best.values())
    pruned = []
    for a in opts:
        dominated = any(
            b is not a and b.jc <= a.jc and b.hit == a.hit and b.ep >= a.ep
            and all(bc >= ac for bc, ac in zip(b.counts, a.counts))
            and (b.ep > a.ep or any(bc > ac for bc, ac in zip(b.counts, a.counts)))
            for b in opts)
        if not dominated:
            pruned.append(a)
    return pruned


def optimize_combos(socketed, ep, meta_gem_id, candidates, jc_candidates,
                    meta_socket_gem_matches: bool):
    """DP over items: best-EP gemming for every achievable gem-hit-rating total.

    socketed: list of (slot_index, Item). Returns list[GemCombo].
    """
    cond = META_CONDITIONS.get(meta_gem_id) if meta_gem_id else None
    if cond and cond[0] == "cmp":
        caps = (12, 12, 12)
    elif cond:
        caps = tuple(cond)
    else:
        caps = (0, 0, 0)

    per_item_options = []
    for slot, item in socketed:
        opts = build_item_options(item, slot, candidates, jc_candidates, ep,
                                  meta_socket_gem_matches)
        # (meta_socket_gem_matches only matters for the item with a meta socket)
        if opts:
            per_item_options.append(opts)

    # state: (capped_counts, jc_used, hit_total) -> (ep, [chosen ItemOption...])
    states = {((0, 0, 0), 0, 0): (0.0, [])}
    for opts in per_item_options:
        new_states = {}
        for (counts, jc, hit), (ep_sum, chosen) in states.items():
            for o in opts:
                njc = jc + o.jc
                if njc > 1:
                    continue
                ncounts = _cap_counts(
                    tuple(c + oc for c, oc in zip(counts, o.counts)), caps) \
                    if caps else (0, 0, 0)
                key = (ncounts, njc, hit + o.hit)
                nep = ep_sum + o.ep
                prev = new_states.get(key)
                if prev is None or nep > prev[0]:
                    new_states[key] = (nep, chosen + [o])
        states = new_states

    # Collect, per hit total, the best valid state (meta condition satisfied).
    best_per_hit = {}
    for (counts, jc, hit), (ep_sum, chosen) in states.items():
        if meta_gem_id and not meta_condition_met(meta_gem_id, *counts):
            continue
        prev = best_per_hit.get(hit)
        if prev is None or ep_sum > prev[0]:
            best_per_hit[hit] = (ep_sum, chosen, counts)

    combos = []
    for hit in sorted(best_per_hit):
        ep_sum, chosen, counts = best_per_hit[hit]
        combos.append(GemCombo(
            label=f"+{hit} gem hit", options=chosen, ep=ep_sum, hit=hit,
            counts=counts))
    return combos
