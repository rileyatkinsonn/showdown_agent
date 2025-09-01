from typing import List, Optional, Dict, Set
from dataclasses import dataclass, field

from poke_env.battle import MoveCategory
from poke_env.battle.abstract_battle import AbstractBattle
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
        self.battle_count = 0  # Track number of battles for lead selection
        self.battles_seen = set()  # Track which battles we've seen to increment counter properly
        
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
        self.SWITCH_OUT_MATCHUP_THRESHOLD = -2.5  # More conservative - don't switch good positions
        
        # Game theory parameters
        self.PREDICTION_CONFIDENCE_THRESHOLD = 0.7
        self.RISK_REWARD_THRESHOLD = 1.2
        
        # Keep it simple - no complex counter database

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

    def _would_tera_help(self, active: Pokemon, opponent: Pokemon) -> bool:
        """Check if Terastallizing would actually improve the matchup"""
        if not hasattr(active, 'tera_type') or not active.tera_type:
            return False
            
        # Get current type effectiveness
        current_weakness = max([active.damage_multiplier(t) for t in opponent.types if t is not None])
        current_resistance = max([opponent.damage_multiplier(t) for t in active.types if t is not None])
        
        # Simulate tera type matchup (simplified)
        tera_type_str = str(active.tera_type)
        
        # Common tera type advantages we know about
        tera_improvements = {
            # Arceus-Fairy to Fire helps vs Steel types but hurts vs Water/Ground
            ('arceusfairy', 'fire'): {'good_vs': ['steel', 'grass', 'ice', 'bug'], 'bad_vs': ['water', 'ground', 'rock']},
            # Zacian to Flying helps vs Fighting/Ground
            ('zaciancrowned', 'flying'): {'good_vs': ['fighting', 'ground', 'grass', 'bug'], 'bad_vs': ['electric', 'ice', 'rock']},
        }
        
        active_key = active.species.lower().replace('-', '')
        tera_key = tera_type_str.lower()
        
        if (active_key, tera_key) in tera_improvements:
            improvement = tera_improvements[(active_key, tera_key)]
            opponent_types = [str(t).lower() for t in opponent.types if t is not None]
            
            # Check if opponent has types we're good against after tera
            helps = any(opp_type in improvement['good_vs'] for opp_type in opponent_types)
            hurts = any(opp_type in improvement['bad_vs'] for opp_type in opponent_types)
            
            return helps and not hurts
        
        # Default: only tera if we're weak to opponent
        return current_weakness > 1.5
    
    def _should_tera(self, battle: AbstractBattle, n_remaining_mons: int):
        if not battle.can_tera:
            return False
            
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        
        if not active or not opponent:
            return False
        
        current_matchup = self._estimate_matchup(active, opponent)
        
        # Critical situations - always tera if it would help
        if n_remaining_mons == 1:
            return self._would_tera_help(active, opponent)
        
        # Last full HP mon - but only if tera actually improves matchup
        full_hp_mons = [m for m in battle.team.values() if m.current_hp_fraction == 1]
        if len(full_hp_mons) == 1 and active.current_hp_fraction == 1:
            return self._would_tera_help(active, opponent)
        
        # Don't tera in bad matchups unless it fixes the matchup
        if current_matchup < -0.5:
            return self._would_tera_help(active, opponent) and n_remaining_mons <= 3
        
        # In mirror matches, be more aggressive with tera
        is_mirror_match = len([p for p in battle.opponent_team.values() if p.species == active.species]) > 0
        
        if is_mirror_match and current_matchup > 0 and active.current_hp_fraction >= 0.8:
            return self._would_tera_help(active, opponent)
        
        # Standard good matchup tera - but verify it actually helps
        if (
            current_matchup > 0.5 and 
            active.current_hp_fraction >= 0.8 and
            self._would_tera_help(active, opponent)
        ):
            return True
        
        return False

    def _should_switch_out(self, battle: AbstractBattle):
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        
        if not active or not opponent:
            return False
        
        # Update opponent tracking
        self.opponent_tracker.update_pokemon(opponent.species, opponent)
        
        # KINGAMBIT MUST NEVER SWITCH VS KORAIDON - IT HAS SUCKER PUNCH ADVANTAGE!
        if active.species == 'kingambit' and opponent.species == 'koraidon':
            return False
        
        # NEVER SWITCH UNLESS LITERALLY DYING
        # Only switch if we're at critical health (5% or less) AND have no offensive moves
        if active.current_hp_fraction <= 0.05:  # Only at 5% HP or less
            # Check if we have any decent attacking moves first
            if battle.available_moves:
                attacking_moves = [m for m in battle.available_moves if m.base_power and m.base_power > 0]
                if attacking_moves:
                    return False
            
            # Only switch if we have a MUCH better option
            good_switches = [
                m for m in battle.available_switches
                if self._estimate_matchup(m, opponent) > 2.0  # Need overwhelming advantage
            ]
            if good_switches:
                return True
        
        # NEVER SWITCH otherwise - ALWAYS stay and fight!
        return False

    def _stat_estimation(self, mon: Pokemon, stat: str):
        # Stats boosts value
        if mon.boosts[stat] > 1:
            boost = (2 + mon.boosts[stat]) / 2
        else:
            boost = 2 / (2 - mon.boosts[stat])
        return ((2 * mon.base_stats[stat] + 31) + 5) * boost

    def _calculate_move_value(self, move, active: Pokemon, opponent: Pokemon, battle: AbstractBattle):
        """Simple move evaluation that works"""
        
        # CRITICAL: Kingambit ALWAYS prefers Sucker Punch vs physical attackers
        if active.species == 'kingambit' and move.id == 'suckerpunch':
            # Absolutely maximum priority for Koraidon
            if opponent.species == 'koraidon':
                return 99999999  # EXTREME priority vs Koraidon
            
            # High priority vs other physical attackers
            physical_attackers = ['zaciancrowned', 'kingambit']
            if opponent.species in physical_attackers:
                return 999999  # Force Sucker Punch selection
            else:
                return 100000  # Still very high value against others
        
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

        # SMART SUCKER PUNCH: Only vs physical attackers, NOT vs status Pokemon
        if active.species == 'kingambit':
            sucker_punch = next((move for move in battle.available_moves if move.id == 'suckerpunch'), None)
            if sucker_punch:
                # Use Sucker Punch vs physical attackers that will likely attack
                physical_attackers = ['koraidon', 'zaciancrowned', 'kingambit']
                if opponent.species in physical_attackers:
                    return self.create_order(sucker_punch)

                # DON'T use Sucker Punch vs status Pokemon (Deoxys, Arceus with status moves)
                # These will just make Sucker Punch fail
        
        # Track if this is a new battle (but don't increment here since teampreview handles it)
        if self.turn_count == 1:
            pass  # Battle count is handled in teampreview now

        # Update opponent tracking
        if opponent:
            self.opponent_tracker.update_pokemon(opponent.species, opponent)
            
        # Track opponent's last move if we can infer it
        if hasattr(battle, 'opponent_active_pokemon') and battle.opponent_active_pokemon:
            # This is a simplified way - in a real implementation you'd parse the battle log
            pass

        # KINGAMBIT MOVE SELECTION: Smart counter-play vs specific matchups
        if battle.available_moves and active.species == 'kingambit':
            # VS DEOXYS: Never use Sucker Punch - it will use status moves. Use direct attacks!
            if opponent.species == 'deoxysspeed':
                # Use Kowtow Cleave or Iron Head - direct attacks that OHKO Deoxys
                kowtow_cleave = next((move for move in battle.available_moves if move.id == 'kowtowcleave'), None)
                iron_head = next((move for move in battle.available_moves if move.id == 'ironhead'), None)
                if kowtow_cleave:
                    return self.create_order(kowtow_cleave)
                elif iron_head:
                    return self.create_order(iron_head)
            
            # Additional Sucker Punch logic for other matchups (but NOT Deoxys!)
            sucker_punch = next((move for move in battle.available_moves if move.id == 'suckerpunch'), None)
            if sucker_punch:
                # Use Sucker Punch against physical attackers (but never Deoxys!)
                physical_attackers = ['kingambit']
                if opponent.species in physical_attackers:
                    return self.create_order(sucker_punch)
                
                # Only use vs Eternatus/Arceus if they're likely to attack (not Deoxys!)
                elif opponent.species in ['eternatus', 'arceusfairy']:
                    return self.create_order(sucker_punch)
        
        # ZACIAN MOVE SELECTION: Optimize for mirror matches and key threats
        if battle.available_moves and active.species == 'zaciancrowned':
            # VS ZACIAN MIRROR: Be aggressive, go for immediate damage
            if opponent.species == 'zaciancrowned':
                behemoth_blade = next((move for move in battle.available_moves if move.id == 'behemothblade'), None)
                if behemoth_blade:
                    return self.create_order(behemoth_blade)
            
            # VS KORAIDON: Use Close Combat for super effective damage
            if opponent.species == 'koraidon':
                close_combat = next((move for move in battle.available_moves if move.id == 'closecombat'), None)
                if close_combat:
                    return self.create_order(close_combat)
                    
            # VS KINGAMBIT: Use Close Combat for super effective damage
            if opponent.species == 'kingambit':
                close_combat = next((move for move in battle.available_moves if move.id == 'closecombat'), None)
                if close_combat:
                    return self.create_order(close_combat)
        
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

            # Enhanced setup logic - but be more careful in mirrors
            current_matchup = self._estimate_matchup(active, opponent)
            is_mirror_match = active.species == opponent.species
            
            if (
                    active.current_hp_fraction >= 0.8
                    and current_matchup > (0.8 if is_mirror_match else 0.5)
            ):
                setup_moves = [
                    move for move in battle.available_moves
                    if (move.boosts
                        and sum(move.boosts.values()) >= 2
                        and move.target == "self"
                        and min([active.boosts[s] for s, v in move.boosts.items() if v > 0]) < 6)
                ]
                
                if setup_moves:
                    # In mirrors, only setup if we have a clear advantage
                    if is_mirror_match:
                        if current_matchup > 1.2 and opponent.current_hp_fraction < active.current_hp_fraction:
                            return self.create_order(setup_moves[0])
                    # Against different Pokemon, use normal logic
                    elif self.opponent_tracker.profile.aggression_level < 0.6:
                        return self.create_order(setup_moves[0])
                    elif current_matchup > 1.0:
                        return self.create_order(setup_moves[0])

            # Don't duplicate Sucker Punch logic - it's handled above now
            
            # Eternatus mirror - prioritize speed
            if opponent.species == 'eternatus' and active.species == 'eternatus':
                # Use Agility if we're at full HP and they are too
                agility_move = next((move for move in battle.available_moves if move.id == 'agility'), None)
                if agility_move and active.current_hp_fraction == 1.0 and opponent.current_hp_fraction > 0.8:
                    return self.create_order(agility_move)
            
            # Zacian mirrors - go for the KO
            if opponent.species == 'zaciancrowned' and active.species == 'zaciancrowned':
                behemoth_blade = next((move for move in battle.available_moves if move.id == 'behemothblade'), None)
                if behemoth_blade:
                    return self.create_order(behemoth_blade)
            
            if opponent.species == 'zaciancrowned' and active.species == 'arceusfairy':
                # Don't tera to Fire vs Zacian - it resists Fire
                should_tera = False
            else:
                should_tera = self._should_tera(battle, n_remaining_mons)
            
            # Choose best attacking move - prioritize high base power when ahead
            n_remaining_mons = len([m for m in battle.team.values() if m.fainted is False])
            n_opp_remaining_mons = 6 - len([m for m in battle.opponent_team.values() if m.fainted is True])
            
            # If we're ahead in numbers, be more aggressive with powerful moves
            if n_remaining_mons > n_opp_remaining_mons:
                best_move = max(
                    battle.available_moves,
                    key=lambda m: m.base_power * self._calculate_move_value(m, active, opponent, battle)
                )
            else:
                best_move = max(
                    battle.available_moves,
                    key=lambda m: self._calculate_move_value(m, active, opponent, battle)
                )
            
            if 'should_tera' not in locals():
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
    
    def teampreview(self, battle):
        """Handle team preview - choose lead Pokemon"""
        # Only increment battle count once per unique battle
        battle_id = getattr(battle, 'battle_tag', str(id(battle)))
        if battle_id not in self.battles_seen:
            self.battle_count += 1
            self.battles_seen.add(battle_id)
        
        if hasattr(battle, 'opponent_team') and battle.opponent_team:
            for species, pokemon in battle.opponent_team.items():
                self.opponent_tracker.team_preview_seen.add(species)
        
        # ADAPTIVE LEAD STRATEGY: Back to Kingambit with improved move selection 
        preferred_lead = "kingambit"
        
        # Find the preferred lead  
        team_list = list(battle.team.values())
        for i, pokemon in enumerate(team_list):
            if pokemon.species == preferred_lead:
                return f"/team {i + 1}"
        
        # Fallback to Kingambit if Zacian not found
        for i, pokemon in enumerate(team_list):
            if pokemon.species == 'kingambit':
                return f"/team {i + 1}"
        
        return "/team 1"
    
    def choose_team_preview(self, battle):
        """Alternative method name that poke-env might use"""
        return self.teampreview(battle)
    
    def team_preview(self, battle):
        """Another alternative method name"""
        return self.teampreview(battle)
