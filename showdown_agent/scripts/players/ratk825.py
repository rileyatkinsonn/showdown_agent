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

class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)

    def choose_move(self, battle: AbstractBattle):
        gen_data = GenData.from_format(battle.format)
        my_pokemon = battle.active_pokemon
        opp_pokemon = battle.opponent_active_pokemon

        def move_score(move):
            move_info = gen_data.moves.get(move.id, {})
            if not move_info:
                return 0
            base_power = move_info.get("basePower", move.base_power or 0)
            accuracy = move_info.get("accuracy", move.accuracy or 100)
            category = move_info.get("category", "Status")

            score = 0

            # STAB
            if move.type in {my_pokemon.type_1, my_pokemon.type_2}:
                base_power *= 1.5

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

