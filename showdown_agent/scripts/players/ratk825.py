from poke_env.battle import AbstractBattle
from poke_env.player import Player
from poke_env.data import GenData
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional, Any
from enum import Enum

# Team defined at module level for expert_main.py compatibility
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


class ActionType(Enum):
    MOVE = "move"
    SWITCH = "switch"


@dataclass
class GameAction:
    action_type: ActionType
    move_or_pokemon: Any
    expected_value: float
    reasoning: str
    priority: int = 0


@dataclass
class BattleContext:
    my_pokemon: Any
    opponent_pokemon: Any
    battle: AbstractBattle
    turn_number: int
    gen_data: Any


class DecisionLayer(ABC):
    def __init__(self, priority: int):
        self.priority = priority

    @abstractmethod
    def evaluate(self, context: BattleContext) -> List[GameAction]:
        pass

    @abstractmethod
    def should_override(self, context: BattleContext) -> bool:
        pass


class EmergencyLayer(DecisionLayer):
    def __init__(self):
        super().__init__(priority=1000)

    def should_override(self, context: BattleContext) -> bool:
        return self._is_emergency(context)

    def evaluate(self, context: BattleContext) -> List[GameAction]:
        actions = []

        if self._will_be_kod(context):
            for switch in context.battle.available_switches:
                actions.append(GameAction(
                    ActionType.SWITCH, switch, 100.0, "Emergency switch", self.priority
                ))

        if self._should_heal(context):
            recovery_moves = [m for m in context.battle.available_moves
                              if m.id in ['recover', 'roost', 'softboiled']]
            for move in recovery_moves:
                actions.append(GameAction(
                    ActionType.MOVE, move, 95.0, "Emergency heal", self.priority
                ))

        return actions

    def _is_emergency(self, context: BattleContext) -> bool:
        return self._will_be_kod(context) or self._should_heal(context)

    def _will_be_kod(self, context: BattleContext) -> bool:
        if not context.my_pokemon:
            return False
        hp_fraction = getattr(context.my_pokemon, 'current_hp_fraction', 1.0) or 1.0
        return hp_fraction <= 0.20

    def _should_heal(self, context: BattleContext) -> bool:
        if not context.my_pokemon:
            return False
        hp_fraction = getattr(context.my_pokemon, 'current_hp_fraction', 1.0) or 1.0
        has_recovery = any(m.id in ['recover', 'roost', 'softboiled']
                           for m in context.battle.available_moves)
        return hp_fraction <= 0.35 and has_recovery


class StrategicLayer(DecisionLayer):
    def __init__(self):
        super().__init__(priority=800)

    def should_override(self, context: BattleContext) -> bool:
        return self._should_setup(context) or self._should_disrupt(context)

    def evaluate(self, context: BattleContext) -> List[GameAction]:
        actions = []

        # Setup moves
        if self._should_setup(context):
            setup_moves = [m for m in context.battle.available_moves
                           if m.id in ['swordsdance', 'calmmind', 'agility']]
            for move in setup_moves:
                safety = self._setup_safety(context)
                if safety >= 0.6:
                    actions.append(GameAction(
                        ActionType.MOVE, move, safety * 120, f"Setup {move.id}", self.priority
                    ))

        # Disruption
        if self._should_disrupt(context):
            taunt_moves = [m for m in context.battle.available_moves if m.id == 'taunt']
            for move in taunt_moves:
                actions.append(GameAction(
                    ActionType.MOVE, move, 90.0, "Disrupt with taunt", self.priority
                ))

        # Hazard setting
        hazard_moves = [m for m in context.battle.available_moves
                        if m.id in ['spikes', 'stealthrock']]
        for move in hazard_moves:
            if self._should_set_hazards(context, move):
                actions.append(GameAction(
                    ActionType.MOVE, move, 85.0, f"Set {move.id}", self.priority
                ))

        return actions

    def _should_setup(self, context: BattleContext) -> bool:
        if not context.my_pokemon:
            return False
        hp_fraction = getattr(context.my_pokemon, 'current_hp_fraction', 1.0) or 1.0
        has_setup = any(m.id in ['swordsdance', 'calmmind', 'agility']
                        for m in context.battle.available_moves)
        return hp_fraction >= 0.7 and has_setup

    def _should_disrupt(self, context: BattleContext) -> bool:
        has_taunt = any(m.id == 'taunt' for m in context.battle.available_moves)
        return has_taunt and context.turn_number <= 6

    def _should_set_hazards(self, context: BattleContext, move) -> bool:
        if move.id == 'spikes':
            current_spikes = context.battle.opponent_side_conditions.get('spikes', 0)
            return current_spikes < 2 and context.turn_number <= 8
        elif move.id == 'stealthrock':
            has_rocks = context.battle.opponent_side_conditions.get('stealthrock', 0)
            return not has_rocks and context.turn_number <= 5
        return False

    def _setup_safety(self, context: BattleContext) -> float:
        if not context.my_pokemon:
            return 0.0
        hp_fraction = getattr(context.my_pokemon, 'current_hp_fraction', 1.0) or 1.0
        return min(hp_fraction * 1.3, 1.0)


