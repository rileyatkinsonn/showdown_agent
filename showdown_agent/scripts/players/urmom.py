from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

from poke_env.battle import MoveCategory
from poke_env.battle.abstract_battle import AbstractBattle
from poke_env.battle.double_battle import DoubleBattle
from poke_env.battle.pokemon import Pokemon
from poke_env.battle.side_condition import SideCondition
from poke_env.player.battle_order import BattleOrder
from poke_env.player.player import Player
from poke_env.data import GenData


# =========================
# Team
# =========================
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


# =========================
# ISMCTS Helpers (state + nodes)
# =========================
@dataclass
class SimMon:
    species: str
    types: List[str]
    base_stats: Dict[str, int]
    hp: float  # 0..1
    boosts: Dict[str, int] = field(default_factory=lambda: {"atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0})
    fainted: bool = False

    def clone(self) -> "SimMon":
        return SimMon(
            species=self.species,
            types=list(self.types),
            base_stats=dict(self.base_stats),
            hp=self.hp,
            boosts=dict(self.boosts),
            fainted=self.fainted,
        )


@dataclass
class SimState:
    me_active: SimMon
    opp_active: SimMon
    me_bench: Dict[str, SimMon]  # key: species
    opp_bench: Dict[str, SimMon]
    me_sr: bool
    opp_sr: bool
    me_spikes: int
    opp_spikes: int
    terminal: bool = False

    def clone(self) -> "SimState":
        return SimState(
            me_active=self.me_active.clone(),
            opp_active=self.opp_active.clone(),
            me_bench={k: v.clone() for k, v in self.me_bench.items()},
            opp_bench={k: v.clone() for k, v in self.opp_bench.items()},
            me_sr=self.me_sr,
            opp_sr=self.opp_sr,
            me_spikes=self.me_spikes,
            opp_spikes=self.opp_spikes,
            terminal=self.terminal,
        )


class ISNode:
    """
    Information-set node:
      - keyed by observable features only (species, coarse HP bins, hazards).
      - underlying determinization (sampled moves, RNG) does not change the key.
    """
    __slots__ = ("player", "key", "parent", "children", "N", "W", "untried")

    def __init__(self, player: int, key: Tuple, parent: Optional["ISNode"] = None):
        self.player = player  # 1 = us (MAX), -1 = opp (MIN)
        self.key = key
        self.parent = parent
        self.children: Dict[Tuple, ISNode] = {}
        self.N: int = 0
        self.W: float = 0.0
        self.untried: List[Tuple] = []  # list of hashable action keys

    def ucb(self, c: float = 1.25) -> float:
        if self.N == 0:
            return float("inf")
        mean = self.W / self.N
        return mean + c * math.sqrt(math.log(max(1, self.parent.N)) / self.N)

    def select(self) -> Tuple[Tuple, "ISNode"]:
        return max(self.children.items(), key=lambda kv: kv[1].ucb())

    def add_child(self, action_key: Tuple, child: "ISNode"):
        self.children[action_key] = child
        try:
            self.untried.remove(action_key)
        except ValueError:
            pass

    def update(self, value_for_us: float):
        self.N += 1
        # flip sign for opponent nodes so that W accumulates "value for us" consistently
        self.W += value_for_us if self.player == 1 else -value_for_us


# =========================
# Agent
# =========================
class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)
        self.gen = GenData.from_gen(9)

        # Heuristic tables
        self.ENTRY_HAZARDS = {
            "spikes": SideCondition.SPIKES,
            "stealthrock": SideCondition.STEALTH_ROCK,
            "stickyweb": SideCondition.STICKY_WEB,
            "toxicspikes": SideCondition.TOXIC_SPIKES,
        }
        self.ANTI_HAZARDS_MOVES = {"rapidspin", "defog"}
        self.SETUP_MOVES = {"swordsdance", "calmmind", "agility", "dragondance", "nastyplot"}
        self.PRIORITY_MOVES = {"suckerpunch", "extremespeed", "quickattack", "bulletpunch", "iceshard", "aquajet"}
        self.RECOVERY_MOVES = {"recover", "roost", "moonlight", "synthesis", "morningsun", "slackoff", "softboiled"}

        self.SPEED_TIER_COEF = 0.1
        self.HP_COEF = 0.4
        self.SWITCH_OUT_MATCHUP_THRESHOLD = -2.0

    # ---------- Basic knowledge ----------
    def _species_key(self, s: str) -> str:
        return (s or "").lower().replace("-", "").replace("_", "")

    def _pokedex(self, species: str) -> Dict:
        sp = self._species_key(species)
        data = self.gen.pokedex.get(sp, {})
        if not data and "crowned" in sp:
            data = self.gen.pokedex.get(sp.replace("crowned", ""), {})
        return data

    def _types_of(self, species: str) -> List[str]:
        return [t.lower() for t in self._pokedex(species).get("types", [])]

    def _base_stats_of(self, species: str) -> Dict[str, int]:
        return self._pokedex(species).get("baseStats", {"hp": 100, "atk": 100, "def": 100, "spa": 100, "spd": 100, "spe": 100})

    def _type_mult(self, atk_type: str, def_types: List[str]) -> float:
        if not def_types:
            return 1.0
        eff = 1.0
        atk = (atk_type or "").upper()
        for dt in def_types:
            d_upper = (dt or "").upper()
            eff *= self.gen.type_chart.get(d_upper, {}).get(atk, 1.0)
        return float(eff)

    def _estimate_matchup(self, me: SimMon | Pokemon, opp: SimMon | Pokemon) -> float:
        if hasattr(me, "types"):
            my_types = [t if isinstance(t, str) else getattr(t, "name", str(t)).lower() for t in me.types]
        else:
            my_types = [getattr(t, "name", str(t)).lower() for t in getattr(me, "types", []) if t]

        if hasattr(opp, "types"):
            opp_types = [t if isinstance(t, str) else getattr(t, "name", str(t)).lower() for t in opp.types]
        else:
            opp_types = [getattr(t, "name", str(t)).lower() for t in getattr(opp, "types", []) if t]

        off = max([self._type_mult(t, opp_types) for t in my_types], default=1.0)
        deff = max([self._type_mult(t, my_types) for t in opp_types], default=1.0)
        score = off - deff

        try:
            my_spe = me.base_stats["spe"] if isinstance(me, SimMon) else me.base_stats["spe"]
            op_spe = opp.base_stats["spe"] if isinstance(opp, SimMon) else opp.base_stats["spe"]
            if my_spe > op_spe:
                score += self.SPEED_TIER_COEF
            elif op_spe > my_spe:
                score -= self.SPEED_TIER_COEF
        except Exception:
            pass

        try:
            my_hp = me.hp if isinstance(me, SimMon) else (me.current_hp_fraction or 0.0)
            op_hp = opp.hp if isinstance(opp, SimMon) else (opp.current_hp_fraction or 0.0)
            score += my_hp * self.HP_COEF
            score -= op_hp * self.HP_COEF
        except Exception:
            pass
        return float(score)

    def _stat_ratio(self, atk_bs: int, def_bs: int, atk_boost: int, def_boost: int) -> float:
        def boost_mult(n):
            return (2 + n) / 2 if n > 0 else 2 / max(1, (2 - n))
        return max(1.0, (atk_bs * boost_mult(atk_boost)) / max(1.0, def_bs * boost_mult(def_boost)))

    # ---------- Move metadata ----------
    def _move_meta(self, move_id: str) -> Dict:
        m = self.gen.moves.get(move_id, {})
        recoil = m.get("recoil", 0)
        if isinstance(recoil, list):
            recoil = abs(recoil[0])
        heal = m.get("heal", 0)
        if isinstance(heal, list):
            heal = heal[0]
        return {
            "id": move_id,
            "type": (m.get("type") or "").lower(),
            "bp": m.get("basePower", 0) or 0,
            "category": m.get("category", "Status"),
            "accuracy": m.get("accuracy", 1.0) if m.get("accuracy") is not None else 1.0,
            "priority": m.get("priority", 0) or 0,
            "recoil": recoil or 0,
            "heal": heal or 0,
            "target": m.get("target", "normal"),
            "secondary": m.get("secondary", None),
        }

    # ---------- Determinization ----------
    def _sample_opponent_moveset(self, species: str, revealed: List[str]) -> List[str]:
        revealed_norm = set((mid or "").lower() for mid in revealed)
        types = self._types_of(species)
        candidates = []
        for mid, md in self.gen.moves.items():
            t = (md.get("type") or "").lower()
            bp = md.get("basePower", 0) or 0
            cat = md.get("category", "Status")
            if cat in ("Physical", "Special") and bp >= 60:
                stab_bonus = 2 if t in types else 1
                for _ in range(stab_bonus):
                    candidates.append(mid)
        if not candidates:
            candidates = list(self.gen.moves.keys())

        chosen: List[str] = [m for m in revealed_norm if m]
        tries = 0
        while len(chosen) < 4 and tries < 64:
            m = random.choice(candidates).lower()
            if m not in chosen:
                chosen.append(m)
            tries += 1
        return chosen[:4]

    # ---------- Forward model ----------
    def _damage_proxy(self, atk: SimMon, move_meta: Dict, defender: SimMon) -> float:
        teff = self._type_mult(move_meta["type"], defender.types) if move_meta["type"] else 1.0
        if teff == 0.0 or move_meta["category"] == "Status" or move_meta["bp"] == 0:
            return 0.0

        if move_meta["category"] == "Physical":
            ratio = self._stat_ratio(atk.base_stats["atk"], defender.base_stats["def"], atk.boosts["atk"], defender.boosts["def"])
        else:
            ratio = self._stat_ratio(atk.base_stats["spa"], defender.base_stats["spd"], atk.boosts["spa"], defender.boosts["spd"])

        stab = 1.5 if move_meta["type"] in atk.types else 1.0
        acc = move_meta["accuracy"] if isinstance(move_meta["accuracy"], (int, float)) else 1.0
        base = move_meta["bp"] * ratio * stab * teff * acc
        return max(0.0, min(1.0, base / 300.0))

    def _apply_attack(self, state: SimState, attacker_is_me: bool, move_meta: Dict):
        atk = state.me_active if attacker_is_me else state.opp_active
        dfn = state.opp_active if attacker_is_me else state.me_active
        dmg = self._damage_proxy(atk, move_meta, dfn)
        roll = random.uniform(0.85, 1.0)
        dph = dmg * roll
        dfn.hp = max(0.0, dfn.hp - dph)
        if dfn.hp <= 0.0:
            dfn.fainted = True

    def _apply_switch_in_hazards(self, state: SimState, switch_is_me: bool):
        sr = state.me_sr if switch_is_me else state.opp_sr
        spikes_layers = state.me_spikes if switch_is_me else state.opp_spikes
        mon = state.me_active if switch_is_me else state.opp_active

        total = 0.0
        if sr:
            teff = self._type_mult("rock", mon.types)
            total += min(0.5, 0.125 * teff)
        if spikes_layers > 0:
            total += {1: 0.125, 2: 0.167, 3: 0.25}.get(spikes_layers, 0.125)

        mon.hp = max(0.0, mon.hp - total)
        if mon.hp <= 0.0:
            mon.fainted = True

    # ---------- State evaluation ----------
    def _eval_state(self, s: SimState) -> float:
        if s.me_active.fainted and s.opp_active.fainted and not s.me_bench and not s.opp_bench:
            return 0.0
        if s.me_active.fainted and not s.me_bench:
            return -10.0
        if s.opp_active.fainted and not s.opp_bench:
            return +10.0

        my_hp = s.me_active.hp + sum(m.hp for m in s.me_bench.values())
        op_hp = s.opp_active.hp + sum(m.hp for m in s.opp_bench.values())
        mu = self._estimate_matchup(s.me_active, s.opp_active)
        my_alive = 1 + sum(1 for m in s.me_bench.values() if not m.fainted and m.hp > 0)
        op_alive = 1 + sum(1 for m in s.opp_bench.values() if not m.fainted and m.hp > 0)
        return (my_hp - op_hp) * 3.0 + mu * 2.0 + (my_alive - op_alive) * 1.0

    # ---------- Information set key ----------
    def _hp_bin(self, hp: float) -> int:
        return int((hp if hp is not None else 0.0) * 10)

    def _node_key(self, s: SimState, player: int) -> Tuple:
        return (
            player,
            s.me_active.species,
            self._hp_bin(s.me_active.hp),
            s.opp_active.species,
            self._hp_bin(s.opp_active.hp),
            tuple(sorted([(k, self._hp_bin(m.hp)) for k, m in s.me_bench.items()])),
            tuple(sorted([(k, self._hp_bin(m.hp)) for k, m in s.opp_bench.items()])),
            s.me_sr,
            s.opp_sr,
            s.me_spikes,
            s.opp_spikes,
        )

    # ---------- Build initial SimState from live battle ----------
    def _to_simstate(self, battle: AbstractBattle) -> SimState:
        me_act = battle.active_pokemon
        op_act = battle.opponent_active_pokemon

        me_active = SimMon(
            species=me_act.species,
            types=[getattr(t, "name", str(t)).lower() for t in me_act.types if t],
            base_stats=me_act.base_stats,
            hp=(me_act.current_hp_fraction or 0.0),
        )
        opp_active = SimMon(
            species=op_act.species,
            types=[getattr(t, "name", str(t)).lower() for t in op_act.types if t],
            base_stats=op_act.base_stats,
            hp=(op_act.current_hp_fraction or 0.0),
        )

        me_bench = {}
        for p in battle.team.values():
            if p is me_act:
                continue
            me_bench[p.species] = SimMon(
                species=p.species,
                types=[getattr(t, "name", str(t)).lower() for t in p.types if t],
                base_stats=p.base_stats,
                hp=(p.current_hp_fraction or 0.0),
                fainted=bool(p.fainted),
            )

        opp_bench = {}
        for p in battle.opponent_team.values():
            if p and (not p.fainted) and p is not op_act:
                opp_bench[p.species] = SimMon(
                    species=p.species,
                    types=[getattr(t, "name", str(t)).lower() for t in p.types if t],
                    base_stats=p.base_stats,
                    hp=(p.current_hp_fraction or 0.0) if p.current_hp_fraction is not None else 1.0,
                    fainted=bool(p.fainted),
                )

        me_sr = SideCondition.STEALTH_ROCK in battle.side_conditions
        opp_sr = SideCondition.STEALTH_ROCK in battle.opponent_side_conditions
        me_spikes = battle.side_conditions.get(SideCondition.SPIKES, 0)
        opp_spikes = battle.opponent_side_conditions.get(SideCondition.SPIKES, 0)

        return SimState(
            me_active=me_active,
            opp_active=opp_active,
            me_bench=me_bench,
            opp_bench=opp_bench,
            me_sr=me_sr,
            opp_sr=opp_sr,
            me_spikes=me_spikes,
            opp_spikes=opp_spikes,
        )

    # ---------- Legal actions as HASHABLE KEYS ----------
    def _legal_my_action_keys(self, battle: AbstractBattle) -> List[Tuple]:
        # only moves inside ISMCTS (switching handled by outer policy)
        return [("move", m.id) for m in battle.available_moves]

    def _legal_opp_action_keys(self, s: SimState, opp_moveset: List[str]) -> List[Tuple]:
        acts: List[Tuple] = []
        for mid in opp_moveset:
            acts.append(("opp_move", mid))
        for sp, mon in s.opp_bench.items():
            if not mon.fainted and mon.hp > 0.0:
                acts.append(("opp_switch", sp))
        return acts if acts else [("opp_pass", None)]

    # ---------- Apply actions from KEYS ----------
    def _do_my_action_key(self, s: SimState, action_key: Tuple):
        kind, payload = action_key
        if kind == "move":
            md = self._move_meta(payload)  # payload is move_id
            self._apply_attack(s, attacker_is_me=True, move_meta=md)
            if s.opp_active.fainted:
                if s.opp_bench:
                    sw = max(s.opp_bench.values(), key=lambda mm: mm.hp)
                    s.opp_active = sw.clone()
                    del s.opp_bench[sw.species]
                    self._apply_switch_in_hazards(s, switch_is_me=False)
                else:
                    s.terminal = True

    def _do_opp_action_key(self, s: SimState, action_key: Tuple):
        kind, payload = action_key
        if kind == "opp_move":
            md = self._move_meta(payload)  # payload is move_id
            self._apply_attack(s, attacker_is_me=False, move_meta=md)
            if s.me_active.fainted:
                if s.me_bench:
                    sw = max(s.me_bench.values(), key=lambda mm: mm.hp)
                    s.me_active = sw.clone()
                    del s.me_bench[sw.species]
                    self._apply_switch_in_hazards(s, switch_is_me=True)
                else:
                    s.terminal = True
        elif kind == "opp_switch":
            sp = payload
            if sp in s.opp_bench:
                if not s.opp_active.fainted and s.opp_active.hp > 0:
                    s.opp_bench[s.opp_active.species] = s.opp_active.clone()
                s.opp_active = s.opp_bench[sp].clone()
                del s.opp_bench[sp]
                self._apply_switch_in_hazards(s, switch_is_me=False)
        else:
            pass

    # ---------- Rollout ----------
    def _rollout_policy(self, s: SimState, opp_moveset: List[str], depth: int = 2) -> float:
        d = 0
        while (not s.terminal) and d < depth:
            # our greedy synthetic STAB move (fast)
            best_md = None
            best_score = -1.0
            for t in s.me_active.types:
                md = {"id": f"stab_{t}", "type": t, "bp": 90, "category": "Physical", "accuracy": 0.95}
                sc = self._damage_proxy(s.me_active, md, s.opp_active)
                if sc > best_score:
                    best_score, best_md = sc, md
            if best_md:
                self._apply_attack(s, attacker_is_me=True, move_meta=best_md)
                if s.opp_active.fainted:
                    if s.opp_bench:
                        sw = max(s.opp_bench.values(), key=lambda mm: mm.hp)
                        s.opp_active = sw.clone()
                        del s.opp_bench[sw.species]
                        self._apply_switch_in_hazards(s, switch_is_me=False)
                    else:
                        s.terminal = True
                        break

            # opponent greedy response
            opp_actions = self._legal_opp_action_keys(s, opp_moveset)
            best_a, best_val = None, -1.0
            for a in opp_actions:
                if a[0] == "opp_move":
                    val = self._damage_proxy(s.opp_active, self._move_meta(a[1]), s.me_active)
                elif a[0] == "opp_switch":
                    val = s.opp_bench[a[1]].hp
                else:
                    val = 0.0
                if val > best_val:
                    best_val, best_a = val, a
            self._do_opp_action_key(s, best_a)
            d += 1

        return self._eval_state(s)

    # ---------- ISMCTS core ----------
    def _ismcts(self, battle: AbstractBattle, iters: int = 96) -> Optional:
        root_state = self._to_simstate(battle)

        # Map move_id -> Move object so we can return a real order at the end
        move_by_id = {m.id: m for m in battle.available_moves}
        my_action_keys = self._legal_my_action_keys(battle)
        if not my_action_keys:
            return None

        root = ISNode(player=1, key=self._node_key(root_state, player=1), parent=None)
        root.untried = list(my_action_keys)

        for _ in range(max(iters, 16)):
            # determinize hidden info
            revealed = list(battle.opponent_active_pokemon.moves.keys())
            opp_moveset = self._sample_opponent_moveset(battle.opponent_active_pokemon.species, revealed=revealed)

            state = root_state.clone()
            node = root

            # Selection
            while not node.untried and node.children and not state.terminal:
                _, node = node.select()

            # Expansion
            if node.untried and not state.terminal:
                ak = random.choice(node.untried)  # action key
                child_state = state.clone()
                if node.player == 1:
                    self._do_my_action_key(child_state, ak)
                    next_player = -1
                else:
                    self._do_opp_action_key(child_state, ak)
                    next_player = 1

                child_key = self._node_key(child_state, next_player)
                child = ISNode(player=next_player, key=child_key, parent=node)
                child.untried = (
                    self._legal_opp_action_keys(child_state, opp_moveset) if next_player == -1 else list(my_action_keys)
                )
                node.add_child(ak, child)
                node = child
                state = child_state

            # Rollout
            value = self._rollout_policy(state.clone(), opp_moveset, depth=2)

            # Backprop
            while node is not None:
                node.update(value_for_us=value)
                node = node.parent

        if not root.children:
            return None
        # Best by visits
        best_key, _child = max(root.children.items(), key=lambda kv: kv[1].N)
        # best_key is ('move', move_id)
        if best_key[0] != "move":
            return None
        mid = best_key[1]
        return move_by_id.get(mid)

    # ---------- High-level policy (preserves your heuristics) ----------
    def _protect_win_condition_now(self, battle: AbstractBattle) -> bool:
        active = battle.active_pokemon
        opp = battle.opponent_active_pokemon
        if not active or not opp:
            return False
        if active.species.lower() not in {"koraidon", "zaciancrowned"}:
            return False
        mu = self._estimate_matchup(
            SimMon(active.species, [t.name.lower() for t in active.types if t], active.base_stats, active.current_hp_fraction or 0.0),
            SimMon(opp.species, [t.name.lower() for t in opp.types if t], opp.base_stats, opp.current_hp_fraction or 0.0),
        )
        if mu < -1.5:
            return True
        if (active.current_hp_fraction or 0.0) < 0.4 and any(mid in self.PRIORITY_MOVES for mid in opp.moves):
            return True
        return False

    def choose_move(self, battle: AbstractBattle):
        if isinstance(battle, DoubleBattle):
            return self.choose_random_doubles_move(battle)

        if battle.active_pokemon is None or battle.opponent_active_pokemon is None:
            return self.choose_random_move(battle)

        # Protect win-cons via simple switch rule
        if self._protect_win_condition_now(battle) and battle.available_switches:
            opp = battle.opponent_active_pokemon
            best_sw = max(battle.available_switches, key=lambda s: self._estimate_matchup(
                SimMon(s.species, [t.name.lower() for t in s.types if t], s.base_stats, s.current_hp_fraction or 0.0),
                SimMon(opp.species, [t.name.lower() for t in opp.types if t], opp.base_stats, opp.current_hp_fraction or 0.0)
            ))
            return self.create_order(best_sw)

        # Opportunistic hazards/clear before search
        if battle.available_moves:
            n_opp_remaining = 6 - sum(1 for p in battle.opponent_team.values() if p and p.fainted)
            for m in battle.available_moves:
                if n_opp_remaining >= 3 and m.id in self.ENTRY_HAZARDS and self.ENTRY_HAZARDS[m.id] not in battle.opponent_side_conditions:
                    return self.create_order(m)
                if battle.side_conditions and m.id in self.ANTI_HAZARDS_MOVES:
                    return self.create_order(m)

        # Run ISMCTS to pick our move
        if battle.available_moves:
            best = self._ismcts(battle, iters=96)
            if best is not None:
                return self.create_order(best)

        # Else switch by heuristic
        if battle.available_switches:
            opp = battle.opponent_active_pokemon
            return self.create_order(max(battle.available_switches, key=lambda s: self._estimate_matchup(
                SimMon(s.species, [t.name.lower() for t in s.types if t], s.base_stats, s.current_hp_fraction or 0.0),
                SimMon(opp.species, [t.name.lower() for t in opp.types if t], opp.base_stats, opp.current_hp_fraction or 0.0)
            )))

        return self.choose_random_move(battle)

    # Team preview
    def teampreview(self, battle):
        team_list = list(battle.team.values())
        lead_priority = ["zaciancrowned", "kingambit", "deoxysspeed"]
        for pref in lead_priority:
            for i, p in enumerate(team_list):
                if p.species == pref:
                    return f"/team {i + 1}"
        return "/team 1"

    def choose_team_preview(self, battle):
        return self.teampreview(battle)

    def team_preview(self, battle):
        return self.teampreview(battle)
