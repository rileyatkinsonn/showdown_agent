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


class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)
        
        # Battle state tracking
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
        
        # Threat categories
        self.SETUP_MOVES = {"swordsdance", "calmmind", "agility", "dragondance", "nastyplot"}
        self.PRIORITY_MOVES = {"suckerpunch", "extremespeed", "quickattack", "bulletpunch"}
        self.HAZARD_MOVES = {"spikes", "stealthrock", "toxicspikes", "stickyweb"}
        self.RECOVERY_MOVES = {"recover", "roost", "moonlight", "synthesis", "morningsun"}

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

    def _evaluate_hazard_pressure(self, battle: AbstractBattle):
        """Evaluate how much hazard damage is affecting our team"""
        our_hazards = len(battle.side_conditions)
        their_hazards = len(battle.opponent_side_conditions)
        
        # Count how many of our team are hurt by hazards
        vulnerable_count = 0
        for pokemon in battle.team.values():
            if not pokemon.fainted and pokemon.current_hp_fraction < 0.8:
                vulnerable_count += 1
        
        hazard_pressure = our_hazards * 2 + vulnerable_count
        return hazard_pressure

    def _analyze_opponent_team(self, battle: AbstractBattle):
        threats = []
        for species, pokemon in battle.opponent_team.items():
            if pokemon and not pokemon.fainted:
                threat_level = 0
                
                # Check revealed moves for threat assessment
                for move_id in pokemon.moves:
                    if move_id in self.SETUP_MOVES:
                        threat_level += 2
                    elif move_id in self.PRIORITY_MOVES:
                        threat_level += 1
                    elif move_id in self.RECOVERY_MOVES:
                        threat_level += 1
                
                # Type matchup vs our team
                our_team_matchups = []
                for our_mon in battle.team.values():
                    if not our_mon.fainted:
                        matchup = self._estimate_matchup(our_mon, pokemon)
                        our_team_matchups.append(matchup)
                
                if our_team_matchups:
                    avg_matchup = sum(our_team_matchups) / len(our_team_matchups)
                    if avg_matchup < -1:  # Opponent has advantage vs our team
                        threat_level += 2
                
                threats.append((pokemon, threat_level))
        
        return sorted(threats, key=lambda x: x[1], reverse=True)

    def _predict_opponent_switch(self, battle: AbstractBattle):
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        
        # If opponent is in a bad matchup, predict switch
        if self._estimate_matchup(active, opponent) > 1.5:
            # Look for best switch target
            threats = self._analyze_opponent_team(battle)
            for threat_pokemon, threat_level in threats:
                if (threat_pokemon.species != opponent.species and 
                    self._estimate_matchup(active, threat_pokemon) < 0):
                    return threat_pokemon
        return None

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
        
        # Enhanced switch logic considering opponent threats
        predicted_switch = self._predict_opponent_switch(battle)
        
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
            
            # Enhanced switching: consider opponent setup potential
            for move_id in opponent.moves:
                if move_id in self.SETUP_MOVES and opponent.current_hp_fraction > 0.7:
                    return True
            
            # Consider predicted opponent switch
            if predicted_switch:
                best_vs_predicted = max([
                    self._estimate_matchup(m, predicted_switch) 
                    for m in battle.available_switches
                ], default=-999)
                if best_vs_predicted > 0.5:
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

            # Enhanced setup logic considering opponent team
            threats = self._analyze_opponent_team(battle)
            high_threat_count = sum(1 for _, threat_level in threats if threat_level >= 2)
            
            if (
                    active.current_hp_fraction == 1
                    and self._estimate_matchup(active, opponent) > 0
                    and high_threat_count <= 2  # Don't setup if too many threats remain
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
                        # Extra check: don't setup if opponent has priority moves
                        has_priority = any(move_id in self.PRIORITY_MOVES 
                                         for move_id in opponent.moves)
                        if not has_priority or active.current_hp_fraction > 0.8:
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
            return self.create_order(move, terastallize=self._should_tera(battle, n_remaining_mons))

        if battle.available_switches:
            switches: List[Pokemon] = battle.available_switches
            
            # Hazard management: prioritize Pokemon that can handle hazard pressure
            hazard_pressure = self._evaluate_hazard_pressure(battle)
            
            if hazard_pressure >= 4:  # High hazard pressure
                # Prioritize switching to Pokemon with:
                # 1. High HP (can tank hazard damage)
                # 2. Good defensive stats
                # 3. Recovery moves or defensive utility
                
                hazard_resistant = []
                for switch in switches:
                    resistance_score = 0
                    
                    # High HP Pokemon handle hazards better
                    if switch.current_hp_fraction >= 0.8:
                        resistance_score += 2
                    
                    # Defensive Pokemon (Arceus-Fairy) are better hazard absorbers
                    if switch.species == 'arceusfairy':
                        resistance_score += 3
                    elif switch.species in ['eternatus', 'kingambit']:  # Bulky Pokemon
                        resistance_score += 1
                    
                    # Check if they have recovery moves
                    for move_id in switch.moves:
                        if move_id in self.RECOVERY_MOVES:
                            resistance_score += 2
                            break
                    
                    hazard_resistant.append((switch, resistance_score))
                
                if hazard_resistant:
                    # Sort by resistance score, then by matchup
                    best_resistant = max(hazard_resistant, 
                                       key=lambda x: (x[1], self._estimate_matchup(x[0], opponent)))
                    if best_resistant[1] >= 2:  # Good resistance score
                        return self.create_order(best_resistant[0])
            
            # Enhanced switch selection considering opponent team
            predicted_switch = self._predict_opponent_switch(battle)
            
            if predicted_switch:
                # Switch to counter predicted opponent switch
                best_vs_predicted = max(
                    switches,
                    key=lambda s: self._estimate_matchup(s, predicted_switch)
                )
                if self._estimate_matchup(best_vs_predicted, predicted_switch) > 0:
                    return self.create_order(best_vs_predicted)
            
            # Consider overall threat level of opponent team
            threats = self._analyze_opponent_team(battle)
            if threats:
                # Switch to handle biggest threat
                biggest_threat = threats[0][0]
                best_vs_threat = max(
                    switches,
                    key=lambda s: self._estimate_matchup(s, biggest_threat)
                )
                if self._estimate_matchup(best_vs_threat, biggest_threat) > 0:
                    return self.create_order(best_vs_threat)
            
            # Default: best matchup vs current opponent
            return self.create_order(
                max(
                    switches,
                    key=lambda s: self._estimate_matchup(s, opponent),
                )
            )

        return self.choose_random_move(battle)

    def teampreview(self, battle):
        team_list = list(battle.team.values())
        
        # Safer lead priority: Zacian (versatile) > Kingambit (trades well) > Deoxys (risky but rewarding)
        lead_priority = ['zaciancrowned', 'kingambit', 'deoxysspeed']
        
        for preferred_lead in lead_priority:
            for i, pokemon in enumerate(team_list):
                if pokemon.species == preferred_lead:
                    return f"/team {i + 1}"
        
        # Fallback to first Pokemon
        return "/team 1"

    # poke-env alt names
    def choose_team_preview(self, battle):
        return self.teampreview(battle)

    def team_preview(self, battle):
        return self.teampreview(battle)