class TacticalLayer(DecisionLayer):
    def __init__(self):
        super().__init__(priority=500)

    def _type_name(self, t) -> Optional[str]:
        if t is None:
            return None
        # poke_env Type objects often have .name; strings are fine too
        return (getattr(t, "name", t)).upper()

    def _pokemon_type_names(self, pokemon) -> List[str]:
        names = []
        for attr in ("type_1", "type_2"):
            t = getattr(pokemon, attr, None)
            if t:
                names.append(self._type_name(t))
        return names

    def _move_type_name(self, context: BattleContext, move) -> Optional[str]:
        # Prefer static data (stable and complete), fall back to runtime object
        mi = context.gen_data.moves.get(move.id, {})
        t = mi.get("type")
        if t:
            return str(t).upper()
        # fallback to poke_env move.type if present
        return self._type_name(getattr(move, "type", None))

    def _type_effectiveness(self, context: BattleContext, move_type: Optional[str],
                            defender_types: List[str]) -> float:
        if not move_type:
            return 1.0
        chart = context.gen_data.type_chart
        eff = 1.0
        for d in defender_types:
            # chart[DEFENDER][ATTACKER]
            eff *= chart.get(d, {}).get(move_type, 1.0)
        return eff

    def _stab_multiplier(self, context: BattleContext, move_type: Optional[str]) -> float:
        if not move_type:
            return 1.0
        original_types = set(self._pokemon_type_names(context.my_pokemon))
        # Try to read tera type if available
        tera = getattr(context.my_pokemon, "tera_type", None) \
               or getattr(context.my_pokemon, "terastallize_type", None)
        tera = self._type_name(tera)

        # SV STAB (approximation):
        # - If move matches Tera type => 2.0
        # - Else if move matches original types => 1.5
        # - Else => 1.0
        if tera and move_type == tera:
            return 2.0
        if move_type in original_types:
            return 1.5
        return 1.0

    def should_override(self, context: BattleContext) -> bool:
        return False

    def evaluate(self, context: BattleContext) -> List[GameAction]:
        actions = []

        # Attacking moves
        for move in context.battle.available_moves:
            if move.base_power and move.base_power > 0:
                score = self._move_score(context, move)
                if score > 0:
                    actions.append(GameAction(
                        ActionType.MOVE, move, score, f"Attack with {move.id}", self.priority
                    ))

        # Utility moves
        for move in context.battle.available_moves:
            if move.id in ['thunderwave', 'recover']:
                score = self._utility_score(context, move)
                if score > 0:
                    actions.append(GameAction(
                        ActionType.MOVE, move, score, f"Utility {move.id}", self.priority
                    ))

        # Switches
        for switch in context.battle.available_switches:
            score = self._switch_score(context, switch)
            if score > 25:
                actions.append(GameAction(
                    ActionType.SWITCH, switch, score, f"Switch to {switch.species}", self.priority
                ))

        return actions

    def _move_score(self, context: BattleContext, move) -> float:
        mi = context.gen_data.moves.get(move.id, {})
        base_power = mi.get("basePower", move.base_power or 0)

        # Accuracy -> [0..1]
        acc = mi.get("accuracy", move.accuracy)
        if acc is True:
            acc = 1.0
        elif acc in (False, None):
            acc = 0.0
        else:
            acc = float(acc) / 100.0

        # Type effectiveness via GenData.type_chart
        move_type = self._move_type_name(context, move)
        defender_types = self._pokemon_type_names(context.opponent_pokemon)
        effectiveness = self._type_effectiveness(context, move_type, defender_types)

        # Skip terrible hits / immunities
        if effectiveness == 0.0 or effectiveness <= 0.25:
            return 0.0

        # STAB (with rough Tera handling)
        stab = self._stab_multiplier(context, move_type)

        # Simple species/context bonuses you already had
        species_bonus = self._species_move_bonus(context, move)
        priority_bonus = 20 if getattr(move, "priority", 0) > 0 else 0

        return (base_power * effectiveness * stab * acc) + species_bonus + priority_bonus

    def _utility_score(self, context: BattleContext, move) -> float:
        if move.id == "thunderwave":
            opp = context.opponent_pokemon
            if opp and not getattr(opp, "status", None):
                opp_types = self._pokemon_type_names(opp)
                # Electric types cannot be paralyzed at all
                if "ELECTRIC" in opp_types:
                    return 0.0
                # Ground is immune to Electric-type moves (TWave fails)
                elec_vs_target = self._type_effectiveness(context, "ELECTRIC", opp_types)
                if elec_vs_target == 0.0:
                    return 0.0
                return 50.0
        elif move.id == "recover":
            me = context.my_pokemon
            if me:
                hp_frac = getattr(me, "current_hp_fraction", 1.0) or 1.0
                if hp_frac <= 0.6:
                    return 80.0
        return 0.0

    def _switch_score(self, context: BattleContext, switch) -> float:
        if not switch:
            return 0.0

        hp_fraction = getattr(switch, 'current_hp_fraction', 1.0) or 1.0
        base_score = hp_fraction * 40.0

        # Don't switch if current Pokemon is healthy
        if context.my_pokemon:
            my_hp = getattr(context.my_pokemon, 'current_hp_fraction', 1.0) or 1.0
            if my_hp > 0.6:
                base_score -= 30.0

        return max(base_score, 0.0)

    def _get_pokemon_types(self, pokemon):
        types = []
        try:
            if hasattr(pokemon, 'type_1') and pokemon.type_1:
                types.append(pokemon.type_1)
            if hasattr(pokemon, 'type_2') and pokemon.type_2:
                types.append(pokemon.type_2)
        except:
            pass
        return types

    def _species_move_bonus(self, context: BattleContext, move) -> float:
        if not context.my_pokemon or not hasattr(context.my_pokemon, 'species'):
            return 0.0

        species = context.my_pokemon.species

        # Kingambit Supreme Overlord
        if 'Kingambit' in species and move.id in ['suckerpunch', 'kowtowcleave', 'ironhead']:
            try:
                fainted_allies = sum(1 for p in context.battle.team.values() if p.fainted)
                return fainted_allies * 10
            except:
                pass

        # Sucker Punch prediction
        if move.id == 'suckerpunch' and context.opponent_pokemon:
            opp_hp = getattr(context.opponent_pokemon, 'current_hp_fraction', 1.0) or 1.0
            if opp_hp <= 0.4:
                return 25.0
            else:
                return 10.0

        return 0.0


