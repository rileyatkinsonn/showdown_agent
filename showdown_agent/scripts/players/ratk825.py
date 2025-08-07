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

        def move_damage_estimation(move , attacker, defender):
            if not move.base_power or not move.type:
                return 0

            try:
                effectiveness = move.type.damage_multiplier(defender.type_1, defender.type_2)
            except Exception:
                effectiveness = 1.0

            stab = 1.5 if move.type in {attacker.type_1, attacker.type_2} else 1.0
            accuracy = move.accuracy / 100 if move.accuracy else 1.0
            return move.base_power * stab * effectiveness * accuracy

        def evaluate_state(my_move: Move):
                if not opp_pokemon or not my_move:
                    return 0

                my_dmg = move_damage_estimation(my_move, my_pokemon, opp_pokemon)

                if not opp_pokemon.moves:
                    return my_dmg  # assume no counterplay

                worst_case_dmg = max(
                    (move_damage_estimation(opp_move, opp_pokemon, my_pokemon) for opp_move in
                     opp_pokemon.moves.values()),
                    default=0
                )

                return my_dmg - worst_case_dmg

        if battle.available_moves:
            best_move = max(battle.available_moves, key=evaluate_state)
            return self.create_order(best_move)

        if battle.available_switches:
            return self.create_order(max(battle.available_switches, key=lambda p: p.current_hp_fraction))

        return self.choose_random_move(battle)