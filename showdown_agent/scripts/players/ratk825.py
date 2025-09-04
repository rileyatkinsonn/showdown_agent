from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Tuple, List, Optional, Hashable
from collections import defaultdict
import math
import random
import time

# --- poke-env imports (runtime dependency) ---
from poke_env.player.player import Player
from poke_env.player.battle_order import BattleOrder
from poke_env.battle.abstract_battle import AbstractBattle
from poke_env.battle.pokemon import Pokemon
from poke_env.battle.side_condition import SideCondition
from poke_env.battle.pokemon_type import PokemonType
from poke_env.battle.move_category import MoveCategory
from poke_env.data import GenData, to_id_str


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

# ============================================================
# 1) Lightweight types
# ============================================================

Action = Tuple[str, Any]  # e.g., ("move", move_id), ("switch", ident), ("tera_move", move_id)
Reward = float

@dataclass(frozen=True)
class InfosetKey:
    key: Tuple[Hashable, ...]


@dataclass
class Infoset:
    key: InfosetKey
    obs: Dict[str, Any]  # observable snapshot (hp bins, hazards, weather, etc.)


@dataclass
class ConcreteState:
    # one determinization (hidden choices) + a compact abstract state we mutate in rollouts
    hidden: Dict[str, Any]
    obs: Dict[str, Any]
    # abstract mutable fields for rollout
    me_hp_bin: int
    opp_hp_bin: int
    me_boosts: Dict[str, int]
    opp_boosts: Dict[str, int]
    me_tera_used: bool
    opp_tera_used: bool
    side_conds: Dict[str, int]
    opp_side_conds: Dict[str, int]
    terminal: bool = False
    # bookkeeping
    my_kos: int = 0
    opp_kos: int = 0
    ply: int = 0


@dataclass
class SearchResult:
    best_action: Action
    action_stats: Dict[Action, Tuple[int, float]]  # visits, mean-Q


# ============================================================
# 2) Small helpers
# ============================================================

def bucket_hp(x: float) -> int:
    # 0..10 bins
    x = max(0.0, min(1.0, x))
    return int(round(10 * x))

def clamp_boost(v: int) -> int:
    return max(-2, min(2, v))

def type_mult(gendata: GenData, atk: Optional[str], defend_types: List[str]) -> float:
    if not atk or not defend_types:
        return 1.0
    row = getattr(gendata, "type_chart", {}).get(to_id_str(atk), {})
    mult = 1.0
    for d in defend_types:
        mult *= float(row.get(to_id_str(d), 1.0))
    return mult

# ============================================================
# 3) Infoset extraction (observable only)
# ============================================================

def _freeze_for_key(x):
    if isinstance(x, dict):
        # sort keys for stability, and freeze values
        return tuple((k, _freeze_for_key(v)) for k, v in sorted(x.items(), key=lambda kv: str(kv[0])))
    if isinstance(x, list):
        return tuple(_freeze_for_key(v) for v in x)
    if isinstance(x, tuple):
        return tuple(_freeze_for_key(v) for v in x)
    # enums or objects: fall back to their string id if needed
    try:
        hash(x)
        return x
    except TypeError:
        return str(x)

