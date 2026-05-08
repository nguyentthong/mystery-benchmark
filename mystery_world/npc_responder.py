"""
NPC response engine for stateful interview interactions.

Lying is injected into the system prompt from ground-truth flags.
The LLM has no agency over whether to lie -- that decision comes from WorldState.
Uses any OpenAI-compatible endpoint (vLLM, Together AI, etc.).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mystery_world.entities import Character
    from mystery_world.world import WorldState

def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models (e.g. Qwen3)."""
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    # Also strip any leftover "Thinking Process:" preamble style output
    text = re.sub(r"(?i)^thinking process:.*?(?=\n\S)", "", text, flags=re.DOTALL)
    return text.strip()


_DEFAULT_NPC_URL = "http://localhost:8000/v1"
_DEFAULT_NPC_MODEL = "Qwen/Qwen2.5-27B-Instruct"
_FIXED_SEED = 42


def _witnessed_summary(char: "Character", state: "WorldState") -> str:
    lines = []
    for eid in char.witnessed_events:
        for te in state.ground_truth_timeline:
            key = f"{te.step}_{te.actor_id}_{te.action}"
            if (key == eid or te.action == eid) and te.is_public:
                lines.append(f"- {te.details}")
                break
    return "\n".join(lines) if lines else "- Nothing notable that you can clearly recall."


def _relationship_summary(char: "Character", state: "WorldState") -> str:
    lines = []
    for rel in char.relationships:
        target = state.characters.get(rel.target_id)
        if not target:
            continue
        if rel.sentiment > 0.3:
            feeling = f"friendly with"
        elif rel.sentiment < -0.3:
            feeling = f"hostile toward"
        else:
            feeling = f"neutral toward"
        lines.append(f"- You are {feeling} {target.full_name} ({rel.kind}).")
    return "\n".join(lines) if lines else "- No strong connections to speak of."


def _corroboration_summary(char: "Character", state: "WorldState") -> str:                                                                                                             
    """                                                                                                                                                                                
    If this character is a genuine alibi corroborator for someone,
    tell them that fact so they can confirm it truthfully.                                                                                                                             
    Lying corroborators are handled separately by _lying_instruction.
    """                                                                                                                                                                                
    lines = []                          
    for other in state.characters.values():                                                                                                                                            
        if (                                                                                                                                                                           
            other.alibi_corroborator_id == char.id                                                                                                                                     
            and other.alibi_corroboration_is_genuine                                                                                                                                   
            and other.is_alive                            
        ):                                                                                                                                                                             
            lines.append(
                f"- You were with {other.full_name} during the relevant time window "                                                                                                  
                f"and can confirm their whereabouts."     
            )
    return "\n".join(lines) if lines else ""                                                                                                                                           


def _lying_instruction(char: "Character", state: "WorldState") -> str:
    """
    Returns ground-truth-derived instruction text telling the character HOW to lie.
    Empty string if the character should be honest.
    """
    culprit = state.get_culprit()
    parts: list[str] = []

    if char.is_culprit:
        alibi_claim = char.alibi_details or "you were attending to private matters of your own"
        parts.append(
            "IMPORTANT: You committed the murder. Never admit this under any circumstances. "
            f"If asked about your whereabouts at the time of the crime, claim: \"{alibi_claim}\". "
            "If pressed hard, deflect by expressing concern or casting vague suspicion on others. "
            "Do not contradict yourself."
        )
        parts.append(
            "DO NOT VOLUNTEER ALONENESS. Never use the words 'alone', 'by myself', "
            "'no one saw me', 'nobody was with me', 'I spoke to no one', 'I had no "
            "company', or similar declarations of solitude unless the detective "
            "directly asks whether you were alone. Describe what you were doing "
            "without naming a witness and without denying one. If asked an open "
            "question like 'where were you?', name the place and the activity only. "
            "Volunteering aloneness is a confession-adjacent tell."
        )
    elif (
        culprit is not None
        and culprit.alibi_corroborator_id == char.id
        and not culprit.alibi_corroboration_is_genuine
    ):
        parts.append(
            f"IMPORTANT: You have agreed to provide a false alibi for {culprit.full_name}. "
            f"If asked where {culprit.full_name} was, confirm they were with you. "
            "Do not admit this arrangement -- act as if it is the simple truth."
        )

    return "\n".join(parts)


