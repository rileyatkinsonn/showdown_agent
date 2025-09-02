from typing import List, Optional, Dict, Set
from dataclasses import dataclass, field

from poke_env.battle import MoveCategory, Target
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
            types = [t.name.lower() for t in pokemon.types] if pokemon and pokemon.types else []
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
                opp_mon.types = [t.name.lower() for t in pokemon.types]

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
            return {move: 1.0 / len(available_moves) for move in available_moves}

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
        return {move: prob / total for move, prob in predictions.items()}


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
        
        # Performance tracking
        self.battles_won = 0
        self.battles_total = 0
        self.predictions_made = 0
        self.predictions_correct = 0
        self._last_opponent_species = None
        self._predicted_switch_last_turn = False
        self.battle_count = 0  # Track number of battles for lead selection
        self.battles_seen = set()  # Track which battles we've seen
        self.opponent_lead_history = {}  # Track opponent's lead patterns

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
        """Basic move evaluation - minimax will handle opponent predictions"""
        physical_ratio = self._stat_estimation(active, "atk") / self._stat_estimation(opponent, "def")
        special_ratio = self._stat_estimation(active, "spa") / self._stat_estimation(opponent, "spd")

        acc = move.accuracy if move.accuracy is not None else 1.0
        hits = getattr(move, "n_damaging_hits", 1)
        bp = move.base_power or 0
        stab = 1.5 if (move.type and move.type in active.types) else 1.0
        base_value = bp * stab * (
            physical_ratio if move.category == MoveCategory.PHYSICAL else special_ratio) * acc * hits * opponent.damage_multiplier(
            move)

        return base_value
    
    def _predict_opponent_response(self, our_move, active: Pokemon, opponent: Pokemon, battle: AbstractBattle) -> Dict[str, float]:
        """Predict opponent's most likely responses to our move using learned data"""
        responses = {}
        
        # Get opponent's known moves and behavioral patterns
        known_pokemon = self.opponent_tracker.known_pokemon.get(opponent.species)
        if not known_pokemon:
            # No data yet - use species defaults
            return self._get_default_opponent_responses(opponent.species, our_move)
        
        # Predict based on learned move patterns
        predicted_moves = self._predict_likely_moves(opponent.species, active)
        
        # Estimate opponent's best responses
        total_probability = 0.0
        
        # 1. Switching likelihood
        current_matchup = self._estimate_matchup(opponent, active)
        switch_likelihood = self.opponent_tracker.predict_switch_likelihood(current_matchup, opponent.species)
        
        if switch_likelihood > 0.3:  # They might switch
            # Find their best switch-in against us
            best_switch = self._predict_best_switch_in(battle)
            if best_switch:
                responses[f"switch_to_{best_switch}"] = switch_likelihood
                total_probability += switch_likelihood
        
        # 2. Stay and attack with likely moves
        stay_probability = 1.0 - switch_likelihood
        if stay_probability > 0:
            for move_type, move_prob in predicted_moves.items():
                adjusted_prob = move_prob * stay_probability
                
                # Adjust probability based on our move
                if our_move:
                    if 'setup' in move_type and our_move.base_power > opponent.current_hp * 0.6:
                        # Less likely to setup if we can KO them
                        adjusted_prob *= 0.3
                    elif 'priority' in move_type and our_move.priority <= 0 and active.current_hp_fraction < 0.5:
                        # More likely to use priority if we're low HP and moving first
                        adjusted_prob *= 1.5
                    elif 'attacking' in move_type and current_matchup < 0:
                        # More likely to attack if they have advantage
                        adjusted_prob *= 1.3
                
                responses[move_type] = adjusted_prob
                total_probability += adjusted_prob
        
        # Normalize probabilities
        if total_probability > 0:
            responses = {resp: prob/total_probability for resp, prob in responses.items()}
        
        return responses
    
    def _get_default_opponent_responses(self, species: str, our_move) -> Dict[str, float]:
        """Default responses when we have no learned data"""
        species_lower = species.lower().replace('-', '')
        
        # Species-specific response patterns
        if 'kingambit' in species_lower:
            if our_move and our_move.base_power == 0:  # Non-attacking move
                return {'sucker_punch': 0.7, 'setup_attack': 0.2, 'switch': 0.1}
            else:
                return {'attacking_move': 0.6, 'sucker_punch': 0.3, 'switch': 0.1}
        elif 'zaciancrowned' in species_lower:
            return {'attacking_move': 0.7, 'setup_swords_dance': 0.2, 'switch': 0.1}
        elif 'deoxysspeed' in species_lower:
            return {'status_move': 0.5, 'hazard_move': 0.3, 'switch': 0.2}
        else:
            return {'attacking_move': 0.6, 'setup_move': 0.2, 'switch': 0.2}
    
    def _minimax_evaluate_move(self, move, active: Pokemon, opponent: Pokemon, battle: AbstractBattle, depth: int = 1) -> float:
        """Minimax-style move evaluation using learned opponent data"""
        if depth <= 0:
            return self._calculate_move_value(move, active, opponent, battle)
        
        # Our move value
        our_move_value = self._calculate_move_value(move, active, opponent, battle)
        
        # Predict opponent responses
        opponent_responses = self._predict_opponent_response(move, active, opponent, battle)
        
        # Calculate expected value considering opponent's best response
        total_expected_value = 0.0
        
        for response, probability in opponent_responses.items():
            # Estimate the outcome after opponent's response
            response_penalty = 0.0
            
            if 'switch' in response:
                # Opponent switches - our move hits current target, they bring in counter
                switch_penalty = 0.2  # General switching penalty
                if 'switch_to_' in response:
                    switch_target = response.replace('switch_to_', '')
                    if switch_target in self.opponent_tracker.known_pokemon:
                        switch_types = self.opponent_tracker.known_pokemon[switch_target].types
                        if switch_types:
                            # Check if their switch-in resists our move
                            effectiveness = self._get_type_effectiveness(move.type.name, switch_types)
                            if effectiveness < 1.0:
                                switch_penalty = 0.4  # They switched to a resist
                response_penalty = switch_penalty
                
            elif 'sucker_punch' in response:
                if move.base_power == 0:
                    # They used Sucker Punch on our status move - it fails
                    response_penalty = -0.3  # This is good for us
                else:
                    # They hit us with Sucker Punch
                    response_penalty = 0.4
                    
            elif 'setup' in response:
                # They used a setup move - bad for us long term
                response_penalty = 0.5
                
            elif 'attacking' in response:
                # They attacked - estimate damage ratio
                if active.current_hp_fraction < 0.3:
                    response_penalty = 0.6  # We might get KOed
                else:
                    response_penalty = 0.2  # Normal damage trade
                    
            elif 'status' in response or 'hazard' in response:
                # Status/hazard moves - moderate penalty
                response_penalty = 0.3
            
            # Weight the penalty by probability
            total_expected_value += (our_move_value - response_penalty * our_move_value) * probability
        
        return total_expected_value

    def _should_predict_switch(self, battle: AbstractBattle) -> Optional[str]:
        """Passive prediction - just track data without making risky plays"""
        opponent = battle.opponent_active_pokemon
        if not opponent:
            return None

        # Only predict for data collection, don't act on it aggressively
        current_matchup = self._estimate_matchup(battle.active_pokemon, opponent)
        switch_likelihood = self.opponent_tracker.predict_switch_likelihood(
            -current_matchup, opponent.species
        )

        # Just predict the most likely switch for learning purposes
        if switch_likelihood > 0.5:
            return self._predict_best_switch_in(battle)

        return None
    
    def _parse_battle_events(self, battle: AbstractBattle):
        """Enhanced battle event parsing with better move detection"""
        opponent = battle.opponent_active_pokemon
        if not opponent:
            return
            
        # Enhanced move detection using multiple signals
        if hasattr(battle, 'turn') and battle.turn > 1:
            move_detected = False
            was_risky = False
            was_setup = False
            
            # Check HP changes
            if battle.active_pokemon and hasattr(self, '_our_last_hp'):
                our_current_hp = battle.active_pokemon.current_hp_fraction
                hp_change = self._our_last_hp - our_current_hp
                
                if hp_change > 0.1:  # Significant damage taken
                    self._infer_opponent_move_type(opponent.species, "strong_attack")
                    move_detected = True
                    was_risky = hp_change > 0.3  # Big damage = risky play
                elif hp_change > 0.01:  # Minor damage
                    self._infer_opponent_move_type(opponent.species, "weak_attack")
                    move_detected = True
                    
            # Check status changes
            if battle.active_pokemon:
                current_status = battle.active_pokemon.status.name if battle.active_pokemon.status else None
                last_status = getattr(self, '_our_last_status', None)
                if current_status != last_status:
                    if current_status:  # Status was inflicted
                        self._infer_opponent_move_type(opponent.species, f"status_{current_status}")
                        move_detected = True
                        
            # Check stat changes (boosts)
            if battle.active_pokemon:
                our_last_boosts = getattr(self, '_our_last_boosts', {})
                for stat, boost in battle.active_pokemon.boosts.items():
                    old_boost = our_last_boosts.get(stat, 0)
                    if boost != old_boost:
                        if boost < old_boost:  # Stat was lowered
                            self._infer_opponent_move_type(opponent.species, f"stat_drop_{stat}")
                            move_detected = True
                            
            # Check opponent stat changes (they might have used setup)
            opp_last_boosts = getattr(self, '_opp_last_boosts', {})
            for stat, boost in opponent.boosts.items():
                old_boost = opp_last_boosts.get(stat, 0)
                if boost > old_boost:  # Opponent boosted stats
                    self._infer_opponent_move_type(opponent.species, f"setup_{stat}")
                    move_detected = True
                    was_setup = True
                        
            # If no clear move detected but opponent was active, assume neutral move
            if not move_detected and self.turn_count > 1:
                self._infer_opponent_move_type(opponent.species, "unknown_move")
                
        # Store current state for next turn
        if battle.active_pokemon:
            self._our_last_hp = battle.active_pokemon.current_hp_fraction
            self._our_last_status = battle.active_pokemon.status.name if battle.active_pokemon.status else None
            self._our_last_boosts = dict(battle.active_pokemon.boosts)
        else:
            self._our_last_hp = 1.0
            self._our_last_status = None
            self._our_last_boosts = {}
            
        if opponent:
            self._opp_last_boosts = dict(opponent.boosts)
        else:
            self._opp_last_boosts = {}
    
    def _learn_from_team_preview(self, battle: AbstractBattle):
        """Learn opponent's team composition during team preview/early battle"""
        if hasattr(battle, 'opponent_team') and battle.opponent_team:
            for species, pokemon in battle.opponent_team.items():
                self.opponent_tracker.add_pokemon(species, pokemon)
                self.opponent_tracker.team_preview_seen.add(species)
                
        # Also learn from any visible opponent Pokemon
        if battle.opponent_active_pokemon:
            species = battle.opponent_active_pokemon.species
            if species not in self.opponent_tracker.known_pokemon:
                self.opponent_tracker.add_pokemon(species, battle.opponent_active_pokemon)
    
    def _infer_opponent_move_type(self, species: str, move_type: str):
        """Record inferred move usage with enhanced categorization"""
        was_risky = False
        was_setup = False
        
        if "strong_attack" in move_type or "weak_attack" in move_type:
            was_risky = "strong" in move_type
            self.opponent_tracker.log_move_used(species, move_type, was_risky=was_risky)
            
        elif "status" in move_type:
            # Status moves are generally not risky but can be strategic
            self.opponent_tracker.log_move_used(species, move_type, was_risky=False)
            
        elif "setup" in move_type:
            # Setup moves indicate strategic play
            was_setup = True
            self.opponent_tracker.log_move_used(species, move_type, was_setup=was_setup)
            
        elif "stat_drop" in move_type:
            # Stat dropping moves are aggressive
            was_risky = True
            self.opponent_tracker.log_move_used(species, move_type, was_risky=was_risky)
            
        else:
            # Unknown/neutral moves
            self.opponent_tracker.log_move_used(species, move_type)
    
    def _predict_likely_moves(self, opponent_species: str, our_active: Pokemon) -> Dict[str, float]:
        """Predict what moves opponent is likely to use based on learned data and current situation"""
        known_pokemon = self.opponent_tracker.known_pokemon.get(opponent_species)
        
        if not known_pokemon or not known_pokemon.moves_seen:
            return self._get_default_move_predictions(opponent_species, our_active)
        
        # Analyze learned move patterns
        move_predictions = {}
        total_seen = len(known_pokemon.moves_seen)
        
        # Base predictions from observed moves
        for move in known_pokemon.moves_seen:
            base_prob = 1.0 / total_seen
            
            # Adjust based on move type and situation
            if "setup" in move and our_active.current_hp_fraction > 0.8:
                base_prob *= 1.3  # More likely to setup when we're healthy
            elif "attack" in move and our_active.current_hp_fraction < 0.5:
                base_prob *= 1.2  # More likely to attack when we're weak
            elif "status" in move and "status" not in str(our_active.status):
                base_prob *= 1.1  # More likely to status if we don't have one
                
            move_predictions[move] = base_prob
            
        # Normalize probabilities
        total_prob = sum(move_predictions.values())
        if total_prob > 0:
            move_predictions = {move: prob/total_prob for move, prob in move_predictions.items()}
            
        return move_predictions
    
    def _get_default_move_predictions(self, species: str, our_active: Pokemon) -> Dict[str, float]:
        """Default move predictions for unknown Pokemon based on species and situation"""
        species_lower = species.lower().replace('-', '')
        
        # Species-specific predictions based on common Uber strategies
        if 'deoxysspeed' in species_lower:
            return {
                "hazard_move": 0.4,     # Deoxys often sets spikes/hazards
                "status_move": 0.3,     # Thunder Wave, Taunt
                "attacking_move": 0.2,  # Psycho Boost
                "switching": 0.1        # Sometimes switches after hazards
            }
        elif 'kingambit' in species_lower:
            return {
                "attacking_move": 0.5,  # Kowtow Cleave, Iron Head
                "setup_move": 0.3,      # Swords Dance
                "priority_move": 0.2    # Sucker Punch
            }
        elif 'zaciancrowned' in species_lower:
            return {
                "attacking_move": 0.6,  # Behemoth Blade, Close Combat
                "setup_move": 0.3,      # Swords Dance
                "coverage_move": 0.1    # Wild Charge
            }
        elif 'arceusfairy' in species_lower:
            return {
                "setup_move": 0.4,      # Calm Mind
                "attacking_move": 0.3,  # Judgment
                "support_move": 0.2,    # Recover, Taunt
                "switching": 0.1
            }
        elif 'eternatus' in species_lower:
            return {
                "setup_move": 0.4,      # Agility
                "attacking_move": 0.5,  # Meteor Beam, Dynamax Cannon
                "coverage_move": 0.1    # Fire Blast
            }
        elif 'koraidon' in species_lower:
            return {
                "attacking_move": 0.5,  # Scale Shot, Close Combat
                "setup_move": 0.3,      # Swords Dance
                "utility_move": 0.2     # Flame Charge for speed
            }
        else:
            # Generic Pokemon
            return {
                "attacking_move": 0.6,
                "setup_move": 0.2,
                "status_move": 0.1,
                "switching": 0.1
            }
    
    def _predict_best_switch_in(self, battle: AbstractBattle):
        """Predict what Pokemon opponent will switch to"""
        current_opponent = battle.opponent_active_pokemon
        our_active = battle.active_pokemon
        
        if not current_opponent or not our_active:
            return None
            
        best_counter = None
        best_matchup_score = -999
        
        # Check all known opponent Pokemon
        for species, pokemon_data in self.opponent_tracker.known_pokemon.items():
            if not pokemon_data.is_alive or species == current_opponent.species:
                continue  # Skip fainted or currently active Pokemon
                
            # Estimate how good this matchup would be for them
            # (Simplified - would need to reconstruct Pokemon object)
            type_advantage = self._estimate_type_matchup(
                pokemon_data.types,  # already ['steel', 'fairy', ...]
                [t.name.lower() for t in our_active.types]  # <- normalize enums to strings
            )
            
            if type_advantage > best_matchup_score:
                best_matchup_score = type_advantage
                best_counter = species
                
        return best_counter

    def _get_type_effectiveness(self, attack_type: str, defend_types: List[str]) -> float:
        if not defend_types or not attack_type:
            return 1.0
        row = self.gen_data.type_chart.get(attack_type.lower(), {})
        mult = 1.0
        for d in defend_types:
            mult *= float(row.get(d.lower(), 1.0))
        return mult

    def _estimate_type_matchup(self, attacker_types: List[str], defender_types: List[str]) -> float:
        """Estimate type matchup advantage using real type effectiveness"""
        if not attacker_types or not defender_types:
            return 1.0
            
        best_effectiveness = 0.0
        
        for att_type in attacker_types:
            effectiveness = self._get_type_effectiveness(att_type, defender_types)
            if effectiveness > best_effectiveness:
                best_effectiveness = effectiveness
                
        return best_effectiveness

    def choose_move(self, battle: AbstractBattle):
        self.turn_count += 1
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        if active is None or opponent is None:
            return self.choose_random_move(battle)

        # ACTIVE LEARNING: Parse battle events and update tracking
        self._parse_battle_events(battle)

        # Update opponent tracking with current Pokemon data
        if opponent:
            self.opponent_tracker.update_pokemon(opponent.species, opponent)

        # Learn from team preview if first turn
        if self.turn_count == 1:
            self._learn_from_team_preview(battle)
            
        # Validate our predictions from last turn and update learning
        if self.turn_count > 1 and opponent:
            # Check if opponent switched (basic validation)
            if hasattr(self, '_last_opponent_species') and self._last_opponent_species:
                if self._last_opponent_species != opponent.species:
                    # They switched - if we predicted it, count as correct
                    if self._predicted_switch_last_turn:
                        self.predictions_correct += 1
                        
            self._last_opponent_species = opponent.species
            self._predicted_switch_last_turn = False  # Reset for this turn
            
        # CRITICAL FIX: Better lead selection and early game strategy  
        if self.turn_count == 1:
            # Learn their lead patterns for future battles
            active_clean = active.species.lower().replace('-', '')
            opponent_clean = opponent.species.lower().replace('-', '')
            battle_id = getattr(battle, 'battle_tag', str(id(battle)))
            
            # Track opponent's lead (only once per battle)
            if not hasattr(self, '_battles_tracked_for_leads'):
                self._battles_tracked_for_leads = set()
            
            if battle_id not in self._battles_tracked_for_leads:
                self._battles_tracked_for_leads.add(battle_id)
                self.opponent_lead_history[opponent_clean] = self.opponent_lead_history.get(opponent_clean, 0) + 1
            
            if active_clean == 'arceusfairy' and opponent_clean == 'deoxysspeed':
                # Don't let them get free spikes - switch to our Deoxys
                available_switches = battle.available_switches
                if available_switches:
                    deoxys_switches = [p for p in available_switches if 'deoxysspeed' in p.species.lower()]
                    if deoxys_switches:
                        return self.create_order(deoxys_switches[0])
                        
            elif active_clean == 'kingambit' and opponent_clean in {'koraidon', 'zaciancrowned'}:
                # Don't lead Kingambit vs Close Combat users
                available_switches = battle.available_switches  
                if available_switches:
                    safe_switches = [p for p in available_switches if p.species.lower().replace('-', '') in ['arceusfairy', 'eternatus']]
                    if safe_switches:
                        return self.create_order(safe_switches[0])

        if battle.available_moves and (
                not self._should_switch_out(battle) or not battle.available_switches
        ):
            n_remaining_mons = len([m for m in battle.team.values() if m.fainted is False])
            n_opp_remaining_mons = 6 - len([m for m in battle.opponent_team.values() if m.fainted is True])

            # Minimax-based prediction plays using learned data
            if self.turn_count > 2:  # Only after we have some learning data
                # Look for high-confidence prediction opportunities
                opponent_responses = self._predict_opponent_response(None, active, opponent, battle)
                switch_probability = sum(prob for resp, prob in opponent_responses.items() if 'switch' in resp)
                
                if switch_probability > 0.7:  # Very likely to switch
                    predicted_switch = self._predict_best_switch_in(battle)
                    if predicted_switch and predicted_switch in self.opponent_tracker.known_pokemon:
                        switch_types = self.opponent_tracker.known_pokemon[predicted_switch].types
                        if switch_types:
                            # Find moves that are super effective vs predicted switch
                            prediction_moves = []
                            for move in battle.available_moves:
                                effectiveness = self._get_type_effectiveness(move.type.name, switch_types)
                                if effectiveness >= 2.0:  # Super effective
                                    prediction_value = move.base_power * effectiveness * 1.5  # STAB bonus
                                    prediction_moves.append((move, prediction_value))
                            
                            if prediction_moves:
                                best_prediction = max(prediction_moves, key=lambda x: x[1])
                                prediction_move, prediction_value = best_prediction
                                
                                # Compare with minimax evaluation of normal moves
                                best_normal_value = max(
                                    self._minimax_evaluate_move(m, active, opponent, battle, depth=1) 
                                    for m in battle.available_moves
                                )
                                
                                # Only use prediction if it's significantly better
                                if prediction_value > best_normal_value * 1.3:
                                    self.predictions_made += 1
                                    self._predicted_switch_last_turn = True
                                    return self.create_order(prediction_move)

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
                        and move.target == Target.SELF
                        and min([active.boosts[s] for s, v in move.boosts.items() if v > 0]) < 6)
                ]

                if setup_moves:
                    # Less likely to setup against aggressive opponents who won't let us
                    if self.opponent_tracker.profile.aggression_level < 0.6:
                        return self.create_order(setup_moves[0])
                    elif current_matchup > 1.0:  # Only if we have a really good matchup
                        return self.create_order(setup_moves[0])

            # Use minimax evaluation with accuracy considerations
            move_values = {}
            for move in battle.available_moves:
                base_value = self._minimax_evaluate_move(move, active, opponent, battle, depth=1)
                
                # CRITICAL FIX: Account for accuracy issues
                if move.id == 'fireblast' and move.accuracy < 1.0:
                    # Fire Blast keeps missing - heavily penalize unless it's a KO
                    predicted_damage = move.base_power * opponent.damage_multiplier(move)
                    if predicted_damage < opponent.current_hp * 0.9:  # Not a likely KO
                        base_value *= 0.3  # Heavy penalty for inaccurate moves that don't KO
                        
                # Bonus for guaranteed accuracy moves in critical situations
                if move.accuracy == 1.0 and active.current_hp_fraction < 0.3:
                    base_value *= 1.2
                    
                move_values[move] = base_value
                
            best_move = max(move_values.keys(), key=lambda m: move_values[m])

            should_tera = self._should_tera(battle, n_remaining_mons)
            return self.create_order(best_move, terastallize=should_tera)

        if battle.available_switches:
            switches: List[Pokemon] = battle.available_switches

            # Enhanced switching logic with battle-specific improvements
            switch_scores = {}
            for switch in switches:
                base_score = self._estimate_matchup(switch, opponent)

                # CRITICAL: Avoid switching into obvious bad matchups
                if switch.species.lower() in 'zaciancrowned' and opponent.species.lower() in 'zaciancrowned':
                    base_score -= 1.0  # Heavy penalty for Zacian vs Zacian
                    
                if switch.species.lower() in ['kingambit'] and opponent.species.lower() in ['zaciancrowned', 'koraidon']:
                    base_score -= 0.8  # Kingambit gets destroyed by Close Combat
                    
                # Prefer switches that resist opponent's likely moves
                opponent_responses = self._predict_opponent_response(None, active, opponent, battle)
                for response, prob in opponent_responses.items():
                    if 'close_combat' in response and 'fairy' in str(switch.types).lower():
                        base_score += prob * 0.5  # Fairy resists Fighting
                    elif 'behemoth_blade' in response and switch.species.lower() in ['eternatus']:
                        base_score += prob * 0.3  # Eternatus can live Behemoth Blade

                # Bonus for unexpected switches against predictable opponents
                if self.opponent_tracker.profile.risk_tolerance < 0.4:
                    base_score += 0.2

                switch_scores[switch] = base_score

            best_switch = max(switches, key=lambda s: switch_scores[s])
            return self.create_order(best_switch)

        return self.choose_random_move(battle)
    
    def _get_optimal_lead(self, opponent_name: str = None) -> str:
        """Determine optimal lead based on opponent patterns and learning data"""
        
        # If we know opponent's lead patterns, counter them
        if self.opponent_lead_history:
            most_common_opponent_lead = max(self.opponent_lead_history.keys(), 
                                           key=lambda k: self.opponent_lead_history[k])
            
            # Counter their most common lead
            lead_counters = {
                'deoxysspeed': 'deoxysspeed',  # Speed tie for spikes
                'koraidon': 'arceusfairy',     # Resists Close Combat
                'zaciancrowned': 'eternatus',  # Can live Behemoth Blade
                'kingambit': 'koraidon',       # Close Combat beats Kingambit
                'arceusfairy': 'kingambit',    # Dark beats Fairy
                'eternatus': 'kingambit',      # Can Sucker Punch
            }
            
            if most_common_opponent_lead in lead_counters:
                return lead_counters[most_common_opponent_lead]
        
        # Default leads based on battle count (adaptive strategy)
        battle_mod = self.battle_count % 6
        
        if battle_mod == 0:
            return 'kingambit'      # Aggressive lead with Sucker Punch
        elif battle_mod == 1:
            return 'deoxysspeed'    # Speed control and spikes
        elif battle_mod == 2:
            return 'zaciancrowned'  # Pure offense
        elif battle_mod == 3:
            return 'arceusfairy'    # Defensive pivot
        elif battle_mod == 4:
            return 'eternatus'      # Special attacker
        else:
            return 'koraidon'       # Physical powerhouse
    
    def teampreview(self, battle):
        """Enhanced team preview with learned opponent data"""
        # Only increment battle count once per unique battle
        battle_id = getattr(battle, 'battle_tag', str(id(battle)))
        if battle_id not in self.battles_seen:
            self.battle_count += 1
            self.battles_seen.add(battle_id)
        
        # Learn opponent team composition
        if hasattr(battle, 'opponent_team') and battle.opponent_team:
            for species, pokemon in battle.opponent_team.items():
                self.opponent_tracker.team_preview_seen.add(species)
                self.opponent_tracker.add_pokemon(species, pokemon)
        
        # Get opponent name for personalized strategy
        opponent_name = getattr(battle, 'opponent_username', None)
        
        # Determine optimal lead
        preferred_lead = self._get_optimal_lead(opponent_name)
        
        # Find the preferred lead in our team
        team_list = list(battle.team.values())
        for i, pokemon in enumerate(team_list):
            species_clean = pokemon.species.lower().replace('-', '')
            if species_clean == preferred_lead or pokemon.species == preferred_lead:
                return f"/team {i + 1}"
        
        # Fallback: Try to find any of our good leads
        fallback_leads = ['kingambit', 'deoxysspeed', 'zaciancrowned']
        for lead in fallback_leads:
            for i, pokemon in enumerate(team_list):
                if pokemon.species.lower().replace('-', '') == lead or pokemon.species == lead:
                    return f"/team {i + 1}"
        
        return "/team 1"
    
    def choose_team_preview(self, battle):
        """Alternative method name that poke-env might use"""
        return self.teampreview(battle)
    
    def team_preview(self, battle):
        """Another alternative method name"""
        return self.teampreview(battle)
    
    def _on_battle_end(self, battle: AbstractBattle):
        """Track performance and learning outcomes"""
        self.battles_total += 1
        if battle.won:
            self.battles_won += 1
            
        # Calculate win rate
        win_rate = self.battles_won / self.battles_total if self.battles_total > 0 else 0
        
        # Learning happens silently to avoid console spam
        pass