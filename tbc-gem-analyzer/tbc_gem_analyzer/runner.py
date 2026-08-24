"""Builds RaidSimRequests and runs them through the wowsimcli binary."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

from .bosses import Boss, encounter_json
from .importers import EquippedGear

DEFAULT_TALENTS = "00532012502-023305200005015002321151"  # combat swords

# Windfury main hand is assumed: the sim engine ignores any MH imbue while a
# Windfury Totem party buff is active, so mhImbueId stays unset.
DEFAULT_CONSUMABLES = {
    "flaskId": 22854,       # Flask of Relentless Assault
    "foodId": 33872,        # Spicy Hot Talbuk
    "potId": 22838,         # Haste Potion
    "conjuredId": 7676,     # Thistle Tea
    "ohImbueId": 27186,     # Deadly Poison (OH)
    "superSapper": True,    # Super Sapper Charge on cooldown
}

ELIXIR_OF_DEMONSLAYING = 9224     # battle elixir, +265 AP vs demons
ELIXIR_OF_MAJOR_FORTITUDE = 32062  # guardian elixir paired with demonslaying
ADAMANTITE_SHARPENING_STONE = 29453  # OH imbue for poison-immune bosses

# Murder (+1%/point damage) only works against these mob types; when an
# alternate (non-Murder) build is provided it is used for all other bosses.
MURDER_MOB_TYPES = {"MobTypeHumanoid", "MobTypeGiant", "MobTypeBeast",
                    "MobTypeDragonkin"}

DEFAULT_PARTY_BUFFS = {
    "battleShout": "TristateEffectImproved",
    "ferociousInspiration": 1,
    "strengthOfEarthTotem": "TristateEffectImproved",
    "graceOfAirTotem": "TristateEffectImproved",
    "windfuryTotem": "TristateEffectImproved",
    "leaderOfThePack": "TristateEffectRegular",
    "totemTwisting": True,
    "drums": "LesserDrumsOfBattle",
}

DEFAULT_RAID_BUFFS = {
    "bloodlust": True,
    "giftOfTheWild": "TristateEffectImproved",
}

DEFAULT_INDIVIDUAL_BUFFS = {
    "blessingOfKings": True,
    "blessingOfMight": "TristateEffectImproved",
    "unleashedRage": True,
}

DEFAULT_DEBUFFS = {
    "exposeWeaknessUptime": 0.9,
    "exposeWeaknessHunterAgility": 1210,
    "bloodFrenzy": True,
    "huntersMark": "TristateEffectImproved",
    "improvedSealOfTheCrusader": "TristateEffectImproved",
    "mangle": True,
    "misery": True,
    "curseOfRecklessness": True,
    "faerieFire": "TristateEffectImproved",
    "giftOfArthas": True,
    "sunderArmor": True,
}

PROFESSION_NAMES = {
    "alchemy": "Alchemy", "blacksmithing": "Blacksmithing",
    "enchanting": "Enchanting", "engineering": "Engineering",
    "herbalism": "Herbalism", "inscription": "Inscription",
    "jewelcrafting": "Jewelcrafting", "leatherworking": "Leatherworking",
    "mining": "Mining", "skinning": "Skinning", "tailoring": "Tailoring",
}


class SimError(Exception):
    pass


class SimRunner:
    def __init__(self, sim_repo: str, gear: EquippedGear, apl_path: str | None = None,
                 talents: str | None = None, settings_path: str | None = None,
                 talents_alt: str | None = None, demonslaying: bool = True,
                 armor_debuff: str = "sunder"):
        self.cli = os.path.join(sim_repo, "wowsimcli-bin")
        if not os.path.exists(self.cli):
            raise SimError(
                f"wowsimcli binary not found at {self.cli}. Run setup.sh first "
                "(it clones wowsims/tbc-new and builds the CLI with -tags with_db).")
        apl_path = apl_path or os.path.join(
            sim_repo, "ui", "rogue", "dps", "apls", "swords.apl.json")
        with open(apl_path) as f:
            self.apl = json.load(f)
        self.gear = gear
        self.talents = talents or gear.talents or DEFAULT_TALENTS
        self.talents_alt = talents_alt  # used on non-Murder mob types if set
        self.demonslaying = demonslaying

        overrides = {}
        if settings_path:
            with open(settings_path) as f:
                overrides = json.load(f)
        self.consumables = {**DEFAULT_CONSUMABLES, **overrides.get("consumables", {})}
        self.party_buffs = {**DEFAULT_PARTY_BUFFS, **overrides.get("partyBuffs", {})}
        self.raid_buffs = {**DEFAULT_RAID_BUFFS, **overrides.get("raidBuffs", {})}
        self.individual_buffs = {**DEFAULT_INDIVIDUAL_BUFFS,
                                 **overrides.get("individualBuffs", {})}
        self.debuffs = {**DEFAULT_DEBUFFS, **overrides.get("debuffs", {})}
        if armor_debuff == "iea":
            # You are the Improved Expose Armor rogue: IEA replaces Sunder.
            self.debuffs.pop("sunderArmor", None)
            self.debuffs["exposeArmor"] = "TristateEffectImproved"

        profs = [PROFESSION_NAMES.get(p.lower()) for p in gear.professions]
        self.professions = [p for p in profs if p][:2]

    def consumables_for_boss(self, boss: Boss) -> dict:
        cons = dict(self.consumables)
        if self.demonslaying and boss.mob_type == "MobTypeDemon":
            cons.pop("flaskId", None)
            cons["battleElixirId"] = ELIXIR_OF_DEMONSLAYING
            cons["guardianElixirId"] = ELIXIR_OF_MAJOR_FORTITUDE
        if boss.poison_immune:
            cons["ohImbueId"] = ADAMANTITE_SHARPENING_STONE
        return cons

    def talents_for_boss(self, boss: Boss) -> str:
        if self.talents_alt and boss.mob_type not in MURDER_MOB_TYPES:
            return self.talents_alt
        return self.talents

    def build_request(self, equipment_json: dict, boss: Boss, iterations: int,
                      seed: int = 20260824, bonus_stats: list | None = None) -> dict:
        player = {
            "name": self.gear.char_name,
            "race": self.gear.race,
            "class": "ClassRogue",
            "equipment": equipment_json,
            "consumables": self.consumables_for_boss(boss),
            "buffs": self.individual_buffs,
            "rogue": {"options": {"classOptions": {}}},
            "talentsString": self.talents_for_boss(boss),
            "rotation": self.apl,
            "reactionTimeMs": 100,
            "distanceFromTarget": 5,
        }
        if self.professions:
            player["profession1"] = self.professions[0]
            if len(self.professions) > 1:
                player["profession2"] = self.professions[1]
        if bonus_stats:
            player["bonusStats"] = {"stats": bonus_stats}
        return {
            "raid": {
                "parties": [{"players": [player], "buffs": self.party_buffs}],
                "buffs": self.raid_buffs,
                "debuffs": self.debuffs,
            },
            "encounter": encounter_json(boss),
            "simOptions": {"iterations": iterations, "randomSeed": seed},
        }

    def run(self, request: dict) -> dict:
        """Run one sim; returns {'dps': avg, 'stdev': sd, 'duration': avg_sec}."""
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(request, f)
            infile = f.name
        try:
            proc = subprocess.run([self.cli, "sim", "--infile", infile],
                                  capture_output=True, text=True, timeout=1800)
            if proc.returncode != 0:
                raise SimError(f"wowsimcli failed: {proc.stderr[-500:]}")
            result = json.loads(proc.stdout)
        finally:
            os.unlink(infile)
        err = result.get("error")
        if err:
            raise SimError(f"sim error: {err.get('message', '')[:800]}")
        try:
            pm = result["raidMetrics"]["parties"][0]["players"][0]
            dps = pm["dps"]
        except (KeyError, TypeError, IndexError):
            raise SimError(f"unexpected sim output: {proc.stdout[:400]}")
        duration = result.get("avgIterationDuration") or 0
        return {
            "dps": dps.get("avg", 0.0),
            "stdev": dps.get("stdev", 0.0),
            "duration": duration,
        }

    def run_many(self, requests: list, workers: int = 3, progress=None) -> list:
        """Run several sims concurrently; returns results in order."""
        results = [None] * len(requests)
        done = [0]

        def work(i):
            results[i] = self.run(requests[i])
            done[0] += 1
            if progress:
                progress(done[0], len(requests))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, range(len(requests))))
        return results


def equipment_with_combo(gear: EquippedGear, db, combo) -> dict:
    """Return equipment protojson with the combo's gems applied."""
    eq = copy.deepcopy(gear.to_equipment_json())
    for slot, spec in enumerate(gear.slots):
        if not spec:
            continue
        item = db.item(spec.id)
        if not item or not item.gem_sockets:
            continue
        opt = combo.gems_for_slot(slot)
        if opt is None:
            continue
        gems = list(spec.gems) + [0] * (len(item.gem_sockets) - len(spec.gems))
        gems = gems[:len(item.gem_sockets)]
        for gem_id, sock_idx in zip(opt.gems, item.colored_socket_indices):
            gems[sock_idx] = gem_id
        eq["items"][slot]["gems"] = gems
    return eq
