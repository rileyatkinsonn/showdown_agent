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
                return -999
            base_power = move_info.get("basePower", move.base_power or 0)
            accuracy = _acc_to_pct(move_info.get("accuracy", move.accuracy))
            category = move_info.get("category", "Status")

            # Early check for ineffective moves
            try:
                effectiveness = move.type.damage_multiplier(opp_pokemon.type_1, opp_pokemon.type_2)
                if effectiveness == 0:
                    return -999
            except Exception:
                effectiveness = 1.0

            # Gate Thunder Wave
            if move.id == "thunderwave":
                if getattr(opp_pokemon, "status", None):
                    return -999
                if effectiveness == 0 or (opp_pokemon.type_1 and opp_pokemon.type_1.name == "ELECTRIC") or \
                        (opp_pokemon.type_2 and opp_pokemon.type_2.name == "ELECTRIC"):
                    return -999

            score = 0

            # STAB
            try:
                if move.type in {my_pokemon.type_1, my_pokemon.type_2}:
                    base_power *= 1.5
            except Exception:
                pass

            # Sun boost
            if move.type.name == "FIRE" and battle.weather.get("SunnyDay", 0):
                base_power *= 1.5

            # Setup rule
            if move.id in {"swordsdance", "calmmind", "agility"}:
                hp_ok = (my_pokemon.current_hp_fraction or 1.0) >= 0.5
                try:
                    best_dmg_now = 0.0
                    for m2 in battle.available_moves:
                        mi2 = gen_data.moves.get(m2.id, {})
                        bp2 = (mi2.get("basePower", m2.base_power or 0)) * (
                                _acc_to_pct(mi2.get("accuracy", m2.accuracy)) / 100.0)
                        try:
                            if m2.type in {my_pokemon.type_1, my_pokemon.type_2}:
                                bp2 *= 1.5
                            if m2.type.name == "FIRE" and battle.weather.get("SunnyDay", 0):
                                bp2 *= 1.5
                            bp2 *= m2.type.damage_multiplier(opp_pokemon.type_1, opp_pokemon.type_2)
                        except Exception:
                            pass
                        best_dmg_now = max(best_dmg_now, bp2)
                except Exception:
                    best_dmg_now = 0.0
                if hp_ok and best_dmg_now < (getattr(opp_pokemon, "current_hp", 1) or 1) * 0.35:
                    score += 50  # Aggressive setup

            # Type effectiveness
            base_power *= effectiveness
            score += base_power * (accuracy / 100)

            # Status/utility
            if category == "Status":
                if move.id == "recover" and (my_pokemon.current_hp_fraction or 1.0) <= 0.65:  # Higher threshold
                    score += 60  # High priority
                if move.id == "taunt":
                    try:
                        if opp_pokemon.moves and any(
                                m.id in {"swordsdance", "calmmind", "agility"} for m in opp_pokemon.moves.values()):
                            score += 45
                        elif battle.turn <= 3:
                            score += 35
                    except Exception:
                        pass
                if my_pokemon.status and move.id == "thunderwave":
                    score -= 40

            # Specific move priorities
            if my_pokemon.species and "Deoxys-Speed" in my_pokemon.species:
                if move.id == "spikes":
                    try:
                        opp_remaining = len(battle.opponent_team) - sum(
                            p.fainted for p in battle.opponent_team.values())
                        spikes_layers = battle.opponent_side_conditions.get("spikes", 0)
                        if opp_remaining >= 2 and (not spikes_layers or spikes_layers < 3) and battle.turn <= 3:
                            score += 60
                    except Exception:
                        pass
                if move.id == "taunt" and battle.turn <= 2:
                    score += 40
                if move.id == "psychoboost" and (opp_pokemon.type_1 and opp_pokemon.type_1.name == "DARK" or \
                                                 opp_pokemon.type_2 and opp_pokemon.type_2.name == "DARK"):
                    return -999

            if my_pokemon.species and "Eternatus" in my_pokemon.species and move.id == "fireblast":
                try:
                    if opp_pokemon.type_1 and opp_pokemon.type_1.name == "FAIRY" or \
                            opp_pokemon.type_2 and opp_pokemon.type_2.name == "FAIRY":
                        score += 50
                    if battle.weather.get("SunnyDay", 0):
                        score += 30
                except Exception:
                    pass

            if my_pokemon.species and "Koraidon" in my_pokemon.species:
                if move.id == "flamecharge":
                    try:
                        if opp_pokemon.type_1 and opp_pokemon.type_1.name == "FAIRY" or \
                                opp_pokemon.type_2 and opp_pokemon.type_2.name == "FAIRY":
                            score += 50
                        if battle.weather.get("SunnyDay", 0):
                            score += 30
                    except Exception:
                        pass
                if move.id == "closecombat" and (opp_pokemon.type_1 and opp_pokemon.type_1.name == "DARK" or \
                                                 opp_pokemon.type_2 and opp_pokemon.type_2.name == "DARK"):
                    score += 40

            if move.id == "suckerpunch" and (opp_pokemon.current_hp_fraction or 1.0) <= 0.3:
                try:
                    last_move = battle.opponent_last_move
                    if last_move and gen_data.moves.get(last_move.id, {}).get("category", "Status") != "Status":
                        score += 35
                    else:
                        score += 10
                except Exception:
                    score += 20

            if move.priority > 0 or (move.id == "wildcharge" and opp_pokemon.moves and any(
                    m.priority > 0 for m in opp_pokemon.moves.values())):
                score += 25

            # Hazard penalty
            try:
                if (battle.opponent_side_conditions.get("stealthrock", 0) or battle.opponent_side_conditions.get(
                        "spikes", 0)) and \
                        (my_pokemon.current_hp_fraction or 1.0) <= 0.5 and move.id != "recover":
                    score -= 30
            except Exception:
                pass

            # Boost for high-damage moves
            if base_power >= 100 and effectiveness >= 1:
                score += 20

            return score

        def switch_score(switch):
            score = 0
            try:
                for mv in opp_pokemon.moves.values():
                    effectiveness = mv.type.damage_multiplier(switch.type_1, switch.type_2)
                    if effectiveness < 1:
                        score += 25
                    elif effectiveness > 1:
                        score -= 25
            except Exception:
                pass
            hp_frac = switch.current_hp_fraction if switch.current_hp_fraction is not None else 1.0
            try:
                if battle.opponent_side_conditions.get("stealthrock", 0) or battle.opponent_side_conditions.get(
                        "spikes", 0):
                    score += hp_frac * 35
            except Exception:
                pass
            # Mirror match matchups
            if switch.species:
                try:
                    if switch.species == "Eternatus" and (opp_pokemon.type_1 and opp_pokemon.type_1.name == "FAIRY" or \
                                                          opp_pokemon.type_2 and opp_pokemon.type_2.name == "FAIRY"):
                        score += 50
                    if switch.species == "Koraidon" and (opp_pokemon.type_1 and opp_pokemon.type_1.name == "DARK" or \
                                                         opp_pokemon.type_2 and opp_pokemon.type_2.name == "DARK"):
                        score += 50
                    if switch.species == "Kingambit" and (opp_pokemon.type_1 and opp_pokemon.type_1.name == "STEEL" or \
                                                          opp_pokemon.type_2 and opp_pokemon.type_2.name == "STEEL"):
                        score += 40
                    if switch.species == "Arceus-Fairy" and (
                            opp_pokemon.type_1 and opp_pokemon.type_1.name == "DRAGON" or \
                            opp_pokemon.type_2 and opp_pokemon.type_2.name == "DRAGON"):
                        score += 40
                except Exception:
                    pass
            if battle.weather.get("SunnyDay", 0) and switch.species in ["Eternatus", "Koraidon"]:
                score += 30
            # Account for stat boosts
            if switch.species in ["Zacian-Crowned", "Kingambit"]:
                score += 15  # Intrepid Sword, Supreme Overlord
            return score + (hp_frac * 25)

        def dmg_est(attacker, defender, mv):
            if attacker is None or defender is None or mv is None:
                return 0.0
            mi = gen_data.moves.get(mv.id, {})
            bp = float(mv.base_power or mi.get("basePower") or 0.0)
            if bp <= 0 or mi.get("category", "Status") == "Status":
                return 0.0
            acc = _acc_to_pct(mi.get("accuracy", mv.accuracy)) / 100.0
            try:
                stab = 1.5 if mv.type in {attacker.type_1, attacker.type_2} else 1.0
                if mv.type.name == "FIRE" and battle.weather.get("SunnyDay", 0):
                    stab *= 1.5
                eff = mv.type.damage_multiplier(defender.type_1, defender.type_2)
                if eff == 0:
                    return 0.0
            except Exception:
                stab = 1.0
                eff = 1.0
            # Boost for stat-enhancing abilities
            boost = 1.0
            if attacker.species == "Zacian-Crowned" and mv.category == "Physical":
                boost *= 1.3  # Intrepid Sword
            if attacker.species == "Kingambit" and mv.category == "Physical":
                boost *= 1.2 + 0.1 * sum(p.fainted for p in battle.team.values())  # Supreme Overlord
            return bp * acc * stab * eff * boost

        def opp_best_move_damage(vs_defender):
            best = 0.0
            if getattr(opp_pokemon, "moves", None):
                for mv in opp_pokemon.moves.values():
                    best = max(best, dmg_est(opp_pokemon, vs_defender, mv))
            if best == 0.0:
                try:
                    for t in (opp_pokemon.type_1, opp_pokemon.type_2):
                        if not t:
                            continue
                        eff = t.damage_multiplier(vs_defender.type_1, vs_defender.type_2)
                        boost = 1.0
                        if opp_pokemon.species == "Zacian-Crowned":
                            boost *= 1.3
                        if opp_pokemon.species == "Kingambit":
                            boost *= 1.2 + 0.1 * sum(p.fainted for p in battle.opponent_team.values())
                        best = max(best, 120.0 * 1.5 * eff * boost)
                except Exception:
                    pass
            return best

        def our_best_damage_next_turn(vs_defender):
            best = 0.0
            for mv in battle.available_moves:
                best = max(best, dmg_est(my_pokemon, vs_defender, mv))
            return best

        def opp_switch_targets():
            bench = []
            for p in battle.opponent_team.values():
                if not p or p.fainted or p.active:
                    continue
                bench.append(p)
            return bench

        ALPHA = 1.2  # Increased to prioritize defense
        GAMMA = 0.7  # Reduced to focus on immediate impact

        def value_if_we_use_move(mv):
            our_immediate = move_score(mv)
            opp_back = opp_best_move_damage(my_pokemon)
            worst = our_immediate - ALPHA * opp_back
            for tgt in opp_switch_targets():
                immediate_on_switch = dmg_est(my_pokemon, tgt, mv)
                our_next = our_best_damage_next_turn(tgt)
                their_next = opp_best_move_damage(my_pokemon)
                line_val = immediate_on_switch + GAMMA * (our_next - ALPHA * their_next)
                worst = min(worst, line_val)
            return worst

        def value_if_we_switch(sw):
            opp_free = opp_best_move_damage(sw)
            our_next = 0.0
            if getattr(sw, "moves", None):
                for mv in sw.moves.values():
                    our_next = max(our_next, dmg_est(sw, opp_pokemon, mv))
            if our_next == 0.0:
                try:
                    for t in (sw.type_1, sw.type_2):
                        if not t:
                            continue
                        eff = t.damage_multiplier(opp_pokemon.type_1, opp_pokemon.type_2)
                        boost = 1.0
                        if sw.species == "Zacian-Crowned":
                            boost *= 1.3
                        if sw.species == "Kingambit":
                            boost *= 1.2 + 0.1 * sum(p.fainted for p in battle.team.values())
                        our_next = max(our_next, 120.0 * 1.5 * eff * boost)
                except Exception:
                    pass
            hp_frac = sw.current_hp_fraction if sw.current_hp_fraction is not None else 1.0
            return -ALPHA * opp_free + GAMMA * our_next + 5.0 * hp_frac

        best_action = None
        best_score = float("-inf")

        for mv in battle.available_moves:
            sc = value_if_we_use_move(mv)
            if sc > best_score:
                best_score = sc
                best_action = self.create_order(mv)

        for sw in battle.available_switches:
            sc = value_if_we_switch(sw)
            if sc > best_score:
                best_score = sc
                best_action = self.create_order(sw)

        return best_action or self.choose_random_move(battle)