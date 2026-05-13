"""
Verification utilities for benchmark instances.

Provides:
    1. Structural consistency checks (graph connectivity, entity references)
    2. Solvability verification (sufficient evidence, breakable alibis)
    3. Human annotation export (readable case summaries for annotators)
    4. Cross-instance diversity metrics
    5. Cross-hardware reproducibility (M10): semantic checksum + perceptual
       hash of rendered frames, plus reference-set round-trip.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from mystery_world.entities import CharacterRole, EdgeType, EvidenceState, EvidenceType
from mystery_world.world import WorldState


# ---------------------------------------------------------------------------
# Structural consistency
# ---------------------------------------------------------------------------

def check_structural_consistency(state: WorldState) -> dict[str, Any]:
    """Verify internal consistency of a generated world state."""
    issues: list[str] = []

    # 1. Location graph is connected
    if state.locations:
        visited: set[str] = set()
        start = next(iter(state.locations))
        stack = [start]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            loc = state.locations.get(node)
            if loc:
                for adj in loc.adjacent_ids:
                    if adj not in visited:
                        stack.append(adj)
        if visited != set(state.locations.keys()):
            unreachable = set(state.locations.keys()) - visited
            issues.append(f"Disconnected locations: {unreachable}")
    
    # 2. All character location_ids reference valid locations
    for cid, char in state.characters.items():
        if char.location_id and char.location_id not in state.locations:
            issues.append(f"Character {char.full_name} references invalid location {char.location_id}")
    
    # 3. All evidence location_ids reference valid locations
    for eid, ev in state.evidence.items():
        if ev.location_id and ev.location_id not in state.locations:
            issues.append(f"Evidence {ev.name} references invalid location {ev.location_id}")

    # 4. Culprit and victim exist
    if state.culprit_id not in state.characters:
        issues.append(f"Culprit ID {state.culprit_id} not in characters")
    if state.victim_id not in state.characters:
        issues.append(f"Victim ID {state.victim_id} not in characters")
    
    # 5. Murder weapon exists
    if state.murder_weapon_id not in state.objects:
        issues.append(f"Murder weapon ID {state.murder_weapon_id} not in objects")
    
    # 6. Murder location exists
    if state.murder_location_id not in state.locations:
        issues.append(f"Murder location ID {state.murder_location_id} not in locations")
    
    # 7. Exactly one culprit
    culprits = [c for c in state.characters.values() if c.is_culprit]
    if len(culprits) != 1:
        issues.append(f"Expected exactly 1 culprit, found {len(culprits)}")
    
    # 8. Exactly one victim
    victims = [c for c in state.characters.values() if CharacterRole.VICTIM in c.roles]
    if len(victims) != 1:
        issues.append(f"Expected exactly 1 victim, found {len(victims)}")
    
    # 9. Adjacency is symmetric
    for lid, loc in state.locations.items():
        for adj_id in loc.adjacent_ids:
            adj = state.locations.get(adj_id)
            if adj and lid not in adj.adjacent_ids:
                issues.append(f"Asymmetric adjacency: {lid} -> {adj_id} but not reverse")
    
    return {
        "consistent": len(issues) == 0,
        "issues": issues,
        "num_locations": len(state.locations),
        "num_characters": len(state.characters),
        "num_evidence": len(state.evidence),
        "num_objects": len(state.objects),
    }


# ---------------------------------------------------------------------------
# Solvability
# ---------------------------------------------------------------------------

def check_solvability(state: WorldState) -> dict[str, Any]:
    """
    Comprehensive solvability check.

    A mystery is solvable if:
        - There exists a logical chain from discoverable evidence to the culprit
        - The culprit's alibi is breakable (unverified or no corroborator)
        - At least 2 pieces of usable evidence point to the culprit
        - The murder weapon is discoverable
        - Only one fact
    """
    culprit = state.get_culprit()
    if culprit is None:
        return {"solvable": False, "reason": "No culprit defined"}
    
    # Evidence pointing to culprit
    culprit_evidence = [
        e for e in state.evidence.values()
        if e.linked_character_id == state.culprit_id
        and not e.is_red_herring
    ]
    usable_culprit_evidence = [e for e in culprit_evidence if e.is_usable()]

    # Evidence types available
    evidence_types = Counter(e.evidence_type for e in usable_culprit_evidence)

    # Alibi analysis
    alibi_breakable = (        
        not culprit.has_alibi
        or culprit.alibi_corroborator_id is None                                  
        or not culprit.alibi_corroboration_is_genuine                           
        or culprit.alibi_has_gap                                                  
    )  

    # Murder weapon discoverable
    weapon_obj = state.objects.get(state.murder_weapon_id)
    weapon_discoverable = weapon_obj is not None and weapon_obj.location_id in state.locations

    # Can innocents be eliminated?
    non_culprit_suspects = [
        c for c in state.characters.values()
        if CharacterRole.SUSPECT in c.roles and not c.is_culprit
    ]
    suspects_with_corroborated_alibis = sum(                                          
        1 for s in non_culprit_suspects                                         
        if s.has_alibi and s.alibi_corroborator_id is not None
    )
    suspects_with_unverified_alibis = sum(
        1 for s in non_culprit_suspects                                           
        if s.has_alibi and s.alibi_corroborator_id is None                      
    )

    # Solution uniqueness: is there enough to distinguish culprit from others?    
    distinguishing_evidence = len(usable_culprit_evidence)                      

    solvable = (               
        distinguishing_evidence >= 2
        and alibi_breakable    
        and weapon_discoverable                                                 
        and EvidenceType.PHYSICAL in evidence_types
    )
    return {
        "solvable": solvable,
        "culprit_evidence_total": len(culprit_evidence),
        "culprit_evidence_usable": len(usable_culprit_evidence),
        "evidence_type_breakdown": {k.name: v for k, v in evidence_types.items()},
        "alibi_breakable": alibi_breakable,                                       
        "weapon_discoverable": weapon_discoverable,                               
        "suspects_with_corroborated_alibis": suspects_with_corroborated_alibis,   
        "suspects_with_unverified_alibis": suspects_with_unverified_alibis,    
        "total_non_culprit_suspects": len(non_culprit_suspects),                  
        "red_herrings": sum(1 for e in state.evidence.values() if e.is_red_herring),                                 
    }


def check_locard_solvability(state: WorldState) -> dict[str, Any]:
    """Verify the Locard triangle is closable: each edge has at least one
    discoverable fresh evidence pointing to the correct entities."""
    issues: list[str] = []
    murder_ts = state.murder_timestamp
    threshold = state.freshness_threshold

    for edge in EdgeType:
        found = False
        for ev in state.evidence.values():
            if ev.is_red_herring or ev.state == EvidenceState.DESTROYED or ev.discovery_difficulty >= 1.0:
                continue
            if ev.relevance is None or ev.relevance.edge_type != edge:
                continue
            if abs(ev.relevance.contact_timestamp - murder_ts) >= threshold:
                continue
            rel = ev.relevance
            if edge == EdgeType.SUSPECT_WEAPON and state.culprit_id in rel.subject_ids and state.murder_weapon_id in rel.subject_ids:
                found = True
            elif edge == EdgeType.WEAPON_VICTIM and state.murder_weapon_id in rel.subject_ids and state.victim_id in rel.subject_ids:
                found = True
            elif edge == EdgeType.SUSPECT_ROOM and state.culprit_id in rel.subject_ids and state.murder_location_id in rel.subject_ids:
                found = True
            if found:
                break
        if not found:
            issues.append(f"No discoverable fresh evidence for edge {edge.name}")

    return {
        "solvable": len(issues) == 0,
        "issues": issues,
        "triangle_edges_covered": 3 - len(issues),
    }
    
# ---------------------------------------------------------------------------
# Human annotation export
# ---------------------------------------------------------------------------

def export_annotation_sheet(state: WorldState, output_path: str | Path) -> None:
    """
    Export a human-readable case summary for annotation / quality review.

    Annotators verify:
        - The case is logically solvable
        - The narrative is coherent
        - Difficulty feels appropriate for the labelled level
    """
    culprit = state.get_culprit()
    victim = state.get_victim()
    weapon = state.objects.get(state.murder_weapon_id)
    murder_loc = state.locations.get(state.murder_location_id)

    lines = [
        "=" * 70,
        f"CASE #{state.seed} — ANNOTATION SHEET",
        "=" * 70,
        "",
        "--- GROUND TRUTH (for annotator reference only) ---",
        f"  Culprit: {culprit.full_name if culprit else 'N/A'}",
        f"  Victim:  {victim.full_name if victim else 'N/A'}",
        f"  Weapon:  {weapon.name if weapon else 'N/A'}",
        f"  Location: {murder_loc.name if murder_loc else 'N/A'}",
        f"  Motive:  {state.motive}",
        f"  Time of murder: step {state.murder_step}",
        "",
        "--- CHARACTERS ---",
    ]

    for cid, char in state.characters.items():
        roles = ", ".join(r.name for r in char.roles)
        alibi = f"Alibi: {char.alibi_details}" if char.has_alibi else "No alibi"
        motive = f"Motive: {char.motive}" if char.motive else ""
        lines.append(f"  {char.full_name} [{roles}] ({char.personality})")
        lines.append(f"  {alibi}")
        if motive:
            lines.append(f"    {motive}")
        if char.is_culprit:
            lines.append(f"    ** THIS IS THE CULPRIT **")
        lines.append("")

    lines.append("--- LOCATIONS ---")
    for lid, loc in state.locations.items():
        adj = [state.locations[a].name for a in loc.adjacent_ids if a in state.locations]
        lines.append(f"  {loc.name} ({loc.tag.name}) → {', '.join(adj)}")
    lines.append("")


    lines.append("--- EVIDENCE ---")
    for eid, ev in state.evidence.items():
        linked = state.characters.get(ev.linked_character_id)
        linked_name = linked.full_name if linked else "N/A"
        herring = " [RED HERRING]" if ev.is_red_herring else ""
        lines.append(f"    Points to: {linked_name}{herring}")
        lines.append(f"    Location: {state.locations.get(ev.location_id, type('', (), {'name': 'unknown'})()).name}")
        lines.append(f"    Difficulty: {ev.discovery_difficulty:.2f}")
        lines.append("")
    
    lines.extend([
        "--- ANNOTATION QUESTIONS ---",
        "1. Is this case logically solvable given the evidence? [Yes / No / Partially]",
        "2. Is the narrative coherent (no contradictions)? [Yes / No]",
        "3. Rate difficulty (1=trivial, 5=expert): [ ]",
        "4. Are the red herrings plausible? [Yes / No / N/A]",
        "5. Any issues or notes:",
        "",
        "Annotator: _______________  Date: _______________",
    ])

    Path(output_path).write_text("\n".join(lines))

# ---------------------------------------------------------------------------
# Diversity metrics across a benchmark suite
# ---------------------------------------------------------------------------

def compute_diversity_metrics(instances_dir: str | Path) -> dict[str, Any]:
    """
    Compute diversity statistics across a benchmark suite to verify
    that procedural generation produces varied instances.
    """
    instances_dir = Path(instances_dir)
    manifest = json.loads((instances_dir / "manifest.json").read_text())

    culprits: list[str] = []
    weapons: list[str] = []
    locations: list[str] = []
    motives: list[str] = []

    for entry in manifest:
        culprits.append(entry.get("culprit", ""))
        weapons.append(entry.get("weapon", ""))
        locations.append(entry.get("location", ""))
        motives.append(entry.get("motive", ""))

    def _entropy(items: list[str]) -> float:
        counts = Counter(items)
        total = sum(counts.values())
        probs = [c / total for c in counts.values()]
        return -sum(p * np.log2(p) for p in probs if p > 0)

    return {
        "n_instances": len(manifest),
        "unique_culprits": len(set(culprits)),
        "unique_weapons": len(set(weapons)),
        "unique_locations": len(set(locations)),
        "unique_motives": len(set(motives)),
        "culprit_entropy": _entropy(culprits),
        "weapon_entropy": _entropy(weapons),
        "location_entropy": _entropy(locations),
        "motive_entropy": _entropy(motives),
    }


# ---------------------------------------------------------------------------
# Cross-hardware reproducibility (M10)
# ---------------------------------------------------------------------------
#
# The semantic state generated from a seed is fully deterministic and is
# verified via a SHA-256 checksum over a canonical, sorted representation.
# Rendered images vary slightly across GPUs / SDL versions / font hinting, so
# they are verified via dhash (difference hash) -- a 64-bit perceptual hash
# whose Hamming distance is small for visually-identical images and large for
# semantically-different ones. Strict-mode runs compare against a stored
# reference set; loose-mode runs check internal consistency within a single
# process.


# Bits of Hamming distance allowed between two dhashes for the images to
# count as visually equivalent. 0-5 is a typical "near-duplicate" band; we
# use 6 as a comfortable margin that still catches rendering regressions.
DEFAULT_PHASH_HAMMING_THRESHOLD: int = 6


@dataclass
class ReferenceData:
    """The reproducibility reference for one (seed, config) pair.

    semantic_checksum is over the canonical content of the world state and
    must match exactly across hardware. rendered_hashes is one dhash per
    observation point along the canonical itinerary; their Hamming distance
    to the recomputed dhashes is allowed up to a small threshold.
    """
    seed: int
    config_hash: str
    semantic_checksum: str
    rendered_hashes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReferenceData":
        return cls(**d)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "ReferenceData":
        return cls.from_dict(json.loads(Path(path).read_text()))


def semantic_checksum(state: WorldState) -> str:
    """SHA-256 hex digest over a sorted, canonical representation of the
    state's research-significant content. Two machines that generated the
    same state from the same seed must agree on this value. Excludes
    fields that are runtime-mutable (current_step, event_log, weather)."""
    parts: list[str] = [
        f"seed={state.seed}",
        f"culprit={state.culprit_id}",
        f"victim={state.victim_id}",
        f"weapon={state.murder_weapon_id}",
        f"location={state.murder_location_id}",
        f"body_loc={state.body_location_id}",
        f"murder_step={state.murder_step}",
        f"murder_ts={state.murder_timestamp}",
        f"fresh_thr={state.freshness_threshold}",
        f"motive={state.motive}",
    ]
    # Locations: id -> name + sorted adjacency.
    for lid in sorted(state.locations):
        loc = state.locations[lid]
        adj = ",".join(sorted(loc.adjacent_ids))
        parts.append(f"loc:{lid}:{loc.name}:adj=[{adj}]:tag={loc.tag.name}")
    # Characters: id -> name + roles + sorted alibi claims.
    for cid in sorted(state.characters):
        c = state.characters[cid]
        roles = ",".join(sorted(r.name for r in c.roles))
        alibis = "|".join(
            f"{a.location_name}@step{a.step}:{a.clock_time_str}"
            for a in c.alibi_claims
        )
        parts.append(f"char:{cid}:{c.full_name}:roles=[{roles}]:alibi=[{alibis}]")
    # Objects: id -> name + location.
    for oid in sorted(state.objects):
        o = state.objects[oid]
        parts.append(f"obj:{oid}:{o.name}:loc={o.location_id}:ev={o.evidence_id or ''}")
    # Evidence: id -> type, state, relevance.
    for eid in sorted(state.evidence):
        ev = state.evidence[eid]
        ts = ev.relevance.contact_timestamp if ev.relevance else "none"
        label = ev.relevance.surface_label.name if ev.relevance else "none"
        parts.append(
            f"ev:{eid}:type={ev.evidence_type.name}:state={ev.state.name}"
            f":linked={ev.linked_character_id or ''}:ts={ts}:label={label}"
        )
    # Canonical itinerary: ordered action sequence.
    for entry in state.canonical_itinerary:
        kw = ",".join(f"{k}={v}" for k, v in sorted(entry.get("kwargs", {}).items()))
        parts.append(f"itin:{entry['action']}:[{kw}]")
    blob = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def config_checksum(state: WorldState) -> str:
    """SHA-256 hex of the ComplexityConfig used to generate ``state``."""
    blob = json.dumps(state.config.to_dict(), sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def perceptual_hash(png_bytes: bytes) -> str:
    """64-bit dhash (difference hash) of a PNG image, returned as a
    16-char hex string. Two visually-identical images have Hamming
    distance 0; small per-pixel variation typically stays within ~6 bits.
    Uses Pillow for decode + grayscale + resize."""
    from PIL import Image   # local import to keep verify.py import-light
    img = Image.open(io.BytesIO(png_bytes)).convert("L").resize((9, 8))
    pixels = list(img.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def hamming_distance(h1: str, h2: str) -> int:
    """Hamming distance between two hex dhash strings."""
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


def compute_reference(state: WorldState, render: bool = True) -> ReferenceData:
    """Compute the reproducibility reference for a fresh state.

    ``render=True`` (default) replays the canonical itinerary against a
    deep-copied state with visual_mode=True and dhashes each
    result.image. Set ``render=False`` to skip rendering (e.g. when
    verifying only the semantic checksum on a machine without pygame).
    """
    # Local import: avoid pulling pygame into verify.py at module load.
    from mystery_world.world import AgentAction, MysteryEnvironment

    s = copy.deepcopy(state)
    semantic = semantic_checksum(s)
    cfg = config_checksum(s)

    rendered_hashes: list[str] = []
    if render:
        env = MysteryEnvironment(s, visual_mode=True)
        initial = env.get_observation_image()
        if initial is not None:
            rendered_hashes.append(perceptual_hash(initial))
        for entry in s.canonical_itinerary:
            action = AgentAction[entry["action"]]
            if action == AgentAction.ACCUSE:
                continue
            result = env.step(action, **entry.get("kwargs", {}))
            if result.image is not None:
                rendered_hashes.append(perceptual_hash(result.image))
    return ReferenceData(
        seed=s.seed,
        config_hash=cfg,
        semantic_checksum=semantic,
        rendered_hashes=rendered_hashes,
    )


def verify_against_reference(
    state: WorldState,
    reference: ReferenceData,
    hamming_threshold: int = DEFAULT_PHASH_HAMMING_THRESHOLD,
) -> dict[str, Any]:
    """Verify ``state`` matches a stored ``reference``. The semantic
    checksum must match exactly; rendered-frame dhashes are allowed up
    to ``hamming_threshold`` bits of difference per frame to absorb
    GPU/driver variation.
    """
    current = compute_reference(state, render=True)
    report: dict[str, Any] = {
        "semantic_match": current.semantic_checksum == reference.semantic_checksum,
        "config_match": current.config_hash == reference.config_hash,
        "n_frames_current": len(current.rendered_hashes),
        "n_frames_reference": len(reference.rendered_hashes),
        "frame_count_match": len(current.rendered_hashes) == len(reference.rendered_hashes),
        "frame_hamming_distances": [],
        "max_frame_hamming": 0,
        "frames_within_threshold": True,
        "hamming_threshold": hamming_threshold,
    }
    if not report["frame_count_match"]:
        report["frames_within_threshold"] = False
        return report
    for h_cur, h_ref in zip(current.rendered_hashes, reference.rendered_hashes):
        d = hamming_distance(h_cur, h_ref)
        report["frame_hamming_distances"].append(d)
        report["max_frame_hamming"] = max(report["max_frame_hamming"], d)
        if d > hamming_threshold:
            report["frames_within_threshold"] = False
    return report


def export_reference(state: WorldState, output_path: str | Path) -> ReferenceData:
    """Compute and save a reference JSON for ``state``. Returns the
    ReferenceData that was written."""
    ref = compute_reference(state, render=True)
    ref.save(output_path)
    return ref
