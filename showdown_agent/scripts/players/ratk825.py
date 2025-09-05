from typing import List, Optional, Dict, Set, Tuple, Union, Any
from dataclasses import dataclass, field
import time
import random
import argparse
import sys
from enum import Enum

from poke_env.battle import MoveCategory, Target
from poke_env.battle.abstract_battle import AbstractBattle
from poke_env.battle.double_battle import DoubleBattle
from poke_env.battle.pokemon import Pokemon
from poke_env.battle.side_condition import SideCondition
from poke_env.player.battle_order import BattleOrder
from poke_env.player.player import Player
from poke_env.data import GenData

# Configuration constants
@dataclass
class CONFIG:
    TIME_BUDGET_MS: int = 100
    MAX_BRANCH: int = 4  # Our moves to consider at MAX nodes
    MIN_BRANCH: int = 3  # Opponent responses to consider at MIN nodes
    P_CUTOFF: float = 0.05  # Minimum probability to consider
    RISK_LAMBDA: float = 0.3  # Risk aversion parameter
    MAX_DEPTH: int = 2
    TT_SIZE_BITS: int = 16  # 64K entries
    
    # Evaluation weights
    MATERIAL_WEIGHT: float = 3.0
    SPEED_CONTROL_WEIGHT: float = 0.5
    HAZARDS_WEIGHT: float = 1.0
    ENDGAME_WEIGHT: float = 2.0
    TERA_AVAILABILITY_WEIGHT: float = 1.5

# Debug flag (global)
DEBUG = False

class NodeType(Enum):
    MAX = "MAX"  # Our turn
    CHANCE = "CHANCE"  # Opponent's turn (expectimax)

class TTFlag(Enum):
    EXACT = "EXACT"
    LOWER_BOUND = "LOWER"
    UPPER_BOUND = "UPPER"

@dataclass
class TTEntry:
    depth: int
    value: float
    flag: TTFlag
    best_action: Optional[Any] = None

class TeraAction:
    """Wrapper for Tera activation"""
    def __init__(self, base_action, tera_type: str):
        self.base_action = base_action
        self.tera_type = tera_type
        
    def __repr__(self):
        return f"Tera{self.tera_type}({self.base_action})"

