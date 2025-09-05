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

    def _detect_setup_threats(self, battle: AbstractBattle):
        """Detect when opponent has setup sweepers that need immediate attention"""
        setup_threats = []
        
        for species, pokemon in battle.opponent_team.items():
            if pokemon and not pokemon.fainted:
                threat_score = 0
                setup_moves = []
                
                # Check for setup moves in revealed moveset
                for move_id in pokemon.moves:
                    if move_id in self.SETUP_MOVES:
                        setup_moves.append(move_id)
                        if move_id in ['swordsdance', 'dragondance']:
                            threat_score += 3  # Physical setup very dangerous
                        elif move_id in ['calmmind', 'nastyplot']:
                            threat_score += 3  # Special setup very dangerous
                        elif move_id == 'agility':
                            threat_score += 2  # Speed setup dangerous
                        else:
                            threat_score += 2
                
                # High HP setup sweepers are immediate threats
                if setup_moves and pokemon.current_hp_fraction >= 0.7:
                    threat_score += 2
                
                # Boosted Pokemon are critical threats
                if pokemon.boosts:
                    for stat, boost in pokemon.boosts.items():
                        if boost > 0 and stat in ['atk', 'spa', 'spe']:
                            threat_score += boost * 2
                
                if threat_score > 0:
                    setup_threats.append((pokemon, threat_score, setup_moves))
        
        return sorted(setup_threats, key=lambda x: x[1], reverse=True)

    def _assess_endgame_situation(self, battle: AbstractBattle):
        """Determine if we're in endgame and should prioritize winning over safety"""
        our_remaining = len([p for p in battle.team.values() if not p.fainted])
        opp_remaining = len([p for p in battle.opponent_team.values() if p and not p.fainted])
        
        # Endgame indicators
        total_remaining = our_remaining + opp_remaining
        is_endgame = total_remaining <= 4
        
        # Calculate our advantage/disadvantage
        advantage_score = 0
        
        # Pokemon count advantage
        if our_remaining > opp_remaining:
            advantage_score += (our_remaining - opp_remaining) * 2
        elif opp_remaining > our_remaining:
            advantage_score -= (opp_remaining - our_remaining) * 2
        
        # HP advantage in remaining pokemon
        our_total_hp = sum(p.current_hp_fraction for p in battle.team.values() if not p.fainted)
        opp_total_hp = sum(p.current_hp_fraction for p in battle.opponent_team.values() if p and not p.fainted)
        
        if our_total_hp > opp_total_hp:
            advantage_score += (our_total_hp - opp_total_hp) * 3
        else:
            advantage_score -= (opp_total_hp - our_total_hp) * 3
        
        # Win condition assessment
        win_urgency = 0
        if is_endgame:
            if advantage_score < -2:  # We're behind
                win_urgency = 3  # Very urgent - need to take risks
            elif advantage_score < 0:  # Slightly behind
                win_urgency = 2  # Moderately urgent
            elif advantage_score > 2:  # We're ahead
                win_urgency = -1  # Play safe, don't throw
            else:  # Even
                win_urgency = 1  # Slight urgency
        
        return {
            'is_endgame': is_endgame,
            'our_remaining': our_remaining,
            'opp_remaining': opp_remaining,
            'advantage_score': advantage_score,
            'win_urgency': win_urgency
        }

    def _detect_momentum_opportunities(self, battle: AbstractBattle):
        """Identify when opponent is in a bad position we can exploit"""
        momentum_score = 0
        opportunities = []
        
        opponent = battle.opponent_active_pokemon
        if not opponent:
            return {'momentum_score': 0, 'opportunities': []}
        
        # Opponent weakened and we have advantage
        if opponent.current_hp_fraction < 0.5:
            active_matchup = self._estimate_matchup(battle.active_pokemon, opponent)
            if active_matchup > 0:
                momentum_score += 2
                opportunities.append("opponent_low_hp_good_matchup")
        
        # Opponent has stat drops
        negative_boosts = sum(min(0, boost) for boost in opponent.boosts.values())
        if negative_boosts < -2:
            momentum_score += 2
            opportunities.append("opponent_debuffed")
        
        # We have setup opportunities (opponent can't threaten us)
        if (battle.active_pokemon.current_hp_fraction > 0.7 and 
            self._estimate_matchup(battle.active_pokemon, opponent) > 1):
            # Check if we have setup moves
            for move in battle.available_moves:
                if (move.boosts and sum(move.boosts.values()) >= 2 and 
                    move.target == "self"):
                    momentum_score += 3
                    opportunities.append("setup_opportunity")
                    break
        
        # Multiple opponent pokemon at low HP
        low_hp_opponents = sum(1 for p in battle.opponent_team.values() 
                              if p and not p.fainted and p.current_hp_fraction < 0.4)
        if low_hp_opponents >= 2:
            momentum_score += 2
            opportunities.append("multiple_weak_opponents")
        
        # Opponent forced into bad switches (no good options)
        if self._estimate_matchup(battle.active_pokemon, opponent) > 1.5:
            # Check if opponent has any good switch options
            good_switches = 0
            for opp_pokemon in battle.opponent_team.values():
                if (opp_pokemon and not opp_pokemon.fainted and 
                    opp_pokemon.species != opponent.species):
                    if self._estimate_matchup(battle.active_pokemon, opp_pokemon) <= 0:
                        good_switches += 1
            
            if good_switches <= 1:
                momentum_score += 2
                opportunities.append("opponent_limited_switches")
        
        return {
            'momentum_score': momentum_score,
            'opportunities': opportunities
        }

    def _get_move_data(self, move_id: str):
        """Get detailed move data from GenData"""
        move_data = self.gen_data.moves.get(move_id, {})
        
        # Handle heal field which can be a number or list
        heal_data = move_data.get('heal', 0)
        heal_amount = heal_data[0] if isinstance(heal_data, list) else heal_data
        
        # Handle recoil field which can be a number or list  
        recoil_data = move_data.get('recoil', 0)
        recoil_amount = abs(recoil_data[0]) if isinstance(recoil_data, list) else abs(recoil_data) if recoil_data else 0
        
        return {
            'priority': move_data.get('priority', 0),
            'has_secondary': bool(move_data.get('secondary', False)),
            'heal': heal_amount,
            'recoil': recoil_amount,
            'status_chance': move_data.get('secondary', {}).get('chance', 0) if move_data.get('secondary') else 0,
            'target': move_data.get('target', 'normal'),
            'flags': move_data.get('flags', {}),
            'base_power': move_data.get('basePower', 0)
        }

    def _analyze_move_value(self, move, battle: AbstractBattle):
        """Enhanced move analysis using GenData"""
        move_data = self._get_move_data(move.id)
        value_score = 0
        
        # Priority moves are valuable for revenge killing
        if move_data['priority'] > 0:
            # Check if opponent is in KO range
            if battle.opponent_active_pokemon.current_hp_fraction < 0.4:
                value_score += 2
        
        # Status moves with good secondary effects
        if move_data['has_secondary'] and move_data['status_chance'] >= 30:
            value_score += 1
        
        # Healing moves are valuable when low HP
        if move_data['heal'] > 0 and battle.active_pokemon.current_hp_fraction < 0.5:
            value_score += move_data['heal'] / 25  # Scale healing value
        
        # Penalize recoil moves when low HP
        if move_data['recoil'] and battle.active_pokemon.current_hp_fraction < 0.3:
            value_score -= 1
        
        # Multi-target moves less valuable in singles
        if move_data['target'] in ['allAdjacent', 'allAdjacentFoes']:
            value_score -= 0.5
        
        return value_score

    def _get_pokemon_data(self, species: str):
        """Get detailed Pokemon data from GenData"""
        # Handle form variations (e.g., 'zaciancrowned' -> 'zacian')
        base_species = species.lower().replace('-', '').replace('_', '')
        
        pokemon_data = self.gen_data.pokedex.get(base_species, {})
        if not pokemon_data and 'crowned' in base_species:
            pokemon_data = self.gen_data.pokedex.get(base_species.replace('crowned', ''), {})
        
        return {
            'base_stats': pokemon_data.get('baseStats', {}),
            'types': pokemon_data.get('types', []),
            'abilities': pokemon_data.get('abilities', {}),
            'weight': pokemon_data.get('weightkg', 0),
            'tier': pokemon_data.get('tier', 'Unknown')
        }

    def _enhanced_threat_assessment(self, pokemon, battle: AbstractBattle):
        """Enhanced threat assessment using GenData"""
        threat_score = 0
        pokemon_data = self._get_pokemon_data(pokemon.species)
        
        # High base attack/special attack Pokemon are threats
        base_stats = pokemon_data['base_stats']
        if base_stats:
            max_offensive_stat = max(base_stats.get('atk', 0), base_stats.get('spa', 0))
            if max_offensive_stat >= 130:  # Uber-tier offensive stats
                threat_score += 2
            elif max_offensive_stat >= 110:
                threat_score += 1
            
            # High speed is dangerous
            speed = base_stats.get('spe', 0)
            if speed >= 100:
                threat_score += 1
        
        # Check if Pokemon has dangerous abilities
        abilities = pokemon_data['abilities']
        dangerous_abilities = ['supremeoverlord', 'intrepidsword', 'orichalcumpulse']
        if any(ability in abilities.values() for ability in dangerous_abilities):
            threat_score += 1
        
        # Factor in current HP and boosts
        if pokemon.current_hp_fraction > 0.8:
            threat_score += 1
        
        # Check for stat boosts
        if pokemon.boosts:
            offensive_boosts = pokemon.boosts.get('atk', 0) + pokemon.boosts.get('spa', 0)
            threat_score += max(0, offensive_boosts)
        
        return threat_score

    def _calculate_type_effectiveness(self, attacking_type: str, defending_types: list):
        """Calculate type effectiveness using GenData type chart"""
        if not defending_types:
            return 1.0
        
        effectiveness = 1.0
        attacking_type_upper = str(attacking_type).upper()
        
        for defending_type in defending_types:
            if defending_type:
                # Handle both PokemonType objects and strings
                if hasattr(defending_type, 'name'):
                    # PokemonType object - use .name attribute
                    defending_type_upper = defending_type.name.upper()
                else:
                    # String - convert directly
                    defending_type_upper = str(defending_type).upper()
                
                # GenData type chart structure: defending_type -> attacking_type -> multiplier
                type_multiplier = self.gen_data.type_chart.get(defending_type_upper, {}).get(attacking_type_upper, 1.0)
                effectiveness *= type_multiplier
        
        return effectiveness

    def _evaluate_tera_options(self, pokemon, battle: AbstractBattle):
        """Evaluate which Tera type would be most effective"""
        available_types = ["NORMAL", "FIRE", "WATER", "ELECTRIC", "GRASS", "ICE", 
                          "FIGHTING", "POISON", "GROUND", "FLYING", "PSYCHIC", 
                          "BUG", "ROCK", "GHOST", "DRAGON", "DARK", "STEEL", "FAIRY"]
        
        best_tera = None
        best_score = -999
        
        # Get opponent threats
        opponent_team = [p for p in battle.opponent_team.values() if p and not p.fainted]
        
        for tera_type in available_types:
            score = 0
            
            # Offensive benefit: How well does this type hit opponent team?
            for opp_pokemon in opponent_team:
                effectiveness = self._calculate_type_effectiveness(tera_type.lower(), opp_pokemon.types)
                score += (effectiveness - 1.0) * 2  # Bonus for super effective
            
            # Defensive benefit: How well does this type resist opponent attacks?
            for opp_pokemon in opponent_team:
                for opp_type in opp_pokemon.types:
                    if opp_type:
                        resistance = self._calculate_type_effectiveness(str(opp_type), [tera_type.lower()])
                        score += (1.0 - resistance)  # Bonus for resisting
            
            # Prefer current Tera type slightly (avoid waste)
            if hasattr(pokemon, 'tera_type') and pokemon.tera_type and tera_type.lower() == pokemon.tera_type.lower():
                score += 0.5
            
            if score > best_score:
                best_score = score
                best_tera = tera_type.lower()
        
        return best_tera, best_score

    def _find_coverage_gaps(self, battle: AbstractBattle):
        """Find opponent Pokemon we have poor coverage against"""
        coverage_gaps = []
        
        for opp_pokemon in battle.opponent_team.values():
            if not opp_pokemon or opp_pokemon.fainted:
                continue
                
            best_effectiveness = 0
            for our_pokemon in battle.team.values():
                if our_pokemon.fainted:
                    continue
                    
                for our_type in our_pokemon.types:
                    if our_type:
                        effectiveness = self._calculate_type_effectiveness(str(our_type), opp_pokemon.types)
                        best_effectiveness = max(best_effectiveness, effectiveness)
            
            # If our best coverage is not very effective or worse
            if best_effectiveness <= 0.5:
                coverage_gaps.append((opp_pokemon, best_effectiveness))
        
        return coverage_gaps

    def _analyze_opponent_team(self, battle: AbstractBattle):
        threats = []
        for species, pokemon in battle.opponent_team.items():
            if pokemon and not pokemon.fainted:
                # Use enhanced threat assessment
                threat_level = self._enhanced_threat_assessment(pokemon, battle)
                
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
            active = battle.active_pokemon
            
            # Evaluate optimal Tera type
            best_tera, tera_score = self._evaluate_tera_options(active, battle)
            
            # Use Tera if we get significant benefit (score > 1.0)
            if tera_score > 1.0:
                # Last full HP mon
                if (
                        len([m for m in battle.team.values() if m.current_hp_fraction == 1])
                        == 1
                        and active.current_hp_fraction == 1
                ):
                    return True
                # Significant type advantage gained
                if tera_score > 2.0 and active.current_hp_fraction > 0.5:
                    return True
                # Matchup advantage and full hp on full hp
                if (
                        self._estimate_matchup(active, battle.opponent_active_pokemon) > 0
                        and active.current_hp_fraction == 1
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

        # Assess current battle state
        endgame = self._assess_endgame_situation(battle)
        momentum = self._detect_momentum_opportunities(battle)

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

            # Enhanced setup logic with endgame and momentum considerations
            threats = self._analyze_opponent_team(battle)
            high_threat_count = sum(1 for _, threat_level in threats if threat_level >= 2)
            
            # More aggressive setup in favorable endgame situations
            setup_threshold = 0 if endgame['win_urgency'] >= 2 else 0.8
            threat_limit = 3 if endgame['win_urgency'] >= 2 else 2
            
            # Prioritize setup when we have momentum
            should_setup = False
            if momentum['momentum_score'] >= 3 and "setup_opportunity" in momentum['opportunities']:
                should_setup = True  # Force setup when we have a clear opportunity
            elif (
                    active.current_hp_fraction >= setup_threshold
                    and self._estimate_matchup(active, opponent) > 0
                    and high_threat_count <= threat_limit
            ):
                should_setup = True
            
            if should_setup:
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
                        # In endgame or with momentum, be more aggressive about setup
                        has_priority = any(move_id in self.PRIORITY_MOVES 
                                         for move_id in opponent.moves)
                        if (not has_priority or 
                            active.current_hp_fraction > 0.8 or 
                            endgame['win_urgency'] >= 2 or
                            momentum['momentum_score'] >= 3):
                            return self.create_order(move)

            # Enhanced move selection with endgame/momentum consideration
            move_scores = []
            for m in battle.available_moves:
                # Skip completely ineffective moves (0x damage)
                type_effectiveness = opponent.damage_multiplier(m)
                if type_effectiveness == 0:
                    continue
                    
                base_score = (m.base_power
                              * (1.5 if m.type in active.types else 1)
                              * (
                                  physical_ratio
                                  if m.category == MoveCategory.PHYSICAL
                                  else special_ratio
                              )
                              * m.accuracy
                              * m.expected_hits
                              * type_effectiveness)
                
                # Add move value analysis from GenData
                move_value = self._analyze_move_value(m, battle)
                base_score += move_value * 10  # Scale the bonus appropriately
                
                # Boost aggressive moves in endgame/momentum situations
                if endgame['win_urgency'] >= 2 or momentum['momentum_score'] >= 2:
                    # Prioritize high power moves when we need to win
                    if m.base_power >= 100:
                        base_score *= 1.3
                    # Prioritize multi-hit moves that can break through
                    if m.expected_hits > 1:
                        base_score *= 1.2
                
                # In advantageous endgame, prioritize moves that secure wins
                if endgame['is_endgame'] and endgame['advantage_score'] > 0:
                    # Prioritize moves that can KO
                    estimated_damage = base_score / (opponent.current_hp_fraction * 100)
                    if estimated_damage >= 0.8:  # Likely KO
                        base_score *= 1.4
                
                move_scores.append((m, base_score))
            
            # Fallback if all moves are ineffective (shouldn't happen in normal play)
            if not move_scores:
                move_scores = [(m, 1) for m in battle.available_moves]
            
            best_move = max(move_scores, key=lambda x: x[1])[0]
            
            # More aggressive tera usage in critical moments
            should_tera = self._should_tera(battle, n_remaining_mons)
            if not should_tera and battle.can_tera:
                # Force tera in critical endgame situations
                if (endgame['win_urgency'] >= 3 or 
                    (momentum['momentum_score'] >= 4 and endgame['is_endgame'])):
                    should_tera = True
            
            return self.create_order(best_move, terastallize=should_tera)

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
            
            # Setup threat management: Priority #1
            setup_threats = self._detect_setup_threats(battle)
            if setup_threats:
                biggest_setup_threat = setup_threats[0]  # (pokemon, threat_score, setup_moves)
                threat_pokemon, threat_score, setup_moves = biggest_setup_threat
                
                # High priority: counter immediate setup threats
                if threat_score >= 5:  # Critical setup threat
                    best_counter = None
                    best_counter_score = -999
                    
                    for switch in switches:
                        counter_score = 0
                        
                        # Prioritize Pokemon that resist the setup sweeper
                        matchup = self._estimate_matchup(switch, threat_pokemon)
                        if matchup > 0:
                            counter_score += matchup * 2
                        
                        # Taunt users counter setup
                        if 'taunt' in switch.moves:
                            counter_score += 3
                        
                        # Priority move users can revenge kill
                        for move_id in switch.moves:
                            if move_id in self.PRIORITY_MOVES:
                                counter_score += 2
                                break
                        
                        # Defensive Pokemon can often handle setup sweepers
                        if switch.species == 'arceusfairy' and switch.current_hp_fraction > 0.6:
                            counter_score += 2
                        
                        if counter_score > best_counter_score:
                            best_counter_score = counter_score
                            best_counter = switch
                    
                    if best_counter and best_counter_score >= 3:
                        return self.create_order(best_counter)

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