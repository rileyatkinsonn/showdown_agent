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

        # ---------- your original scoring (kept) ----------
        def move_score(move):
            move_info = gen_data.moves.get(move.id, {})
            if not move_info:
                return 0
            base_power = move_info.get("basePower", move.base_power or 0)
            accuracy = _acc_to_pct(move_info.get("accuracy", move.accuracy))
            category = move_info.get("category", "Status")

            # Gate TWave into immunities / already statused
            if move.id == "thunderwave":
                if getattr(opp_pokemon, "status", None):
                    return -999
                try:
                    eff = move.type.damage_multiplier(opp_pokemon.type_1, opp_pokemon.type_2)
                    if eff == 0 or (opp_pokemon.type_1 and opp_pokemon.type_1.name == "ELECTRIC") or \
                            (opp_pokemon.type_2 and opp_pokemon.type_2.name == "ELECTRIC"):
                        return -999
                except Exception:
                    pass

            score = 0

            # STAB
            try:
                if move.type in {my_pokemon.type_1, my_pokemon.type_2}:
                    base_power *= 1.5
            except Exception:
                pass

            # Lightweight setup rule
            if move.id in {"swordsdance", "calmmind"}:
                hp_ok = (my_pokemon.current_hp_fraction or 1.0) >= 0.7
                try:
                    best_dmg_now = 0.0
                    for m2 in battle.available_moves:
                        mi2 = gen_data.moves.get(m2.id, {})
                        bp2 = (mi2.get("basePower", m2.base_power or 0)) * (
                                    _acc_to_pct(mi2.get("accuracy", m2.accuracy)) / 100.0)
                        try:
                            if m2.type in {my_pokemon.type_1, my_pokemon.type_2}:
                                bp2 *= 1.5
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
                effectiveness = move.type.damage_multiplier(opp_pokemon.type_1, opp_pokemon.type_2)
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
                if move.id == "spikes":
                    try:
                        opp_remaining = len(battle.opponent_team) - sum(
                            p.fainted for p in battle.opponent_team.values())
                        spikes_layers = battle.opponent_side_conditions.get("spikes", 0)
                        if opp_remaining >= 2 and (not spikes_layers or spikes_layers < 2) and battle.turn <= 3:
                            score += 40
                    except Exception:
                        pass
                if move.id == "taunt" and battle.turn <= 2:
                    score += 25

            if move.id == "suckerpunch":
                if (opp_pokemon.current_hp_fraction or 1.0) <= 0.35:
                    score += 15

            return score

        def switch_score(switch):
            score = 0
            for mv in opp_pokemon.moves.values():
                try:
                    effectiveness = mv.type.damage_multiplier(switch.type_1, switch.type_2)
                except Exception:
                    effectiveness = 1.0
                if effectiveness < 1:
                    score += 10
                elif effectiveness > 1:
                    score -= 10
            hp_frac = switch.current_hp_fraction if switch.current_hp_fraction is not None else 1.0
            return score + (hp_frac * 10)

        # ---------- lightweight damage estimator for tree ----------
        def dmg_est(attacker, defender, mv):
            """Approx expected damage-like score for tree math (BP * acc * STAB * SE)."""
            if attacker is None or defender is None or mv is None:
                return 0.0
            mi = gen_data.moves.get(mv.id, {})
            bp = float(mv.base_power or mi.get("basePower") or 0.0)
            if bp <= 0 or mi.get("category", "Status") == "Status":
                return 0.0
            acc = _acc_to_pct(mi.get("accuracy", mv.accuracy)) / 100.0
            try:
                stab = 1.5 if mv.type in {attacker.type_1, attacker.type_2} else 1.0
            except Exception:
                stab = 1.0
            try:
                eff = mv.type.damage_multiplier(defender.type_1, defender.type_2)
            except Exception:
                eff = 1.0
            if eff == 0:
                return 0.0
            return bp * acc * stab * eff

        def opp_best_move_damage(vs_defender):
            """Best revealed opp damage into vs_defender; fallback to STAB proxy 80BP."""
            best = 0.0
            if getattr(opp_pokemon, "moves", None):
                for mv in opp_pokemon.moves.values():
                    best = max(best, dmg_est(opp_pokemon, vs_defender, mv))
            if best == 0.0:
                # STAB proxy
                try:
                    for t in (opp_pokemon.type_1, opp_pokemon.type_2):
                        if not t:
                            continue
                        # base 80 * STAB * effectiveness
                        eff = t.damage_multiplier(vs_defender.type_1, vs_defender.type_2)
                        best = max(best, 80.0 * 1.5 * eff)
                except Exception:
                    pass
            return best

        def our_best_damage_next_turn(vs_defender):
            best = 0.0
            for mv in battle.available_moves:
                best = max(best, dmg_est(my_pokemon, vs_defender, mv))
            return best

        # ---------- opponent bench for switch responses ----------
        def opp_switch_targets():
            bench = []
            for p in battle.opponent_team.values():
                if not p or p.fainted or p.active:
                    continue
                bench.append(p)
            return bench

        # ---------- depth-2 evaluation ----------
        ALPHA = 1.0  # weight on opp damage
        GAMMA = 0.8  # weight on next-turn value

        def value_if_we_use_move(mv):
            # Opp stays & attacks
            our_immediate = move_score(mv)  # keep your heuristic value
            opp_back = opp_best_move_damage(my_pokemon)
            worst = our_immediate - ALPHA * opp_back

            # Opp switches: they pick the one worst for us
            for tgt in opp_switch_targets():
                immediate_on_switch = dmg_est(my_pokemon, tgt, mv)  # our hit on the switch-in
                our_next = our_best_damage_next_turn(tgt)
                their_next = opp_best_move_damage(my_pokemon)  # rough: assume they hit our current mon
                line_val = immediate_on_switch + GAMMA * (our_next - ALPHA * their_next)
                worst = min(worst, line_val)

            return worst

        def value_if_we_switch(sw):
            # They likely attack our switch-in
            opp_free = opp_best_move_damage(sw)
            our_next = 0.0
            if getattr(sw, "moves", None):
                for mv in sw.moves.values():
                    our_next = max(our_next, dmg_est(sw, opp_pokemon, mv))
            if our_next == 0.0:
                # proxy: STAB 80 into current opp
                try:
                    for t in (sw.type_1, sw.type_2):
                        if not t:
                            continue
                        eff = t.damage_multiplier(opp_pokemon.type_1, opp_pokemon.type_2)
                        our_next = max(our_next, 80.0 * 1.5 * eff)
                except Exception:
                    pass
            hp_frac = sw.current_hp_fraction if sw.current_hp_fraction is not None else 1.0
            return -ALPHA * opp_free + GAMMA * our_next + 5.0 * hp_frac

        # ---------- true selection: argmax over our actions, opponent plays minimizer ----------
        best_action = None
        best_score = float("-inf")

        # Evaluate our moves (skip immune damaging moves)
        for mv in battle.available_moves:
            mi = gen_data.moves.get(mv.id, {})
            if mi.get("category", "Status") != "Status":
                try:
                    if mv.type.damage_multiplier(opp_pokemon.type_1, opp_pokemon.type_2) == 0:
                        continue
                except Exception:
                    pass
            sc = value_if_we_use_move(mv)
            if sc > best_score:
                best_score = sc
                best_action = self.create_order(mv)

        # Evaluate our switches
        for sw in battle.available_switches:
            sc = value_if_we_switch(sw)
            if sc > best_score:
                best_score = sc
                best_action = self.create_order(sw)

        return best_action or self.choose_random_move(battle)
