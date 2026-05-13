# MysteryArena — Temporal reasoning over visual observation sequences: design review

> Prompt for Gemini. Identical prompt for ChatGPT at `docs/option_b_review_chatgpt.md`. Identical so the two responses are directly comparable.
>
> (Filename is a legacy artifact from earlier framings. The current design is described below.)

## Context

I'm building a research benchmark called **MysteryArena** for evaluating LLM- and VLM-based detective agents on procedurally generated murder-mystery scenarios. Target: NeurIPS submission. The agent must determine **who** killed the victim, **what weapon** was used, and **where**, under partial observability and an action budget, against NPCs that may lie.

### Current architecture

- **Turn-based environment** (`mystery_world/world.py`): `MysteryEnvironment.step(action)` increments a discrete `current_step`, then runs `process_all_events()` simulating weather, evidence decay (semantic freshness only), NPC movement room-to-room, culprit tampering, witness memory.
- **Evidence entities** carry a `contact_timestamp` and a `relevance` label (`CLEARLY_FRESH | AMBIGUOUS | CLEARLY_STALE`).
- **Deterministic given a seed.** `benchmark/verify.py` validates this; JSONL trajectory replay depends on it. Reproducibility is a benchmark contract.
- **3D branch** (`thong/graphics_3d`): Godot 4 client over a thin WebSocket protocol (`server/godot_server.py`). Godot is currently a dumb terminal — it asks `get_current_room`, renders static geometry, displays evidence as text strings in the HUD. NPCs are not rendered as 3D entities yet.

### What we considered and rejected

Three earlier framings were tried and discarded:

1. **Real-time persistent 3D with WASD motor control** — would give a realistic embodied agent, but motor/perception/camera-control confounds entangle with temporal reasoning. We explicitly want to isolate temporal reasoning, not bundle it with motor skill.

2. **Pure-text temporal narratives** — the *existing* textual benchmark already tests some temporal reasoning, but it labels time explicitly (verb tense, time adverbs, narrator phrasing like "the trace is still fresh"). We want to test whether the agent can extract temporal structure from *visual change* rather than from labels.

3. **"Video-like temporal modeling" framed generically** — too vague to defend in a paper. Sharpened to the specific claim below.

## The research claim being tested

**Can an LLM agent reconstruct a coherent temporal narrative from a sequence of partial visual observations, where the temporal information is encoded in visual change rather than in text labels?**

Concretely: the textual env (NPC dialogue, narrator output, action results, scoring, reward, action budget, full `MysteryEnvironment` API) is left untouched. Each `env.step(action)` *additionally* returns a visual observation. The visual observation is **either a single image** (one frame from the agent's viewpoint at the end of the action's game-time interval) **or a short clip** (N frames captured at fixed sub-step offsets across the interval). Both are first-class — image and clip share the same return schema (a list of N frames, with N = 1 being the image case), and the dispatch between them is a design knob the benchmark exposes (per action type and/or per experimental condition). Over an episode (~50 actions), the agent accumulates a **sequence of visual observations** (some images, some clips, depending on dispatch). The benchmark's temporal-reasoning load lives in this sequence: the agent must integrate change-relations across observations (the same bloodstain at observation 3 vs. observation 7 is the same stain, just darker), track persistent object identity through interrupted visual access, infer ordering and causation between visits to the same room, and reconstruct the time of death from the freshness gradient observed across different rooms at different times.

## The validation curve we want

To defend the temporal-reasoning claim, four agent variants must show statistically meaningful, ordered gaps:

| variant | sees | isolates |
|---|---|---|
| Text-only LLM | textual obs only | baseline (no visual channel) |
| History-blind VLM | only the current frame, no past observations | single-frame perception |
| History-aware snapshot VLM | all past single-frame observations | **cross-observation temporal reasoning** |
| History-aware video model | all past clips | cross-observation + within-clip temporal reasoning |