@dataclass(frozen=True)
class GameState:
    """Immutable game state representation"""
    # Our active Pokemon
    our_species: str
    our_types: Tuple[str, ...]  # NEW: ('steel','dark') etc.
    our_hp_frac: float
    our_boosts: Tuple[int, ...]  # (atk, def, spa, spd, spe, acc, eva)
    our_status: Optional[str]
    our_tera_used: bool
    our_tera_type: Optional[str]  # Current tera type if tera'd

    # Opponent active Pokemon
    opp_species: str
    opp_types: Tuple[str, ...]  # NEW
    opp_hp_frac: float
    opp_boosts: Tuple[int, ...]
    opp_status: Optional[str]
    opp_tera_used: bool
    opp_tera_type: Optional[str]

    # Team info (alive Pokemon by species)
    our_bench: frozenset
    opp_bench: frozenset

    # Field conditions (use counts, not just presence)
    our_hazards: Tuple[int, int, int, int]  # (rocks, spikes, tspikes, webs)
    opp_hazards: Tuple[int, int, int, int]
    weather: Optional[str]
    terrain: Optional[str]

    # Game state
    turn_count: int
    last_our_action: Optional[str]
    last_opp_action: Optional[str]

    @classmethod
    def from_battle(cls, battle: AbstractBattle) -> 'GameState':
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        if not active or not opponent:
            return cls(
                our_species="unknown", our_types=(), our_hp_frac=1.0, our_boosts=(0, 0, 0, 0, 0, 0, 0),
                our_status=None, our_tera_used=False, our_tera_type=None,
                opp_species="unknown", opp_types=(), opp_hp_frac=1.0, opp_boosts=(0, 0, 0, 0, 0, 0, 0),
                opp_status=None, opp_tera_used=False, opp_tera_type=None,
                our_bench=frozenset(), opp_bench=frozenset(),
                our_hazards=(0, 0, 0, 0), opp_hazards=(0, 0, 0, 0),
                weather=None, terrain=None, turn_count=1,
                last_our_action=None, last_opp_action=None
            )

        # Our Pokemon
        our_boosts = tuple(active.boosts.get(stat, 0) for stat in ['atk', 'def', 'spa', 'spd', 'spe', 'acc', 'eva'])
        our_status = active.status.name if active.status else None
        our_tera_used = getattr(active, 'terastallized', False)
        our_tera_type = getattr(active, 'tera_type', None)
        if our_tera_type:
            our_tera_type = our_tera_type.name if hasattr(our_tera_type, 'name') else str(our_tera_type)
        our_types = tuple(t.name.lower() for t in (active.types or []) if t)  # NEW

        # Opponent Pokemon
        opp_boosts = tuple(opponent.boosts.get(stat, 0) for stat in ['atk', 'def', 'spa', 'spd', 'spe', 'acc', 'eva'])
        opp_status = opponent.status.name if opponent.status else None
        opp_tera_used = getattr(opponent, 'terastallized', False)
        opp_tera_type = getattr(opponent, 'tera_type', None)
        if opp_tera_type:
            opp_tera_type = opp_tera_type.name if hasattr(opp_tera_type, 'name') else str(opp_tera_type)
        opp_types = tuple(t.name.lower() for t in (opponent.types or []) if t)  # NEW

        # Bench
        our_bench = frozenset(p.species for p in battle.team.values() if not p.fainted and p.species != active.species)
        opp_bench = frozenset(
            p.species for p in (battle.opponent_team or {}).values() if not p.fainted and p.species != opponent.species)

        # Hazards (counts)
        def hazards_tuple(side: Dict[SideCondition, int]) -> Tuple[int, int, int, int]:
            rocks = side.get(SideCondition.STEALTH_ROCK, 0)
            spikes = side.get(SideCondition.SPIKES, 0)
            tspikes = side.get(SideCondition.TOXIC_SPIKES, 0)
            webs = side.get(SideCondition.STICKY_WEB, 0)
            return (rocks, spikes, tspikes, webs)

        our_haz = hazards_tuple(battle.side_conditions or {})
        opp_haz = hazards_tuple(battle.opponent_side_conditions or {})

        weather = str(battle.weather) if battle.weather else None
        terrain = str(battle.fields) if battle.fields else None

        return cls(
            our_species=active.species, our_types=our_types, our_hp_frac=active.current_hp_fraction,
            our_boosts=our_boosts, our_status=our_status, our_tera_used=our_tera_used, our_tera_type=our_tera_type,
            opp_species=opponent.species, opp_types=opp_types, opp_hp_frac=opponent.current_hp_fraction,
            opp_boosts=opp_boosts, opp_status=opp_status, opp_tera_used=opp_tera_used, opp_tera_type=opp_tera_type,
            our_bench=our_bench, opp_bench=opp_bench,
            our_hazards=our_haz, opp_hazards=opp_haz,
            weather=weather, terrain=terrain,
            turn_count=getattr(battle, 'turn', 1), last_our_action=None, last_opp_action=None
        )

    def _replace(self, **changes) -> "GameState":
        # copy all fields, apply changes (works with frozen dataclass)
        data = {
            'our_species': self.our_species,
            'our_types': self.our_types,
            'our_hp_frac': self.our_hp_frac,
            'our_boosts': self.our_boosts,
            'our_status': self.our_status,
            'our_tera_used': self.our_tera_used,
            'our_tera_type': self.our_tera_type,
            'opp_species': self.opp_species,
            'opp_types': self.opp_types,
            'opp_hp_frac': self.opp_hp_frac,
            'opp_boosts': self.opp_boosts,
            'opp_status': self.opp_status,
            'opp_tera_used': self.opp_tera_used,
            'opp_tera_type': self.opp_tera_type,
            'our_bench': self.our_bench,
            'opp_bench': self.opp_bench,
            'our_hazards': self.our_hazards,
            'opp_hazards': self.opp_hazards,
            'weather': self.weather,
            'terrain': self.terrain,
            'turn_count': self.turn_count,
            'last_our_action': self.last_our_action,
            'last_opp_action': self.last_opp_action,
        }
        data.update(changes)
        return GameState(**data)


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


def legal_actions(state: GameState, battle: AbstractBattle, for_us: bool = True) -> List[Any]:
    """Return all legal actions for the current player"""
    if not for_us:
        return []  # we model opp via predict_responses()

    actions: List[Any] = []

    # Moves (+ optional Tera-on-move only)
    if battle.available_moves:
        # derive real tera type if we know it; otherwise fall back to first current type; else None
        allowed_tera = None
        tt = getattr(battle.active_pokemon, "tera_type", None)
        if tt:
            allowed_tera = tt.name if hasattr(tt, "name") else str(tt)
        elif state.our_types:
            allowed_tera = state.our_types[0]

        for move in battle.available_moves:
            actions.append(move)
            if not state.our_tera_used and battle.can_tera and allowed_tera:
                actions.append(TeraAction(move, allowed_tera))

    # Switches (never Tera-wrap a switch)
    if battle.available_switches:
        actions.extend(battle.available_switches)

    return actions

def simulate(state: GameState, action: Any, for_us: bool,
             type_chart: Dict, species_data: Dict) -> GameState:
    """Deterministic forward model - estimate state after action"""
    if for_us:
        return _simulate_our_action(state, action, type_chart, species_data)
    else:
        return _simulate_opponent_action(state, action, type_chart, species_data)

