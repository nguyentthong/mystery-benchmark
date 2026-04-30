"""VLM-as-world-model planner.

At each step the agent enumerates the actions reachable from the current room
(MOVE to each exit, EXAMINE_OBJECT for each visible object, TALK_TO for each
visible NPC, plus ACCUSE if it judges itself ready). The VLM is asked, in a
single call, to **imagine** the resulting observation for each candidate, score
how informative that imagined outcome would be, and pick the highest-scoring
action.

This tests whether the VLM's own forward-dynamics intuition — used as an
explicit lookahead — beats the pure-VLM baseline (which only reasons over the
current observation). No external dynamics model is trained; the world model
*is* the VLM, queried in prediction mode.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents._multimodal_client import MultimodalClient
from agents.base_agent import BaseAgent
from agents.vlm_agent import VLMAgent
from mystery_world.entities import CharacterRole
from mystery_world.renderer.observation import render_observation_png
from mystery_world.world import AgentAction, MysteryEnvironment


SYSTEM_PROMPT = """\
You are a detective AI planning through imagined futures. You receive:
  • An IMAGE of your current room
  • A TEXT observation
  • A list of CANDIDATE ACTIONS reachable from this room

For each candidate action, IMAGINE the resulting observation (1-2 sentences:
what would you see, what new fact would you likely learn?), then score its
information value on a 0-10 scale where:
  0  = redundant with what you already know
  5  = plausibly useful, low certainty
  10 = directly disambiguates the (suspect, weapon, location) tuple

Output EXACTLY this JSON:

{
  "imagined_outcomes": [
    {
      "action": "<ACTION_NAME>",
      "action_args": {"<key>": "<value>", ...},
      "predicted_observation": "<1-2 sentence forecast>",
      "info_score": <0-10>
    },
    ...
  ],
  "chosen_index": <integer index into imagined_outcomes>,
  "reasoning": "<why the chosen action wins on information value>"
}

If you are confident in the (suspect, weapon, location) tuple, INCLUDE an
ACCUSE candidate with the full action_args (suspect_name, weapon_name,
location_name, suspect_weapon_evidence ids, weapon_victim_evidence ids,
suspect_room_evidence ids, alibi_contradiction, eliminations).
"""


def _parse_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


class WorldModelVLMAgent(BaseAgent):
    """VLM plans by imagining the next observation for each candidate action."""

    def __init__(
        self,
        agent_id: str = "wm_vlm_agent",
        provider: str = "anthropic",
        model: str = "claude-sonnet-4-20250514",
        history_window: int = 6,
    ):
        super().__init__(agent_id)
        self.client = MultimodalClient(provider=provider, model=model)
        self.history_window = history_window
        self.briefing: str = ""
        self._env: MysteryEnvironment | None = None

    def initialize(self, env: MysteryEnvironment, briefing: str) -> None:
        self.briefing = briefing
        self._env = env
        state = env.state
        suspects = [c for c in state.characters.values() if CharacterRole.SUSPECT in c.roles]
        for c in suspects:
            self.belief_state.suspect_probs[c.full_name] = 1.0 / max(1, len(suspects))
        weapons = [o for o in state.objects.values() if o.is_weapon]
        for o in weapons:
            self.belief_state.weapon_probs[o.name] = 1.0 / max(1, len(weapons))
        for loc in state.locations.values():
            self.belief_state.location_probs[loc.name] = 1.0 / max(1, len(state.locations))

    # ------------------------------------------------------------------

    def _enumerate_candidates(self) -> list[dict[str, Any]]:
        """Affordances available from the current room — same info the agent
        would see in the text observation, just structured for the prompt."""
        env = self._env
        if env is None:
            return [{"action": "EXAMINE_LOCATION", "action_args": {}}]
        loc = env.get_current_location()
        if loc is None:
            return [{"action": "EXAMINE_LOCATION", "action_args": {}}]

        candidates: list[dict[str, Any]] = [
            {"action": "EXAMINE_LOCATION", "action_args": {}},
            {"action": "CHECK_INVENTORY", "action_args": {}},
        ]
        for aid in loc.adjacent_ids:
            adj = env.state.locations.get(aid)
            if adj:
                candidates.append({
                    "action": "MOVE",
                    "action_args": {"target_location": adj.name},
                })
        for oid in loc.objects_here:
            obj = env.state.objects.get(oid)
            if obj:
                candidates.append({
                    "action": "EXAMINE_OBJECT",
                    "action_args": {"object_name": obj.name},
                })
        for cid in loc.characters_here:
            ch = env.state.characters.get(cid)
            if ch and ch.is_alive:
                candidates.append({
                    "action": "TALK_TO",
                    "action_args": {
                        "character_name": ch.full_name,
                        "question": "Where were you at the time of the murder?",
                    },
                })
        return candidates

    def _build_user_text(self, observation: str, candidates: list[dict[str, Any]]) -> str:
        env = self._env
        budget = env.budget_remaining if env else 0
        parts = [self.briefing, ""]
        if self.observation_history:
            parts.append("=== RECENT OBSERVATIONS ===")
            for obs in self.observation_history[-self.history_window:]:
                parts.append(obs)
                parts.append("---")
        parts.append(f"=== CURRENT OBSERVATION (budget: {budget}) ===")
        parts.append(observation)
        parts.append("")
        parts.append("=== CANDIDATE ACTIONS ===")
        for i, c in enumerate(candidates):
            parts.append(f"  [{i}] {c['action']} {json.dumps(c['action_args'])}")
        parts.append("")
        parts.append(
            "For each candidate, imagine the resulting observation and score "
            "its information value. Pick the highest-scoring action. Respond "
            "with the JSON object."
        )
        return "\n".join(parts)

    def decide_action(self, observation: str) -> tuple[AgentAction, dict[str, str]]:
        env = self._env
        candidates = self._enumerate_candidates()
        image_bytes = render_observation_png(env) if env else b""
        images = [image_bytes] if image_bytes else []

        user_text = self._build_user_text(observation, candidates)
        response, tokens = self.client.complete(SYSTEM_PROMPT, user_text, images=images, max_tokens=2048)
        self.total_tokens_used += tokens
        parsed = _parse_json(response)

        # Pull the chosen action; fall back to highest score, then to a safe default
        outcomes = parsed.get("imagined_outcomes") or []
        chosen_idx = parsed.get("chosen_index")
        chosen: dict[str, Any] | None = None
        if isinstance(chosen_idx, int) and 0 <= chosen_idx < len(outcomes):
            chosen = outcomes[chosen_idx]
        elif outcomes:
            chosen = max(
                outcomes,
                key=lambda o: o.get("info_score", 0) if isinstance(o, dict) else 0,
            )

        if not chosen:
            return AgentAction.EXAMINE_LOCATION, {}

        # Record the imagined-future trace for evaluation
        self.belief_state.reasoning_trace.append(
            f"chose {chosen.get('action')} (predicted: {chosen.get('predicted_observation','?')})"
        )

        action_str = (chosen.get("action") or "EXAMINE_LOCATION").upper()
        try:
            action = AgentAction[action_str]
        except KeyError:
            action = AgentAction.EXAMINE_LOCATION
        return action, chosen.get("action_args", {}) or {}

    def update_beliefs(self, observation: str) -> None:
        # The world-model framing keeps belief tracking implicit in the
        # imagined-outcomes dialogue; nothing to update structurally here.
        pass