The headline claim is the gap between **history-blind VLM** and **history-aware snapshot VLM** — that's the temporal-reasoning isolation. The history-aware video model is the upper bound that adds within-clip motion signal.

## Key design commitments

1. **Textual env unchanged.** Existing master-branch simulation, scoring, and API are preserved. The visual channel is *additive*.

2. **Semantic action space, no motor control.** Pre-defined verbs from the existing `AgentAction` enum (`world.py`). No WASD, no camera aim. This rules out the motor/control confound.

3. **Post-action visual observation per `step()`.** Either a single image (N = 1) or an N-frame clip (N > 1) rendered from a camera anchored at the detective's standing position in their current room, oriented toward the focus of the action. Image and clip share the same return schema; the dispatch between them is configurable per action type and per experimental condition.

4. **Persistent scene mutated by events.** Godot process kept alive across actions. Env-side `WorldState` authoritative; Godot mirrors via event stream. Continuous change (evidence aging, candle burning, lighting drift) is shader-driven from `age = game_time − contact_time`. Discrete change (NPC movement, prop relocations) is event-driven via a timestamp queue applied during clip render.

5. **Deterministic logical clock.** Godot does not tick on wall-clock; `game_time` advances by fixed Δt per env step. Frames captured at fixed sub-step offsets within Δt. Same seed → same frame sequence (modulo bounded GPU non-determinism across hardware).

6. **Observations must evolve as the environment progresses, on persistent identities (load-bearing invariant).** This is the property that makes the visual channel meaningful for temporal reasoning at all.
   - *Between observations*, the same room must look meaningfully different on subsequent visits: stains darker, candles shorter, props relocated or removed, NPCs in new positions, lighting drifted. If a re-entered room looks identical to its previous capture, the visual sequence has no temporal load between those two observations.
   - *Within a clip*, at least one element must be visibly in transition (NPC crossing, lighting shifting, evidence aging perceptibly across the N frames).
   - *Change must appear on persistent identities*: the stain in observation 7 is the *same* stain as in observation 3, just aged; the candle in observation 12 is the *same* candle as in observation 4, just shorter. Persistent identity is what makes cross-observation reasoning possible — without it, the model can't relate one observation to another.
   - *The generator is required to guarantee these properties per episode* — a per-episode change-density floor, plus persistent-identity tagging of evolving evidence. Episodes that fail the floor are rejected or relabeled.
   - Within-clip motion is secondary signal compared to cross-observation persistent-identity change.

7. **Off-camera state evolution.** Events happen in rooms the agent isn't in. The agent only *sees* the rooms they visit; on revisit, the room reflects everything that has happened, and the agent must *infer* the off-camera timeline from the visible deltas.

8. **Modality of the visual channel is a presentation choice.** 2D top-down or 3D first-person — both can deliver cross-observation change-relations. Modality is not the research question; temporal reasoning is.

### Budget

~1.5–2.5 weeks of focused engineering, dominated by shader authoring, the clip-render loop, and the event-streaming protocol.

## What I want from you

Be rigorous and concrete. Do not be polite.

1. **Memory vs. reasoning confound.** A model with perfect visual memory but no temporal reasoning could still do well by retrieving past observations. How do we tell apart "this model integrates temporally" from "this model just remembers visually"? What's a clean test that separates them?

2. **Sequence length vs. multimodal context limits.** An episode produces ~50 observations. Most current VLMs degrade past 16–32 images in a single context. Either (a) we cap visual history at the model's working window — then the model under test bounds what we can claim — or (b) the agent must summarize the visual history textually between actions, which collapses the temporal reasoning back to text. How do you resolve this tension cleanly?

3. **Within-clip vs. across-clip temporal load.** Within-clip continuity (8 frames of a candle guttering over 5 game-minutes) is easy for a video model. Cross-clip change-relations (the same stain at clip 3 vs. clip 7, an hour of game-time apart, with the agent doing other things in between) require persistent-identity tracking through interrupted visual access. Which is the headline claim, and how do we balance the two without one dominating?