class DecisionEngine:
    def __init__(self):
        self.layers = [
            EmergencyLayer(),
            StrategicLayer(),
            TacticalLayer()
        ]
        self.layers.sort(key=lambda x: x.priority, reverse=True)

    def choose_best_action(self, context: BattleContext) -> Any:
        all_actions = []

        for layer in self.layers:
            try:
                layer_actions = layer.evaluate(context)

                if layer.should_override(context) and layer_actions:
                    all_actions = layer_actions
                    break

                all_actions.extend(layer_actions)
            except:
                continue

        if not all_actions:
            # Fallback to first available move
            if context.battle.available_moves:
                return context.battle.available_moves[0]
            return None

        best_action = max(all_actions, key=lambda x: (x.priority, x.expected_value))
        return best_action.move_or_pokemon


class CustomAgent(Player):
    """Clean layered agent that expert_main.py can import"""

    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)
        self.decision_engine = DecisionEngine()

    def choose_move(self, battle: AbstractBattle):
        try:
            if not battle.active_pokemon or not battle.opponent_active_pokemon:
                return self.choose_random_move(battle)

            context = BattleContext(
                my_pokemon=battle.active_pokemon,
                opponent_pokemon=battle.opponent_active_pokemon,
                battle=battle,
                turn_number=battle.turn,
                gen_data=GenData.from_format(battle.format)
            )

            chosen_action = self.decision_engine.choose_best_action(context)

            if chosen_action:
                return self.create_order(chosen_action)
            else:
                return self.choose_random_move(battle)

        except Exception:
            return self.choose_random_move(battle)