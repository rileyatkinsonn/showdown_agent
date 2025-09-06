from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from poke_env.battle.abstract_battle import AbstractBattle
from poke_env.battle.double_battle import DoubleBattle
from poke_env.battle.pokemon import Pokemon
from poke_env.battle.side_condition import SideCondition
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


# ---------- lightweight sim ----------
@dataclass
class SimMon:
    species: str
    types: List[str]
    base_stats: Dict[str, int]
    hp: float
    boosts: Dict[str, int] = field(default_factory=lambda: {"atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0})
    fainted: bool = False

    def clone(self) -> "SimMon":
        return SimMon(self.species, list(self.types), dict(self.base_stats), self.hp, dict(self.boosts), self.fainted)


@dataclass
class SimState:
    me_active: SimMon
    opp_active: SimMon
    me_bench: Dict[str, SimMon]
    opp_bench: Dict[str, SimMon]
    me_sr: bool
    opp_sr: bool
    me_spikes: int
    opp_spikes: int
    terminal: bool = False

    def clone(self) -> "SimState":
        return SimState(
            self.me_active.clone(),
            self.opp_active.clone(),
            {k: v.clone() for k, v in self.me_bench.items()},
            {k: v.clone() for k, v in self.opp_bench.items()},
            self.me_sr, self.opp_sr, self.me_spikes, self.opp_spikes, self.terminal
        )


# ---------- IS node (our decision points only) ----------
class ISNode:
    __slots__ = ("key", "parent", "children", "N", "W", "untried")

    def __init__(self, key: Tuple, parent: Optional["ISNode"] = None):
        self.key = key
        self.parent = parent
        # children are keyed by a JOINT edge: (my_action_key, opp_action_key)
        self.children: Dict[Tuple[Tuple, Tuple], ISNode] = {}
        self.N = 0
        self.W = 0.0
        # untried holds MY action keys; each expansion will bind one sampled opp reply
        self.untried: List[Tuple] = []

    def ucb(self, c: float = 1.25) -> float:
        if self.N == 0:
            return float("inf")
        return (self.W / self.N) + c * math.sqrt(math.log(max(1, self.parent.N)) / self.N)

    def select(self) -> Tuple[Tuple[Tuple, Tuple], "ISNode"]:
        return max(self.children.items(), key=lambda kv: kv[1].ucb())

    def add_child(self, joint_key: Tuple[Tuple, Tuple], child: "ISNode"):
        self.children[joint_key] = child

    def update(self, val: float):
        self.N += 1
        self.W += val


# ---------- agent ----------
class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)
        self.gen = GenData.from_gen(9)

        self.ENTRY_HAZARDS = {
            "spikes": SideCondition.SPIKES,
            "stealthrock": SideCondition.STEALTH_ROCK,
            "stickyweb": SideCondition.STICKY_WEB,
            "toxicspikes": SideCondition.TOXIC_SPIKES,
        }
        self.ANTI_HAZARDS_MOVES = {"rapidspin", "defog"}
        self.PRIORITY_MOVES = {"suckerpunch", "extremespeed", "quickattack", "bulletpunch", "iceshard", "aquajet"}

    # ----- helpers -----
    def _types_of(self, species: str) -> List[str]:
        sp = (species or "").lower().replace("-", "").replace("_", "")
        return [t.lower() for t in self.gen.pokedex.get(sp, {}).get("types", [])]

    def _base_stats_of(self, species: str) -> Dict[str, int]:
        sp = (species or "").lower().replace("-", "").replace("_", "")
        d = self.gen.pokedex.get(sp, {})
        if not d and "crowned" in sp:
            d = self.gen.pokedex.get(sp.replace("crowned", ""), {})
        return d.get("baseStats", {"hp": 100, "atk": 100, "def": 100, "spa": 100, "spd": 100, "spe": 100})

    def _type_mult(self, atk_type: str, def_types: List[str]) -> float:
        if not def_types: return 1.0
        atk = (atk_type or "").upper()
        eff = 1.0
        for dt in def_types:
            eff *= self.gen.type_chart.get((dt or "").upper(), {}).get(atk, 1.0)
        return float(eff)

    def _stat_ratio(self, atk_bs: int, def_bs: int, atk_boost: int, def_boost: int) -> float:
        def mult(n): return (2 + n) / 2 if n > 0 else 2 / max(1, (2 - n))
        return max(1.0, (atk_bs * mult(atk_boost)) / max(1.0, def_bs * mult(def_boost)))

    def _estimate_matchup(self, me: SimMon | Pokemon, opp: SimMon | Pokemon) -> float:
        my_types = [getattr(t, "name", t).lower() for t in (me.types if isinstance(me, SimMon) else me.types) if t]
        opp_types = [getattr(t, "name", t).lower() for t in (opp.types if isinstance(opp, SimMon) else opp.types) if t]
        off = max([self._type_mult(t, opp_types) for t in my_types], default=1.0)
        deff = max([self._type_mult(t, my_types) for t in opp_types], default=1.0)
        score = off - deff
        try:
            my_spe = me.base_stats["spe"] if isinstance(me, SimMon) else me.base_stats["spe"]
            op_spe = opp.base_stats["spe"] if isinstance(opp, SimMon) else opp.base_stats["spe"]
            score += 0.1 if my_spe > op_spe else (-0.1 if op_spe > my_spe else 0.0)
        except Exception:
            pass
        try:
            my_hp = me.hp if isinstance(me, SimMon) else (me.current_hp_fraction or 0.0)
            op_hp = opp.hp if isinstance(opp, SimMon) else (opp.current_hp_fraction or 0.0)
            score += (my_hp - op_hp) * 0.4
        except Exception:
            pass
        return float(score)

    # NEW: abstract actions for simulated states (deeper than root)
    def _legal_my_actions_sim(self, s: SimState) -> List[tuple]:
        acts = []
        types = s.me_active.types or ["normal"]
        for t in types:
            acts.append(("move", f"stab_{t}_P"))  # physical STAB proxy
            acts.append(("move", f"stab_{t}_S"))  # special STAB proxy
        for sp in self._best_switch_candidates(s, k=2):
            acts.append(("switch", sp))
        return acts

    def _move_meta(self, move_id: str) -> Dict:
        if move_id.startswith("stab_"):  # e.g., "stab_dark_P"
            _, t, kind = move_id.split("_", 2)
            return {
                "id": move_id,
                "type": t.lower(),
                "bp": 85 if kind == "P" else 90,  # small bias to special if you like
                "category": "Physical" if kind == "P" else "Special",
                "accuracy": 0.95,
                "priority": 0,
            }
        m = self.gen.moves.get(move_id, {})
        return {
            "id": move_id,
            "type": (m.get("type") or "").lower(),
            "bp": m.get("basePower", 0) or 0,
            "category": m.get("category", "Status"),
            "accuracy": m.get("accuracy", 1.0) if m.get("accuracy") is not None else 1.0,
            "priority": m.get("priority", 0) or 0,
        }

    # ----- determinization -----
    def _sample_opp_moveset(self, species: str, revealed: List[str]) -> List[str]:
        keep = {m.lower() for m in revealed if m}
        types = set(self._types_of(species))
        pool: List[str] = []
        for mid, md in self.gen.moves.items():
            t = (md.get("type") or "").lower()
            bp = md.get("basePower", 0) or 0
            cat = md.get("category", "Status")
            if cat in ("Physical", "Special") and bp >= 60:
                weight = 3 if t in types else 1
                pool.extend([mid] * weight)
        if not pool:
            pool = list(self.gen.moves.keys())
        while len(keep) < 4:
            keep.add(random.choice(pool).lower())
        return list(keep)[:4]

    # ----- forward model -----
    def _damage_proxy(self, atk: SimMon, md: Dict, dfn: SimMon) -> float:
        if md["category"] == "Status" or md["bp"] <= 0: return 0.0
        teff = self._type_mult(md["type"], dfn.types)
        if teff == 0.0: return 0.0
        if md["category"] == "Physical":
            ratio = self._stat_ratio(atk.base_stats["atk"], dfn.base_stats["def"], atk.boosts["atk"], dfn.boosts["def"])
        else:
            ratio = self._stat_ratio(atk.base_stats["spa"], dfn.base_stats["spd"], atk.boosts["spa"], dfn.boosts["spd"])
        stab = 1.5 if md["type"] in atk.types else 1.0
        acc = md["accuracy"] if isinstance(md["accuracy"], (int, float)) else 1.0
        base = md["bp"] * ratio * stab * teff * acc
        return max(0.0, min(1.0, base / 300.0))

    def _apply_attack(self, s: SimState, attacker_is_me: bool, md: Dict):
        atk = s.me_active if attacker_is_me else s.opp_active
        dfn = s.opp_active if attacker_is_me else s.me_active
        dmg = self._damage_proxy(atk, md, dfn) * random.uniform(0.85, 1.0)
        dfn.hp = max(0.0, dfn.hp - dmg)
        if dfn.hp <= 0.0:
            dfn.fainted = True

    def _apply_switch_in_hazards(self, s: SimState, switch_is_me: bool):
        sr = s.me_sr if switch_is_me else s.opp_sr
        spikes = s.me_spikes if switch_is_me else s.opp_spikes
        mon = s.me_active if switch_is_me else s.opp_active
        total = 0.0
        if sr:
            total += min(0.5, 0.125 * self._type_mult("rock", mon.types))
        if spikes:
            total += {1: 0.125, 2: 0.167, 3: 0.25}.get(spikes, 0.125)
        mon.hp = max(0.0, mon.hp - total)
        if mon.hp <= 0.0:
            mon.fainted = True

    # ----- evaluation -----
    def _eval(self, s: SimState) -> float:
        if s.me_active.fainted and not s.me_bench: return -10.0
        if s.opp_active.fainted and not s.opp_bench: return 10.0
        my_hp = s.me_active.hp + sum(m.hp for m in s.me_bench.values())
        op_hp = s.opp_active.hp + sum(m.hp for m in s.opp_bench.values())
        mu = self._estimate_matchup(s.me_active, s.opp_active)
        return (my_hp - op_hp) * 2.5 + mu * 2.0

    # ----- info-set key -----
    def _hp_bin(self, hp: float) -> int: return int(max(0.0, min(1.0, hp)) * 10)

    def _node_key(self, s: SimState) -> Tuple:
        return (
            s.me_active.species, self._hp_bin(s.me_active.hp),
            s.opp_active.species, self._hp_bin(s.opp_active.hp),
            tuple(sorted((k, self._hp_bin(v.hp)) for k, v in s.me_bench.items())),
            tuple(sorted((k, self._hp_bin(v.hp)) for k, v in s.opp_bench.items())),
            s.me_sr, s.opp_sr, s.me_spikes, s.opp_spikes
        )

    # ----- build state -----
    def _to_sim(self, battle: AbstractBattle) -> SimState:
        me = battle.active_pokemon
        op = battle.opponent_active_pokemon
        me_active = SimMon(me.species, [t.name.lower() for t in me.types if t], me.base_stats, me.current_hp_fraction or 0.0)
        opp_active = SimMon(op.species, [t.name.lower() for t in op.types if t], op.base_stats, op.current_hp_fraction or 0.0)

        me_bench, opp_bench = {}, {}
        for p in battle.team.values():
            if p is me: continue
            me_bench[p.species] = SimMon(p.species, [t.name.lower() for t in p.types if t], p.base_stats, p.current_hp_fraction or 0.0, fainted=bool(p.fainted))
        for p in battle.opponent_team.values():
            if p and (not p.fainted) and p is not op:
                opp_bench[p.species] = SimMon(p.species, [t.name.lower() for t in p.types if t], p.base_stats, p.current_hp_fraction or 1.0, fainted=bool(p.fainted))

        me_sr = SideCondition.STEALTH_ROCK in battle.side_conditions
        opp_sr = SideCondition.STEALTH_ROCK in battle.opponent_side_conditions
        me_spikes = battle.side_conditions.get(SideCondition.SPIKES, 0)
        opp_spikes = battle.opponent_side_conditions.get(SideCondition.SPIKES, 0)

        return SimState(me_active, opp_active, me_bench, opp_bench, me_sr, opp_sr, me_spikes, opp_spikes)

    # ----- actions -----
    def _best_switch_candidates(self, s: SimState, k: int = 2) -> List[str]:
        if not s.me_bench: return []
        items = list(s.me_bench.items())
        items.sort(key=lambda kv: self._estimate_matchup(kv[1], s.opp_active), reverse=True)
        return [sp for sp, _ in items[:k]]

    def _legal_my_actions(self, battle: AbstractBattle, s: SimState) -> List[Tuple]:
        acts = [("move", m.id) for m in battle.available_moves]
        for sp in self._best_switch_candidates(s, 2):
            acts.append(("switch", sp))
        return acts

    def _legal_opp_actions(self, s: SimState, opp_moveset: List[str]) -> List[Tuple]:
        acts = [("opp_move", mid) for mid in opp_moveset]
        for sp, mon in s.opp_bench.items():
            if not mon.fainted and mon.hp > 0.0:
                acts.append(("opp_switch", sp))
        return acts if acts else [("opp_pass", None)]

    # ----- joint turn application -----
    def _apply_joint(self, s: SimState, my_ak: Tuple, opp_ak: Tuple):
        # Switches happen before attacks
        # 1) both switch
        if my_ak[0] == "switch" and opp_ak[0] == "opp_switch":
            # my switch
            sp = my_ak[1]
            if sp in s.me_bench:
                if not s.me_active.fainted and s.me_active.hp > 0:
                    s.me_bench[s.me_active.species] = s.me_active.clone()
                s.me_active = s.me_bench[sp].clone()
                del s.me_bench[sp]
                self._apply_switch_in_hazards(s, True)
            # opp switch
            sp2 = opp_ak[1]
            if sp2 in s.opp_bench:
                if not s.opp_active.fainted and s.opp_active.hp > 0:
                    s.opp_bench[s.opp_active.species] = s.opp_active.clone()
                s.opp_active = s.opp_bench[sp2].clone()
                del s.opp_bench[sp2]
                self._apply_switch_in_hazards(s, False)
            return

        # 2) my switch, opp attacks
        if my_ak[0] == "switch" and opp_ak[0] != "opp_switch":
            sp = my_ak[1]
            if sp in s.me_bench:
                if not s.me_active.fainted and s.me_active.hp > 0:
                    s.me_bench[s.me_active.species] = s.me_active.clone()
                s.me_active = s.me_bench[sp].clone()
                del s.me_bench[sp]
                self._apply_switch_in_hazards(s, True)
            if opp_ak[0] == "opp_move":
                self._apply_attack(s, False, self._move_meta(opp_ak[1]))
            return

        # 3) opp switch, my attack
        if opp_ak[0] == "opp_switch" and my_ak[0] != "switch":
            sp2 = opp_ak[1]
            if sp2 in s.opp_bench:
                if not s.opp_active.fainted and s.opp_active.hp > 0:
                    s.opp_bench[s.opp_active.species] = s.opp_active.clone()
                s.opp_active = s.opp_bench[sp2].clone()
                del s.opp_bench[sp2]
                self._apply_switch_in_hazards(s, False)
            if my_ak[0] == "move":
                self._apply_attack(s, True, self._move_meta(my_ak[1]))
            return

        # 4) attack vs attack → priority then speed
        if my_ak[0] == "move" and opp_ak[0] == "opp_move":
            my_md = self._move_meta(my_ak[1])
            op_md = self._move_meta(opp_ak[1])
            my_pri, op_pri = my_md["priority"], op_md["priority"]
            my_spe = s.me_active.base_stats.get("spe", 100) + s.me_active.boosts.get("spe", 0)
            op_spe = s.opp_active.base_stats.get("spe", 100) + s.opp_active.boosts.get("spe", 0)
            my_first = (my_pri > op_pri) or (my_pri == op_pri and my_spe >= op_spe)

            if my_first:
                self._apply_attack(s, True, my_md)
                if not s.opp_active.fainted:
                    self._apply_attack(s, False, op_md)
            else:
                self._apply_attack(s, False, op_md)
                if not s.me_active.fainted:
                    self._apply_attack(s, True, my_md)
            return

        # 5) any pass
        if my_ak[0] == "move" and opp_ak[0] == "opp_pass":
            self._apply_attack(s, True, self._move_meta(my_ak[1]))
        # if my_ak is switch and opp_pass, switch only handled above

        # auto-switch on faint (greedy highest HP)
        if s.opp_active.fainted:
            if s.opp_bench:
                sw = max(s.opp_bench.values(), key=lambda mm: mm.hp)
                s.opp_active = sw.clone()
                del s.opp_bench[sw.species]
                self._apply_switch_in_hazards(s, False)
            else:
                s.terminal = True
        if s.me_active.fainted:
            if s.me_bench:
                sw = max(s.me_bench.values(), key=lambda mm: mm.hp)
                s.me_active = sw.clone()
                del s.me_bench[sw.species]
                self._apply_switch_in_hazards(s, True)
            else:
                s.terminal = True

    # ----- opp reply policy (ε-greedy over Top-K) -----
    def _opp_topk_sample(self, s: SimState, opp_actions: List[Tuple], k: int = 3, eps: float = 0.1) -> Tuple:
        scored = []
        for a in opp_actions:
            if a[0] == "opp_move":
                val = self._damage_proxy(s.opp_active, self._move_meta(a[1]), s.me_active)
            elif a[0] == "opp_switch":
                val = s.opp_bench[a[1]].hp if a[1] in s.opp_bench else 0.0
            else:
                val = 0.0
            scored.append((val, a))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [a for _, a in scored[:max(1, k)]]
        if random.random() < eps:
            return random.choice(opp_actions)
        return random.choice(top)

    # ----- rollout (joint turns, shallow) -----
    def _rollout(self, s: SimState, opp_moveset: List[str], depth: int = 2) -> float:
        d = 0
        while (not s.terminal) and d < depth:
            # pick a rough best move for us (phys/special by higher stat)
            use_phys = s.me_active.base_stats.get("atk", 100) >= s.me_active.base_stats.get("spa", 100)
            my_md = None
            best = -1.0
            for t in (s.me_active.types or ["normal"]):
                md = {"id": f"stab_{t}_{'P' if use_phys else 'S'}", "type": t, "bp": 90,
                      "category": "Physical" if use_phys else "Special", "accuracy": 0.95, "priority": 0}
                sc = self._damage_proxy(s.me_active, md, s.opp_active)
                if sc > best:
                    best, my_md = sc, md
            my_ak = ("move", my_md["id"]) if my_md else ("move", "tackle")

            opp_acts = self._legal_opp_actions(s, opp_moveset)
            opp_ak = self._opp_topk_sample(s, opp_acts, k=3, eps=0.1)

            self._apply_joint(s, my_ak, opp_ak)
            d += 1
        return self._eval(s)

    # ----- ISMCTS (joint turn) -----
    def _ismcts(self, battle: AbstractBattle, iters: int = 256) -> Tuple[str, Optional[str]]:
        root_state = self._to_sim(battle)
        my_actions = self._legal_my_actions(battle, root_state)
        if not my_actions:
            return ("pass", None)

        revealed = list(battle.opponent_active_pokemon.moves.keys())
        root = ISNode(self._node_key(root_state), None)
        root.untried = list(my_actions)

        # We also need a map to count visits per MY action (aggregating different opp replies)
        visit_by_my_action: Dict[Tuple, int] = {}

        for _ in range(max(32, iters)):
            state = root_state.clone()
            node = root
            # fresh determinization each iter
            opp_moveset = self._sample_opp_moveset(battle.opponent_active_pokemon.species, revealed)

            # Selection
            while not node.untried and node.children and not state.terminal:
                joint_key, child = node.select()
                my_ak, opp_ak = joint_key
                self._apply_joint(state, my_ak, opp_ak)
                node = child

            # Expansion
            if node.untried and not state.terminal:
                my_ak = random.choice(node.untried)
                opp_ak = self._opp_topk_sample(state, self._legal_opp_actions(state, opp_moveset), k=3, eps=0.2)
                child_state = state.clone()
                self._apply_joint(child_state, my_ak, opp_ak)
                child = ISNode(self._node_key(child_state), node)
                child.untried = self._legal_my_actions_sim(child_state)
                node.add_child((my_ak, opp_ak), child)
                # leave my_ak in untried? no — consume once to cap branching
                node.untried.remove(my_ak)
                node = child
                state = child_state

            # Rollout
            value = self._rollout(state.clone(), opp_moveset, depth=3)

            # Backprop
            cur = node
            while cur:
                cur.update(value)
                cur = cur.parent

        # Choose best root move by aggregated child visits
        for (my_ak, _opp_ak), child in root.children.items():
            visit_by_my_action[my_ak] = visit_by_my_action.get(my_ak, 0) + child.N
        if not visit_by_my_action:
            return ("pass", None)
        best_my_action = max(visit_by_my_action.items(), key=lambda kv: kv[1])[0]
        kind, payload = best_my_action
        if kind == "move":
            return ("move", payload)
        else:
            return ("switch", payload)

    # ----- outer policy -----
    def choose_move(self, battle: AbstractBattle):
        if isinstance(battle, DoubleBattle):
            return self.choose_random_doubles_move(battle)
        if not battle.active_pokemon or not battle.opponent_active_pokemon:
            return self.choose_random_move(battle)

        # quick, cheap heuristics first (hazards / clear)
        if battle.available_moves:
            n_opp_remaining = 6 - sum(1 for p in battle.opponent_team.values() if p and p.fainted)
            for m in battle.available_moves:
                if n_opp_remaining >= 3 and m.id in self.ENTRY_HAZARDS and self.ENTRY_HAZARDS[m.id] not in battle.opponent_side_conditions:
                    return self.create_order(m)
                if battle.side_conditions and m.id in self.ANTI_HAZARDS_MOVES:
                    return self.create_order(m)

        # run ISMCTS
        if battle.available_moves:
            decision, payload = self._ismcts(battle, iters=160)
            if decision == "move":
                mv = next((m for m in battle.available_moves if m.id == payload), None)
                if mv is not None:
                    return self.create_order(mv)
            elif decision == "switch" and battle.available_switches:
                # map species → actual switch object
                sw = next((s for s in battle.available_switches if s.species == payload), None)
                if sw is not None:
                    return self.create_order(sw)

        # fallback: best switch by matchup
        if battle.available_switches:
            opp = battle.opponent_active_pokemon
            def mu(s):
                me = SimMon(s.species, [t.name.lower() for t in s.types if t], s.base_stats, s.current_hp_fraction or 0.0)
                op = SimMon(opp.species, [t.name.lower() for t in opp.types if t], opp.base_stats, opp.current_hp_fraction or 0.0)
                return self._estimate_matchup(me, op)
            sw = max(battle.available_switches, key=mu)
            return self.create_order(sw)

        return self.choose_random_move(battle)

    # team preview
    def teampreview(self, battle):
        order = ["zaciancrowned", "kingambit", "deoxysspeed"]
        team_list = list(battle.team.values())
        for pref in order:
            for i, p in enumerate(team_list):
                if p.species == pref:
                    return f"/team {i + 1}"
        return "/team 1"

    def choose_team_preview(self, battle): return self.teampreview(battle)
    def team_preview(self, battle): return self.teampreview(battle)
