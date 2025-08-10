from poke_env.battle import AbstractBattle, Move
from poke_env.player import Player
from poke_env.data import GenData

import numpy as np
from poke_env.teambuilder import Teambuilder

team = """


Deoxys-Speed @ Focus Sash  
Ability: Pressure  
Tera Type: Ghost  
EVs: 248 HP / 8 SpA / 252 Spe  
Timid Nature  
IVs: 0 Atk  
- Thunder Wave  
- Spikes  
- Taunt  
- Psycho Boost  

Kingambit @ Dread Plate  
Ability: Supreme Overlord  
Tera Type: Dark  
EVs: 56 HP / 252 Atk / 200 Spe  
Adamant Nature  
- Swords Dance  
- Kowtow Cleave  
- Iron Head  
- Sucker Punch  

Zacian-Crowned @ Rusted Sword  
Ability: Intrepid Sword  
Tera Type: Flying  
EVs: 252 Atk / 4 SpD / 252 Spe  
Jolly Nature  
- Swords Dance  
- Behemoth Blade  
- Close Combat  
- Wild Charge  

Arceus-Fairy @ Pixie Plate  
Ability: Multitype  
Tera Type: Fire  
EVs: 248 HP / 72 Def / 188 Spe  
Bold Nature  
IVs: 0 Atk  
- Calm Mind  
- Judgment  
- Taunt  
- Recover  

Eternatus @ Power Herb  
Ability: Pressure  
Tera Type: Fire  
EVs: 124 HP / 252 SpA / 132 Spe  
Modest Nature  
IVs: 0 Atk  
- Agility  
- Meteor Beam  
- Dynamax Cannon  
- Fire Blast  

Koraidon @ Life Orb  
Ability: Orichalcum Pulse  
Tera Type: Fire  
EVs: 8 HP / 248 Atk / 252 Spe  
Jolly Nature  
- Swords Dance  
- Scale Shot  
- Flame Charge  
- Close Combat  
"""


pokemons = team.strip().split('\n\n')

def _acc_to_pct(acc) -> float:
    # GenData accuracy can be bool, int, or None
    if acc is True:
        return 100.0
    if acc is False or acc is None:
        return 0.0
    try:
        return float(acc)
    except Exception:
        return 100.0

class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)

    def choose_move(self, battle: AbstractBattle):
        gen_data = GenData.from_format(battle.format)
        my_pokemon = battle.active_pokemon
        opp_pokemon = battle.opponent_active_pokemon

        if my_pokemon is None or opp_pokemon is None:
            return self.choose_random_move(battle)

        def move_score(move):
            move_info = gen_data.moves.get(move.id, {})
            if not move_info:
                return 0
            base_power = move_info.get("basePower", move.base_power or 0)
            accuracy = _acc_to_pct(move_info.get("accuracy", move.accuracy))
            category = move_info.get("category", "Status")


            # Gate TWave into immunities / already statused
            if move.id == "thunderwave":
                # don't try if target has a status already
                if getattr(opp_pokemon, "status", None):
                    return -999
                try:
                    # Electric vs Ground = 0; Electric-types are immune to paralysis from electric moves
                    eff = move.type.damage_multiplier(opp_pokemon.type_1, opp_pokemon.type_2)
                    if eff == 0 or (opp_pokemon.type_1 and opp_pokemon.type_1.name == "ELECTRIC") or \
                            (opp_pokemon.type_2 and opp_pokemon.type_2.name == "ELECTRIC"):
                        return -999
                except Exception:
                    pass

            score = 0

            # STAB
            if move.type in {my_pokemon.type_1, my_pokemon.type_2}:
                base_power *= 1.5

            # Lightweight setup rule
            if move.id in {"swordsdance", "calmmind"}:
                # Only when healthy and not obviously threatened
                hp_ok = (my_pokemon.current_hp_fraction or 1.0) >= 0.7
                # Rough check: if our best raw damaging move this turn is mediocre, setup instead
                try:
                    best_dmg_now = 0.0
                    for m2 in battle.available_moves:
                        mi2 = gen_data.moves.get(m2.id, {})
                        bp2 = (mi2.get("basePower", m2.base_power or 0)) * (
                                    _acc_to_pct(mi2.get("accuracy", m2.accuracy)) / 100.0)
                        # STAB + effectiveness
                        if m2.type in {my_pokemon.type_1, my_pokemon.type_2}:
                            bp2 *= 1.5
                        try:
                            bp2 *= m2.type.damage_multiplier(opp_pokemon.type_1, opp_pokemon.type_2)
                        except Exception:
                            pass
                        best_dmg_now = max(best_dmg_now, bp2)
                except Exception:
                    best_dmg_now = 0.0
                if hp_ok and best_dmg_now < (getattr(opp_pokemon, "current_hp", 1) or 1) * 0.6:
                    score += 25

            # Type effectiveness
            try:
                effectiveness = move.type.damage_multiplier(
                    opp_pokemon.type_1, opp_pokemon.type_2
                )
            except Exception:
                effectiveness = 1.0
            base_power *= effectiveness

            # Accuracy factor
            score += base_power * (accuracy / 100)

            # Bonuses for status/utility
            if category == "Status":
                if move.id in {"stealthrock", "toxicspikes", "willowisp", "defog"}:
                    score += 20
                if move.id in {"roost", "painsplit"}:
                    score += 10

            if move.id in {"uturn", "voltswitch"}:
                score += 15

            if move.priority > 0:
                score += 10

            # Deoxys-Speed: early-game plan
            if my_pokemon.species and "Deoxys-Speed" in my_pokemon.species:
                # prefer Spikes turn 1–3 if they have backups and we don't already have 2+ layers
                if move.id == "spikes":
                    try:
                        opp_remaining = len(battle.opponent_team) - sum(
                            p.fainted for p in battle.opponent_team.values())
                        spikes_layers = battle.opponent_side_conditions.get("spikes", 0)
                        if opp_remaining >= 2 and (not spikes_layers or spikes_layers < 2) and battle.turn <= 3:
                            score += 40
                    except Exception:
                        pass
                # prefer Taunt early to block their hazards/setup
                if move.id == "taunt" and battle.turn <= 2:
                    score += 25

            if move.id == "suckerpunch":
                if (opp_pokemon.current_hp_fraction or 1.0) <= 0.35:
                    score += 15

            return score

        def switch_score(switch):
            score = 0
            for move in opp_pokemon.moves.values():
                try:
                    effectiveness = move.type.damage_multiplier(
                        switch.type_1, switch.type_2
                    )
                except Exception:
                    effectiveness = 1.0
                if effectiveness < 1:
                    score += 10
                elif effectiveness > 1:
                    score -= 10
            return score + (switch.current_hp_fraction * 10)

        best_action = None
        best_score = float('-inf')

        for move in battle.available_moves:
            score = move_score(move)
            if score > best_score:
                best_score = score
                best_action = self.create_order(move)

        for switch in battle.available_switches:
            score = switch_score(switch)
            if score > best_score:
                best_score = score
                best_action = self.create_order(switch)

        return best_action or self.choose_random_move(battle)

