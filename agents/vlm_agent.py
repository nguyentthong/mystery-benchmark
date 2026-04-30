"""Vision-Language Model agent (pure multimodal baseline).

Same JSON action protocol as `LLMAgent`, but each step the model receives a
rendered image of the agent's current room **plus** the existing text
observation. Vision is the new channel; everything else stays identical so
text-only vs. multimodal can be compared apples-to-apples on the same seeds.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents._multimodal_client import MultimodalClient
from agents.base_agent import BaseAgent
from mystery_world.entities import CharacterRole
from mystery_world.renderer.observation import render_observation_png
from mystery_world.world import AgentAction, MysteryEnvironment

SYSTEM_PROMPT = """\
You are a detective AI solving a procedurally generated murder mystery.
You must determine WHO committed the murder, WHAT weapon was used, and WHERE it occurred.

Each turn you receive:
  • An IMAGE: a top-down view of your current room. Sprites are category-coded:
    - red diamond  = weapon
    - teal circle  = evidence you've already examined
    - tan square   = a regular interactable object (may hide evidence)
    - blue circle with two eyes = a living NPC
    - dark grey ellipse on the floor = a body / victim
    - tan/brown rectangles around the perimeter are doors leading to other rooms
  • TEXT: the natural-language observation (room name, who is here, exits, last action result).

Output EXACTLY this JSON:
{
  "reasoning": "<step-by-step reasoning, including what the image shows>",
  "beliefs": {
    "top_suspect": "<name or null>",
    "suspect_confidence": <0.0-1.0>,
    "top_weapon": "<name or null>",
    "weapon_confidence": <0.0-1.0>,
    "top_location": "<name or null>",
    "location_confidence": <0.0-1.0>,
    "eliminated_suspects": ["<name>", ...],
    "new_facts": ["<fact>", ...]
  },
  "action": "<ACTION_NAME>",
  "action_args": {"<key>": "<value>", ...}
}

Actions: MOVE (target_location), EXAMINE_LOCATION, EXAMINE_OBJECT (object_name),
TALK_TO (character_name, question), TAKE_OBJECT (object_name),
CHECK_INVENTORY, WAIT, ACCUSE.

ACCUSE action_args schema (all keys required for full credit):
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
"""


def _parse_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"action": "EXAMINE_LOCATION", "action_args": {}, "beliefs": {}, "reasoning": text}


class VLMAgent(BaseAgent):
    """Pure-VLM baseline: each step gets (image + text obs + history) → action."""

    def __init__(
        self,
        agent_id: str = "vlm_agent",
        provider: str = "anthropic",
        model: str = "claude-sonnet-4-20250514",
        history_window: int = 8,
    ):
        super().__init__(agent_id)
        self.client = MultimodalClient(provider=provider, model=model)
        self.history_window = history_window
        self.briefing: str = ""
        self._env: MysteryEnvironment | None = None

    def initialize(self, env: MysteryEnvironment, briefing: str) -> None:
        self.briefing = briefing
        self._env = env
        # Seed uniform priors for belief tracking
        state = env.state
        suspects = [c for c in state.characters.values() if CharacterRole.SUSPECT in c.roles]
        for c in suspects:
            self.belief_state.suspect_probs[c.full_name] = 1.0 / max(1, len(suspects))
        weapons = [o for o in state.objects.values() if o.is_weapon]
        for o in weapons:
            self.belief_state.weapon_probs[o.name] = 1.0 / max(1, len(weapons))
        for loc in state.locations.values():
            self.belief_state.location_probs[loc.name] = 1.0 / max(1, len(state.locations))

    def _build_user_text(self, current_obs: str, budget: int) -> str:
        parts = [self.briefing, ""]
        if self.observation_history:
            parts.append("=== PREVIOUS OBSERVATIONS ===")
            for obs in self.observation_history[-self.history_window:]:
                parts.append(obs)
                parts.append("---")
        parts.append(f"=== CURRENT OBSERVATION (budget remaining: {budget}) ===")
        parts.append(current_obs)
        parts.append("")
        parts.append("Image attached above shows your current room. Respond with the JSON object.")
        return "\n".join(parts)

    def decide_action(self, observation: str) -> tuple[AgentAction, dict[str, str]]:
        env = self._env
        budget = env.budget_remaining if env else 0
        user_text = self._build_user_text(observation, budget)
        image_bytes = render_observation_png(env) if env else b""
        images = [image_bytes] if image_bytes else []

        response, tokens = self.client.complete(SYSTEM_PROMPT, user_text, images=images)
        self.total_tokens_used += tokens
        parsed = _parse_json(response)

        self._apply_belief_update(parsed.get("beliefs", {}))

        action_str = (parsed.get("action") or "EXAMINE_LOCATION").upper()
        try:
            action = AgentAction[action_str]
        except KeyError:
            action = AgentAction.EXAMINE_LOCATION
        return action, parsed.get("action_args", {}) or {}

    def update_beliefs(self, observation: str) -> None:
        # Beliefs are updated inside decide_action from the model's output.
        pass

    def _apply_belief_update(self, beliefs: dict[str, Any]) -> None:
        if not beliefs:
            return
        if (top := beliefs.get("top_suspect")):
            for k in self.belief_state.suspect_probs:
                self.belief_state.suspect_probs[k] = 0.05
            conf = float(beliefs.get("suspect_confidence", 0.5))
            self.belief_state.suspect_probs[top] = conf
        if (top := beliefs.get("top_weapon")):
            for k in self.belief_state.weapon_probs:
                self.belief_state.weapon_probs[k] = 0.05
            conf = float(beliefs.get("weapon_confidence", 0.5))
            self.belief_state.weapon_probs[top] = conf
        if (top := beliefs.get("top_location")):
            for k in self.belief_state.location_probs:
                self.belief_state.location_probs[k] = 0.05
            conf = float(beliefs.get("location_confidence", 0.5))
            self.belief_state.location_probs[top] = conf
        for name in beliefs.get("eliminated_suspects", []) or []:
            self.belief_state.eliminated_suspects.add(name)
        for fact in beliefs.get("new_facts", []) or []:
            self.belief_state.known_facts.append(fact)
        self.belief_state.normalize()
