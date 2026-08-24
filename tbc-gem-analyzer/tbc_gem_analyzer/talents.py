"""Talent-string helpers built on the wowsims talent tree data.

Used to auto-derive the dual-spec "no Murder" build: on bosses where Murder
does nothing (Demons/Elementals/Undead/Mechanical), its points are flex and
go into Improved Eviscerate / Vile Poisons / Improved Poisons instead.
"""
from __future__ import annotations

import json
import os


class TalentTrees:
    def __init__(self, sim_repo: str):
        path = os.path.join(sim_repo, "ui", "core", "talents", "trees", "rogue.json")
        with open(path) as f:
            self.trees = json.load(f)
        self.order = []  # per tree: [(fieldName, maxPoints), ...] in string order
        for tree in self.trees:
            talents = sorted(tree["talents"],
                             key=lambda t: (t["location"]["rowIdx"], t["location"]["colIdx"]))
            self.order.append([(t["fieldName"], t.get("maxPoints", 1)) for t in talents])

    def parse(self, talents_str: str) -> dict:
        """talent string -> {fieldName: points}"""
        out = {}
        for ti, tree_str in enumerate((talents_str or "").split("-")):
            for oi, ch in enumerate(tree_str):
                if oi < len(self.order[ti]) and ch.isdigit() and int(ch):
                    out[self.order[ti][oi][0]] = int(ch)
        return out

    def build(self, points: dict) -> str:
        tree_strs = []
        for tree in self.order:
            s = "".join(str(points.get(name, 0)) for name, _ in tree).rstrip("0")
            tree_strs.append(s)
        while tree_strs and not tree_strs[-1]:
            tree_strs.pop()
        return "-".join(tree_strs)

    def max_points(self, field: str) -> int:
        for tree in self.order:
            for name, mx in tree:
                if name == field:
                    return mx
        return 0


# Preferred flex talents for the freed Murder points, in order. Simmed with
# the wowsims swords APL: Vile Poisons > Improved Poisons > Improved
# Eviscerate (Eviscerate is not part of the rotation, so Imp Evisc is worth
# ~0 dps; on poison-immune bosses the flex choice does not matter at all).
FLEX_TALENTS = ("vilePoisons", "improvedPoisons", "improvedEviscerate")


def derive_no_murder_build(talents_str: str, sim_repo: str,
                           flex: str | None = None) -> str | None:
    """Move Murder's points into flex talents. Returns None if no Murder."""
    trees = TalentTrees(sim_repo)
    pts = trees.parse(talents_str)
    freed = pts.pop("murder", 0)
    if not freed:
        return None
    targets = [flex] if flex else list(FLEX_TALENTS)
    for t in targets:
        if freed <= 0:
            break
        room = trees.max_points(t) - pts.get(t, 0)
        take = min(room, freed)
        if take > 0:
            pts[t] = pts.get(t, 0) + take
            freed -= take
    if freed > 0:  # nowhere to put them; give up on auto-derivation
        return None
    return trees.build(pts)
