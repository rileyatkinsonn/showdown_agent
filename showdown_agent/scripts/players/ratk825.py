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

        def get_pokemon_types(pokemon):
            """Get types as strings for a pokemon"""
            types = []
            try:
                if pokemon.type_1 and hasattr(pokemon.type_1, 'name'):
                    types.append(pokemon.type_1.name)
                if pokemon.type_2 and hasattr(pokemon.type_2, 'name'):
                    types.append(pokemon.type_2.name)
            except:
                pass
            return types

        def is_mirror_match():
            """Check if we're in a mirror match (same team)"""
            return battle.teampreview_opponent_team is not None and len(battle.teampreview_opponent_team) == 6

        def move_score(move):
            move_info = gen_data.moves.get(move.id, {})
            if not move_info:
                return -float('inf')

            base_power = move_info.get("basePower", move.base_power or 0)
            accuracy = _acc_to_pct(move_info.get("accuracy", move.accuracy))
            category = move_info.get("category", "Status")

            # Type effectiveness calculation
            effectiveness = 1.0
            opp_types = get_pokemon_types(opp_pokemon)

            try:
                if move.type and move.type.name:
                    for t in opp_types:
                        eff = move.type.damage_multiplier(t)
                        effectiveness *= eff
                    if effectiveness <= 0:
                        return -float('inf')
            except Exception:
                effectiveness = 1.0

            # Gate obviously bad moves early
            if move.id == "thunderwave" and (getattr(opp_pokemon, "status", None) or "ELECTRIC" in opp_types):
                return -float('inf')

            # CRITICAL FIX: Don't use Dynamax Cannon against Fairy types
            if move.id == "dynamaxcannon" and "FAIRY" in opp_types:
                return -float('inf')

            # CRITICAL FIX: Don't use Psycho Boost against Steel types
            if move.id == "psychoboost" and "STEEL" in opp_types:
                return -float('inf')

            score = 0

            # STAB calculation
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

            # Weather boost
            if move.type and move.type.name == "FIRE" and hasattr(battle, 'weather') and battle.weather:
                if any("sun" in str(w).lower() for w in battle.weather):
                    base_power *= 1.5

            # Mirror match specific logic
            if is_mirror_match():
                # In mirror matches, prioritize setup and defensive plays early
                if battle.turn <= 3:
                    if move.id in {"swordsdance", "calmmind", "agility"} and (
                            my_pokemon.current_hp_fraction or 1.0) >= 0.7:
                        score += 200
                    if move.id == "recover" and (my_pokemon.current_hp_fraction or 1.0) <= 0.8:
                        score += 180

                # Prioritize Taunt to prevent opponent setup
                if move.id == "taunt" and battle.turn <= 5:
                    score += 160

                # Speed control with Thunder Wave
                if move.id == "thunderwave" and not getattr(opp_pokemon, "status", None):
                    score += 140

            # Setup move logic - more conservative
            if move.id in {"swordsdance", "calmmind", "agility"}:
                hp_threshold = 0.7 if is_mirror_match() else 0.5
                if (my_pokemon.current_hp_fraction or 1.0) >= hp_threshold:
                    # Check if we have time to setup
                    try:
                        predicted_damage = 0
                        if hasattr(opp_pokemon, 'moves') and opp_pokemon.moves:
                            for opp_move in opp_pokemon.moves.values():
                                if opp_move.base_power and opp_move.base_power > predicted_damage:
                                    predicted_damage = opp_move.base_power

                        # Only setup if we won't be KO'd next turn
                        if predicted_damage < (my_pokemon.current_hp_fraction or 1.0) * 300:
                            score += 150
                    except Exception:
                        score += 100

            # Core damage calculation
            base_power *= effectiveness
            score += base_power * (accuracy / 100)

            # Status move improvements
            if category == "Status":
                if move.id == "recover":
                    hp_frac = my_pokemon.current_hp_fraction or 1.0
                    if hp_frac <= 0.5:
                        score += 200
                    elif hp_frac <= 0.8:
                        score += 120

                if move.id == "taunt":
                    # Higher priority early game and against setup threats
                    if battle.turn <= 8:
                        score += 130
                    try:
                        if hasattr(opp_pokemon, 'moves') and opp_pokemon.moves:
                            setup_moves = {"swordsdance", "calmmind", "agility", "recover"}
                            if any(m.id in setup_moves for m in opp_pokemon.moves.values()):
                                score += 150
                    except:
                        pass

            # Species-specific logic improvements
            if my_pokemon.species:
                if "Deoxys-Speed" in my_pokemon.species:
                    if move.id == "spikes":
                        try:
                            spikes_layers = battle.opponent_side_conditions.get("spikes", 0)
                            remaining_opponents = sum(1 for p in battle.opponent_team.values() if not p.fainted)
                            if remaining_opponents >= 2 and spikes_layers < 2 and battle.turn <= 8:
                                score += 170
                        except:
                            pass

                elif "Kingambit" in my_pokemon.species:
                    # Supreme Overlord boost calculation
                    fainted_allies = sum(1 for p in battle.team.values() if p.fainted)
                    if move.id in {"suckerpunch", "kowtowcleave", "ironhead"}:
                        score += fainted_allies * 15

                elif "Arceus-Fairy" in my_pokemon.species:
                    if move.id == "judgment":
                        # Extra damage vs Dragons and Fighting types
                        if any(t in {"DRAGON", "FIGHTING", "DARK"} for t in opp_types):
                            score += 80

            # Priority move handling
            if hasattr(move, 'priority') and move.priority > 0:
                score += 60
                # Extra bonus if opponent is low on HP
                if (opp_pokemon.current_hp_fraction or 1.0) <= 0.3:
                    score += 80

            # Sucker Punch specific logic
            if move.id == "suckerpunch":
                try:
                    # Predict if opponent will use an attacking move
                    if (opp_pokemon.current_hp_fraction or 1.0) <= 0.4:
                        score += 100
                    else:
                        score += 40

                    # Penalty against Fairy types
                    if "FAIRY" in opp_types:
                        score -= 50
                except:
                    score += 40

            # Accuracy penalties
            if accuracy < 85 and category != "Status":
                score -= 60

            # High power move bonus
            if base_power >= 120 and effectiveness >= 1:
                score += 70

            return score

        def switch_score(switch):
            score = 0
            try:
                # Type matchup evaluation
                switch_types = get_pokemon_types(switch)
                opp_types = get_pokemon_types(opp_pokemon)

                # Check defensive matchup
                if hasattr(opp_pokemon, 'moves') and opp_pokemon.moves:
                    for mv in opp_pokemon.moves.values():
                        if mv.type and switch.type_1:
                            eff = mv.type.damage_multiplier(switch.type_1, switch.type_2) if mv.type else 1.0
                            if eff < 0.5:
                                score += 100
                            elif eff > 2:
                                score -= 120

                # HP consideration
                hp_frac = switch.current_hp_fraction if switch.current_hp_fraction is not None else 1.0
                score += hp_frac * 100

                # Hazard damage consideration
                if battle.opponent_side_conditions.get("stealthrock", 0) or battle.opponent_side_conditions.get(
                        "spikes", 0):
                    if hp_frac < 0.7:
                        score -= 80

                # Mirror match switching logic
                if is_mirror_match():
                    # Specific advantageous switches in mirrors
                    if switch.species == "Arceus-Fairy" and any(t in {"DRAGON", "FIGHTING", "DARK"} for t in opp_types):
                        score += 120
                    elif switch.species == "Kingambit" and "FAIRY" in opp_types:
                        score += 100
                    elif switch.species == "Zacian-Crowned":
                        score += 80  # Generally strong

                # Don't switch if current Pokemon can still be useful
                if (my_pokemon.current_hp_fraction or 0) > 0.6:
                    score -= 50

            except Exception:
                pass

            return score

        # Evaluate all possible actions
        best_action = None
        best_score = float("-inf")

        # Evaluate moves
        for mv in battle.available_moves:
            sc = move_score(mv)
            if sc > best_score:
                best_score = sc
                best_action = self.create_order(mv)

        # Evaluate switches
        for sw in battle.available_switches:
            sc = switch_score(sw)
            if sc > best_score:
                best_score = sc
                best_action = self.create_order(sw)

        # Emergency fallback - if no good action found, prefer attacking moves
        if best_action is None or best_score < -100:
            attacking_moves = [m for m in battle.available_moves if (m.base_power or 0) > 0]
            if attacking_moves:
                best_action = self.create_order(max(attacking_moves, key=lambda x: x.base_power or 0))

        return best_action or self.choose_random_move(battle)