def _moves_first(state: GameState, our_action: Any, species_data: Dict) -> str:
    """Return 'us' or 'opp' by priority then speed (very rough)."""
    our_pri = 0
    if hasattr(our_action, "priority") and our_action.priority is not None:
        val = getattr(our_action.priority, "value", our_action.priority)
        try:
            our_pri = int(val)
        except Exception:
            our_pri = 0
    opp_pri = 0  # unknown; assume 0

    if our_pri != opp_pri:
        return "us" if our_pri > opp_pri else "opp"

    our_spd = _sp(state.our_species, species_data)["speed"]
    opp_spd = _sp(state.opp_species, species_data)["speed"]
    # crude boost handling: +1 = x1.5, +2 = x2
    def boost_mult(stage: int) -> float:
        table = { -6:0.25, -5:0.29, -4:0.33, -3:0.4, -2:0.5, -1:0.66, 0:1.0, 1:1.5, 2:2.0, 3:2.5, 4:3.0, 5:3.5, 6:4.0 }
        return table.get(max(-6, min(6, stage)), 1.0)
    our_spd *= boost_mult(state.our_boosts[4] if state.our_boosts else 0)
    opp_spd *= boost_mult(state.opp_boosts[4] if state.opp_boosts else 0)
    return "us" if our_spd >= opp_spd else "opp"

def _simulate_our_action(state: GameState, action: Any,
                        type_chart: Dict, species_data: Dict) -> GameState:
    """Simulate our action"""
    # Handle Tera actions
    tera_used = state.our_tera_used
    tera_type = state.our_tera_type
    base_action = action

    if isinstance(action, TeraAction):
        tera_used = True
        tera_type = action.tera_type
        base_action = action.base_action

    # Estimate damage/effects
    new_opp_hp = state.opp_hp_frac
    new_our_hp = state.our_hp_frac
    new_our_boosts = state.our_boosts
    new_opp_boosts = state.opp_boosts
    new_our_status = state.our_status
    new_opp_status = state.opp_status

    if hasattr(base_action, 'base_power') and base_action.base_power:
        dmg = _estimate_damage(base_action, state, type_chart, species_data, for_us=True)
        order = _moves_first(state, base_action, species_data)

        new_our_hp = state.our_hp_frac
        new_opp_hp = state.opp_hp_frac

        if order == "us":
            new_opp_hp = max(0.0, new_opp_hp - dmg)
            if new_opp_hp > 0.0:
                # opp hits back with a pseudo-strong attack
                after = _simulate_opponent_action(state._replace(opp_hp_frac=new_opp_hp), "attack_strong", type_chart,
                                                  species_data)
                new_our_hp = after.our_hp_frac
        else:
            # opp hits first
            after = _simulate_opponent_action(state, "attack_strong", type_chart, species_data)
            new_our_hp = after.our_hp_frac
            if new_our_hp > 0.0:
                new_opp_hp = max(0.0, new_opp_hp - dmg)

    elif hasattr(base_action, 'boosts') and base_action.boosts:
        # Stat changes
        boosts_list = list(new_our_boosts)
        for i, stat in enumerate(['atk', 'def', 'spa', 'spd', 'spe', 'acc', 'eva']):
            if stat in base_action.boosts:
                boosts_list[i] = max(-6, min(6, boosts_list[i] + base_action.boosts[stat]))
        new_our_boosts = tuple(boosts_list)

    return state._replace(
        our_tera_used=tera_used,
        our_tera_type=tera_type,
        our_hp_frac=new_our_hp,
        opp_hp_frac=new_opp_hp,
        our_boosts=new_our_boosts,
        opp_boosts=new_opp_boosts,
        our_status=new_our_status,
        opp_status=new_opp_status
    )

def _simulate_opponent_action(state: GameState, action: str,
                              type_chart: Dict, species_data: Dict) -> GameState:
    """Simulate opponent action deterministically.
       For 'attack_*' assume a plausible 80–100 BP STAB coverage with best type vs our types."""
    new_our_hp = state.our_hp_frac
    new_opp_hp = state.opp_hp_frac
    new_our_boosts = state.our_boosts
    new_opp_boosts = state.opp_boosts

    if action.startswith("switch_"):
        # Switch: no damage, but could apply hazards on entry (approximate: lose a small fraction)
        # Here we just record last action; detailed hazard application is optional.
        return state._replace(last_opp_action=action)

    if "setup" in action:
        boosts = list(new_opp_boosts)
        boosts[0] = min(6, boosts[0] + 2)  # +2 Atk (simplified)
        new_opp_boosts = tuple(boosts)
        return state._replace(opp_boosts=new_opp_boosts, last_opp_action=action)

    if "status" in action:
        # Apply a small detriment; for simplicity, mark paralysis if none
        if not state.our_status:
            return state._replace(last_opp_action=action, our_status="par")
        return state._replace(last_opp_action=action)

    # Attacks: synthesize a pseudo-move with decent BP of a type that hits us best
    class _PseudoMove:
        def __init__(self, bp, tname, acc=1.0):
            self.base_power = bp
            self.type = type("T", (), {"name": tname})  # mock enum-like
            self.accuracy = acc
            self.category = MoveCategory.PHYSICAL

    # Pick best attacking type = max effectiveness vs our types
    best_t = None
    best_mult = -1.0
    for att_type in type_chart.keys():
        mult = 1.0
        for d in state.our_types:
            mult *= float(type_chart.get(att_type, {}).get(d, 1.0))
        if mult > best_mult:
            best_mult = mult
            best_t = att_type

    pseudo = _PseudoMove(90, best_t or "normal", 0.95)
    dmg = _estimate_damage(pseudo, state, type_chart, species_data, for_us=False)
    new_our_hp = max(0.0, state.our_hp_frac - dmg)

    return state._replace(our_hp_frac=new_our_hp, last_opp_action=action)

