from typing import List, Optional, Dict, Set
from dataclasses import dataclass, field

from poke_env.battle import MoveCategory
from poke_env.battle.abstract_battle import AbstractBattle
from poke_env.battle.double_battle import DoubleBattle
from poke_env.battle.pokemon import Pokemon
from poke_env.battle.side_condition import SideCondition
from poke_env.player.battle_order import BattleOrder
from poke_env.player.player import Player
from poke_env.data import GenData

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


@dataclass
class OpponentPokemon:
    """Tracks what we know about an opponent's Pokemon"""
    species: str
    types: List[str] = field(default_factory=list)
    moves_seen: Set[str] = field(default_factory=set)
    ability: Optional[str] = None
    item: Optional[str] = None
    current_hp_fraction: float = 1.0
    is_alive: bool = True
    status: Optional[str] = None
    
    def add_move(self, move_id: str):
        self.moves_seen.add(move_id)
    
    def update_hp(self, hp_fraction: float):
        self.current_hp_fraction = hp_fraction
        if hp_fraction <= 0:
            self.is_alive = False

class OpponentTracker:
    """Tracks opponent team and battle state"""
    def __init__(self):
        self.known_pokemon: Dict[str, OpponentPokemon] = {}
        self.team_preview_seen: Set[str] = set()
        self.active_pokemon_history: List[str] = []
        
    def add_pokemon(self, species: str, pokemon: Pokemon = None):
        """Add a new Pokemon to our knowledge"""
        if species not in self.known_pokemon:
            types = [str(t) for t in pokemon.types] if pokemon and pokemon.types else []
            self.known_pokemon[species] = OpponentPokemon(
                species=species,
                types=types,
                current_hp_fraction=pokemon.current_hp_fraction if pokemon else 1.0,
                is_alive=not pokemon.fainted if pokemon else True,
                status=pokemon.status.name if pokemon and pokemon.status else None
            )
            
    def update_pokemon(self, species: str, pokemon: Pokemon):
        """Update known info about a Pokemon"""
        if species not in self.known_pokemon:
            self.add_pokemon(species, pokemon)
        else:
            opp_mon = self.known_pokemon[species]
            opp_mon.current_hp_fraction = pokemon.current_hp_fraction
            opp_mon.is_alive = not pokemon.fainted
            opp_mon.status = pokemon.status.name if pokemon.status else None
            if pokemon.types:
                opp_mon.types = [str(t) for t in pokemon.types]
                
    def log_move_used(self, species: str, move_id: str):
        """Record that we saw this Pokemon use this move"""
        if species in self.known_pokemon:
            self.known_pokemon[species].add_move(move_id)
            
    def get_alive_count(self) -> int:
        """Get number of opponent Pokemon still alive"""
        return sum(1 for mon in self.known_pokemon.values() if mon.is_alive)
        
    def predict_switch_likelihood(self, current_matchup_score: float) -> float:
        """Predict how likely opponent is to switch based on matchup"""
        if current_matchup_score < -2.0:  # Very bad matchup for them
            return 0.7  # Likely to switch
        elif current_matchup_score < -1.0:  # Bad matchup
            return 0.4  # Somewhat likely
        elif current_matchup_score > 1.5:  # Good matchup for them
            return 0.1  # Very unlikely to switch
        else:
            return 0.2  # Default low chance

