import random
import time
import math
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass
from enum import Enum

from poke_env.battle.abstract_battle import AbstractBattle
from poke_env.battle.double_battle import DoubleBattle
from poke_env.battle.move_category import MoveCategory
from poke_env.battle.pokemon import Pokemon
from poke_env.battle.side_condition import SideCondition
from poke_env.battle.target import Target
from poke_env.player.battle_order import (
    BattleOrder,
    DefaultBattleOrder,
    DoubleBattleOrder,
    SingleBattleOrder,
)
from poke_env.player.player import Player
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union, Tuple
from abc import ABC, abstractmethod
import random
import math

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

class PlayerType(Enum):
    MAX = "MAX"  # Our turn
    MIN = "MIN"  # Opponent's turn
    CHANCE = "CHANCE"  # Random events


class ActionType(Enum):
    MOVE = "MOVE"
    SWITCH = "SWITCH"
    DYNAMAX_MOVE = "DYNAMAX_MOVE"


@dataclass
class Action:
    """Represents a single game action"""
    type: ActionType
    target: str  # move name or pokemon name
    is_dynamax: bool = False

    def __str__(self):
        prefix = "DMAX_" if self.is_dynamax else ""
        return f"{prefix}{self.type.value}_{self.target}"


@dataclass
class GameState:
    """Represents complete game state"""
    my_active: str  # "Pokemon_X"
    opp_active: str  # "Pokemon_Y"
    my_team_hp: List[float]  # [1.0, 0.8, 1.0, 0.0, 1.0]
    opp_team_hp: List[float]
    turn_number: int
    field_conditions: Dict[str, bool] = field(default_factory=dict)

    def __hash__(self):
        return hash((self.my_active, self.opp_active, tuple(self.my_team_hp),
                     tuple(self.opp_team_hp), self.turn_number))


@dataclass
class TreeNode:
    """Base class for all tree nodes"""
    state: GameState
    player: PlayerType
    parent: Optional['TreeNode'] = None
    children: Dict[str, 'TreeNode'] = field(default_factory=dict)

    # MCTS statistics
    visits: int = 0
    total_value: float = 0.0

    @property
    def average_value(self) -> float:
        return self.total_value / self.visits if self.visits > 0 else 0.0

    @abstractmethod
    def get_legal_actions(self) -> List[Union[Action, str]]:
        """Return legal actions/outcomes for this node"""
        pass

    @abstractmethod
    def select_best_child(self, exploration_constant: float = 1.4) -> 'TreeNode':
        """Select best child using appropriate strategy"""
        pass


class MaxNode(TreeNode):
    """Node representing our turn (maximizing player)"""

    def __init__(self, state: GameState, parent: Optional[TreeNode] = None):
        super().__init__(state, PlayerType.MAX, parent)
        self.untried_actions: List[Action] = self._generate_actions()

    def _generate_actions(self) -> List[Action]:
        """Generate all possible actions from this state"""
        actions = []
        # Add regular moves only (no dynamax)
        for move in ["Move_A", "Move_B", "Move_C", "Move_D"]:
            actions.append(Action(ActionType.MOVE, move))
            # Remove dynamax actions since they're not available
            # actions.append(Action(ActionType.MOVE, move, is_dynamax=True))

        # Add switches
        for i, hp in enumerate(self.state.my_team_hp):
            if hp > 0 and i != self._get_active_index():
                actions.append(Action(ActionType.SWITCH, f"Pokemon_{i}"))

        return actions

    def _get_active_index(self) -> int:
        """Get index of currently active pokemon"""
        return int(self.state.my_active.split('_')[1]) if '_' in self.state.my_active else 0

    def get_legal_actions(self) -> List[Action]:
        return self.untried_actions

    def select_best_child(self, exploration_constant: float = 1.4) -> 'TreeNode':
        """UCB1 selection for MAX nodes"""
        if not self.children:
            return self

        best_child = None
        best_value = float('-inf')

        for child in self.children.values():
            if child.visits == 0:
                ucb_value = float('inf')
            else:
                exploitation = child.average_value
                exploration = exploration_constant * math.sqrt(
                    math.log(self.visits) / child.visits
                )
                ucb_value = exploitation + exploration

            if ucb_value > best_value:
                best_value = ucb_value
                best_child = child

        return best_child