def make_infoset(battle: AbstractBattle) -> Infoset:
    me = battle.active_pokemon
    opp = battle.opponent_active_pokemon

    def boost_tuple(mon: Optional[Pokemon]):
        if not mon:
            return ()
        return tuple(sorted((k, clamp_boost(v)) for k, v in mon.boosts.items()))

    obs: Dict[str, Any] = {
        "turn": battle.turn,
        "me_active": getattr(me, "species", None),
        "opp_active": getattr(opp, "species", None),
        "me_types": [t.name.lower() for t in (me.types if me else []) if t],
        "opp_types": [t.name.lower() for t in (opp.types if opp else []) if t],
        "me_hp_bin": bucket_hp(getattr(me, "current_hp_fraction", 1.0)) if me else 10,
        "opp_hp_bin": bucket_hp(getattr(opp, "current_hp_fraction", 1.0)) if opp else 10,
        "me_boosts": boost_tuple(me),
        "opp_boosts": boost_tuple(opp),
        "side_conds": tuple(sorted((to_id_str(str(k.name)), v) for k, v in battle.side_conditions.items())),
        "opp_side_conds": tuple(sorted((to_id_str(str(k.name)), v) for k, v in battle.opponent_side_conditions.items())),
        "weather": tuple(sorted((to_id_str(str(k.name)), v) for k, v in battle.weather.items())),
        "fields": tuple(sorted((to_id_str(str(k.name)), v) for k, v in battle.fields.items())),
        "me_tera_used": battle.used_tera,
        "opp_tera_used": battle.opponent_used_tera,
        "revealed_opp_moves": tuple(sorted(getattr(opp, "moves", {}).keys())) if opp else (),
        "revealed_opp_item": getattr(opp, "item", None) if opp else None,
        "revealed_opp_ability": to_id_str(getattr(opp, "ability", "")) if opp and getattr(opp, "ability", None) else None,
    }
    key = InfosetKey(_freeze_for_key(obs))
    return Infoset(key=key, obs=obs)

# ============================================================
# 4) Determinization (simple expert-friendly priors)
# ============================================================

# You can flesh this out per species if you want; keep it small and expert-coded (no ML)
SPECIES_PRIORS: Dict[str, Dict[str, Any]] = {
    # "zaciancrowned": {"tera": ["flying", "steel"], "items": ["rustedsword"], "common_moves": ["behemothblade","closecombat","wildcharge","swordsdance"]},
    # ...
}

def sample_determinization(info: Infoset, gendata: GenData) -> ConcreteState:
    opp_species = (info.obs.get("opp_active") or "").lower().replace(" ", "").replace("-", "")
    prior = SPECIES_PRIORS.get(opp_species, {})
    revealed = set(info.obs["revealed_opp_moves"])
    common = prior.get("common_moves", [])
    fill = [m for m in common if m not in revealed]
    random.shuffle(fill)
    moveset = list(revealed) + fill[: max(0, 4 - len(revealed))]

    item = info.obs["revealed_opp_item"] or random.choice(prior.get("items", ["leftovers", "boots", "sash"]))
    tera = "none" if info.obs["opp_tera_used"] else random.choice(prior.get("tera", ["steel", "fire", "fairy"]))
    ev_template = prior.get("ev", "offensive")  # "offensive"/"bulky"/"balanced"

    # Build mutable abstract state for rollout
    conc = ConcreteState(
        hidden={"opp_moveset": tuple(moveset), "opp_item": item, "opp_tera": tera, "opp_ev": ev_template},
        obs=info.obs,
        me_hp_bin=info.obs["me_hp_bin"],
        opp_hp_bin=info.obs["opp_hp_bin"],
        me_boosts=dict(info.obs["me_boosts"]),
        opp_boosts=dict(info.obs["opp_boosts"]),
        me_tera_used=bool(info.obs["me_tera_used"]),
        opp_tera_used=bool(info.obs["opp_tera_used"]),
        side_conds={k: v for k, v in info.obs["side_conds"]},
        opp_side_conds={k: v for k, v in info.obs["opp_side_conds"]},
    )
    return conc


# ============================================================
# 5) Node + UCT + progressive widening
# ============================================================

class Node:
    __slots__ = ("key", "N", "Q", "N_a", "children", "untried")

    def __init__(self, key: InfosetKey, legal_actions: List[Action]):
        self.key = key
        self.N = 0
        self.Q: Dict[Action, float] = defaultdict(float)
        self.N_a: Dict[Action, int] = defaultdict(int)
        self.children: Dict[Action, Node] = {}
        self.untried: List[Action] = list(legal_actions)

    def ucb(self, a: Action, c: float = 1.2) -> float:
        n = self.N_a[a]
        if n == 0:
            return float("inf")
        return (self.Q[a] / n) + c * math.sqrt(max(1e-9, math.log(self.N) / n))

    def record(self, a: Action, r: Reward):
        self.N += 1
        self.N_a[a] += 1
        self.Q[a] += r