def _estimate_damage(move, state: GameState, type_chart: Dict, species_data: Dict, for_us: bool) -> float:
    """Estimate damage as fraction of target HP using defender types + simple STAB; calibrated scale."""
    # Guards
    if not move or not hasattr(move, 'base_power'):
        return 0.0
    bp = getattr(move, 'base_power', 0) or 0
    if bp <= 0:
        return 0.0

    # Determine attacker/defender types
    attacker_types = state.our_types if for_us else state.opp_types
    defender_types = state.opp_types if for_us else state.our_types

    # STAB (simple): if move type matches any attacker type OR tera type
    stab = 1.0
    mtype = None
    if hasattr(move, 'type') and move.type and hasattr(move.type, 'name'):
        mtype = move.type.name.lower()
        if mtype in attacker_types:
            stab = 1.5
        else:
            # tera override if active
            tera_type = state.our_tera_type if for_us else state.opp_tera_type
            if tera_type and mtype == str(tera_type).lower():
                stab = 1.5

    # Type effectiveness
    type_mult = 1.0
    if mtype and defender_types:
        row = type_chart.get(mtype, {})
        for d in defender_types:
            type_mult *= float(row.get(d, 1.0))
        type_mult = max(0.0, min(4.0, type_mult))

    # Accuracy
    acc = getattr(move, 'accuracy', None)
    acc = 1.0 if acc is None else max(0.0, min(1.0, float(acc)))

    # Very simple boost ratio (use atk/spa vs def/spd heuristic via move category if available)
    # If not available, treat as neutral.
    ratio = 1.0

    # Calibrated base: make 100 BP neutral non-STAB ≈ 0.25 HP
    # Scale = 400 works well: 100 / 400 = 0.25
    SCALE = 400.0
    raw = (bp / SCALE) * stab * type_mult * acc * ratio

    return max(0.0, min(1.0, raw))

@dataclass
class OpponentData:
    """Trimmed opponent tracking - only essentials"""
    moves_seen: Dict[str, Set[str]] = field(default_factory=dict)  # species -> moves
    switch_count: Dict[str, int] = field(default_factory=dict)  # species -> switches
    
    # Behavioral profile (EMA-updated floats)
    aggression: float = 0.5  # Prefers attacking vs setup/support
    switchiness: float = 0.5  # Tendency to switch
    setup_tendency: float = 0.5  # Prefers setup moves
    
    def add_move(self, species: str, move: str):
        if species not in self.moves_seen:
            self.moves_seen[species] = set()
        self.moves_seen[species].add(move)
    
    def record_switch(self, species: str):
        self.switch_count[species] = self.switch_count.get(species, 0) + 1
    
    def update_aggression(self, was_aggressive: bool, alpha: float = 0.1):
        """EMA update for aggression"""
        target = 1.0 if was_aggressive else 0.0
        self.aggression = (1 - alpha) * self.aggression + alpha * target
    
    def update_switchiness(self, did_switch: bool, alpha: float = 0.1):
        """EMA update for switchiness"""
        target = 1.0 if did_switch else 0.0
        self.switchiness = (1 - alpha) * self.switchiness + alpha * target
    
    def update_setup_tendency(self, used_setup: bool, alpha: float = 0.1):
        """EMA update for setup tendency"""
        target = 1.0 if used_setup else 0.0
        self.setup_tendency = (1 - alpha) * self.setup_tendency + alpha * target

def predict_responses(state: GameState, opponent_data: OpponentData, 
                      type_chart: Dict, species_data: Dict) -> Dict[str, float]:
    """Small, useful set: two attack flavors + one setup/status + best two switches."""
    responses: Dict[str, float] = {}

    # Two attack variants (representing strong/neutral)
    responses["attack_strong"] = 0.40 * (0.6 + opponent_data.aggression)
    responses["attack_neutral"] = 0.20 * (0.6 + opponent_data.aggression)

    # Setup / Status
    responses["setup"] = 0.15 * (0.5 + opponent_data.setup_tendency)
    responses["status"] = 0.10 if state.our_status is None else 0.05

    # Up to two best switches by type advantage
    switches = []
    for species in state.opp_bench:
        # crude: prefer switches whose species name hints resist (fallback to random)
        score = 1.0
        if "resist" in species.lower():
            score *= 1.5
        switches.append((species, score))
    switches.sort(key=lambda x: x[1], reverse=True)
    for species, score in switches[:2]:
        responses[f"switch_{species}"] = 0.1 * (0.5 + opponent_data.switchiness) * score

    # Normalize
    total = sum(responses.values())
    if total > 0:
        for k in list(responses.keys()):
            responses[k] /= total

    # Keep top-K
    return dict(sorted(responses.items(), key=lambda x: x[1], reverse=True)[:CONFIG.MIN_BRANCH])

