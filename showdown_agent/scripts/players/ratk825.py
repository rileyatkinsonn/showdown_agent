from typing import List

from poke_env.battle import AbstractBattle, Move, SideCondition, Pokemon, MoveCategory
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
        self.ENTRY_HAZARDS = {
            "spikes": SideCondition.SPIKES,
            "stealhrock": SideCondition.STEALTH_ROCK,
            "stickyweb": SideCondition.STICKY_WEB,
            "toxicspikes": SideCondition.TOXIC_SPIKES,
        }

        self.ANTI_HAZARDS_MOVES = {"rapidspin", "defog"}

        self.SPEED_TIER_COEFICIENT = 0.1
        self.HP_FRACTION_COEFICIENT = 0.4
        self.SWITCH_OUT_MATCHUP_THRESHOLD = -2

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

    def _should_tera(self, battle: AbstractBattle, n_remaining_mons: int):
        if battle.can_tera:
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
        gen_data = GenData.from_gen(9)
        
        # Get the Pokemon's species identifier, normalizing the name
        species_id = mon.species.lower().replace(' ', '').replace('-', '')
        
        # Get base stats from GenData pokedex
        if species_id in gen_data.pokedex:
            base_stat = gen_data.pokedex[species_id]['baseStats'][stat]
        else:
            # Fallback to Pokemon object's base_stats if GenData lookup fails
            base_stat = mon.base_stats[stat]
        
        # Estimate actual stat assuming level 50, neutral nature, 31 IVs, and moderate EVs (85)
        # Formula: ((2 * base + IV + EV/4) * level / 100) + 5
        estimated_stat = int((2 * base_stat + 31 + 85 // 4) * 50 / 100) + 5
        
        # Apply boosts from battle conditions
        boost_multiplier = max(0.25, 1 + (mon.boosts.get(stat, 0) * 0.5))
        
        return estimated_stat * boost_multiplier

    def _predict_opponent_moves(self, opponent: Pokemon):
        """Predict likely moves based on revealed moves and common movesets"""
        gen_data = GenData.from_gen(9)
        species_id = opponent.species.lower().replace(' ', '').replace('-', '')
        
        # Start with revealed moves
        revealed_moves = set(opponent.moves.keys()) if opponent.moves else set()
        move_slots_remaining = 4 - len(revealed_moves)
        
        # Common move categories to predict
        predicted_moves = {
            'setup': set(),
            'stab': set(),
            'coverage': set(),
            'utility': set(),
            'priority': set()
        }
        
        try:
            if species_id in gen_data.learnset:
                possible_moves = gen_data.learnset[species_id]
                
                for move_name in possible_moves:
                    if move_name in revealed_moves:
                        continue
                        
                    if move_name in gen_data.moves:
                        move_data = gen_data.moves[move_name]
                        
                        # Setup moves (stat boosting)
                        if (move_data.get('boosts') and move_data.get('target') == 'self' and 
                            any(v > 0 for v in move_data['boosts'].values())):
                            predicted_moves['setup'].add(move_name)
                        
                        # STAB moves
                        if move_data.get('type') in [t.name for t in opponent.types if t]:
                            predicted_moves['stab'].add(move_name)
                        
                        # Priority moves
                        if move_data.get('priority', 0) > 0:
                            predicted_moves['priority'].add(move_name)
                        
                        # Utility moves
                        if (move_data.get('category') == 'Status' or 
                            move_name in ['recover', 'roost', 'softboiled', 'moonlight']):
                            predicted_moves['utility'].add(move_name)
                        
                        # Coverage moves (different type from STAB)
                        elif (move_data.get('category') in ['Physical', 'Special'] and 
                              move_data.get('basePower', 0) >= 70):
                            predicted_moves['coverage'].add(move_name)
        except (KeyError, AttributeError, TypeError):
            # Fallback: use basic type-based prediction
            pass
        
        return {
            'revealed': revealed_moves,
            'predicted': predicted_moves,
            'slots_remaining': move_slots_remaining
        }

    def _analyze_opponent_threat(self, opponent: Pokemon, my_pokemon: Pokemon):
        """Analyze how threatening an opponent is to our Pokemon"""
        move_analysis = self._predict_opponent_moves(opponent)
        threat_level = 0
        
        # Base threat from type advantage
        for my_type in my_pokemon.types:
            if my_type:
                threat_level += opponent.damage_multiplier(my_type)
        
        # Increased threat if opponent has setup moves and good HP
        if (move_analysis['predicted']['setup'] and 
            opponent.current_hp_fraction > 0.7):
            threat_level += 1.5
        
        # Priority move threat when we're low HP
        if (move_analysis['predicted']['priority'] and 
            my_pokemon.current_hp_fraction < 0.5):
            threat_level += 1.0
        
        # Speed advantage consideration
        if self._stat_estimation(opponent, "spe") > self._stat_estimation(my_pokemon, "spe"):
            threat_level += 0.5
        
        return threat_level

    def _should_predict_switch(self, battle: AbstractBattle):
        """Predict if opponent might switch and prepare accordingly"""
        opponent = battle.opponent_active_pokemon
        active = battle.active_pokemon
        
        # Opponent likely to switch if they're at severe disadvantage
        matchup_score = self._estimate_matchup(opponent, active)
        
        # Check if we have a strong type advantage
        type_advantage = 0
        for my_type in active.types:
            if my_type:
                type_advantage = max(type_advantage, opponent.damage_multiplier(my_type))
        
        # Predict switch if opponent is heavily disadvantaged
        if matchup_score < -1.5 or type_advantage >= 2.0:
            # Look for moves that hit predicted switch-ins
            best_coverage_move = None
            best_coverage_score = 0
            
            for move in battle.available_moves:
                if move.base_power > 0:
                    coverage_score = 0
                    # Score based on how well this move hits common switch-ins
                    for species, pokemon in battle.opponent_team.items():
                        if not pokemon.fainted and pokemon != opponent:
                            coverage_score += pokemon.damage_multiplier(move)
                    
                    if coverage_score > best_coverage_score:
                        best_coverage_score = coverage_score
                        best_coverage_move = move
            
            return best_coverage_move
        
        return None

    def choose_move(self, battle: AbstractBattle):
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        if active is None or opponent is None:
            return self.choose_random_move(battle)

        # Analyze opponent threats and predict their strategy
        opponent_threat = self._analyze_opponent_threat(opponent, active)
        predicted_switch_move = self._should_predict_switch(battle)
        
        # Rough estimation of damage ratio
        physical_ratio = self._stat_estimation(active, "atk") / self._stat_estimation(opponent, "def")
        special_ratio = self._stat_estimation(active, "spa") / self._stat_estimation( opponent, "spd")

        if battle.available_moves and (not self._should_switch_out(battle) or not battle.available_switches):
            n_remaining_mons = len( [m for m in battle.team.values() if m.fainted is False])
            n_opp_remaining_mons = 6 - len([m for m in battle.opponent_team.values() if m.fainted is True])

            # If we predict opponent will switch, use coverage move instead
            if predicted_switch_move and opponent_threat < 2.0:
                return self.create_order(predicted_switch_move)
            
            # Entry hazard...
            for move in battle.available_moves:
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

            # Setup moves - but be more cautious if opponent is very threatening
            setup_safety_threshold = 3.0 if opponent_threat > 2.5 else 1.0
            if (active.current_hp_fraction >= 0.8 and 
                self._estimate_matchup(active, opponent) > 0 and
                opponent_threat < setup_safety_threshold):
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
                move, terastallize=self._should_tera(battle, n_remaining_mons)
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