def progressive_widening(node: Node, legal_actions: List[Action], k: float = 1.2, alpha: float = 0.6):
    limit = int(k * (node.N ** alpha)) + 1
    # keep untried limited (simple heuristic: keep initial order)
    if len(node.children) + len(node.untried) > limit:
        keep = max(0, limit - len(node.children))
        node.untried = node.untried[:keep]

# ============================================================
# 6) Adapter: converts poke-env battle to abstract actions, & tiny rollout model
# ============================================================

class PokeEnvAdapter:
    """
    A very small 'environment adapter':
    - At root, it reads legal actions from poke-env (moves/switches/tera+move).
    - For ISMCTS/rollout, it holds a ConcreteState and applies abstract effects:
        * We don't need pixel-accurate simulation; we update hp bins using heuristic scores.
    """

    def __init__(self, player: Player, battle: AbstractBattle, gendata: GenData, heuristics_fn=None, iters_time_budget_s: float = 1.2):
        self.player = player
        self.battle = battle
        self.gendata = gendata
        self.heuristics_fn = heuristics_fn  # optional: your external heuristic chooser
        self.time_budget_s = iters_time_budget_s

        # cache current legal actions at root
        self.root_actions: List[Action] = self._extract_legal_actions(self.battle)

    # -------- root API --------

    def legal_actions(self) -> List[Action]:
        return list(self.root_actions)

    def to_battle_order(self, action: Action) -> BattleOrder:
        kind, payload = action
        if kind == "move":
            return self.player.create_order(payload)  # payload is Move object
        if kind == "switch":
            return self.player.create_order(payload)  # payload is Pokemon object
        if kind == "tera_move":
            move = payload
            return self.player.create_order(move, terastallize=True)
        # fallback
        return self.player.choose_random_move(self.battle)

    def heuristic_best_action(self) -> Action:
        # fallback when search does nothing
        # pick the highest scoring move by a simple heuristic
        best: Optional[Action] = None
        best_v = -1e9
        for a in self.root_actions:
            v = self._root_action_score(a)
            if v > best_v:
                best, best_v = a, v
        return best or self.root_actions[0]

    def clone_with(self, conc: ConcreteState) -> "RolloutEnv":
        # Build a rollout environment that owns the abstract mutable state
        return RolloutEnv(conc, self.gendata)

    def infoset_key(self) -> InfosetKey:
        # (only used inside RolloutEnv; at root we already have it from make_infoset)
        info = make_infoset(self.battle)
        return info.key

    # -------- helpers --------

    def _extract_legal_actions(self, battle: AbstractBattle) -> List[Action]:
        acts: List[Action] = []
        # moves
        for mv in (battle.available_moves or []):
            if battle.can_tera and (not battle.used_tera):
                # include tera+move as a distinct option (but you can gate with progressive widening)
                acts.append(("tera_move", mv))
            acts.append(("move", mv))
        # switches
        for sw in (battle.available_switches or []):
            acts.append(("switch", sw))
        # progressive widening will trim
        return acts

    def _root_action_score(self, a: Action) -> float:
        # a quick score for initial ordering / fallback
        kind, payload = a
        me = self.battle.active_pokemon
        opp = self.battle.opponent_active_pokemon
        if kind == "switch":
            if not opp or not payload:
                return 0.0
            return self._estimate_matchup(payload, opp)
        else:
            mv = payload
            return self._move_value(mv, me, opp)

    # --- simple reusable parts from your heuristics ---

    def _estimate_matchup(self, mon: Pokemon, opp: Pokemon) -> float:
        # type edges (max multiplier of opponent hitting me minus me hitting opp)
        my_def_types = [t for t in mon.types if t]
        opp_def_types = [t for t in opp.types if t]
        # rough: assume best STAB of each
        my_best = 1.0
        for t in my_def_types:
            my_best = max(my_best, type_mult(self.gendata, t.name.lower(), [d.name.lower() for d in opp_def_types]))
        opp_best = 1.0
        for t in opp_def_types:
            opp_best = max(opp_best, type_mult(self.gendata, t.name.lower(), [d.name.lower() for d in my_def_types]))
        # hp consideration
        hp_term = (getattr(mon, "current_hp_fraction", 1.0) - getattr(opp, "current_hp_fraction", 1.0))
        return (my_best - opp_best) + 0.4 * hp_term

    def _move_value(self, move, me: Optional[Pokemon], opp: Optional[Pokemon]) -> float:
        if not (me and opp and move):
            return 0.0
        # coarse value: base power * STAB * type mult * acc * (off/def ratios coarse)
        bp = move.base_power or 0
        acc = move.accuracy if move.accuracy is not None else 1.0
        stab = 1.5 if (move.type and move.type in me.types) else 1.0
        mult = opp.damage_multiplier(move)
        # favor priority a bit
        prio = getattr(move, "priority", 0) or 0
        pr_bonus = 0.05 * prio
        # penalize recoil / misses slightly
        miss_pen = (1.0 - acc) * 0.2
        return bp * stab * mult * acc * (1.0 + pr_bonus) * (1.0 - miss_pen)