def derive_npc_gloss(
    char: "Character", state: "WorldState", question: str | None = None,
) -> str:
    """Canonical underlying claim from the NPC, in plain English.

    Strong-voice mode (Tier C) makes parsing styled NPC dialogue hard; the
    gloss is a question-independent summary of the NPC's whereabouts claim,
    derived from world state, so agents that fail to parse the styled
    response can still extract the underlying fact. This is the public
    benchmark's narrator-gloss companion to NPC dialogue.
    """
    if char.has_alibi and char.alibi_details:
        return f"Whereabouts claim: {char.alibi_details}"
    for other in state.characters.values():
        if (
            getattr(other, "alibi_corroborator_id", None) == char.id
            and getattr(other, "alibi_corroboration_is_genuine", False)
            and other.is_alive
        ):
            other_loc = state.locations.get(other.location_id)
            other_loc_name = other_loc.name if other_loc else "an unspecified room"
            return (
                f"Whereabouts claim: was with {other.full_name} in the "
                f"{other_loc_name} during the relevant time."
            )
    return "Whereabouts claim: no alibi provided."


def build_npc_system_prompt(char: "Character", state: "WorldState") -> str:
    from mystery_world.entities import CharacterRole
    role_label = "suspect" if CharacterRole.SUSPECT in char.roles else "witness"

    current_loc = state.locations.get(char.location_id)
    current_loc_name = current_loc.name if current_loc else "unknown"

    # Check if this character is a genuine alibi corroborator for another suspect.
    # If so, their whereabouts line must reflect being with that suspect -- not "alone".
    corroborated_suspect = None
    for other in state.characters.values():
        if (
            other.alibi_corroborator_id == char.id
            and other.alibi_corroboration_is_genuine
            and other.is_alive
        ):
            corroborated_suspect = other
            break

    if char.has_alibi:
        alibi_line = f"Your whereabouts: {char.alibi_details}"
    elif corroborated_suspect is not None:
        suspect_loc = state.locations.get(corroborated_suspect.location_id)
        suspect_loc_name = suspect_loc.name if suspect_loc else current_loc_name
        alibi_line = (
            f"At the time of the murder you were with {corroborated_suspect.full_name} "
            f"in the {suspect_loc_name}. You can confirm they were there with you."
        )
    else:
        alibi_line = (
            f"At the time of the murder you were in the {current_loc_name}, alone. "
            f"You have no alibi and no one can confirm your whereabouts."
        )

    lying_block = _lying_instruction(char, state)

    known_locations = ", ".join(loc.name for loc in state.locations.values())
    known_people = ", ".join(
        c.full_name for c in state.characters.values() if c.id != char.id and c.is_alive
    )

    from mystery_world.entities import SPEECH_ARCHETYPES
    style_block = SPEECH_ARCHETYPES.get(
        char.speech_archetype,
        "Speak in plain, direct English.",
    )

    return f"""You are {char.full_name}, a {char.personality} {role_label} being questioned by a detective about a recent murder.
You are {char.full_name}. You are currently in the {current_loc_name}.

VOICE -- speak in this distinct style at all times:
{style_block}

WHAT YOU KNOW (these are the ONLY facts you may draw on):
Your whereabouts: {alibi_line}
Things you personally witnessed:
{_witnessed_summary(char, state)}
Your relationships:
{_relationship_summary(char, state)}

The only locations that exist: {known_locations}.
The only other people: {known_people}.

STRICT RULES -- follow these exactly:
- You are {char.full_name}. Never refer to yourself in the third person.
- You may ONLY state facts listed above. Do NOT invent any other names, locations, times, or events.
- If asked about something not in your knowledge above, say "I don't know" or "I don't recall" -- never fabricate.
- Do not mention any room, person, or object not in the lists above.
- Be consistent with everything you have already said in this conversation.
- Keep responses to 1-3 sentences. Stay in character at all times.
- Do not volunteer information the detective has not asked about. Answer ONLY the question that was asked — nothing extra.
- LAYERED ALIBI REVEAL — this is a hard constraint:
  • When asked simply where you were (e.g. "where were you", "what were you doing at the time"), state ONLY the location and a brief activity. Do NOT mention being alone, by yourself, that no one was there, that you have no alibi, that you cannot be confirmed, or that you spoke to no one. Strip those phrases from any whereabouts text given to you above.
  • Only reveal whether you were alone or with someone if the detective explicitly asks about company, witnesses, or corroboration — e.g. "was anyone with you", "who else was there", "can anyone confirm", "do you have a witness". Until that follow-up is asked, keep the alone/no-witness detail to yourself.
  • If your whereabouts text mentions a corroborator (someone who was with you), the same rule applies: don't volunteer their name on the first "where were you" question — just give the location. Mention them only when asked about company or witnesses.
- If the detective greets you or asks an open-ended question (e.g. "tell me about yourself", "anything to share"), respond briefly without disclosing where you were at the time of the murder. Wait to be asked specifically.
- Never preemptively defend yourself by stating you were alone or had no witnesses. That is volunteering information.
- Apply the VOICE style above to every word you speak.
{lying_block}"""


