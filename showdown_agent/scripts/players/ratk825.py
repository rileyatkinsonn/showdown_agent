from poke_env.battle import AbstractBattle
from poke_env.player import Player
import numpy as np
from poke_env.teambuilder import Teambuilder

team = """
Moltres @ Heavy-Duty Boots  
Ability: Flame Body  
Tera Type: Grass  
EVs: 248 HP / 248 Def / 12 Spe  
Bold Nature  
- Flamethrower  
- Roar  
- U-turn  
- Roost  

Zamazenta @ Heavy-Duty Boots  
Ability: Dauntless Shield  
Tera Type: Fire  
EVs: 252 Atk / 4 SpD / 252 Spe  
Jolly Nature  
- Close Combat  
- Crunch  
- Stone Edge  
- Ice Fang  

Darkrai @ Heavy-Duty Boots  
Ability: Bad Dreams  
Shiny: Yes  
Tera Type: Poison  
EVs: 4 Def / 252 SpA / 252 Spe  
Timid Nature  
- Dark Pulse  
- Knock Off  
- Sludge Bomb  
- Ice Beam  

Hydrapple (M) @ Heavy-Duty Boots  
Ability: Regenerator  
Tera Type: Poison  
EVs: 244 HP / 172 Def / 88 SpA / 4 Spe  
Bold Nature  
IVs: 0 Atk  
- Nasty Plot  
- Fickle Beam  
- Giga Drain  
- Earth Power  

Ting-Lu @ Leftovers  
Ability: Vessel of Ruin  
Tera Type: Water  
EVs: 248 HP / 8 Def / 252 SpD  
Careful Nature  
- Spikes  
- Ruination  
- Earthquake  
- Whirlwind  

Tinkaton @ Air Balloon  
Ability: Mold Breaker  
Tera Type: Ghost  
EVs: 240 HP / 36 Atk / 232 Spe  
Jolly Nature  
- Stealth Rock  
- Gigaton Hammer  
- Encore  
- Thunder Wave  

Dragonite (F) @ Lum Berry  
Ability: Multiscale  
Tera Type: Flying  
EVs: 252 Atk / 4 SpD / 252 Spe  
Jolly Nature  
- Dragon Dance  
- Tera Blast  
- Earthquake  
- Extreme Speed  

Landorus-Therian @ Rocky Helmet  
Ability: Intimidate  
Tera Type: Ghost  
EVs: 240 HP / 64 Def / 156 SpD / 48 Spe  
Jolly Nature  
- Earthquake  
- U-turn  
- Stealth Rock  
- Taunt  

Gholdengo @ Metal Coat  
Ability: Good as Gold  
Tera Type: Steel  
EVs: 120 HP / 192 SpA / 196 Spe  
Modest Nature  
IVs: 0 Atk  
- Nasty Plot  
- Make It Rain  
- Recover  
- Thunderbolt  

Hatterene @ Assault Vest  
Ability: Magic Bounce  
Tera Type: Water  
EVs: 248 HP / 80 SpA / 120 SpD / 60 Spe  
Modest Nature  
IVs: 0 Atk  
- Psyshock  
- Draining Kiss  
- Mystical Fire  
- Psychic Noise  

Ninetales-Alola @ Light Clay  
Ability: Snow Warning  
Tera Type: Poison  
EVs: 248 HP / 8 SpA / 252 Spe  
Timid Nature  
IVs: 0 Atk  
- Aurora Veil  
- Freeze-Dry  
- Encore  
- Roar  

Ceruledge @ Covert Cloak  
Ability: Flash Fire  
Tera Type: Bug  
EVs: 248 HP / 60 Def / 116 SpD / 84 Spe  
Careful Nature  
- Bulk Up  
- Bitter Blade  
- Shadow Sneak  
- Taunt  

Gliscor (M) @ Toxic Orb  
Ability: Poison Heal  
Tera Type: Normal  
EVs: 244 HP / 20 Atk / 36 Def / 112 SpD / 96 Spe  
Jolly Nature  
- Swords Dance  
- Facade  
- Earthquake  
- Agility  
"""


pokemons = team.strip().split('\n\n')

class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        custom_teambuilder = RandomTeamFromPool(pokemons)
        print(f"Custom team: {custom_teambuilder.yield_team()}")
        super().__init__(team=custom_teambuilder, *args, **kwargs)

    def choose_move(self, battle: AbstractBattle):
        if battle.available_moves:
            best_move = max(battle.available_moves, key=lambda move: move.base_power or 0)
            # Creating an order for the selected move
            return self.create_order(best_move)
        elif battle.available_switches:
            # If no moves are available, switch to the first available Pokémon
            return self.create_order(battle.available_switches[0])
        
        return self.choose_random_move(battle)
        


class RandomTeamFromPool(Teambuilder):
    def __init__(self, pokemons):
        self.pokemons = []

        for pokemon in pokemons:
            parsed = self.parse_showdown_team(pokemon)
            self.pokemons.append(parsed[0])  # Each 'parsed' is a list of 1 mon

        self.n_pokemons = len(self.pokemons)
        assert self.n_pokemons >= 6

    def yield_team(self):
        idxs = np.random.choice(self.n_pokemons, 6, replace=False)
        team = [self.pokemons[i] for i in idxs]
        return self.join_team(team)
