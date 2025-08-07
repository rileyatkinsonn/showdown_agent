from poke_env.battle import AbstractBattle, Move
from poke_env.player import Player

import numpy as np
from poke_env.teambuilder import Teambuilder

team = """
Dragapult @ Heavy-Duty Boots  
Ability: Cursed Body  
Tera Type: Dragon  
EVs: 60 Atk / 196 SpA / 252 Spe  
Naive Nature  
- Dragon Darts  
- Hex  
- Will-O-Wisp  
- U-turn  

Dragonite (F) @ Heavy-Duty Boots  
Ability: Multiscale  
Tera Type: Normal  
EVs: 104 HP / 252 Atk / 152 Spe  
Adamant Nature  
- Dragon Dance  
- Earthquake  
- Extreme Speed  
- Roost  

Ting-Lu @ Leftovers  
Ability: Vessel of Ruin  
Tera Type: Water  
EVs: 248 HP / 252 SpD / 8 Spe  
Careful Nature  
- Stealth Rock  
- Ruination  
- Earthquake  
- Whirlwind  

Weezing-Galar (F) @ Heavy-Duty Boots  
Ability: Neutralizing Gas  
Tera Type: Ghost  
EVs: 252 HP / 252 Def / 4 SpD  
Bold Nature  
IVs: 0 Atk  
- Toxic Spikes  
- Will-O-Wisp  
- Pain Split  
- Defog  

Iron Crown @ Choice Specs  
Ability: Quark Drive  
Tera Type: Steel  
EVs: 4 Def / 252 SpA / 252 Spe  
Timid Nature  
IVs: 20 Atk  
- Tachyon Cutter  
- Psyshock  
- Focus Blast  
- Volt Switch  

Zapdos @ Heavy-Duty Boots  
Ability: Static  
Tera Type: Fairy  
EVs: 40 HP / 252 SpA / 216 Spe  
Timid Nature  
IVs: 0 Atk  
- Volt Switch  
- Hurricane  
- Heat Wave  
- Roost  
"""


pokemons = team.strip().split('\n\n')

class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)

    def choose_move(self, battle: AbstractBattle):
        my_pokemon = battle.active_pokemon
        opp_pokemon = battle.opponent_active_pokemon

        def move_damage_estimate(move, attacker, defender):
            if not move.base_power or not move.type:
                return 0

            try:
                effectiveness = move.type.damage_multiplier(defender.type_1, defender.type_2)
            except Exception:
                effectiveness = 1.0

            stab = 1.5 if move.type in {attacker.type_1, attacker.type_2} else 1.0
            accuracy = move.accuracy / 100 if move.accuracy else 1.0

            return move.base_power * stab * effectiveness * accuracy

        def score_state(my_dmg, opp_dmg, my_hp, opp_hp):
            return (
                    3 * my_dmg
                    - 2 * opp_dmg
                    + 1.5 * my_hp
                    - 0.5 * opp_hp
            )

        def simulate_move_outcome(my_move):
            if not opp_pokemon or not my_move:
                return 0

            my_dmg = move_damage_estimate(my_move, my_pokemon, opp_pokemon)

            if not opp_pokemon.moves:
                return score_state(my_dmg, 0, my_pokemon.current_hp_fraction, opp_pokemon.current_hp_fraction)

            worst_case_dmg = max(
                (move_damage_estimate(opp_move, opp_pokemon, my_pokemon) for opp_move in opp_pokemon.moves.values()),
                default=0
            )

            if my_dmg >= opp_pokemon.current_hp:
                faint_bonus = 2.0
            else:
                faint_bonus = 0.0

            return score_state(my_dmg, worst_case_dmg, my_pokemon.current_hp_fraction,
                               opp_pokemon.current_hp_fraction) + faint_bonus

        def simulate_switch_outcome(switch_in):
            if not opp_pokemon:
                return 0

            if not opp_pokemon.moves:
                return switch_in.current_hp_fraction

            worst_case_dmg = max(
                (move_damage_estimate(opp_move, opp_pokemon, switch_in) for opp_move in opp_pokemon.moves.values()),
                default=0
            )

            if worst_case_dmg >= switch_in.current_hp:
                return -5

                # Assume switch takes a hit and does nothing in return
            return score_state(0, worst_case_dmg, switch_in.current_hp_fraction, opp_pokemon.current_hp_fraction)

        best_action = None
        best_score = float('-inf')

        # Evaluate moves
        for move in battle.available_moves:
            score = simulate_move_outcome(move)
            if score > best_score:
                best_score = score
                best_action = self.create_order(move)

        # Evaluate switches
        for switch in battle.available_switches:
            score = simulate_switch_outcome(switch)
            if score > best_score:
                best_score = score
                best_action = self.create_order(switch)

        if best_action:
            return best_action

        return self.choose_random_move(battle)