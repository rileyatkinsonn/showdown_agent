from poke_env.battle import AbstractBattle
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

        if my_pokemon.fainted:
            if battle.available_switches:
                return self.create_order(battle.available_switches[0])
            return self.choose_random_move(battle)

        if my_pokemon.status in {"slp", "frz"}:
            if battle.available_switches:
                best_switch = max(battle.available_switches, key=lambda p: p.current_hp_fraction)
                return self.create_order(best_switch)

        if my_pokemon.current_hp_fraction < 0.25:
            healing_moves = {"roost", "recover", "morningsun", "moonlight", "slackoff", "milkdrink", "softboiled"}
            for move in battle.available_moves:
                if move.id in healing_moves:
                    return self.create_order(move)
            if battle.available_switches:
                best_switch = max(battle.available_switches, key=lambda p: p.current_hp_fraction)
                return self.create_order(best_switch)

        if battle.available_moves:
            opponent = battle.opponent_active_pokemon

            def move_score(move):
                if not move.base_power or not move.type or not opponent:
                    return 0
                try:
                    multiplier = move.type.damage_multiplier(*opponent.types, type_chart=move._type_chart)
                except Exception:
                    multiplier = 1.0
                stab = 1.5 if move.type in {my_pokemon.type_1, my_pokemon.type_2} else 1.0
                return move.base_power * multiplier * stab

            best_move = max(battle.available_moves, key=move_score)

            if battle.can_tera:
                return self.create_order(best_move, terastallize=True)
            return self.create_order(best_move)

        if battle.available_switches:
            return self.create_order(battle.available_switches[0])

        return self.choose_random_move(battle)
