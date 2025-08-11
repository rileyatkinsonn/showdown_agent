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
                return -float('inf')
            base_power = move_info.get("basePower", move.base_power or 0)
            accuracy = _acc_to_pct(move_info.get("accuracy", move.accuracy))
            category = move_info.get("category", "Status")

            # Robust type checking
            effectiveness = 1.0
            opp_types = []
            try:
                if opp_pokemon.type_1 and opp_pokemon.type_1.name:
                    opp_types.append(opp_pokemon.type_1.name)
                if opp_pokemon.type_2 and opp_pokemon.type_2.name:
                    opp_types.append(opp_pokemon.type_2.name)
                if move.type and move.type.name:
                    for t in opp_types:
                        eff = move.type.damage_multiplier(t)
                        effectiveness *= eff
                    if effectiveness <= 0:
                        return -float('inf')
            except Exception:
                effectiveness = 1.0

            # Gate Thunder Wave
            if move.id == "thunderwave":
                if getattr(opp_pokemon, "status", None) or "ELECTRIC" in opp_types:
                    return -float('inf')

            score = 0

            # STAB
            my_types = []
            try:
                if my_pokemon.type_1:
                    my_types.append(my_pokemon.type_1)
                if my_pokemon.type_2:
                    my_types.append(my_pokemon.type_2)
                if move.type in my_types:
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
                        bp2 = (mi2.get("basePower", m2.base_power or 0)) * (_acc_to_pct(mi2.get("accuracy", m2.accuracy)) / 100.0)
                        try:
                            eff2 = 1.0
                            for t in opp_types:
                                eff2 *= m2.type.damage_multiplier(t)
                            if m2.type in my_types:
                                bp2 *= 1.5
                            if m2.type.name == "FIRE" and battle.weather.get("SunnyDay", 0):
                                bp2 *= 1.5
                            bp2 *= eff2
                        except Exception:
                            pass
                        best_dmg_now = max(best_dmg_now, bp2)
                    if hp_ok and best_dmg_now < (getattr(opp_pokemon, "current_hp", 1) or 1) * 0.06:
                        score += 140  # Higher setup priority
                except Exception:
                    pass

            # Type effectiveness
            base_power *= effectiveness
            score += base_power * (accuracy / 100)

            # Status/utility
            if category == "Status":
                if move.id == "recover" and (my_pokemon.current_hp_fraction or 1.0) <= 0.97:  # Higher threshold
                    score += 150  # Higher priority
                if move.id == "taunt":
                    try:
                        if opp_pokemon.moves and any(m.id in {"swordsdance", "calmmind", "agility"} for m in opp_pokemon.moves.values()):
                            score += 110
                        elif battle.turn <= 9:
                            score += 100
                    except Exception:
                        pass
                if my_pokemon.status and move.id == "thunderwave":
                    score -= 110

            # Specific move priorities
            if my_pokemon.species and "Deoxys-Speed" in my_pokemon.species:
                if move.id == "spikes":
                    try:
                        opp_remaining = len(battle.opponent_team) - sum(p.fainted for p in battle.opponent_team.values())
                        spikes_layers = battle.opponent_side_conditions.get("spikes", 0)
                        if opp_remaining >= 2 and (not spikes_layers or spikes_layers < 3) and battle.turn <= 10:
                            score += 150  # Higher Spikes priority
                    except Exception:
                        pass
                if move.id == "taunt" and battle.turn <= 9:
                    score += 105
                if move.id == "psychoboost" and any(t in {"DARK", "FAIRY", "STEEL"} for t in opp_types):
                    return -float('inf')

            if my_pokemon.species and "Eternatus" in my_pokemon.species:
                if move.id == "fireblast":
                    try:
                        if "FAIRY" in opp_types or "STEEL" in opp_types:
                            score += 120
                        if battle.weather.get("SunnyDay", 0):
                            score += 100
                    except Exception:
                        pass
                if move.id == "dynamaxcannon" and any(t in {"FAIRY", "NORMAL"} for t in opp_types):
                    return -float('inf')

            if my_pokemon.species and "Koraidon" in my_pokemon.species:
                if move.id == "flamecharge":
                    try:
                        if "FAIRY" in opp_types or "STEEL" in opp_types:
                            score += 120
                        if battle.weather.get("SunnyDay", 0):
                            score += 100
                    except Exception:
                        pass
                if move.id == "closecombat":
                    try:
                        if any(t in {"FAIRY", "STEEL"} for t in opp_types):
                            score -= 110  # Stronger penalty
                        if "DARK" in opp_types:
                            score += 110
                    except Exception:
                        pass

            if move.id == "suckerpunch":
                try:
                    if any(t in {"FAIRY", "STEEL"} for t in opp_types):
                        score -= 70  # Stronger penalty
                    if (opp_pokemon.current_hp_fraction or 1.0) <= 0.06:
                        last_move = battle.opponent_last_move
                        if last_move and gen_data.moves.get(last_move.id, {}).get("category", "Status") != "Status":
                            score += 100
                        else:
                            score += 50
                    else:
                        score += 30
                except Exception:
                    score += 40

            if move.priority > 0 or (move.id == "wildcharge" and opp_pokemon.moves and any(m.priority > 0 for m in opp_pokemon.moves.values())):
                score += 70

            # Hazard penalty
            try:
                if (battle.opponent_side_conditions.get("stealthrock", 0) or battle.opponent_side_conditions.get("spikes", 0)) and \
                   (my_pokemon.current_hp_fraction or 1.0) <= 0.5 and move.id != "recover":
                    score -= 90
            except Exception:
                pass

            # Boost for high-damage moves
            if base_power >= 100 and effectiveness >= 1:
                score += 60

            # Penalize low-accuracy moves
            if accuracy < 90 and category != "Status":
                score -= 50

            return score

        def switch_score(switch):
            score = 0
            try:
                for mv in opp_pokemon.moves.values():
                    eff = mv.type.damage_multiplier(switch.type_1, switch.type_2) if mv.type and switch.type_1 else 1.0
                    if eff < 1:
                        score += 80
                    elif eff > 1:
                        score -= 80
            except Exception:
                pass
            hp_frac = switch.current_hp_fraction if switch.current_hp_fraction is not None else 1.0
            try:
                if battle.opponent_side_conditions.get("stealthrock", 0) or battle.opponent_side_conditions.get("spikes", 0):
                    score += hp_frac * 100
            except Exception:
                pass
            # Mirror match matchups
            try:
                opp_types = []
                if opp_pokemon.type_1 and opp_pokemon.type_1.name:
                    opp_types.append(opp_pokemon.type_1.name)
                if opp_pokemon.type_2 and opp_pokemon.type_2.name:
                    opp_types.append(opp_pokemon.type_2.name)
                if switch.species == "Eternatus" and any(t in {"FAIRY", "STEEL"} for t in opp_types):
                    score += 120
                if switch.species == "Koraidon" and "DARK" in opp_types:
                    score += 120
                if switch.species == "Kingambit" and any(t in {"FAIRY", "STEEL"} for t in opp_types):
                    score += 110
                if switch.species == "Arceus-Fairy" and any(t in {"DRAGON", "FIGHTING"} for t in opp_types):
                    score += 110
            except Exception:
                pass
            if battle.weather.get("SunnyDay", 0) and switch.species in ["Eternatus", "Koraidon"]:
                score += 100
            if switch.species in ["Zacian-Crowned", "Kingambit"]:
                score += 50
            return score + (hp_frac * 80)

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
                eff = mv.type.damage_multiplier(defender.type_1, defender.type_2) if mv.type and defender.type_1 else 1.0
                if eff == 0:
                    return 0.0
            except Exception:
                stab = 1.0
                eff = 1.0
            boost = 1.0
            if attacker.species == "Zacian-Crowned" and mv.category == "Physical":
                boost *= 1.3
            if attacker.species == "Kingambit" and mv.category == "Physical":
                boost *= 1.2 + 0.1 * sum(p.fainted for p in battle.team.values())
            if defender.species == "Zacian-Crowned" and mv.category == "Physical":
                boost /= 1.3
            if defender.species == "Kingambit" and mv.category == "Physical":
                boost /= 1.2 + 0.1 * sum(p.fainted for p in battle.opponent_team.values())
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
                        eff = t.damage_multiplier(vs_defender.type_1, vs_defender.type_2) if vs_defender.type_1 else 1.0
                        boost = 1.0
                        if opp_pokemon.species == "Zacian-Crowned":
                            boost *= 1.3
                        if opp_pokemon.species == "Kingambit":
                            boost *= 1.2 + 0.1 * sum(p.fainted for p in battle.opponent_team.values())
                        best = max(best, 180.0 * 1.5 * eff * boost)
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

        ALPHA = 2.0  # Stronger defensive focus
        GAMMA = 0.2  # Focus on immediate impact

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
                        eff = t.damage_multiplier(opp_pokemon.type_1, opp_pokemon.type_2) if opp_pokemon.type_1 else 1.0
                        boost = 1.0
                        if sw.species == "Zacian-Crowned":
                            boost *= 1.3
                        if sw.species == "Kingambit":
                            boost *= 1.2 + 0.1 * sum(p.fainted for p in battle.team.values())
                        our_next = max(our_next, 180.0 * 1.5 * eff * boost)
                except Exception:
                    pass
            hp_frac = sw.current_hp_fraction if sw.current_hp_fraction is not None else 1.0
            return -ALPHA * opp_free + GAMMA * our_next + 15.0 * hp_frac

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