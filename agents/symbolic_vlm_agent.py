"""Symbolic-augmented Vision-Language Model agent.

The world-model component is **explicit and structured**: a `KnowledgeGraph`
of entities/relations + a `ConstraintSolver` that eliminates impossible
(suspect, weapon, location) combinations as evidence accumulates. The VLM
acts as both perception (extracts facts from each image+text observation
into the structured store) and policy (picks the next action conditioned on
the current image + structured summary of what's been deduced so far).

This isolates two failure modes a pure VLM tends to mix together:
  - perception: did the model see what was there?
  - belief tracking: did the model integrate observations into a consistent
    state across many turns?

By offloading belief tracking to a deterministic structure we can attribute
gains/losses cleanly between the two.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents._multimodal_client import MultimodalClient
from agents.base_agent import BaseAgent
from agents.symbolic_agent import ConstraintSolver, KnowledgeGraph
from mystery_world.entities import CharacterRole
from mystery_world.renderer.observation import render_observation_png
from mystery_world.world import AgentAction, MysteryEnvironment


PERCEPTION_SYSTEM = """\
You are a perception module for a detective AI. Each turn you receive an image
of the agent's current room and a short text observation. Extract structured
facts and return EXACTLY this JSON:

{
  "characters_visible": [{"name": "<full name from text>", "alive": <bool>}, ...],
  "objects_visible": [{"name": "<from text>", "category": "weapon|evidence|prop", "appears_examined": <bool>}, ...],
  "current_room": "<room name>",
  "exits": ["<adjacent room name>", ...],
  "new_facts": ["<short factual statement, free-form>", ...],
  "eliminations": {
    "suspects": [{"name": "<name>", "reason": "<why>"}],
    "weapons":  [{"name": "<name>", "reason": "<why>"}],
    "locations":[{"name": "<name>", "reason": "<why>"}]
  }
}

Use BOTH the image and the text. The image alone cannot identify entities by
name (no labels are drawn) — cross-reference what you see with the text. Only
report eliminations when the text or accumulated facts justify them.
"""

POLICY_SYSTEM = """\
You are the policy module of a detective AI. You receive:
  • An IMAGE of the current room (sprite legend: red diamond=weapon, teal
    circle=examined evidence, tan square=interactable prop, blue circle with
    eyes=living NPC, dark grey ellipse=body, brown rectangles on walls=doors)
  • The latest TEXT observation
  • A structured WORLD MODEL summary listing entities, relations, eliminated
    candidates, and known facts maintained across all prior turns

Your job is to choose ONE next action to maximise information gain. Output
EXACTLY this JSON:

{
  "reasoning": "<why this action given the world model>",
  "action": "<ACTION_NAME>",
  "action_args": {"<key>": "<value>", ...}
}

Actions: MOVE (target_location), EXAMINE_LOCATION, EXAMINE_OBJECT (object_name),
TALK_TO (character_name, question), TAKE_OBJECT (object_name),
CHECK_INVENTORY, WAIT, ACCUSE.

ACCUSE action_args schema (cite evidence IDs from the world model facts):
{
  "suspect_name": "<name>",
  "weapon_name": "<name>",
  "location_name": "<room>",
  "suspect_weapon_evidence": ["<evidence_id>", ...],
  "weapon_victim_evidence": ["<evidence_id>", ...],
  "suspect_room_evidence": ["<evidence_id>", ...],
  "alibi_contradiction": {
    "claimed_location": "<...>",
    "claimed_time": "<...>",
    "contradiction_evidence": ["<evidence_id>", ...]
  },
  "eliminations": {"<name>": {"evidence_id": "<id>", "corroborator": "<name>"}}
}