class RolloutEnv:
    """
    Rollout environment over a ConcreteState (abstract, not the live battle).
    Stepping rule:
      - On our turn, pick an action (passed by ISMCTS), apply abstract damage to opp_hp_bin.
      - On opp turn, pick a simple opponent action (heuristic) and apply damage to me_hp_bin.
      - Track KOs, basic hazard value, and end after 'cutoff' plies or KO.
    """
    def __init__(self, conc: ConcreteState, gendata: GenData, cutoff: int = 6):
        self.s = conc
        self.gendata = gendata
        self.cutoff = cutoff

        # frozen info for heuristics
        self.me_types = self.s.obs["me_types"]
        self.opp_types = self.s.obs["opp_types"]
        self.opp_moveset = list(self.s.hidden.get("opp_moveset", []))

    def terminal(self) -> bool:
        return self.s.terminal or self.s.ply >= self.cutoff or self.s.me_hp_bin <= 0 or self.s.opp_hp_bin <= 0

    def legal_actions(self) -> List[Action]:
        # abstract action menu: always allow {ATTACK, SETUP, HAZARD, SWITCH, TERA+ATTACK?}
        # we proxy them as simple tuples; mapping back to BattleOrder happens only at root.
        # For rollouts, we only need a few "modes" to evaluate consequences.
        acts: List[Action] = [("A_ATTACK", None)]
        # allow HAZARD early if opponent has >3 mons alive (approx via opp_hp_bin)
        if self.s.opp_hp_bin > 3 and "stealthrock" in self.opp_moveset:  # optional gate
            acts.append(("A_HAZARD", None))
        acts.append(("A_SETUP", None))
        acts.append(("A_SWITCH", None))
        if not self.s.me_tera_used:
            acts.append(("A_TERA_ATTACK", None))
        return acts

    def infoset_key(self) -> InfosetKey:
        # Generate a key consistent with how root keys look (roughly)
        obs_like = (
            ("me_hp_bin", self.s.me_hp_bin),
            ("opp_hp_bin", self.s.opp_hp_bin),
            ("me_tera_used", self.s.me_tera_used),
            ("opp_tera_used", self.s.opp_tera_used),
            ("side_conds", tuple(sorted(self.s.side_conds.items()))),
            ("opp_side_conds", tuple(sorted(self.s.opp_side_conds.items()))),
        )
        return InfosetKey(obs_like)

    def step(self, action: Action):
        if self.terminal():
            return
        # our ply
        self._apply_our_action(action)
        self.s.ply += 1
        self._check_terminal()
        if self.terminal():
            return
        # opponent ply
        opp_action = self._opponent_policy()
        self._apply_opp_action(opp_action)
        self.s.ply += 1
        self._check_terminal()

    def _apply_our_action(self, action: Action):
        k, _ = action
        dmg = 0
        if k in ("A_ATTACK", "A_TERA_ATTACK"):
            base = 2.0  # abstract damage in hp bins; tune
            stab_bonus = 0.5 if k == "A_TERA_ATTACK" else 0.3
            type_bonus = self._best_type_mult(attacker="me", defender="opp")
            dmg = int(round(base + stab_bonus + type_bonus))
            self.s.opp_hp_bin = max(0, self.s.opp_hp_bin - dmg)
            if k == "A_TERA_ATTACK":
                self.s.me_tera_used = True
        elif k == "A_HAZARD":
            # add a little persistent pressure
            self.s.opp_side_conds["stealthrock"] = self.s.opp_side_conds.get("stealthrock", 0) + 1
        elif k == "A_SETUP":
            self.s.me_boosts["spa"] = clamp_boost(self.s.me_boosts.get("spa", 0) + 1)
        elif k == "A_SWITCH":
            # mild defensive heal / reset risk
            self.s.me_hp_bin = min(10, self.s.me_hp_bin + 1)

    def _apply_opp_action(self, action_key: str):
        dmg = 0
        if action_key in ("ATTACK", "TERA_ATTACK"):
            base = 2.0
            stab_bonus = 0.5 if action_key == "TERA_ATTACK" else 0.3
            type_bonus = self._best_type_mult(attacker="opp", defender="me")
            dmg = int(round(base + stab_bonus + type_bonus))
            self.s.me_hp_bin = max(0, self.s.me_hp_bin - dmg)
            if action_key == "TERA_ATTACK":
                self.s.opp_tera_used = True
        elif action_key == "SETUP":
            self.s.opp_boosts["spa"] = clamp_boost(self.s.opp_boosts.get("spa", 0) + 1)
        elif action_key == "HAZARD":
            self.s.side_conds["stealthrock"] = self.s.side_conds.get("stealthrock", 0) + 1
        elif action_key == "SWITCH":
            self.s.opp_hp_bin = min(10, self.s.opp_hp_bin + 1)

    def _best_type_mult(self, attacker: str, defender: str) -> float:
        if attacker == "me":
            atks = self.me_types
            defs = self.opp_types
        else:
            atks = self.opp_types
            defs = self.me_types
        best = 0.0
        for t in atks:
            best = max(best, type_mult(self.gendata, t, defs))
        # translate multiplier to small bin bonus
        if best >= 2.0:
            return 0.8
        if best <= 0.5:
            return -0.5
        return 0.0

    def _check_terminal(self):
        if self.s.me_hp_bin <= 0:
            self.s.terminal = True
            self.s.opp_kos += 1
        if self.s.opp_hp_bin <= 0:
            self.s.terminal = True
            self.s.my_kos += 1

    def _opponent_policy(self) -> str:
        # extremely small opponent policy; you can bias by revealed moves or your tracker
        # prefer attack; sometimes setup; sometimes tera early if not used
        r = random.random()
        if not self.s.opp_tera_used and r < 0.1:
            return "TERA_ATTACK"
        if r < 0.7:
            return "ATTACK"
        if r < 0.85:
            return "SETUP"
        if r < 0.95:
            return "SWITCH"
        return "HAZARD"

    def rollout_evaluate(self, max_plies: int = 6) -> Reward:
        while not self.terminal():
            # choose a heuristic action for *us* during rollout
            a = self._heuristic_action()
            self.step(a)
        # reward: KO diff + chip + small hazard credit
        chip = (10 - self.s.opp_hp_bin) - (10 - self.s.me_hp_bin)
        hazard = 0.2 * (self.s.opp_side_conds.get("stealthrock", 0) - self.s.side_conds.get("stealthrock", 0))
        return 2.0 * (self.s.my_kos - self.s.opp_kos) + 0.3 * chip + hazard

    def _heuristic_action(self) -> Action:
        # very small ladder: if we can likely KO soon -> attack; if we're low -> switch; else setup early
        if self.s.opp_hp_bin <= 2:
            return ("A_ATTACK", None)
        if self.s.me_hp_bin <= 2 and self.s.ply < 4:
            return ("A_SWITCH", None)
        if self.s.ply < 2 and self.s.me_boosts.get("spa", 0) < 2:
            return ("A_SETUP", None)
        return ("A_ATTACK", None)


