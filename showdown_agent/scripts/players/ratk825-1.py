from typing import List, Dict, Set, Optional, Union, Any
from poke_env.battle import AbstractBattle, Move, SideCondition, Pokemon, MoveCategory
from poke_env.player import Player
from poke_env.data import GenData
from poke_env.data.normalize import to_id_str

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


class Gen9Knowledge:
    """Helper class for accessing GenData utilities with error handling."""

    def __init__(self):
        self.gen_data = GenData.from_gen(9)

    def normalize_species_id(self, species_name: str) -> str:
        return to_id_str(species_name or "")

    def type_multiplier(self, move_type: Union[Any, str], defender_types: List[Any]) -> float:
        """Calculate type effectiveness using GenData chart with proper casing."""
        m = move_type.name if hasattr(move_type, "name") else str(move_type)
        m = m.upper()
        mult = 1.0
        for dt in defender_types:
            if not dt:
                continue
            dn = dt.name if hasattr(dt, "name") else str(dt)
            dn = dn.upper()
            mult *= self.gen_data.type_chart.get(m, {}).get(dn, 1.0)
        return mult

    def stab_multiplier(self, attacker_types, move_type, tera_type=None):
        m = (move_type.name if hasattr(move_type, "name") else str(move_type)).lower()
        atypes = {(t.name if hasattr(t, "name") else str(t)).lower() for t in attacker_types if t}
        ttype = (tera_type.name if hasattr(tera_type, "name") else str(tera_type)).lower() if tera_type else None
        if m in atypes and ttype and m == ttype:
            return 2.0
        if m in atypes or (ttype and m == ttype):
            return 1.5
        return 1.0

    def get_base_stats(self, species_id: str) -> Optional[Dict[str, int]]:
        """Get base stats for a Pokemon species."""
        normalized_id = self.normalize_species_id(species_id)
        try:
            if normalized_id in self.gen_data.pokedex:
                return self.gen_data.pokedex[normalized_id]['baseStats']
        except (KeyError, TypeError):
            pass
        return None

    def estimate_stat(self, species_id: str, stat: str, level: int = 50, iv: int = 31, ev: int = 84,
                      nature: str = "neutral") -> Optional[int]:
        """Estimate actual stat value for a Pokemon."""
        base_stats = self.get_base_stats(species_id)
        if not base_stats or stat not in base_stats:
            return None

        base_stat = base_stats[stat]
        nature_multiplier = 1.0

        if nature != "neutral":
            if nature in self.gen_data.natures:
                nature_multiplier = self.gen_data.natures[nature].get(stat, 1.0)

        # Pokemon stat formula
        if stat == 'hp':
            estimated = int(((2 * base_stat + iv + ev // 4) * level / 100) + level + 10)
        else:
            estimated = int(((2 * base_stat + iv + ev // 4) * level / 100) + 5)
            estimated = int(estimated * nature_multiplier)

        return estimated

    def get_learnable_moves(self, species_id: str) -> Set[str]:
        """Get set of moves a Pokemon can learn."""
        normalized_id = self.normalize_species_id(species_id)
        try:
            if normalized_id in self.gen_data.learnset:
                return set(self.gen_data.learnset[normalized_id].keys())
        except (KeyError, TypeError, AttributeError):
            pass
        return set()

    def get_move_data(self, move_id: str) -> Optional[Dict]:
        """Get move data from GenData."""
        try:
            if move_id in self.gen_data.moves:
                return self.gen_data.moves[move_id]
        except (KeyError, TypeError):
            pass
        return None

    def guess_common_roles(self, species_id: str) -> Dict[str, Set[str]]:
        """Categorize potential moves by role."""
        learnable_moves = self.get_learnable_moves(species_id)
        roles = {
            'stab': set(),
            'coverage': set(),
            'setup': set(),
            'utility': set(),
            'priority': set()
        }

        base_stats = self.get_base_stats(species_id)
        if not base_stats:
            return roles

        # Get Pokemon types if available in pokedex
        pokemon_types = set()
        try:
            normalized_id = self.normalize_species_id(species_id)
            if normalized_id in self.gen_data.pokedex:
                types_data = self.gen_data.pokedex[normalized_id].get('types', [])
                pokemon_types = {t.upper() for t in types_data}
        except (KeyError, TypeError, AttributeError):
            pass

        for move_name in learnable_moves:
            move_data = self.get_move_data(move_name)
            if not move_data:
                continue

            move_type = move_data.get('type', '').upper()
            category = move_data.get('category', '')
            base_power = move_data.get('basePower', 0)
            priority = move_data.get('priority', 0)

            # Priority moves
            if priority > 0:
                roles['priority'].add(move_name)

            # Setup moves
            if (move_data.get('boosts') and move_data.get('target') == 'self' and
                    any(v > 0 for v in move_data.get('boosts', {}).values())):
                roles['setup'].add(move_name)

            # STAB moves
            if move_type in pokemon_types and base_power > 0:
                roles['stab'].add(move_name)

            # Coverage moves
            elif category in ['Physical', 'Special'] and base_power >= 70:
                roles['coverage'].add(move_name)

            # Utility moves
            elif (category == 'Status' or
                  move_name in ['recover', 'roost', 'softboiled', 'moonlight', 'synthesis']):
                roles['utility'].add(move_name)

        return roles


def expected_damage_score(attacker: Pokemon, defender: Pokemon, move: Move, knowledge: Gen9Knowledge) -> float:
    """Rank a move by a simple damage proxy. Lean on poke_env's own effectiveness."""
    # Ignore pure status for damage scoring
    if move.base_power is None or move.base_power == 0:
        return 0.0

    # Accuracy normalisation: poke_env often uses 1.0, True, or None
    acc = move.accuracy
    if acc is True or acc is None:
        acc = 1.0
    elif isinstance(acc, (int, float)) and acc > 1.0:
        acc = acc / 100.0
    acc = max(0.0, min(1.0, float(acc)))

    # Expected hits guard
    hits = getattr(move, "expected_hits", 1) or 1

    # Offensive / defensive stats with runtime info first
    if move.category == MoveCategory.PHYSICAL:
        a = attacker.stats.get("atk") or 0
        d = defender.stats.get("def") or 0
        a_stat, d_stat = ("atk", "def")
    else:
        a = attacker.stats.get("spa") or 0
        d = defender.stats.get("spd") or 0
        a_stat, d_stat = ("spa", "spd")

    # Fallback to GenData estimates if runtime not known yet
    if a <= 0:
        a = knowledge.estimate_stat(attacker.species, a_stat) or 100
    if d <= 0:
        d = knowledge.estimate_stat(defender.species, d_stat) or 100

    # Apply boosts
    a *= max(0.25, 1 + (attacker.boosts.get(a_stat, 0) * 0.5))
    d *= max(0.25, 1 + (defender.boosts.get(d_stat, 0) * 0.5))

    stat_ratio = max(0.1, a / d)

    # Use engine's effectiveness calc (captures immunities, tera interaction on defender typing)
    type_mult = defender.damage_multiplier(move)

    # STAB: engine doesn’t expose a direct STAB flag; keep your simple check
    stab = knowledge.stab_multiplier(attacker.types, move.type)

    return float(move.base_power) * stat_ratio * type_mult * stab * acc * hits


def predict_opponent_moves(opponent: Pokemon, knowledge: Gen9Knowledge) -> Dict:
    """Predict a *small* set of likely moves. Prioritise revealed -> STAB -> strong coverage."""
    revealed = set(opponent.moves.keys()) if opponent.moves else set()
    species_id = knowledge.normalize_species_id(opponent.species)

    learnables = knowledge.get_learnable_moves(species_id)
    opp_types = {(t.name if t else None) for t in opponent.types}

    # Score candidates lightly: prefer STAB, decent BP, not-garbage accuracy, and priority
    ranked = []
    for mid in learnables:
        if mid in revealed:
            continue
        md = knowledge.get_move_data(mid)
        if not md:
            continue
        cat = md.get("category", "")
        bp = int(md.get("basePower", 0) or 0)
        mtype = (md.get("type") or "").upper()
        prio = int(md.get("priority", 0) or 0)
        acc = md.get("accuracy", 100)
        acc = (acc if isinstance(acc, (int, float)) else 100.0)
        acc = acc / 100.0 if acc > 1 else (1.0 if acc in (True, None) else float(acc))

        score = 0.0
        if cat in ("Physical", "Special") and bp > 0:
            score += min(bp, 120) / 120.0
            if mtype and mtype in {t.upper() for t in opp_types if t}:
                score += 0.35  # STAB preference
            score += max(0.0, acc - 0.8) * 0.2
        else:
            # Status—lightly score only useful ones
            if mid in {"stealthrock", "spikes", "toxicspikes", "stickyweb"}:
                score += 0.45
            if mid in {"recover", "roost", "softboiled", "slackoff", "moonlight", "morningsun",
                       "synthesis", "strengthsap", "rest"}:
                score += 0.50
            if mid in {"toxic", "willowisp", "thunderwave", "glare", "spore", "stunspore", "nuzzle"}:
                score += 0.25
        if prio > 0:
            score += 0.15

        ranked.append((mid, score))

    ranked.sort(key=lambda x: x[1], reverse=True)

    # Keep it small: revealed first, then top-K likely (prevents over-penalising matchups)
    TOP_K = 6
    shortlist = [m for (m, _) in ranked[:TOP_K]]

    # Categorise a little for downstream logic
    predicted = {"stab": set(), "coverage": set(), "setup": set(),
                 "utility": set(), "priority": set()}
    for mid in shortlist:
        md = knowledge.get_move_data(mid)
        if not md:
            continue
        cat = md.get("category", "")
        mtype = (md.get("type") or "").upper()
        bp = int(md.get("basePower", 0) or 0)
        prio = int(md.get("priority", 0) or 0)
        if prio > 0:
            predicted["priority"].add(mid)
        if md.get("boosts") and any(v > 0 for v in md["boosts"].values()):
            predicted["setup"].add(mid)
        if cat in ("Physical", "Special") and bp > 0:
            if mtype in {t.upper() for t in opp_types if t}:
                predicted["stab"].add(mid)
            else:
                predicted["coverage"].add(mid)
        else:
            predicted["utility"].add(mid)

    return {
        "revealed": revealed,
        "slots_remaining": max(0, 4 - len(revealed)),
        "predicted": predicted,
        "ranked_candidates": shortlist,
    }


class CustomAgent(Player):
    def __init__(self, *args, **kwargs):
        super().__init__(team=team, *args, **kwargs)
        self.current_battle = None
        self.knowledge = Gen9Knowledge()

        self.ENTRY_HAZARDS = {
            "spikes": SideCondition.SPIKES,
            "stealthrock": SideCondition.STEALTH_ROCK,
            "stickyweb": SideCondition.STICKY_WEB,
            "toxicspikes": SideCondition.TOXIC_SPIKES,
        }

        self.ANTI_HAZARDS_MOVES = {"rapidspin", "defog"}

        self.SPEED_TIER_COEFFICIENT = 0.1
        self.HP_FRACTION_COEFFICIENT = 0.4
        self.SWITCH_OUT_MATCHUP_THRESHOLD = -2

    def _estimate_matchup(self, mon: Pokemon, opponent: Pokemon) -> float:
        """Best our-move score minus best predicted opponent score, with speed/HP nudges."""
        # Our best available hit
        our_moves = getattr(mon, "moves", None)
        if our_moves and mon == self.current_battle.active_pokemon:
            cand = [m for m in self.current_battle.available_moves if m.base_power]
        else:
            cand = []  # if not active, be conservative
        our_best = max(
            (expected_damage_score(mon, opponent, m, self.knowledge) for m in cand),
            default=0.0,
        )

        # Opponent shortlist threat (don’t use entire learnset)
        pred = predict_opponent_moves(opponent, self.knowledge)
        opp_best = 0.0
        for mid in pred["ranked_candidates"]:
            md = self.knowledge.get_move_data(mid)
            if not md:
                continue
            bp = int(md.get("basePower", 0) or 0)
            if bp <= 0:
                continue
            # approximate with type+STAB since we don't have Move objects for them
            mtype = md.get("type") or ""
            tmult = self.knowledge.type_multiplier(mtype, list(mon.types))
            stab = self.knowledge.stab_multiplier(list(opponent.types), mtype)
            # modest scaling to keep on same rough scale as our_best
            opp_best = max(opp_best, bp * tmult * stab)
        opp_best *= 0.7  # scale down a bit to keep us aggressive

        score = (our_best / 100.0) - (opp_best / 100.0)

        # Speed + HP nudges (use real stats first)
        my_spe = mon.stats.get("spe") or self.knowledge.estimate_stat(mon.species, "spe") or 100
        op_spe = opponent.stats.get("spe") or self.knowledge.estimate_stat(opponent.species, "spe") or 100
        if my_spe > op_spe:
            score += self.SPEED_TIER_COEFFICIENT
        elif op_spe > my_spe:
            score -= self.SPEED_TIER_COEFFICIENT

        score += (mon.current_hp_fraction - opponent.current_hp_fraction) * self.HP_FRACTION_COEFFICIENT
        return score

    def _should_tera(self, battle: AbstractBattle, n_remaining_mons: int) -> bool:
        """Determine if we should terastallize."""
        if not battle.can_tera:
            return False

        # Last full HP mon
        if (len([m for m in battle.team.values() if m.current_hp_fraction == 1]) == 1 and
                battle.active_pokemon.current_hp_fraction == 1):
            return True

        # Matchup advantage and full hp on full hp
        if (self._estimate_matchup(battle.active_pokemon, battle.opponent_active_pokemon) > 0 and
                battle.active_pokemon.current_hp_fraction == 1 and
                battle.opponent_active_pokemon.current_hp_fraction == 1):
            return True

        # Last Pokemon
        if n_remaining_mons == 1:
            return True

        return False

    def _should_switch_out(self, battle: AbstractBattle) -> bool:
        """Determine if we should switch out."""
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon

        # Check for decent switch-ins
        good_switches = [m for m in battle.available_switches
                         if self._estimate_matchup(m, opponent) > 0]

        if not good_switches:
            return False

        # Reasons to switch out
        if active.boosts.get("def", 0) <= -3 or active.boosts.get("spd", 0) <= -3:
            return True

        if (active.boosts.get("atk", 0) <= -3 and
                active.stats.get("atk", 0) >= active.stats.get("spa", 0)):
            return True

        if (active.boosts.get("spa", 0) <= -3 and
                active.stats.get("atk", 0) <= active.stats.get("spa", 0)):
            return True

        if self._estimate_matchup(active, opponent) < self.SWITCH_OUT_MATCHUP_THRESHOLD:
            return True

        return False

    def _analyze_opponent_threat(self, opponent: Pokemon, my_pokemon: Pokemon) -> float:
        """Analyze opponent threat level."""
        move_analysis = predict_opponent_moves(opponent, self.knowledge)
        threat_level = 0.0

        # Base threat from type advantage
        base = 0.0
        for t in [tt for tt in opponent.types if tt]:
            base = max(base, my_pokemon.damage_multiplier(t))
        threat_level += base

        # Setup move threat
        if move_analysis['predicted']['setup'] and opponent.current_hp_fraction > 0.7:
            threat_level += 1.5

        # Priority move threat when low HP
        if move_analysis['predicted']['priority'] and my_pokemon.current_hp_fraction < 0.5:
            threat_level += 1.0

        # Speed advantage
        our_speed = self.knowledge.estimate_stat(my_pokemon.species, "spe") or my_pokemon.base_stats.get("spe", 100)
        opp_speed = self.knowledge.estimate_stat(opponent.species, "spe") or opponent.base_stats.get("spe", 100)

        if opp_speed > our_speed:
            threat_level += 0.5

        return threat_level

    def _should_predict_switch(self, battle: AbstractBattle) -> Optional[Move]:
        opp = battle.opponent_active_pokemon
        me = battle.active_pokemon
        if not battle.available_moves:
            return None

        disadv = self._estimate_matchup(opp, me)  # how bad is it for them
        if disadv < -2.0:
            # Pick the move that best hits *healthy* non-active opponents
            best_m = None
            best_s = -1.0
            for m in battle.available_moves:
                if not m.base_power:
                    continue
                s = 0.0
                for p in battle.opponent_team.values():
                    if p.fainted or p is opp:
                        continue
                    s += p.damage_multiplier(m)
                if s > best_s:
                    best_s, best_m = s, m
            return best_m
        return None

    def choose_move(self, battle: AbstractBattle):
        """Main move selection logic."""
        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        self.current_battle = battle

        if active is None or opponent is None:
            return self.choose_random_move(battle)

        # Analyze opponent and predict strategy
        opponent_threat = self._analyze_opponent_threat(opponent, active)
        predicted_switch_move = self._should_predict_switch(battle)

        if battle.available_moves and (not self._should_switch_out(battle) or not battle.available_switches):
            n_remaining_mons = len([m for m in battle.team.values() if not m.fainted])
            n_opp_remaining_mons = 6 - len([m for m in battle.opponent_team.values() if m.fainted])

            # Predict opponent switch and use coverage
            if predicted_switch_move and opponent_threat < 2.0:
                return self.create_order(predicted_switch_move)

            # Entry hazards
            for move in battle.available_moves:
                if (n_opp_remaining_mons >= 3 and
                        move.id in self.ENTRY_HAZARDS and
                        self.ENTRY_HAZARDS[move.id] not in battle.opponent_side_conditions):
                    return self.create_order(move)

                # Hazard removal
                elif (battle.side_conditions and
                      move.id in self.ANTI_HAZARDS_MOVES and
                      n_remaining_mons >= 2):
                    return self.create_order(move)

            # Setup moves with threat consideration
            setup_safety_threshold = 1.0 if opponent_threat > 2.5 else 3.0
            if (active.current_hp_fraction >= 0.8 and
                    self._estimate_matchup(active, opponent) > 0 and
                    opponent_threat < setup_safety_threshold):

                for move in battle.available_moves:
                    if (move.boosts and
                            sum(move.boosts.values()) >= 2 and
                            move.target == "self" and
                            min([active.boosts.get(s, 0) for s, v in move.boosts.items() if v > 0]) < 6):
                        return self.create_order(move)

            # Select best attacking move - hybrid approach
            def move_score(m):
                if not m.base_power:
                    return 0.0
                # normalize accuracy + hits safely
                acc = m.accuracy
                acc = 1.0 if acc in (True, None) else (acc / 100.0 if acc and acc > 1 else float(acc))
                hits = getattr(m, "expected_hits", 1) or 1
                simple = (m.base_power *
                          (1.5 if self.knowledge.stab_multiplier(active.types, m.type) > 1 else 1.0) *
                          acc * hits * opponent.damage_multiplier(m))
                return max(expected_damage_score(active, opponent, m, self.knowledge), simple)

            move = max(battle.available_moves, key=move_score)

            move = max(
                battle.available_moves,
                key=lambda m: expected_damage_score(active, opponent, m, self.knowledge),
            )

            return self.create_order(move, terastallize=self._should_tera(battle, n_remaining_mons))

        # Switch if needed
        if battle.available_switches:
            switches: List[Pokemon] = battle.available_switches
            return self.create_order(
                max(switches, key=lambda s: self._estimate_matchup(s, opponent))
            )

        return self.choose_random_move(battle)

    def teampreview(self, battle):
        team_list = list(battle.team.values())

        lead_priority = ['deoxysspeed']

        for preferred_lead in lead_priority:
            for i, pokemon in enumerate(team_list):
                if pokemon.species == preferred_lead:
                    return f"/team {i + 1}"

        # Fallback to first Pokemon
        return "/team 1"