Only ACCUSE when the world model rules out all but one (suspect, weapon,
location) tuple AND you have at least one evidence id for each Locard edge.
"""


def _parse_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


class SymbolicVLMAgent(BaseAgent):
    """Two-stage VLM agent: structured perception → structured planning."""

    def __init__(
        self,
        agent_id: str = "symbolic_vlm_agent",
        provider: str = "anthropic",
        model: str = "claude-sonnet-4-20250514",
    ):
        super().__init__(agent_id)
        self.client = MultimodalClient(provider=provider, model=model)
        self.kg = KnowledgeGraph()
        self.solver = ConstraintSolver()
        self.briefing: str = ""
        self._env: MysteryEnvironment | None = None
        self._all_suspects: list[str] = []
        self._all_weapons: list[str] = []
        self._all_locations: list[str] = []

    # ------------------------------------------------------------------
    def initialize(self, env: MysteryEnvironment, briefing: str) -> None:
        self.briefing = briefing
        self._env = env
        state = env.state

        # Seed KG with all entities the agent could in principle reason about
        for c in state.characters.values():
            roles = [r.name for r in c.roles]
            self.kg.add_entity(
                c.full_name, "suspect" if "SUSPECT" in roles else "character",
                name=c.full_name, roles=roles,
            )
            if CharacterRole.SUSPECT in c.roles:
                self._all_suspects.append(c.full_name)
                self.belief_state.suspect_probs[c.full_name] = 1.0 / max(
                    1, sum(1 for s in state.characters.values() if CharacterRole.SUSPECT in s.roles)
                )
        for o in state.objects.values():
            if o.is_weapon:
                self.kg.add_entity(o.name, "weapon", name=o.name)
                self._all_weapons.append(o.name)
                self.belief_state.weapon_probs[o.name] = 1.0 / max(1, len([
                    x for x in state.objects.values() if x.is_weapon
                ]))
        for loc in state.locations.values():
            self.kg.add_entity(loc.name, "location", name=loc.name)
            self._all_locations.append(loc.name)
            self.belief_state.location_probs[loc.name] = 1.0 / max(1, len(state.locations))

    # ------------------------------------------------------------------
    # Perception step: VLM → structured facts → KG / solver
    # ------------------------------------------------------------------

    def _run_perception(self, image: bytes, text_obs: str) -> None:
        if not image:
            return
        user_msg = (
            f"=== TEXT OBSERVATION ===\n{text_obs}\n\n"
            "Return the perception JSON described in the system prompt."
        )
        response, tokens = self.client.complete(
            PERCEPTION_SYSTEM, user_msg, images=[image], max_tokens=1024,
        )
        self.total_tokens_used += tokens
        parsed = _parse_json(response)

        for fact in parsed.get("new_facts") or []:
            if isinstance(fact, str) and fact.strip():
                self.kg.add_fact(fact.strip(), source="vlm_perception")
                self.belief_state.known_facts.append(fact.strip())

        # Track relations: which characters/objects are co-located each turn
        room = parsed.get("current_room")
        if isinstance(room, str) and room in self._all_locations:
            for ch in parsed.get("characters_visible") or []:
                name = ch.get("name") if isinstance(ch, dict) else None
                if name:
                    self.kg.add_relation(name, room, "seen_at", source="vlm_perception")
            for ob in parsed.get("objects_visible") or []:
                name = ob.get("name") if isinstance(ob, dict) else None
                if name:
                    self.kg.add_relation(name, room, "located_in", source="vlm_perception")

        elims = parsed.get("eliminations") or {}
        for s in elims.get("suspects") or []:
            n, reason = (s.get("name"), s.get("reason", "")) if isinstance(s, dict) else (s, "")
            if n in self._all_suspects:
                self.solver.eliminate_suspect(n, reason)
                self.belief_state.eliminated_suspects.add(n)
        for w in elims.get("weapons") or []:
            n, reason = (w.get("name"), w.get("reason", "")) if isinstance(w, dict) else (w, "")
            if n in self._all_weapons:
                self.solver.eliminate_weapon(n, reason)
                self.belief_state.eliminated_weapons.add(n)
        for l in elims.get("locations") or []:
            n, reason = (l.get("name"), l.get("reason", "")) if isinstance(l, dict) else (l, "")
            if n in self._all_locations:
                self.solver.eliminate_location(n, reason)
                self.belief_state.eliminated_locations.add(n)

    def _world_model_summary(self) -> str:
        candidates = self.solver.get_remaining_candidates(
            self._all_suspects, self._all_weapons, self._all_locations,
        )
        n_combos = self.solver.count_remaining_combos(
            self._all_suspects, self._all_weapons, self._all_locations,
        )
        lines = [
            f"Remaining candidates ({n_combos} (suspect, weapon, location) combos):",
            f"  suspects:  {candidates['suspects']}",
            f"  weapons:   {candidates['weapons']}",
            f"  locations: {candidates['locations']}",
            "",
            self.solver.summarize(),
            "",
            "Recent facts:",
        ]
        for f in self.belief_state.known_facts[-12:]:
            lines.append(f"  • {f}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Policy step: VLM → action
    # ------------------------------------------------------------------

    def decide_action(self, observation: str) -> tuple[AgentAction, dict[str, str]]:
        env = self._env
        budget = env.budget_remaining if env else 0
        image_bytes = render_observation_png(env) if env else b""

        # 1) perception → update structured world model
        self._run_perception(image_bytes, observation)

        # 2) policy → choose action conditioned on world model summary
        wm_summary = self._world_model_summary()
        user_msg = (
            f"{self.briefing}\n\n"
            f"=== CURRENT TEXT OBSERVATION (budget: {budget}) ===\n"
            f"{observation}\n\n"
            f"=== WORLD MODEL ===\n{wm_summary}\n\n"
            "Decide the next action. Respond with the JSON object."
        )
        images = [image_bytes] if image_bytes else []
        response, tokens = self.client.complete(POLICY_SYSTEM, user_msg, images=images, max_tokens=2048)
        self.total_tokens_used += tokens
        parsed = _parse_json(response)

        action_str = (parsed.get("action") or "EXAMINE_LOCATION").upper()
        try:
            action = AgentAction[action_str]
        except KeyError:
            action = AgentAction.EXAMINE_LOCATION
        return action, parsed.get("action_args", {}) or {}

    def update_beliefs(self, observation: str) -> None:
        # Updates happen in _run_perception during decide_action.
        pass