# ============================================================
# 7) ISMCTS main loop
# ============================================================

def ismcts_search(root_info: Infoset,
                  env_adapter: PokeEnvAdapter,
                  gendata: GenData,
                  max_iters: int = 800,
                  uct_c: float = 1.1,
                  time_budget_s: Optional[float] = 1.2) -> SearchResult:

    root_legal = env_adapter.legal_actions()
    # progressive preordering by a simple root heuristic:
    root_legal = sorted(root_legal, key=lambda a: -_root_prior(env_adapter, a))
    root = Node(root_info.key, legal_actions=root_legal)
    nodes: Dict[InfosetKey, Node] = {root_info.key: root}

    start_t = time.time()
    it = 0
    while it < max_iters:
        if time_budget_s is not None and (time.time() - start_t) >= time_budget_s:
            break
        it += 1

        conc = sample_determinization(root_info, gendata)
        env = env_adapter.clone_with(conc)

        path: List[Tuple[Node, Action]] = []
        node = root

        # Selection
        while (not env.terminal()) and (not node.untried):
            legal = env.legal_actions()
            legal = [a for a in legal if (a in node.children) or (a in node.untried)]
            if not legal:
                break
            a = max(legal, key=lambda x: node.ucb(x, c=uct_c))
            path.append((node, a))
            env.step(a)
            info_next = env.infoset_key()
            child = node.children.get(a)
            if child is None:
                child = Node(info_next, env.legal_actions())
                node.children[a] = child
                nodes[info_next] = child
            node = child
            progressive_widening(node, env.legal_actions())

        # Expansion
        if (not env.terminal()) and node.untried:
            a = node.untried.pop(0)
            path.append((node, a))
            env.step(a)
            info_next = env.infoset_key()
            child = Node(info_next, env.legal_actions())
            node.children[a] = child
            node = child

        # Rollout & backup
        R = env.rollout_evaluate()
        for n, a in path:
            n.record(a, R)

    # Choose by visit count (robust child)
    if not root.children:
        best = env_adapter.heuristic_best_action()
        return SearchResult(best_action=best, action_stats={})
    best = max(root.children, key=lambda a: root.N_a[a])
    stats = {a: (root.N_a[a], (root.Q[a] / root.N_a[a]) if root.N_a[a] else 0.0) for a in root.children}
    return SearchResult(best_action=best, action_stats=stats)