def zobrist_hash(state: GameState) -> int:
    """64-bit hash over salient fields for TT."""
    h = 1469598103934665603  # FNV offset basis
    def mix(x: int) -> None:
        nonlocal h
        h ^= x & 0xFFFFFFFFFFFFFFFF
        h *= 1099511628211
        h &= 0xFFFFFFFFFFFFFFFF

    mix(hash(state.our_species))
    mix(hash(state.opp_species))
    mix(int(state.our_hp_frac * 1000))
    mix(int(state.opp_hp_frac * 1000))
    mix(hash(state.our_types))
    mix(hash(state.opp_types))
    mix(hash(state.our_boosts))
    mix(hash(state.opp_boosts))
    mix(hash(state.our_bench))
    mix(hash(state.opp_bench))
    mix(hash(state.our_hazards))
    mix(hash(state.opp_hazards))
    mix(int(state.our_tera_used))
    mix(int(state.opp_tera_used))
    return h  # use full 64-bit int as dict key

def _sp(species: str, species_data: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    k = (species or "").lower().replace("-", "")
    return species_data.get(k, {"power": 100, "speed": 100})

def evaluate(state: GameState, species_data: Dict) -> float:
    score = 0.0

    our_info = _sp(state.our_species, species_data)
    opp_info = _sp(state.opp_species, species_data)

    our_power = our_info["power"]
    opp_power = opp_info["power"]

    material_diff = (state.our_hp_frac * our_power) - (state.opp_hp_frac * opp_power)
    score += material_diff / 100.0 * CONFIG.MATERIAL_WEIGHT

    our_speed = our_info["speed"]
    opp_speed = opp_info["speed"]
    if our_speed > opp_speed:
        score += CONFIG.SPEED_CONTROL_WEIGHT
    elif opp_speed > our_speed:
        score -= CONFIG.SPEED_CONTROL_WEIGHT

    our_h = state.our_hazards
    opp_h = state.opp_hazards
    hazard_diff = (opp_h[0] - our_h[0]) + (opp_h[1] - our_h[1]) * 0.5 + (opp_h[2] - our_h[2]) * 0.5 + (opp_h[3] - our_h[3]) * 0.5
    score += hazard_diff * CONFIG.HAZARDS_WEIGHT

    our_alive = len(state.our_bench) + 1
    opp_alive = len(state.opp_bench) + 1
    if our_alive > opp_alive:
        score += CONFIG.ENDGAME_WEIGHT
    elif opp_alive > our_alive:
        score -= CONFIG.ENDGAME_WEIGHT

    if not state.our_tera_used and state.opp_tera_used:
        score += CONFIG.TERA_AVAILABILITY_WEIGHT
    elif state.our_tera_used and not state.opp_tera_used:
        score -= CONFIG.TERA_AVAILABILITY_WEIGHT

    return max(-10.0, min(10.0, score))




def expectimax_search(state: GameState, depth: int, node_type: NodeType, 
                     alpha: float, beta: float, deadline_ms: int,
                     battle: AbstractBattle, opponent_data: OpponentData,
                     type_chart: Dict, species_data: Dict, 
                     tt: Dict[int, TTEntry]) -> Tuple[float, Optional[Any]]:
    """Expectimax search with alpha-beta pruning on deterministic branches"""
    
    # Time check
    if time.time() * 1000 >= deadline_ms:
        return evaluate(state, species_data), None
    
    # Depth limit
    if depth <= 0:
        return evaluate(state, species_data), None
    
    # Transposition table lookup
    state_hash = zobrist_hash(state)
    if state_hash in tt:
        entry = tt[state_hash]
        if entry.depth >= depth:
            if entry.flag == TTFlag.EXACT:
                return entry.value, entry.best_action
            elif entry.flag == TTFlag.LOWER_BOUND and entry.value >= beta:
                return entry.value, entry.best_action
            elif entry.flag == TTFlag.UPPER_BOUND and entry.value <= alpha:
                return entry.value, entry.best_action
    
    if node_type == NodeType.MAX:
        return _max_node(state, depth, alpha, beta, deadline_ms, battle, 
                        opponent_data, type_chart, species_data, tt, state_hash)
    else:  # CHANCE node
        return _chance_node(state, depth, alpha, beta, deadline_ms, battle,
                           opponent_data, type_chart, species_data, tt, state_hash)

def _max_node(state: GameState, depth: int, alpha: float, beta: float, 
             deadline_ms: int, battle: AbstractBattle, opponent_data: OpponentData,
             type_chart: Dict, species_data: Dict, tt: Dict[int, TTEntry], 
             state_hash: int) -> Tuple[float, Optional[Any]]:
    """MAX node - our turn"""
    
    actions = legal_actions(state, battle, for_us=True)
    
    # Move ordering - prioritize high-value actions
    ordered_actions = _order_our_moves(actions, state, battle, type_chart, species_data)
    
    # Keep only top MAX_BRANCH actions
    ordered_actions = ordered_actions[:CONFIG.MAX_BRANCH]
    
    best_value = float('-inf')
    best_action = None
    original_alpha = alpha
    
    for action in ordered_actions:
        if time.time() * 1000 >= deadline_ms:
            break
            
        # Simulate our action
        new_state = simulate(state, action, True, type_chart, species_data)

        # Opponent's response (expectimax)
        value, _ = expectimax_search(new_state, depth - 1, NodeType.CHANCE,
                                   alpha, beta, deadline_ms, battle, opponent_data,
                                   type_chart, species_data, tt)
        
        if value > best_value:
            best_value = value
            best_action = action
            alpha = max(alpha, value)
        
        # Alpha-beta pruning
        if beta <= alpha:
            break
    
    # Store in transposition table
    flag = TTFlag.EXACT
    if best_value <= original_alpha:
        flag = TTFlag.UPPER_BOUND
    elif best_value >= beta:
        flag = TTFlag.LOWER_BOUND
    
    tt[state_hash] = TTEntry(depth, best_value, flag, best_action)
    
    return best_value, best_action

def _chance_node(state: GameState, depth: int, alpha: float, beta: float,
                deadline_ms: int, battle: AbstractBattle, opponent_data: OpponentData,
                type_chart: Dict, species_data: Dict, tt: Dict[int, TTEntry],
                state_hash: int) -> Tuple[float, Optional[Any]]:
    """CHANCE node - opponent's turn (expectimax)"""

    response_probs = predict_responses(state, opponent_data, type_chart, species_data)
    # Filter and renormalize
    response_probs = {a: p for a, p in response_probs.items() if p >= CONFIG.P_CUTOFF}
    total = sum(response_probs.values())
    if total <= 0:
        # No model? fallback to static eval
        val = evaluate(state, species_data)
        tt[state_hash] = TTEntry(depth, val, TTFlag.EXACT, None)
        return val, None
    response_probs = {a: p/total for a, p in response_probs.items()}

    expected_value = 0.0
    covered = 0.0
    fallback = evaluate(state, species_data)  # baseline for leftover prob

    for action, prob in sorted(response_probs.items(), key=lambda x: x[1], reverse=True):
        if time.time() * 1000 >= deadline_ms:
            break

        # Simulate opponent action (pure over state)
        new_state = simulate(state, action, False, type_chart, species_data)
        child_val, _ = expectimax_search(new_state, depth - 1, NodeType.MAX,
                                         alpha, beta, deadline_ms, battle, opponent_data,
                                         type_chart, species_data, tt)

        expected_value += prob * child_val
        covered += prob

        if covered >= 0.85:
            break

    if covered < 1.0:
        expected_value += (1.0 - covered) * fallback

    tt[state_hash] = TTEntry(depth, expected_value, TTFlag.EXACT, None)
    return expected_value, None

def _order_our_moves(actions: List[Any], state: GameState, battle: AbstractBattle,
                    type_chart: Dict, species_data: Dict) -> List[Any]:
    scored_actions = []
    for action in actions:
        score = 0.0

        # Single, correct Tera handling
        tera_bonus = 0.0
        base_action = action
        eval_state = state
        if isinstance(action, TeraAction):
            base_action = action.base_action
            eval_state = state._replace(our_tera_used=True, our_tera_type=action.tera_type)
            tera_bonus = 0.15  # small bias to break ties only

        # Damage > setup > status > switch
        dmg = 0.0
        if hasattr(base_action, 'base_power') and base_action.base_power:
            dmg = _estimate_damage(base_action, eval_state, type_chart, species_data, for_us=True)
            score += 5.0 * dmg
            if dmg >= eval_state.opp_hp_frac - 0.01:
                score += 2.0
        elif hasattr(base_action, 'boosts') and base_action.boosts:
            score += 0.5 * sum(base_action.boosts.values())
        elif hasattr(base_action, 'category') and base_action.category == MoveCategory.STATUS:
            score += 0.3
        else:
            score += 0.2  # switch

        score += tera_bonus
        scored_actions.append((action, score))

    scored_actions.sort(key=lambda x: x[1], reverse=True)
    return [a for a, _ in scored_actions]

def risk_adjusted_utility(expected_value: float, variance: float, lambda_risk: float) -> float:
    """Apply risk adjustment: U = EV - λ * StdDev"""
    std_dev = variance ** 0.5
    return expected_value - lambda_risk * std_dev

def get_risk_lambda(our_alive: int, opp_alive: int) -> float:
    """Dynamic risk parameter based on game state"""
    if our_alive > opp_alive:  # We're ahead - be more risk averse
        return CONFIG.RISK_LAMBDA * 1.5
    elif our_alive < opp_alive:  # We're behind - take more risks
        return CONFIG.RISK_LAMBDA * 0.5
    else:
        return CONFIG.RISK_LAMBDA

def iterative_deepening_search(state: GameState, deadline_ms: int, battle: AbstractBattle,
                              opponent_data: OpponentData, type_chart: Dict, 
                              species_data: Dict) -> Tuple[Any, List[str]]:
    """Iterative deepening with time management"""
    tt: Dict[int, TTEntry] = {}  # Transposition table
    best_action = None
    principal_variation = []
    nodes_visited = 0
    tt_hits = 0
    
    for depth in range(1, CONFIG.MAX_DEPTH + 1):
        if time.time() * 1000 >= deadline_ms:
            break
            
        try:
            value, action = expectimax_search(
                state, depth, NodeType.MAX, float('-inf'), float('inf'),
                deadline_ms, battle, opponent_data, type_chart, species_data, tt
            )
            
            if action is not None:
                best_action = action
                
                # Build PV (simplified)
                pv = []
                current_state = state
                current_action = action
                pv_depth = 0
                
                while current_action and pv_depth < depth:
                    pv.append(str(current_action))
                    # Would need to simulate and get next best action
                    break  # Simplified for now
                    
                principal_variation = pv
                
                if DEBUG:
                    print(f"Depth {depth}: value={value:.2f}, action={action}, nodes={nodes_visited}, tt_hits={tt_hits}")
            
        except Exception as e:
            if DEBUG:
                print(f"Search error at depth {depth}: {e}")
            break
    
    return best_action, principal_variation

# Precompute type chart and species data
def build_type_chart(gen_data: GenData) -> Dict[str, Dict[str, float]]:
    """Build compact type effectiveness dictionary"""
    type_chart = {}
    
    # Get all types
    for attack_type in gen_data.type_chart:
        type_chart[attack_type.lower()] = {}
        for defend_type, effectiveness in gen_data.type_chart[attack_type].items():
            type_chart[attack_type.lower()][defend_type.lower()] = float(effectiveness)
    
    return type_chart

def build_species_data() -> Dict[str, Dict[str, Any]]:
    """Build species power weights and common move priors"""
    species_data = {
        "deoxysspeed": {
            "power": 90,  # Relative power rating
            "speed": 180,
            "common_moves": {"thunderwave": 0.9, "spikes": 0.8, "taunt": 0.7, "psychoboost": 0.6}
        },
        "kingambit": {
            "power": 135,
            "speed": 50,
            "common_moves": {"swordsdance": 0.8, "kowtowcleave": 0.9, "ironhead": 0.7, "suckerpunch": 0.9}
        },
        "arceusfairy": {
            "power": 120,
            "speed": 120,
            "common_moves": {"calmmind": 0.8, "judgment": 0.9, "taunt": 0.6, "recover": 0.7}
        },
        "zaciancrowned": {
            "power": 170,
            "speed": 148,
            "common_moves": {"swordsdance": 0.7, "behemothblade": 0.9, "closecombat": 0.8, "wildcharge": 0.6}
        },
        "eternatus": {
            "power": 145,
            "speed": 130,
            "common_moves": {"agility": 0.6, "meteorbeam": 0.8, "dynamaxcannon": 0.9, "fireblast": 0.7}
        },
        "koraidon": {
            "power": 135,
            "speed": 135,
            "common_moves": {"swordsdance": 0.7, "scaleshot": 0.8, "flamecharge": 0.6, "closecombat": 0.8}
        }
    }
    
    # Add defaults for unknown species
    default_data = {
        "power": 100,
        "speed": 100,
        "common_moves": {"attack": 0.6, "setup": 0.2, "status": 0.2}
    }
    
    return species_data

# Lightweight hidden-info beliefs
def get_move_priors(species: str, species_data: Dict) -> Dict[str, float]:
    """Get prior move frequencies for unrevealed moves"""
    if species.lower().replace("-", "") in species_data:
        return species_data[species.lower().replace("-", "")]["common_moves"]
    else:
        return {"attack": 0.6, "setup": 0.2, "status": 0.2}

def update_move_beliefs(species: str, seen_moves: Set[str], priors: Dict[str, float]) -> Dict[str, float]:
    """Update move beliefs based on seen moves"""
    # Simple approach: boost seen moves, deflate unseen
    updated = dict(priors)
    
    for move in seen_moves:
        if move in updated:
            updated[move] *= 1.5  # Boost seen moves
    
    # Normalize
    total = sum(updated.values())
    if total > 0:
        updated = {move: prob/total for move, prob in updated.items()}
    
    return updated

class OpponentTracker:
    """Minimal opponent tracking focused on search needs"""

    def __init__(self):
        self.opponent_data = OpponentData()
        self.last_species = None

    def update_turn(self, current_species: str, move_used: Optional[str] = None, 
                   switched: bool = False, was_setup: bool = False, was_aggressive: bool = False):
        """Update tracking for current turn"""
        if switched and current_species != self.last_species:
            self.opponent_data.record_switch(current_species)
            self.opponent_data.update_switchiness(True)
        else:
            self.opponent_data.update_switchiness(False)
        
        if move_used:
            self.opponent_data.add_move(current_species, move_used)
        
        if was_setup:
            self.opponent_data.update_setup_tendency(True)
        
        if was_aggressive:
            self.opponent_data.update_aggression(True)
        
        self.last_species = current_species








class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)

        # Core data
        self.gen_data = GenData.from_gen(9)
        self.type_chart = build_type_chart(self.gen_data)
        self.species_data = build_species_data()
        self.opponent_tracker = OpponentTracker()
        
        # Performance tracking
        self.battles_won = 0
        self.battles_total = 0
        self.nodes_visited = 0
        self.tt_hits = 0
        
        # Debug tracking
        self.search_stats = {
            'depths_reached': [],
            'nodes_per_search': [],
            'tt_hit_rate': 0.0
        }

    def _fallback_move_choice(self, battle: AbstractBattle) -> BattleOrder:
        """Fallback heuristic when search fails"""
        if DEBUG:
            print("Using fallback move selection")
        
        # Simple heuristic: prefer attacking moves, then setup, then switches
        if battle.available_moves:
            # Find strongest attack
            best_move = None
            best_power = 0
            
            for move in battle.available_moves:
                power = move.base_power or 0
                if power > best_power:
                    best_power = power
                    best_move = move
            
            if best_move:
                return self.create_order(best_move)
            else:
                return self.create_order(battle.available_moves[0])
        
        elif battle.available_switches:
            return self.create_order(battle.available_switches[0])
        
        else:
            return self.choose_random_move(battle)

    def search(self, state: GameState, depth: int, alpha: float, beta: float, deadline_ms: int, battle: AbstractBattle) -> Tuple[float, Optional[Any]]:
        """Main search entry point"""
        return iterative_deepening_search(
            state, deadline_ms, battle, self.opponent_tracker.opponent_data,
            self.type_chart, self.species_data
        )
    
    def choose_move(self, battle: AbstractBattle) -> BattleOrder:
        """Main move selection using expectimax search with iterative deepening"""
        # Store battle reference for search
        self.battle = battle
        
        # Build current game state
        try:
            state = GameState.from_battle(battle)
        except Exception as e:
            if DEBUG:
                print(f"Error building GameState: {e}")
            return self._fallback_move_choice(battle)
        
        # Basic opponent tracking update
        if battle.opponent_active_pokemon:
            # Simple tracking - just note if they switched
            current_species = battle.opponent_active_pokemon.species
            switched = (hasattr(self, '_last_opp_species') and 
                       self._last_opp_species != current_species)
            self.opponent_tracker.update_turn(current_species, switched=switched)
            self._last_opp_species = current_species
        
        # Set search deadline
        deadline_ms = int(time.time() * 1000) + CONFIG.TIME_BUDGET_MS
        
        try:
            # Run expectimax search with iterative deepening
            best_action, pv = iterative_deepening_search(
                state, deadline_ms, battle, self.opponent_tracker.opponent_data,
                self.type_chart, self.species_data
            )
            
            if DEBUG:
                print(f"Search completed. Best action: {best_action}")
                print(f"Principal variation: {' -> '.join(pv)}")
            
            # Convert search result to BattleOrder
            if best_action is None:
                return self._fallback_move_choice(battle)
            
            # Handle Tera actions
            if isinstance(best_action, TeraAction):
                base_action = best_action.base_action
                terastallize = True
            else:
                base_action = best_action
                terastallize = False
            
            # Create order
            if hasattr(base_action, 'id'):  # It's a move
                return self.create_order(base_action, terastallize=terastallize)
            elif hasattr(base_action, 'species'):  # It's a switch
                return self.create_order(base_action)
            else:
                return self._fallback_move_choice(battle)
                
        except Exception as e:
            if DEBUG:
                print(f"Search failed: {e}")
            return self._fallback_move_choice(battle)
    
    # Clean up - remove old methods that are no longer needed

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

    
    def teampreview(self, battle):
        """Simple team preview - choose lead Pokemon"""
        # Rotate through team for variety
        team_list = list(battle.team.values())
        if not team_list:
            return "/team 1"
        
        # Simple rotation based on battle count
        lead_index = getattr(self, '_battle_count', 0) % len(team_list)
        self._battle_count = getattr(self, '_battle_count', 0) + 1
        
        return f"/team {lead_index + 1}"
    
    
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
        
        # Debug output if enabled
        if DEBUG:
            print(f"Battle ended. Win rate: {win_rate:.2f} ({self.battles_won}/{self.battles_total})")