class NPCResponder:
    """
    Generates NPC interview responses using a local or remote LLM.

    Parameters
    ----------
    base_url : str
        OpenAI-compatible API base URL.
        For vLLM: "http://localhost:8000/v1"
        For Together AI: "https://api.together.xyz/v1"
    model : str
        Model name as served by the endpoint.
    seed : int
        Fixed seed for reproducibility (passed via extra_body).
    """

    def __init__(
        self,
        base_url: str | None = _DEFAULT_NPC_URL,
        model: str = _DEFAULT_NPC_MODEL,
        seed: int = _FIXED_SEED,
        api_key: str | None = None,
        api_key_env: str | None = None,
    ) -> None:
        """
        api_key / api_key_env:
          - Pass an explicit api_key for hosted providers (OpenAI, Together, etc.)
          - Or pass api_key_env=NAME to read it from os.environ[NAME]
            (e.g. "OPENROUTER_API_KEY"). Falls back to OPENAI_API_KEY for
            backwards compatibility.
          - For a local vLLM endpoint, leave both None -- we fall back to the
            dummy "EMPTY" placeholder so the openai SDK doesn't object.
          - If base_url is None, we use OpenAI's default endpoint, which
            requires a real key.
        """
        self.base_url = base_url
        self.model = model
        self.seed = seed
        self.api_key = api_key
        self._api_key_env = api_key_env
        self._client: Any = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError("openai package required: pip install openai") from exc
        import os
        if self.api_key is not None:
            key = self.api_key
        elif self._api_key_env:
            key = os.environ.get(self._api_key_env, "")
            if not key:
                raise RuntimeError(
                    f"NPCResponder: env var {self._api_key_env} is unset or empty. "
                    f"Export it in the shell that launches the sweep, e.g. "
                    f"`export {self._api_key_env}=...` "
                    f"(this would otherwise silently fail with 401 errors written into NPC dialog)."
                )
        else:
            key = os.environ.get("OPENAI_API_KEY") or "EMPTY"
        kwargs: dict[str, Any] = {"api_key": key or "EMPTY"}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self._client = openai.OpenAI(**kwargs)

    def respond(
        self,
        char: "Character",
        state: "WorldState",
        question: str,
        history: list[dict[str, str]],
    ) -> str:
        """
        Generate the NPC's response to the detective's question.

        Parameters
        ----------
        char : Character
            The NPC being questioned.
        state : WorldState
            Full ground-truth state (used only for lying injection and witnessed events).
        question : str
            The detective's question.
        history : list[dict]
            Prior turns in this interview: [{"role": "user"|"assistant", "content": "..."}]
        """
        self._ensure_client()
        system = build_npc_system_prompt(char, state)
        messages = list(history) + [{"role": "user", "content": question}]

        # OpenAI rejects vLLM-only extras (`chat_template_kwargs`, etc.).
        # Detect endpoint kind and only send the extras vLLM expects.
        is_openai = self.base_url is None or "openai.com" in (self.base_url or "")
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "max_tokens": 512,
            "temperature": 0.7,
        }
        if is_openai:
            # OpenAI supports `seed` as a top-level kwarg.
            kwargs["seed"] = self.seed
        else:
            # vLLM: pass seed + thinking-mode toggle through extra_body.
            kwargs["extra_body"] = {
                "seed": self.seed,
                "chat_template_kwargs": {"enable_thinking": False},
            }

        try:
            resp = self._client.chat.completions.create(**kwargs)
            raw = resp.choices[0].message.content or ""
            return _strip_thinking(raw).strip()
        except Exception as exc:
            return f"{char.full_name} stares at you silently and says nothing. (Error: {exc})"