class MinNode(TreeNode):
    """Node representing opponent's turn (minimizing player)"""

    def __init__(self, state: GameState, parent: Optional[TreeNode] = None):
        super().__init__(state, PlayerType.MIN, parent)
        self.untried_actions: List[Action] = self._generate_opponent_actions()

    def _generate_opponent_actions(self) -> List[Action]:
        """Generate opponent's possible actions"""
        actions = []
        for move in ["Opp_Move_A", "Opp_Move_B", "Opp_Move_C"]:
            actions.append(Action(ActionType.MOVE, move))
            # Remove dynamax for opponent too

        # Add opponent switches
        for i, hp in enumerate(self.state.opp_team_hp):
            if hp > 0 and i != self._get_opp_active_index():
                actions.append(Action(ActionType.SWITCH, f"Opp_Pokemon_{i}"))

        return actions

    def _get_opp_active_index(self) -> int:
        return int(self.state.opp_active.split('_')[1]) if '_' in self.state.opp_active else 0

    def get_legal_actions(self) -> List[Action]:
        return self.untried_actions

    def select_best_child(self, exploration_constant: float = 1.4) -> 'TreeNode':
        """UCB1 selection for MIN nodes (inverted values)"""
        if not self.children:
            return self

        best_child = None
        best_value = float('-inf')

        for child in self.children.values():
            if child.visits == 0:
                ucb_value = float('inf')
            else:
                # MIN node wants to minimize our value, so invert
                exploitation = 1.0 - child.average_value
                exploration = exploration_constant * math.sqrt(
                    math.log(self.visits) / child.visits
                )
                ucb_value = exploitation + exploration

            if ucb_value > best_value:
                best_value = ucb_value
                best_child = child

        return best_child


class ChanceNode(TreeNode):
    """Node representing random events"""

    def __init__(self, state: GameState, action: Action, parent: Optional[TreeNode] = None):
        super().__init__(state, PlayerType.CHANCE, parent)
        self.action = action
        self.outcomes: List[Tuple[str, float]] = self._generate_outcomes()

    def _generate_outcomes(self) -> List[Tuple[str, float]]:
        """Generate random outcomes with probabilities"""
        if self.action.type == ActionType.MOVE:
            return [
                ("Hit", 0.85),
                ("Miss", 0.15)
            ]
        else:
            return [("Success", 1.0)]  # Switches always succeed

    def get_legal_actions(self) -> List[str]:
        return [outcome[0] for outcome in self.outcomes]

    def select_best_child(self, exploration_constant: float = 1.4) -> 'TreeNode':
        """Random selection weighted by probability"""
        if not self.children:
            return self

        # Weight by probability and visit count
        weights = []
        for outcome_name, probability in self.outcomes:
            if outcome_name in self.children:
                child = self.children[outcome_name]
                # Encourage exploration of under-visited but probable outcomes
                weight = probability * (1.0 + 1.0 / (child.visits + 1))
                weights.append((child, weight))

        if not weights:
            return self

        # Weighted random selection
        total_weight = sum(w[1] for w in weights)
        rand_val = random.uniform(0, total_weight)

        cumulative = 0.0
        for child, weight in weights:
            cumulative += weight
            if rand_val <= cumulative:
                return child

        return weights[-1][0]  # Fallback


