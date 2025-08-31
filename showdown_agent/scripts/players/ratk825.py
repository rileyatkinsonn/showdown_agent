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
    switch_in_count: int = 0
    turns_active: int = 0
    damage_dealt: float = 0.0
    damage_taken: float = 0.0
    
    def add_move(self, move_id: str):
        self.moves_seen.add(move_id)
    
    def update_hp(self, hp_fraction: float):
        old_hp = self.current_hp_fraction
        self.current_hp_fraction = hp_fraction
        if hp_fraction <= 0:
            self.is_alive = False
        elif old_hp > hp_fraction:
            self.damage_taken += old_hp - hp_fraction
    
    def record_switch_in(self):
        self.switch_in_count += 1
    
    def record_turn_active(self):
        self.turns_active += 1

@dataclass
class OpponentProfile:
    """Tracks opponent's behavioral patterns"""
    risk_tolerance: float = 0.5  # 0 = risk averse, 1 = risk loving
    aggression_level: float = 0.5  # 0 = passive, 1 = aggressive
    switching_frequency: float = 0.5  # How often they switch
    setup_preference: float = 0.5  # How much they like setup moves
    prediction_attempts: int = 0  # How many times they try to predict us
    successful_predictions: int = 0
    total_turns: int = 0
    switches_made: int = 0
    setup_moves_used: int = 0
    risky_plays: int = 0
    
    def update_switching(self, switched: bool):
        self.total_turns += 1
        if switched:
            self.switches_made += 1
        self.switching_frequency = self.switches_made / max(1, self.total_turns)
    
    def update_risk_profile(self, was_risky: bool, paid_off: bool):
        if was_risky:
            self.risky_plays += 1
            if paid_off:
                self.risk_tolerance = min(1.0, self.risk_tolerance + 0.1)
            else:
                self.risk_tolerance = max(0.0, self.risk_tolerance - 0.05)
    
    def record_setup_move(self):
        self.setup_moves_used += 1
        self.setup_preference = min(1.0, self.setup_moves_used / max(1, self.total_turns))

class OpponentTracker:
    """Tracks opponent team and battle state"""
    def __init__(self):
        self.known_pokemon: Dict[str, OpponentPokemon] = {}
        self.team_preview_seen: Set[str] = set()
        self.active_pokemon_history: List[str] = []
        self.profile = OpponentProfile()
        self.turn_history: List[Dict] = []  # Track each turn's events
        self.last_active_pokemon: Optional[str] = None
        
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
            
            # Check if this is a switch
            if self.last_active_pokemon and self.last_active_pokemon != species:
                opp_mon.record_switch_in()
                self.profile.update_switching(True)
            else:
                self.profile.update_switching(False)
                
            opp_mon.update_hp(pokemon.current_hp_fraction)
            opp_mon.is_alive = not pokemon.fainted
            opp_mon.status = pokemon.status.name if pokemon.status else None
            if pokemon.types:
                opp_mon.types = [str(t) for t in pokemon.types]
            
            opp_mon.record_turn_active()
            self.last_active_pokemon = species
                
    def log_move_used(self, species: str, move_id: str, was_risky: bool = False, was_setup: bool = False):
        """Record that we saw this Pokemon use this move"""
        if species in self.known_pokemon:
            self.known_pokemon[species].add_move(move_id)
            
            if was_setup:
                self.profile.record_setup_move()
            
            # Record turn event
            turn_data = {
                'pokemon': species,
                'move': move_id,
                'risky': was_risky,
                'setup': was_setup
            }
            self.turn_history.append(turn_data)
            
    def get_alive_count(self) -> int:
        """Get number of opponent Pokemon still alive"""
        return sum(1 for mon in self.known_pokemon.values() if mon.is_alive)
    
    def get_switch_pattern_score(self, current_species: str) -> float:
        """Analyze opponent's switching patterns for this Pokemon"""
        if current_species not in self.known_pokemon:
            return 0.5
        
        mon = self.known_pokemon[current_species]
        if mon.turns_active == 0:
            return 0.5
        
        # Pokemon that switch in frequently are more likely to switch again
        switch_tendency = mon.switch_in_count / max(1, mon.turns_active)
        return min(1.0, switch_tendency * 2)
        
    def predict_switch_likelihood(self, current_matchup_score: float, current_species: str) -> float:
        """Enhanced switch prediction using opponent profiling"""
        base_likelihood = 0.2
        
        # Matchup-based prediction
        if current_matchup_score < -2.0:
            base_likelihood = 0.8
        elif current_matchup_score < -1.0:
            base_likelihood = 0.6
        elif current_matchup_score > 1.5:
            base_likelihood = 0.1
        
        # Adjust based on opponent profile
        profile_modifier = 0.0
        
        # Risk-averse players switch more in bad matchups
        if self.profile.risk_tolerance < 0.3 and current_matchup_score < 0:
            profile_modifier += 0.2
        
        # Aggressive players stay in more often
        if self.profile.aggression_level > 0.7:
            profile_modifier -= 0.15
        
        # Factor in this Pokemon's switching history
        switch_pattern = self.get_switch_pattern_score(current_species)
        profile_modifier += (switch_pattern - 0.5) * 0.3
        
        # Factor in overall switching frequency
        if self.profile.switching_frequency > 0.4:
            profile_modifier += 0.1
        
        final_likelihood = max(0.0, min(1.0, base_likelihood + profile_modifier))
        return final_likelihood
    
    def predict_move_choice(self, current_species: str, available_moves: List[str], our_pokemon: Pokemon) -> Dict[str, float]:
        """Predict what move the opponent is likely to use"""
        if current_species not in self.known_pokemon:
            return {move: 1.0/len(available_moves) for move in available_moves}
        
        mon = self.known_pokemon[current_species]
        predictions = {}
        
        for move in available_moves:
            score = 0.25  # Base probability
            
            # If we've seen this move before, they might use it again
            if move in mon.moves_seen:
                score += 0.3
            
            # Aggressive opponents prefer damaging moves
            if self.profile.aggression_level > 0.6:
                if 'attack' in move.lower() or 'punch' in move.lower() or 'blast' in move.lower():
                    score += 0.2
            
            # Setup-loving opponents prefer stat-boosting moves
            if self.profile.setup_preference > 0.3:
                if 'dance' in move.lower() or 'calm' in move.lower() or 'agility' in move.lower():
                    score += 0.3
                    
            predictions[move] = score
        
        # Normalize probabilities
        total = sum(predictions.values())
        return {move: prob/total for move, prob in predictions.items()}