def _root_prior(adapter: PokeEnvAdapter, a: Action) -> float:
    # Prior for root ordering / progressive widening synergy
    kind, payload = a
    if kind == "switch":
        # small bonus if switching into good type vs current opp
        return 0.5 * adapter._estimate_matchup(payload, adapter.battle.opponent_active_pokemon)
    else:
        return adapter._move_value(payload, adapter.battle.active_pokemon, adapter.battle.opponent_active_pokemon)



class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)
        self._iters = kwargs.pop("ismcts_iters", 600)
        self._time_s = kwargs.pop("ismcts_time_s", 1.2)  # wall-clock budget per move
        self._uct_c = kwargs.pop("ismcts_c", 1.1)
        self.gendata = GenData.from_gen(9)

    def choose_move(self, battle: AbstractBattle):
        # Fallback if no actions
        if not (battle.available_moves or battle.available_switches):
            return self.choose_random_move(battle)

        info = make_infoset(battle)
        adapter = PokeEnvAdapter(self, battle, self.gendata, heuristics_fn=None, iters_time_budget_s=self._time_s)
        result = ismcts_search(
            root_info=info,
            env_adapter=adapter,
            gendata=self.gendata,
            max_iters=self._iters,
            uct_c=self._uct_c,
            time_budget_s=self._time_s,
        )
        return adapter.to_battle_order(result.best_action)