class GameStateManager:
    """Enhanced game state manager with real Pokemon logic"""

    def __init__(self):
        self.damage_calculator = SimpleDamageCalculator()

    def apply_action(self, state: GameState, action: Action) -> GameState:
        """Apply an action and return new state"""
        new_state = GameState(
            my_active=state.my_active,
            opp_active=state.opp_active,
            my_team_hp=state.my_team_hp.copy(),
            opp_team_hp=state.opp_team_hp.copy(),
            turn_number=state.turn_number + 1,
            field_conditions=state.field_conditions.copy()
        )

        if action.type == ActionType.SWITCH:
            # Handle switching
            new_state.my_active = action.target

        elif action.type in [ActionType.MOVE, ActionType.DYNAMAX_MOVE]:
            # Simulate damage
            damage = self.damage_calculator.estimate_damage(action, state)
            opp_active_idx = self._get_active_index(state.opp_active)
            if opp_active_idx < len(new_state.opp_team_hp):
                new_state.opp_team_hp[opp_active_idx] = max(0,
                                                            state.opp_team_hp[opp_active_idx] - damage)

        return new_state

    def _get_active_index(self, pokemon_name: str) -> int:
        """Get team index from pokemon name"""
        # Simple mapping - you can make this more sophisticated
        name_to_index = {
            "deoxys-speed": 0, "kingambit": 1, "zacian-crowned": 2,
            "arceus-fairy": 3, "eternatus": 4, "koraidon": 5
        }
        return name_to_index.get(pokemon_name.lower(), 0)

    def is_terminal(self, state: GameState) -> bool:
        """Check if game is over"""
        my_alive = sum(1 for hp in state.my_team_hp if hp > 0)
        opp_alive = sum(1 for hp in state.opp_team_hp if hp > 0)
        return my_alive == 0 or opp_alive == 0

    def evaluate_state(self, state: GameState) -> float:
        """Evaluate position using proven heuristics"""
        if self.is_terminal(state):
            my_alive = sum(1 for hp in state.my_team_hp if hp > 0)
            return 1.0 if my_alive > 0 else 0.0

        # Use team count heavily (like your original heuristic)
        my_count = sum(1 for hp in state.my_team_hp if hp > 0)
        opp_count = sum(1 for hp in state.opp_team_hp if hp > 0)

        if my_count + opp_count == 0:
            return 0.5

        # Team advantage is primary factor
        team_score = my_count / (my_count + opp_count)

        # HP advantage is secondary
        my_hp = sum(state.my_team_hp)
        opp_hp = sum(state.opp_team_hp)
        hp_score = my_hp / (my_hp + opp_hp) if (my_hp + opp_hp) > 0 else 0.5

        # Weight team count much more heavily (like successful bots)
        return 0.8 * team_score + 0.2 * hp_score

    def apply_chance_outcome(self, state: GameState, outcome: str) -> GameState:
        """Apply a chance outcome to state"""
        # For now, just return the state unchanged
        # Later we can add specific logic for different outcomes
        return state

    def apply_action_with_outcome(self, state: GameState, action: Action, outcome: str) -> GameState:
        """Apply action with specific random outcome"""
        new_state = self.apply_action(state, action)

        # Modify based on outcome
        if outcome == "Miss" and action.type in [ActionType.MOVE, ActionType.DYNAMAX_MOVE]:
            # Move missed - no damage dealt, just return state without damage
            return GameState(
                my_active=new_state.my_active,
                opp_active=new_state.opp_active,
                my_team_hp=state.my_team_hp.copy(),  # Use original HP (no damage)
                opp_team_hp=state.opp_team_hp.copy(),  # Use original HP (no damage)
                turn_number=new_state.turn_number,
                field_conditions=new_state.field_conditions
            )
        elif outcome == "Hit" and action.type in [ActionType.MOVE, ActionType.DYNAMAX_MOVE]:
            # Move hit - damage was already applied in apply_action
            return new_state

        return new_state


class SimpleDamageCalculator:
    """Simple damage calculator for MCTS simulation"""

    def estimate_damage(self, action: Action, state: GameState) -> float:
        """Estimate damage as HP percentage"""
        if action.type == ActionType.MOVE:
            base_damage = 0.25  # 25% damage for normal moves
            if action.is_dynamax:
                base_damage = 0.4  # 40% for dynamax moves
            return base_damage
        return 0.0  # No damage for switches