class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)
        
        # Battle state tracking
        self.opponent_tracker = OpponentTracker()
        self._last_switched_turn = -2
        self._tera_used = False
        self.gen_data = GenData.from_gen(9)  # Gen 9 type chart and data
        
        # Constants for decision-making
        self.ENTRY_HAZARDS = {
            "spikes": SideCondition.SPIKES,
            "stealthrock": SideCondition.STEALTH_ROCK,
            "stickyweb": SideCondition.STICKY_WEB,
            "toxicspikes": SideCondition.TOXIC_SPIKES,
        }
        self.ANTI_HAZARDS_MOVES = {"rapidspin", "defog"}
        self.SPEED_TIER_COEFICIENT = 0.1
        self.HP_FRACTION_COEFICIENT = 0.4
        self.SWITCH_OUT_MATCHUP_THRESHOLD = -2.0

    def _estimate_matchup(self, mon: Pokemon, opponent: Pokemon):
        score = max([opponent.damage_multiplier(t) for t in mon.types if t is not None])
        score -= max(
            [mon.damage_multiplier(t) for t in opponent.types if t is not None]
        )
        if mon.base_stats["spe"] > opponent.base_stats["spe"]:
            score += self.SPEED_TIER_COEFICIENT
        elif opponent.base_stats["spe"] > mon.base_stats["spe"]:
            score -= self.SPEED_TIER_COEFICIENT

        score += mon.current_hp_fraction * self.HP_FRACTION_COEFICIENT
        score -= opponent.current_hp_fraction * self.HP_FRACTION_COEFICIENT

        return score

    def _should_dynamax(self, battle: AbstractBattle, n_remaining_mons: int):
        if battle.can_dynamax:
            # Last full HP mon
            if (
                    len([m for m in battle.team.values() if m.current_hp_fraction == 1])
                    == 1
                    and battle.active_pokemon.current_hp_fraction == 1
            ):
                return True
            # Matchup advantage and full hp on full hp
            if (
                    self._estimate_matchup(
                        battle.active_pokemon, battle.opponent_active_pokemon
                    )
                    > 0
                    and battle.active_pokemon.current_hp_fraction == 1
                    and battle.opponent_active_pokemon.current_hp_fraction == 1
            ):
                return True
            if n_remaining_mons == 1:
                return True
        return False

    def _should_switch_out(self, battle: AbstractBattle):
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        # If there is a decent switch in...
        if [
            m
            for m in battle.available_switches
            if self._estimate_matchup(m, opponent) > 0
        ]:
            # ...and a 'good' reason to switch out
            if active.boosts["def"] <= -3 or active.boosts["spd"] <= -3:
                return True
            if (
                    active.boosts["atk"] <= -3
                    and active.stats["atk"] >= active.stats["spa"]
            ):
                return True
            if (
                    active.boosts["spa"] <= -3
                    and active.stats["atk"] <= active.stats["spa"]
            ):
                return True
            if (
                    self._estimate_matchup(active, opponent)
                    < self.SWITCH_OUT_MATCHUP_THRESHOLD
            ):
                return True
        return False

    def _stat_estimation(self, mon: Pokemon, stat: str):
        # Stats boosts value
        if mon.boosts[stat] > 1:
            boost = (2 + mon.boosts[stat]) / 2
        else:
            boost = 2 / (2 - mon.boosts[stat])
        return ((2 * mon.base_stats[stat] + 31) + 5) * boost

    def choose_move(self, battle: AbstractBattle):
        if isinstance(battle, DoubleBattle):
            return self.choose_random_doubles_move(battle)

        # Main mons shortcuts
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        if active is None or opponent is None:
            return self.choose_random_move(battle)

        # Rough estimation of damage ratio
        physical_ratio = self._stat_estimation(active, "atk") / self._stat_estimation(
            opponent, "def"
        )
        special_ratio = self._stat_estimation(active, "spa") / self._stat_estimation(
            opponent, "spd"
        )

        if battle.available_moves and (
                not self._should_switch_out(battle) or not battle.available_switches
        ):
            n_remaining_mons = len(
                [m for m in battle.team.values() if m.fainted is False]
            )
            n_opp_remaining_mons = 6 - len(
                [m for m in battle.opponent_team.values() if m.fainted is True]
            )

            # Entry hazard...
            for move in battle.available_moves:
                # ...setup
                if (
                        n_opp_remaining_mons >= 3
                        and move.id in self.ENTRY_HAZARDS
                        and self.ENTRY_HAZARDS[move.id]
                        not in battle.opponent_side_conditions
                ):
                    return self.create_order(move)

                # ...removal
                elif (
                        battle.side_conditions
                        and move.id in self.ANTI_HAZARDS_MOVES
                        and n_remaining_mons >= 2
                ):
                    return self.create_order(move)

            # Setup moves
            if (
                    active.current_hp_fraction == 1
                    and self._estimate_matchup(active, opponent) > 0
            ):
                for move in battle.available_moves:
                    if (
                            move.boosts
                            and sum(move.boosts.values()) >= 2
                            and move.target == "self"
                            and min(
                        [active.boosts[s] for s, v in move.boosts.items() if v > 0]
                    )
                            < 6
                    ):
                        return self.create_order(move)

            move = max(
                battle.available_moves,
                key=lambda m: m.base_power
                              * (1.5 if m.type in active.types else 1)
                              * (
                                  physical_ratio
                                  if m.category == MoveCategory.PHYSICAL
                                  else special_ratio
                              )
                              * m.accuracy
                              * m.expected_hits
                              * opponent.damage_multiplier(m),
            )
            return self.create_order(
                move, dynamax=self._should_dynamax(battle, n_remaining_mons)
            )

        if battle.available_switches:
            switches: List[Pokemon] = battle.available_switches
            return self.create_order(
                max(
                    switches,
                    key=lambda s: self._estimate_matchup(s, opponent),
                )
            )

        return self.choose_random_move(battle)