4. **Action-conditioned observation coupling.** The agent's actions shape the observation sequence (GOTO different rooms → different next observations). This couples planning with temporal reasoning. Is that a feature (real detective work) or a confound (good planners look like good temporal reasoners)? Should we evaluate with both adaptive and fixed (oracle-itinerary) action sequences as an ablation?

5. **Generator-side guarantee of cross-observation change-relations.** The mystery generator must produce evidence that changes meaningfully across the episode (decay, relocation, removal by culprit) so the visual sequence has temporal load. How is this guaranteed — per-episode invariants, difficulty levels, a "change density" metric? What if random seeds produce episodes with no interesting cross-clip change?

6. **Ablations that isolate temporal reasoning.** Which are essential, which are nice-to-have?
   - **Shuffled-history**: pass past observations in random order. A temporally-reasoning model should drop; a visual-fact-retrieval model should not.
   - **Single-frame ablation**: collapse each clip to one frame. Isolates within-clip vs. cross-clip.
   - **Transcript-of-events ablation**: hand the LLM a textual narration of every observation. If this matches the visual-history score, the visual channel isn't doing temporal-reasoning work.
   - **Memoryless VLM**: only the current frame. Isolates cross-observation reasoning.
   - **Oracle-itinerary**: fix the action sequence. Decouples planning from temporal reasoning.

7. **Failure-mode taxonomy.** Where does an LLM detective fail temporally — forgetting earlier observations, mis-ordering events across visits, missing cross-clip change-relations, conflating evidence between rooms, hallucinating temporal precedence, mis-reading freshness gradients? Should the benchmark report disaggregated metrics per failure mode?

8. **Image vs. clip dispatch, plus Δt and N for clips.** Always-image loses within-clip motion signal; always-clip burns context budget for actions where nothing visually happens during the interval. Should ANALYZE / EXAMINE_OBJECT-on-a-static-prop return a single image, while WAIT / GOTO / TALK_TO (during which other NPCs move and lighting drifts) return a clip? What's a principled dispatch rule that doesn't accidentally turn the benchmark into a test of "which observation type the model handles best"? For action types that do return a clip — too short Δt or too few frames N → no within-clip signal; too long → context budget burned. Is the right principle "set Δt and N such that one meaningful change-relation occurs per clip on average"?

9. **Camera-anchor confound.** Fixed first-person camera from the detective's standing position. Does this introduce a perception confound (some evidence visible from one anchor but not another) that masks temporal reasoning? Should we run an omniscient-overhead ablation as an upper bound on "what if perception were perfect"?

10. **Off-camera inference is a separate problem.** The agent must reconstruct off-camera events (a new stain in the kitchen on visit 4 implies someone was there between visits 3 and 4). This is causal abduction, not strictly temporal reasoning over observations. Is it part of the headline claim, or a separate evaluation track?

11. **Determinism with a visual modality.** GPU non-determinism (vendor, driver, shader compiler) makes raw-pixel comparison across machines unreliable. What's the right reproducibility invariant — perceptual hash per frame, semantic-state checksum on the env side, both? Will reviewers accept "deterministic semantic state + bounded perceptual variance on rendered output" as a reproducibility claim?

12. **Comparable benchmarks.** Where does this sit vs. video-QA (NExT-QA, MovieQA, Perception Test), embodied long-horizon (ALFRED, EmbodiedQA, BEHAVIOR), detective games (Her Story, Obra Dinn), temporal NLP (TempEval, MCTACO, TRACIE)? Where is the contribution genuinely novel; where will a skeptical reviewer say "this is X but worse"?

13. **Cost estimation.** Where am I underestimating engineering time? What's the single most likely thing to blow the schedule given the simplifications from dropping motor control?

14. **Anything I haven't asked about.** What's the biggest blind spot in this design that none of the questions above are pointing at?

Cite concrete failure modes. If something is fine, say so. If something is wrong, explain how it breaks.