class MCTSAlgorithm:
    """Core MCTS algorithm implementation"""

    def __init__(self, game_manager: GameStateManager):
        self.game_manager = game_manager
        self.exploration_constant = 1.4
        self.max_simulations = 1000
        self.max_depth = 6  # Can go deeper without time pressure
        self.min_visits_for_confidence = 50  # Ensure robust decisions

    def search(self, root_state: GameState) -> Action:
        """Main MCTS search function"""
        root = MaxNode(root_state)

        for simulation in range(self.max_simulations):
            # Four phases of MCTS
            leaf = self._selection(root)
            expanded_node = self._expansion(leaf)
            value = self._simulation(expanded_node)
            self._backpropagation(expanded_node, value)

            # Optional: Early termination if we have high confidence
            if simulation > 100 and self._has_confident_choice(root):
                print(f"Early termination at simulation {simulation} - confident choice found")
                break

        # Select best action
        return self._select_final_action(root)

    def _selection(self, node: TreeNode) -> TreeNode:
        """Phase 1: Navigate tree using UCB1 until reaching expandable node"""
        current = node
        path_depth = 0

        while (len(current.children) > 0 and
               len(current.get_legal_actions()) == 0 and
               path_depth < self.max_depth):

            current = current.select_best_child(self.exploration_constant)
            path_depth += 1

            if current is None:
                break

        return current

    def _expansion(self, node: TreeNode) -> TreeNode:
        """Phase 2: Add new child node if possible"""
        if self.game_manager.is_terminal(node.state):
            return node

        # Check if we can expand this node
        untried_actions = node.get_legal_actions()
        if not untried_actions:
            return node

        # Select random untried action
        action = random.choice(untried_actions)
        node.untried_actions.remove(action)

        # Create child node based on action and current player
        if node.player == PlayerType.MAX:
            child_node = self._create_child_from_max_action(node, action)
        elif node.player == PlayerType.MIN:
            child_node = self._create_child_from_min_action(node, action)
        elif node.player == PlayerType.CHANCE:
            child_node = self._create_child_from_chance_outcome(node, action)

        # Add to tree
        action_key = str(action)
        node.children[action_key] = child_node

        return child_node

    def _create_child_from_max_action(self, parent: MaxNode, action: Action) -> TreeNode:
        """Create child node after MAX player takes action"""
        # For now, skip chance nodes and go directly to deterministic outcomes
        # This simplifies the tree while we debug
        new_state = self.game_manager.apply_action(parent.state, action)
        return MinNode(new_state, parent)

    def _create_child_from_min_action(self, parent: MinNode, action: Action) -> TreeNode:
        """Create child node after MIN player takes action"""
        # Same - skip chance nodes for now
        new_state = self.game_manager.apply_action(parent.state, action)
        return MaxNode(new_state, parent)

    def _create_child_from_chance_outcome(self, parent: ChanceNode, outcome: str) -> TreeNode:
        """Create child node after chance outcome is determined"""
        # Apply the original action with the specific outcome
        new_state = self.game_manager.apply_action_with_outcome(
            parent.state, parent.action, outcome
        )

        # Determine whose turn it is after the chance event
        if parent.parent and parent.parent.player == PlayerType.MAX:
            return MinNode(new_state, parent)
        else:
            return MaxNode(new_state, parent)

    def _action_has_randomness(self, action: Action) -> bool:
        """Check if action involves random elements"""
        # Temporarily disable randomness to simplify tree
        return False

    def _simulation(self, node: TreeNode) -> float:
        """Phase 3: Simulate game to completion using heuristics"""
        current_state = node.state
        current_player = node.player
        depth = 0

        # Quick terminal check
        if self.game_manager.is_terminal(current_state):
            return self.game_manager.evaluate_state(current_state)

        # Limited depth simulation to avoid infinite games
        simulation_depth_limit = 8

        while depth < simulation_depth_limit and not self.game_manager.is_terminal(current_state):
            # Use heuristic action selection for fast simulation
            if current_player == PlayerType.MAX:
                action = self._heuristic_action_selection(current_state, is_max_player=True)
                current_player = PlayerType.MIN
            elif current_player == PlayerType.MIN:
                action = self._heuristic_action_selection(current_state, is_max_player=False)
                current_player = PlayerType.MAX
            elif current_player == PlayerType.CHANCE:
                # Handle chance events
                outcomes = self._get_chance_outcomes(current_state)
                outcome = random.choices(
                    [o[0] for o in outcomes],
                    weights=[o[1] for o in outcomes]
                )[0]
                # Apply outcome and continue
                current_state = self.game_manager.apply_chance_outcome(current_state, outcome)
                continue

            # Apply action
            current_state = self.game_manager.apply_action(current_state, action)
            depth += 1

        # Return evaluation of final state
        return self.game_manager.evaluate_state(current_state)

    def _heuristic_action_selection(self, state: GameState, is_max_player: bool) -> Action:
        """Fast heuristic action selection for simulation phase"""
        # Generate possible actions
        if is_max_player:
            temp_node = MaxNode(state)
        else:
            temp_node = MinNode(state)

        actions = temp_node.get_legal_actions()
        if not actions:
            return Action(ActionType.MOVE, "Default_Move")  # Fallback

        # Simple heuristic: prefer attacking moves, then switches
        move_actions = [a for a in actions if a.type in [ActionType.MOVE, ActionType.DYNAMAX_MOVE]]
        if move_actions:
            return random.choice(move_actions)

        return random.choice(actions)

    def _get_chance_outcomes(self, state: GameState) -> List[Tuple[str, float]]:
        """Get possible chance outcomes with probabilities"""
        return [("Hit", 0.85), ("Miss", 0.15)]  # Default for moves

    def _backpropagation(self, node: TreeNode, value: float):
        """Phase 4: Update statistics up the tree"""
        current = node

        while current is not None:
            current.visits += 1

            # Update value based on player type
            if current.player == PlayerType.MAX:
                current.total_value += value
            elif current.player == PlayerType.MIN:
                current.total_value += (1.0 - value)  # MIN wants to minimize MAX's value
            elif current.player == PlayerType.CHANCE:
                current.total_value += value  # Chance nodes are neutral

            current = current.parent

    def _has_confident_choice(self, root: MaxNode) -> bool:
        """Check if we have a confident choice (optional early termination)"""
        if len(root.children) < 2:
            return False

        # Sort children by visit count
        children_by_visits = sorted(
            root.children.values(),
            key=lambda c: c.visits,
            reverse=True
        )

        if len(children_by_visits) < 2:
            return False

        best = children_by_visits[0]
        second_best = children_by_visits[1]

        # Confident if best has many visits and clear value advantage
        return (best.visits > self.min_visits_for_confidence and
                best.average_value > second_best.average_value + 0.1)

    def _select_final_action(self, root: MaxNode) -> Action:
        """Select the final action based on tree statistics"""
        if not root.children:
            # Fallback to random action
            actions = root.get_legal_actions()
            return random.choice(actions) if actions else Action(ActionType.MOVE, "Default")

        # Strategy: Choose most visited child (most robust)
        best_child = max(root.children.values(), key=lambda c: c.visits)

        # Find the action that led to this child
        for action_key, child in root.children.items():
            if child == best_child:
                # Parse action from key - this is simplified
                return self._parse_action_from_key(action_key)

        # Fallback
        return Action(ActionType.MOVE, "Fallback")

    def _parse_action_from_key(self, action_key: str) -> Action:
        """Parse action from string key"""
        # This is a simplified parser - you'd want more robust parsing
        if "DMAX_" in action_key:
            parts = action_key.replace("DMAX_", "").split("_")
            return Action(ActionType.DYNAMAX_MOVE, "_".join(parts[1:]), is_dynamax=True)
        elif "MOVE_" in action_key:
            return Action(ActionType.MOVE, action_key.replace("MOVE_", ""))
        elif "SWITCH_" in action_key:
            return Action(ActionType.SWITCH, action_key.replace("SWITCH_", ""))
        else:
            return Action(ActionType.MOVE, "Default")


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

        # MCTS components
        self.game_manager = GameStateManager()
        self.mcts = MCTSAlgorithm(self.game_manager)
        self.mcts.max_simulations = 500  # Reduce for faster testing

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

    def _battle_to_gamestate(self, battle: AbstractBattle) -> GameState:
        """Convert real battle to abstract GameState"""
        # Get team HP ratios
        my_team_hp = []
        for i in range(6):  # Assuming 6 pokemon teams
            if i < len(battle.team):
                pokemon_id = list(battle.team.keys())[i]
                pokemon = battle.team[pokemon_id]
                my_team_hp.append(pokemon.current_hp_fraction)
            else:
                my_team_hp.append(0.0)

        opp_team_hp = []
        for i in range(6):
            if i < len(battle.opponent_team):
                pokemon_id = list(battle.opponent_team.keys())[i]
                pokemon = battle.opponent_team[pokemon_id]
                opp_team_hp.append(pokemon.current_hp_fraction if pokemon else 0.5)  # Unknown = 50%
            else:
                opp_team_hp.append(0.0)

        return GameState(
            my_active=battle.active_pokemon.species if battle.active_pokemon else "unknown",
            opp_active=battle.opponent_active_pokemon.species if battle.opponent_active_pokemon else "unknown",
            my_team_hp=my_team_hp,
            opp_team_hp=opp_team_hp,
            turn_number=battle.turn,
            field_conditions={}  # Can be enhanced later
        )

    def _action_to_battle_order(self, action: Action, battle: AbstractBattle) -> BattleOrder:
        """Convert abstract Action to real BattleOrder"""
        if action.type == ActionType.MOVE:
            # Map to actual available moves
            move_mapping = {
                "Move_A": 0, "Move_B": 1, "Move_C": 2, "Move_D": 3
            }

            if action.target in move_mapping and move_mapping[action.target] < len(battle.available_moves):
                move = battle.available_moves[move_mapping[action.target]]
                # Don't pass dynamax=True since it's not available
                return self.create_order(move)
            elif battle.available_moves:
                # Fallback to best move using your heuristic
                active = battle.active_pokemon
                opponent = battle.opponent_active_pokemon
                physical_ratio = self._stat_estimation(active, "atk") / self._stat_estimation(opponent, "def")
                special_ratio = self._stat_estimation(active, "spa") / self._stat_estimation(opponent, "spd")

                best_move = max(
                    battle.available_moves,
                    key=lambda m: m.base_power * (1.5 if m.type in active.types else 1) *
                                  (physical_ratio if m.category == MoveCategory.PHYSICAL else special_ratio) *
                                  m.accuracy * m.expected_hits * opponent.damage_multiplier(m),
                )
                return self.create_order(best_move)

        elif action.type == ActionType.SWITCH:
            if battle.available_switches:
                try:
                    switch_index = int(action.target.split('_')[1]) if '_' in action.target else 0
                    if switch_index < len(battle.available_switches):
                        return self.create_order(battle.available_switches[switch_index])
                except (ValueError, IndexError):
                    pass

                # Fallback to best switch
                opponent = battle.opponent_active_pokemon
                best_switch = max(
                    battle.available_switches,
                    key=lambda s: self._estimate_matchup(s, opponent),
                )
                return self.create_order(best_switch)

        # Ultimate fallback
        return self._heuristic_choose_move(battle)
    
    def _should_use_mcts(self, battle: AbstractBattle) -> bool:
        """Decide when to use MCTS vs heuristic"""
        # Use MCTS for important decisions
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        if not active or not opponent:
            return False

        # Use MCTS when:
        # 1. Both pokemon are healthy (important decisions)
        # 2. Close matchup (not obvious what to do)
        # 3. Late game (every decision matters)

        both_healthy = active.current_hp_fraction > 0.5 and opponent.current_hp_fraction > 0.5
        close_matchup = abs(self._estimate_matchup(active, opponent)) < 1.0
        late_game = sum(1 for p in battle.team.values() if not p.fainted) <= 3

        return both_healthy or close_matchup or late_game

    def choose_move(self, battle: AbstractBattle):
        if isinstance(battle, DoubleBattle):
            return self.choose_random_doubles_move(battle)

        # Safety check
        if not battle.active_pokemon or not battle.opponent_active_pokemon:
            return self.choose_random_move(battle)

        # Decide whether to use MCTS or heuristic
        if self._should_use_mcts(battle):
            try:
                print("Using MCTS for decision...")
                # Convert battle to abstract state
                game_state = self._battle_to_gamestate(battle)

                # Run MCTS
                best_action = self.mcts.search(game_state)

                # Convert back to battle order
                battle_order = self._action_to_battle_order(best_action, battle)
                print(f"MCTS chose: {best_action}")
                return battle_order

            except Exception as e:
                print(f"MCTS failed: {e}, falling back to heuristic")
                # Fall through to heuristic

        # Use your original heuristic as fallback
        print("Using heuristic decision...")
        return self._heuristic_choose_move(battle)

    def _heuristic_choose_move(self, battle: AbstractBattle):
        """Your original choose_move logic as a separate method"""
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        # [Copy your entire original choose_move logic here]
        # This is all your existing code from "# Rough estimation of damage ratio" onwards
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