class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)
        
        # Battle state tracking
        self.opponent_tracker = OpponentTracker()
        self._last_switched_turn = -2
        self._tera_used = False
        self.gen_data = GenData.from_gen(9)  # Gen 9 type chart and data
        self.turn_count = 0
        self.our_last_move = None
        self.opponent_last_move = None
        
        # Constants for decision-making
        self.ENTRY_HAZARDS = {
            "spikes": SideCondition.SPIKES,
            "stealthrock": SideCondition.STEALTH_ROCK,
            "stickyweb": SideCondition.STICKY_WEB,
            "toxicspikes": SideCondition.TOXIC_SPIKES,
        }
        self.ANTI_HAZARDS_MOVES = {"rapidspin", "defog"}
        self.SETUP_MOVES = {"swordsdance", "nastyplot", "calmmind", "dragondance", "agility"}
        self.SPEED_TIER_COEFICIENT = 0.1
        self.HP_FRACTION_COEFICIENT = 0.4
        self.SWITCH_OUT_MATCHUP_THRESHOLD = -1.5  # Made less conservative
        
        # Game theory parameters
        self.PREDICTION_CONFIDENCE_THRESHOLD = 0.7
        self.RISK_REWARD_THRESHOLD = 1.2

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

        # Enhanced matchup evaluation with more factors
        
        # Speed control bonus
        speed_diff = mon.base_stats["spe"] - opponent.base_stats["spe"]
        if speed_diff > 20:  # Significant speed advantage
            score += 0.3
        elif speed_diff < -20:  # Significant speed disadvantage
            score -= 0.3
        
        # Ability synergies (simplified)
        if hasattr(mon, 'ability') and mon.ability:
            ability_name = str(mon.ability).lower()
            if 'intimidate' in ability_name and opponent.stats["atk"] > opponent.stats["spa"]:
                score += 0.2
            elif 'pressure' in ability_name:
                score += 0.1
        
        return score

    def _should_tera(self, battle: AbstractBattle, n_remaining_mons: int):
        if not battle.can_tera:
            return False
            
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        
        if not active or not opponent:
            return False
        
        current_matchup = self._estimate_matchup(active, opponent)
        
        # Critical situations - always tera
        if n_remaining_mons == 1:
            return True
        
        # Last full HP mon
        full_hp_mons = [m for m in battle.team.values() if m.current_hp_fraction == 1]
        if len(full_hp_mons) == 1 and active.current_hp_fraction == 1:
            return True
        
        # Enhanced tera logic with opponent profiling
        if current_matchup > 0.5 and active.current_hp_fraction >= 0.8:
            # Against aggressive opponents, tera early to secure advantage
            if self.opponent_tracker.profile.aggression_level > 0.7:
                return True
            
            # Against setup-heavy opponents, tera to prevent their setup
            if self.opponent_tracker.profile.setup_preference > 0.4:
                return True
                
            # Standard good matchup tera
            if (
                active.current_hp_fraction == 1 and 
                opponent.current_hp_fraction == 1
            ):
                return True
        
        # Defensive tera when in trouble
        if current_matchup < -1.0 and active.current_hp_fraction < 0.5:
            # Check if tera would significantly improve our defensive matchup
            # This is a simplification - would need more complex type analysis
            if n_remaining_mons <= 2:
                return True
        
        return False

    def _should_switch_out(self, battle: AbstractBattle):
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        
        # Update opponent tracking
        if opponent:
            self.opponent_tracker.update_pokemon(opponent.species, opponent)
        
        # If there is a decent switch in...
        good_switches = [
            m for m in battle.available_switches
            if self._estimate_matchup(m, opponent) > 0
        ]
        
        if good_switches:
            current_matchup = self._estimate_matchup(active, opponent)
            
            # Enhanced switching logic with game theory
            switch_likelihood = self.opponent_tracker.predict_switch_likelihood(
                -current_matchup,  # Negative because it's from their perspective
                opponent.species
            )
            
            # Standard reasons to switch
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
            
            # Enhanced matchup-based switching
            if current_matchup < self.SWITCH_OUT_MATCHUP_THRESHOLD:
                # If opponent is likely to switch, we might want to stay to punish
                if switch_likelihood > 0.6 and current_matchup > -2.5:
                    return False  # Stay to catch their switch
                return True
            
            # Mind games: sometimes switch when they don't expect it
            if (
                current_matchup > -0.5 and switch_likelihood < 0.3 and 
                self.opponent_tracker.profile.risk_tolerance > 0.6 and
                len(good_switches) > 0
            ):
                # Occasional unexpected switch to throw off predictable opponents
                return True
        
        return False

    def _stat_estimation(self, mon: Pokemon, stat: str):
        # Stats boosts value
        if mon.boosts[stat] > 1:
            boost = (2 + mon.boosts[stat]) / 2
        else:
            boost = 2 / (2 - mon.boosts[stat])
        return ((2 * mon.base_stats[stat] + 31) + 5) * boost

    def _calculate_move_value(self, move, active: Pokemon, opponent: Pokemon, battle: AbstractBattle):
        """Enhanced move evaluation with risk/reward analysis"""
        physical_ratio = self._stat_estimation(active, "atk") / self._stat_estimation(opponent, "def")
        special_ratio = self._stat_estimation(active, "spa") / self._stat_estimation(opponent, "spd")
        
        base_value = (
            move.base_power
            * (1.5 if move.type in active.types else 1)
            * (physical_ratio if move.category == MoveCategory.PHYSICAL else special_ratio)
            * move.accuracy
            * move.expected_hits
            * opponent.damage_multiplier(move)
        )
        
        # Factor in opponent's likely response
        switch_likelihood = self.opponent_tracker.predict_switch_likelihood(
            self._estimate_matchup(active, opponent),
            opponent.species
        )
        
        # If they're likely to switch, powerful moves are less valuable
        if switch_likelihood > 0.6:
            base_value *= 0.7
        
        # Bonus for moves that force switches
        if base_value > opponent.current_hp * 0.8:  # Near-OHKO
            base_value *= 1.3
        
        return base_value
    
    def _should_predict_switch(self, battle: AbstractBattle) -> Optional[Pokemon]:
        """Decide whether to make a prediction play"""
        opponent = battle.opponent_active_pokemon
        if not opponent:
            return None
        
        current_matchup = self._estimate_matchup(battle.active_pokemon, opponent)
        switch_likelihood = self.opponent_tracker.predict_switch_likelihood(
            -current_matchup, opponent.species
        )
        
        if switch_likelihood > self.PREDICTION_CONFIDENCE_THRESHOLD:
            # Find the best counter to their most likely switch-in
            available_switches = [m for m in battle.available_switches if not m.fainted]
            if available_switches:
                # Predict their most likely switch based on our team
                best_prediction = None
                best_score = -999
                
                for potential_switch in available_switches:
                    # They'll likely switch to something that beats us
                    matchup_vs_us = self._estimate_matchup(potential_switch, battle.active_pokemon)
                    if matchup_vs_us > best_score:
                        best_score = matchup_vs_us
                        best_prediction = potential_switch
                
                return best_prediction
        
        return None

    def choose_move(self, battle: AbstractBattle):
        self.turn_count += 1
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        if active is None or opponent is None:
            return self.choose_random_move(battle)

        # Update opponent tracking
        if opponent:
            self.opponent_tracker.update_pokemon(opponent.species, opponent)
            
        # Track opponent's last move if we can infer it
        if hasattr(battle, 'opponent_active_pokemon') and battle.opponent_active_pokemon:
            # This is a simplified way - in a real implementation you'd parse the battle log
            pass

        if battle.available_moves and (
                not self._should_switch_out(battle) or not battle.available_switches
        ):
            n_remaining_mons = len([m for m in battle.team.values() if m.fainted is False])
            n_opp_remaining_mons = 6 - len([m for m in battle.opponent_team.values() if m.fainted is True])

            # Check if we should make a prediction play
            predicted_switch = self._should_predict_switch(battle)
            if predicted_switch and battle.available_moves:
                # Use a move that's good against their predicted switch
                prediction_moves = [
                    move for move in battle.available_moves
                    if predicted_switch.damage_multiplier(move) > 1.5
                ]
                if prediction_moves:
                    best_prediction_move = max(
                        prediction_moves,
                        key=lambda m: self._calculate_move_value(m, active, predicted_switch, battle)
                    )
                    # But only if it's significantly better than our normal play
                    normal_best = max(
                        battle.available_moves,
                        key=lambda m: self._calculate_move_value(m, active, opponent, battle)
                    )
                    prediction_value = self._calculate_move_value(best_prediction_move, active, predicted_switch, battle)
                    normal_value = self._calculate_move_value(normal_best, active, opponent, battle)
                    
                    if prediction_value > normal_value * self.RISK_REWARD_THRESHOLD:
                        return self.create_order(best_prediction_move)

            # Entry hazards with enhanced logic
            for move in battle.available_moves:
                if (
                        n_opp_remaining_mons >= 3
                        and move.id in self.ENTRY_HAZARDS
                        and self.ENTRY_HAZARDS[move.id] not in battle.opponent_side_conditions
                ):
                    # More likely to set hazards against switching-heavy opponents
                    if self.opponent_tracker.profile.switching_frequency > 0.3:
                        return self.create_order(move)
                    elif n_opp_remaining_mons >= 4:  # Only if they have many mons left
                        return self.create_order(move)

                # Hazard removal
                elif (
                        battle.side_conditions
                        and move.id in self.ANTI_HAZARDS_MOVES
                        and n_remaining_mons >= 2
                ):
                    return self.create_order(move)

            # Enhanced setup logic
            current_matchup = self._estimate_matchup(active, opponent)
            if (
                    active.current_hp_fraction >= 0.8
                    and current_matchup > 0.5
            ):
                setup_moves = [
                    move for move in battle.available_moves
                    if (move.boosts
                        and sum(move.boosts.values()) >= 2
                        and move.target == "self"
                        and min([active.boosts[s] for s, v in move.boosts.items() if v > 0]) < 6)
                ]
                
                if setup_moves:
                    # Less likely to setup against aggressive opponents who won't let us
                    if self.opponent_tracker.profile.aggression_level < 0.6:
                        return self.create_order(setup_moves[0])
                    elif current_matchup > 1.0:  # Only if we have a really good matchup
                        return self.create_order(setup_moves[0])

            # Choose best attacking move with enhanced evaluation
            best_move = max(
                battle.available_moves,
                key=lambda m: self._calculate_move_value(m, active, opponent, battle)
            )
            
            should_tera = self._should_tera(battle, n_remaining_mons)
            return self.create_order(best_move, terastallize=should_tera)

        if battle.available_switches:
            switches: List[Pokemon] = battle.available_switches
            
            # Enhanced switch selection
            switch_scores = {}
            for switch in switches:
                base_score = self._estimate_matchup(switch, opponent)
                
                # Bonus for unexpected switches against predictable opponents
                if self.opponent_tracker.profile.risk_tolerance < 0.4:
                    base_score += 0.2
                
                switch_scores[switch] = base_score
            
            best_switch = max(switches, key=lambda s: switch_scores[s])
            return self.create_order(best_switch)

        return self.choose_random_move(battle)
