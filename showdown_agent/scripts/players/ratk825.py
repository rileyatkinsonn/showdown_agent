from typing import List, Optional, Dict, Set
from dataclasses import dataclass, field

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
        
        # Constants for decision making
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

    def _get_type_effectiveness(self, move_type: str, defending_types: List[str]) -> float:
        """Calculate type effectiveness using GenData type chart"""
        if not move_type or not defending_types:
            return 1.0
            
        effectiveness = 1.0
        try:
            for def_type in defending_types:
                if def_type and move_type in self.gen_data.type_chart:
                    effectiveness *= self.gen_data.type_chart[move_type].damage_multiplier(def_type)
        except (KeyError, AttributeError):
            # Fallback to basic calculation if GenData fails
            effectiveness = 1.0
            
        return effectiveness
    
    def _calculate_move_damage_estimate(self, move, attacker: Pokemon, defender: Pokemon) -> float:
        """Estimate move damage using proper type effectiveness"""
        if not move.base_power:
            return 0.0
            
        # Get STAB
        stab = 1.5 if move.type and move.type in attacker.types else 1.0
        
        # Get type effectiveness
        defender_types = [str(t) for t in defender.types] if defender.types else []
        effectiveness = self._get_type_effectiveness(str(move.type), defender_types)
        
        # CRITICAL: Don't use moves that do 0 damage!
        if effectiveness == 0:
            return -1000  # Heavily penalize 0x damage moves
        
        # Basic damage estimate (simplified)
        base_damage = move.base_power * stab * effectiveness * move.accuracy
        
        return base_damage

    def _update_opponent_knowledge(self, battle: AbstractBattle):
        """Update our knowledge of opponent team"""
        # Update active Pokemon
        if battle.opponent_active_pokemon:
            active = battle.opponent_active_pokemon
            self.opponent_tracker.update_pokemon(active.species, active)
            
        # Update team knowledge from battle.opponent_team
        for mon_id, pokemon in battle.opponent_team.items():
            if pokemon:
                self.opponent_tracker.update_pokemon(pokemon.species, pokemon)

    def _estimate_matchup(self, mon: Pokemon, opponent: Pokemon) -> float:
        """Calculate matchup score between two Pokemon using proper type effectiveness"""
        if not mon or not opponent:
            return 0.0
            
        score = 0.0
        
        # Offensive advantage - how well our types hit opponent
        if mon.types and opponent.types:
            our_types = [str(t) for t in mon.types]
            opp_types = [str(t) for t in opponent.types]
            
            # Best type effectiveness we can deal
            best_offensive = max([
                self._get_type_effectiveness(our_type, opp_types)
                for our_type in our_types
            ])
            score += best_offensive
            
            # Best type effectiveness they can deal to us
            best_defensive = max([
                self._get_type_effectiveness(opp_type, our_types)
                for opp_type in opp_types
            ])
            score -= best_defensive
        
        # Speed advantage
        if mon.base_stats["spe"] > opponent.base_stats["spe"]:
            score += self.SPEED_TIER_COEFICIENT
        elif opponent.base_stats["spe"] > mon.base_stats["spe"]:
            score -= self.SPEED_TIER_COEFICIENT

        # HP advantage
        score += mon.current_hp_fraction * self.HP_FRACTION_COEFICIENT
        score -= opponent.current_hp_fraction * self.HP_FRACTION_COEFICIENT

        return score

    def _should_terastallize(self, battle: AbstractBattle) -> bool:
        """Decide whether to use Tera this turn - more aggressive"""
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        if not battle.can_tera or not active or not opponent or self._tera_used:
            return False

        current_matchup = self._estimate_matchup(active, opponent)
        n_remaining = sum(1 for p in battle.team.values() if not p.fainted)

        # Emergency defensive Tera - more lenient HP threshold
        if (active.current_hp_fraction < 0.6 and
                opponent.types and
                max([active.damage_multiplier(t) for t in opponent.types if t]) >= 1.0):
            return True

        # Offensive Tera for potential KO - more aggressive
        if (opponent.current_hp_fraction < 0.5 and
                current_matchup <= 0.5 and
                active.current_hp_fraction > 0.4):
            return True

        # Setup Tera when safe - earlier in game
        if (active.current_hp_fraction > 0.8 and
                any(move.boosts and sum(move.boosts.values()) >= 2
                    for move in battle.available_moves if move.boosts)):
            return True
            
        # Endgame Tera - use it if only 2 Pokemon left
        if n_remaining <= 2 and active.current_hp_fraction > 0.3:
            return True

        # Specific Pokemon Tera strategies
        if active.species == "koraidon" and opponent.species in ["arceus-fairy", "zacian-crowned"]:
            return True  # Fire Tera vs Steel/Fairy
        if active.species == "eternatus" and opponent.species == "kingambit":
            return True  # Fire Tera to resist Dark moves

        return False

    def _should_switch_out(self, battle: AbstractBattle) -> bool:
        """Decide whether to switch out current Pokemon - improved logic"""
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        if not active or not opponent:
            return False

        # Never switch if just switched (prevent switching loops)
        if battle.turn <= 1 or self._last_switched_turn >= battle.turn - 1:
            return False

        current_matchup = self._estimate_matchup(active, opponent)
        
        # Check if we have a good switch option
        good_switches = [
            (mon, self._estimate_matchup(mon, opponent)) 
            for mon in battle.available_switches
        ]
        
        if not good_switches:
            return False
            
        best_switch_matchup = max(good_switches, key=lambda x: x[1])[1]
        
        # Don't switch if we don't have a significantly better option
        if best_switch_matchup <= current_matchup + 0.5:
            return False

        # Switch if severely debuffed
        if (active.boosts["def"] <= -3 or active.boosts["spd"] <= -3 or
            (active.boosts["atk"] <= -3 and active.stats["atk"] >= active.stats["spa"]) or
            (active.boosts["spa"] <= -3 and active.stats["atk"] <= active.stats["spa"])):
            return True

        # Switch if matchup is terrible and we have much better option
        if (current_matchup < self.SWITCH_OUT_MATCHUP_THRESHOLD and 
            best_switch_matchup > current_matchup + 1.0):
            return True
            
        # Switch if we're about to get KO'd and have a better option
        if (active.current_hp_fraction < 0.3 and
            current_matchup < 0 and
            best_switch_matchup > 0.5):
            return True
            
        # Specific bad matchup switches
        if (active.species == "eternatus" and opponent.species == "zacian-crowned" and
            any(mon.species in ["koraidon", "kingambit"] for mon in battle.available_switches)):
            return True
            
        if (active.species == "koraidon" and opponent.species == "arceus-fairy" and
            any(mon.species == "kingambit" for mon in battle.available_switches)):
            return True

        return False

    def _get_move_priority(self, move, battle: AbstractBattle) -> float:
        """Calculate priority score for a move using proper damage estimation"""
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        
        if not active or not opponent:
            return 0.0

        score = 0.0
        
        # Use proper damage calculation for attacking moves
        if move.base_power:
            damage_score = self._calculate_move_damage_estimate(move, active, opponent)
            if damage_score == -1000:  # 0x damage move
                return -1000  # Never use these moves
            score += damage_score
            
        # Don't use status moves that won't work
        if move.id == "thunderwave" and opponent.status:
            return -500  # Already statused
        if move.id in self.ENTRY_HAZARDS:
            if self.ENTRY_HAZARDS[move.id] in battle.opponent_side_conditions:
                return -500  # Hazards already up
                
        # Prioritize KO moves when opponent is low
        if (opponent.current_hp_fraction < 0.35 and 
            move.base_power and move.base_power > 80):
            score *= 2.0
            
        # Setup move bonus when safe
        if (move.boosts and move.target == "self" and 
            active.current_hp_fraction > 0.8 and
            self._estimate_matchup(active, opponent) > 0):
            # Check if we can still boost this stat
            can_boost = any(
                active.boosts.get(stat, 0) < 6 
                for stat, boost in move.boosts.items() 
                if boost > 0
            )
            if can_boost:
                score += 100
            else:
                score -= 200  # Can't boost anymore
            
        # Hazard moves get priority when appropriate
        if (move.id in self.ENTRY_HAZARDS and 
            self.opponent_tracker.get_alive_count() >= 4 and
            self.ENTRY_HAZARDS[move.id] not in battle.opponent_side_conditions):
            score += 75
            
        # Status move penalties unless specific cases
        if (move.base_power == 0 and not move.boosts and 
            move.id not in self.ENTRY_HAZARDS and move.id != "thunderwave"):
            score -= 50
            
        return score

    def _predict_opponent_action(self, battle: AbstractBattle) -> str:
        """Simple prediction based on matchup and game state"""
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        
        if not active or not opponent:
            return "stay"
            
        matchup_from_opp_perspective = self._estimate_matchup(opponent, active)
        
        # If opponent is in a very bad matchup, they'll likely switch
        if matchup_from_opp_perspective < -1.5:
            return "switch"
        # If opponent is in a good matchup, they'll likely stay and attack
        elif matchup_from_opp_perspective > 1.0:
            return "attack"
        # If opponent is low HP, they might switch to preserve the Pokemon
        elif opponent.current_hp_fraction < 0.3:
            return "switch"
        else:
            return "attack"
    
    def _adjust_move_for_prediction(self, move, battle: AbstractBattle) -> float:
        """Adjust move priority based on opponent prediction"""
        prediction = self._predict_opponent_action(battle)
        base_score = self._get_move_priority(move, battle)
        
        # If we predict they'll switch and our move hits the switch-in hard
        if prediction == "switch" and move.base_power and move.base_power > 80:
            # Favor powerful moves that can hit common switch-ins
            if move.type and str(move.type) in ["fire", "fighting", "steel"]:  # Good coverage
                base_score *= 1.2
                
        # If we predict they'll attack, favor defensive plays or super effective hits
        elif prediction == "attack":
            if move.boosts and move.target == "self":  # Setup moves
                base_score *= 0.8  # Less safe if they're attacking
            elif move.base_power and self._get_type_effectiveness(str(move.type), 
                [str(t) for t in battle.opponent_active_pokemon.types]) > 1.0:
                base_score *= 1.3  # Super effective moves
                
        return base_score

    def choose_move(self, battle: AbstractBattle) -> BattleOrder:
        """Main decision making method"""
        if isinstance(battle, DoubleBattle):
            return self.choose_random_doubles_move(battle)

        # Update opponent knowledge
        self._update_opponent_knowledge(battle)
        
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        if not active or not opponent:
            return self.choose_random_move(battle)

        # Decide on Tera usage
        should_tera = self._should_terastallize(battle)
        if should_tera:
            self._tera_used = True

        n_remaining = sum(1 for p in battle.team.values() if not p.fainted)
        n_opp_remaining = self.opponent_tracker.get_alive_count() or 6

        # PRIORITY 1: KO moves when opponent is low HP
        if opponent.current_hp_fraction < 0.35 and battle.available_moves:
            best_attack = max(
                battle.available_moves,
                key=lambda m: self._calculate_move_damage_estimate(m, active, opponent)
            )
            return self.create_order(best_attack, terastallize=should_tera)

        # PRIORITY 2: Check if we should switch
        if self._should_switch_out(battle) and battle.available_switches:
            self._last_switched_turn = battle.turn
            best_switch = max(
                battle.available_switches,
                key=lambda s: self._estimate_matchup(s, opponent)
            )
            return self.create_order(best_switch)

        # PRIORITY 3: Hazard setup when beneficial
        if n_opp_remaining >= 4 and battle.available_moves:
            for move in battle.available_moves:
                if (move.id in self.ENTRY_HAZARDS and 
                    self.ENTRY_HAZARDS[move.id] not in battle.opponent_side_conditions):
                    return self.create_order(move, terastallize=should_tera)

        # PRIORITY 4: Hazard removal when needed
        if battle.side_conditions and n_remaining >= 2:
            for move in battle.available_moves:
                if move.id in self.ANTI_HAZARDS_MOVES:
                    return self.create_order(move, terastallize=should_tera)

        # PRIORITY 5: Setup moves when safe
        if (active.current_hp_fraction >= 0.8 and 
            self._estimate_matchup(active, opponent) > 0.5):
            for move in battle.available_moves:
                if (move.boosts and move.target == "self" and
                    sum(move.boosts.values()) >= 2):
                    # Check if we can still boost this stat
                    can_boost = any(
                        active.boosts.get(stat, 0) < 6 
                        for stat, boost in move.boosts.items() 
                        if boost > 0
                    )
                    if can_boost:
                        return self.create_order(move, terastallize=should_tera)

        # PRIORITY 6: Best attacking move (with prediction)
        if battle.available_moves:
            best_move = max(battle.available_moves, key=lambda m: self._adjust_move_for_prediction(m, battle))
            return self.create_order(best_move, terastallize=should_tera)

        # Fallback
        return self.choose_random_move(battle)