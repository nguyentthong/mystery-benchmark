"""
World state and simulation engine

The ``WorldState`` holds the complete ground truth. The ``MysteryEnvironment``
wraps it with an agent-facing API that returns *observations* (partial information)
and accepts *actions*.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

import numpy as np

from mystery_world import ComplexityConfig
from mystery_world.entities import (
    Character,
    CharacterRole,
    EdgeArgument,
    EdgeRelevance,
    EdgeType,
    Evidence,
    EvidenceState,
    Location,
    RouteConstraint,
    ScoreResult,
    TemporalLabel,
    TimelineEntry,
    WitnessStatement,
    WorldObject,
)
from mystery_world.events import WorldEvent, process_all_events
from mystery_world.npc_responder import NPCResponder

# ---------------------------------------------------------------------------
# Observation paraphrase pools (Tier B: surface diversity for `observe_location`)
#
# Selection is a deterministic function of (world seed, slot, entity id) so the
# same case always renders the same way. Vary form, never truth conditions:
# every variant within a slot must use the same placeholder set.
# ---------------------------------------------------------------------------

_OBS_LOC_OPENER = [
    "You are in the {loc_name}. {description}",
    "You enter the {loc_name}. {description}",
    "Location: {loc_name}. {description}",
    "Now in the {loc_name}. {description}",
    "You step into the {loc_name}. {description}",
    "Surveying the {loc_name}. {description}",
    "Currently: the {loc_name}. {description}",
    "{loc_name}. {description}",
    "You find yourself in the {loc_name}. {description}",
    "Crossing into the {loc_name}. {description}",
    "The {loc_name}: {description}",
    "Position fixed -- {loc_name}. {description}",
    "Inside the {loc_name} now. {description}",
    "You stand in the {loc_name}. {description}",
    "Setting: {loc_name}. {description}",
    "Arrived at the {loc_name}. {description}",
    "Working out of the {loc_name}. {description}",
    "Surveying surroundings -- {loc_name}. {description}",
    "Investigation continues in the {loc_name}. {description}",
    "Footsteps carry you into the {loc_name}. {description}",
    "Took up post in the {loc_name}. {description}",
    "Stationed in the {loc_name}. {description}",
    "You pause in the {loc_name}. {description}",
    "Established in the {loc_name}. {description}",
    "Investigating the {loc_name}. {description}",
    "On the threshold of the {loc_name}. {description}",
    "Inside: {loc_name}. {description}",
    "Drawn into the {loc_name}. {description}",
    "Detective's current room: the {loc_name}. {description}",
    "Holding position in the {loc_name}. {description}",
]

_OBS_BODY_PRESENT = [
    "The body of {full_name} lies here.",
    "{full_name}'s body is here.",
    "You see {full_name}'s body.",
    "{full_name} lies dead here.",
    "The remains of {full_name} are here.",
    "The corpse of {full_name} is on the floor.",
    "{full_name}, lifeless, is here.",
    "Here is the body of {full_name}.",
    "The deceased: {full_name}, here.",
    "{full_name}'s body has been left here.",
    "{full_name}'s lifeless form lies in this room.",
    "You find {full_name} dead here.",
    "The body -- {full_name} -- is in this room.",
    "{full_name} -- dead -- here.",
    "Body of {full_name} on the floor.",
    "{full_name}'s body has not been moved from here.",
    "Deceased: {full_name}. Location confirmed.",
    "You note the body of {full_name}.",
    "{full_name} lies still on the ground here.",
    "The corpse here is {full_name}.",
    "Casualty in this room: {full_name}.",
    "{full_name} -- victim -- present.",
    "There, on the floor: {full_name}.",
    "{full_name} is here, no longer breathing.",
    "Body present: {full_name}.",
    "Recovered remains of {full_name} -- here.",
    "{full_name}: dead in this very room.",
    "You confirm {full_name}'s body in this location.",
    "{full_name} has come to rest here.",
    "Cold and still: {full_name}.",
]

_OBS_CHARS_PRESENT = [
    "Present here: {names}.",
    "Also in the room: {names}.",
    "You see {names} here.",
    "Here with you: {names}.",
    "In this room with you: {names}.",
    "{names} are here.",
    "Other people present: {names}.",
    "The room contains: {names}.",
    "Sharing this room: {names}.",
    "You notice {names} in the room.",
    "Currently in this room: {names}.",
    "Witnesses to your arrival: {names}.",
    "Standing in the room: {names}.",
    "{names} -- here in the room.",
    "Here: {names}.",
    "Persons in the room: {names}.",
    "Persons present: {names}.",
    "You spot {names} here.",
    "Eyes on you: {names}.",
    "{names} are in the room with you.",
    "Already here: {names}.",
    "You find {names} present.",
    "Among those present: {names}.",
    "Within sight: {names}.",
    "Others in this room: {names}.",
    "You count {names} among those here.",
    "In the company of: {names}.",
    "Currently sharing the room: {names}.",
    "Here, alongside you: {names}.",
    "Visible in the room: {names}.",
]

_OBS_PHYSICAL = [
    "{full_name} -- {build}, {hair}, {hands}.",
    "{full_name}: {build}, {hair}, {hands}.",
    "{full_name} ({build}, {hair}, {hands}).",
    "{full_name} -- {build}; you note {hair} and {hands}.",
    "Take in {full_name}: {build}, with {hair}, with {hands}.",
    "{full_name} -- standing {build}, with {hair}, {hands}.",
    "You note: {full_name} -- {build}, {hair}, {hands}.",
    "{full_name} -- a {build} figure, with {hair} and {hands}.",
    "Description of {full_name}: {build}, {hair}, {hands}.",
    "{full_name}, who is {build}, has {hair} and {hands}.",
    "Your eye is drawn to {full_name} -- {build}, {hair}, {hands}.",
    "{full_name} appears {build}; {hair}, {hands}.",
    "{full_name}, {build}, {hair}, {hands}.",
    "Visual on {full_name}: {build}, {hair}, {hands}.",
    "{full_name} -- you note the {build} frame, the {hair}, the {hands}.",
    "{full_name}: a {build} person with {hair} and {hands}.",
    "Build: {build}. Hair: {hair}. Hands: {hands}. -- {full_name}.",
    "{full_name} -- {build}. {hair}. {hands}.",
    "You observe {full_name}: {build}, with {hair}, with {hands}.",
    "{full_name} cuts a {build} figure, {hair}, {hands}.",
    "{full_name}'s appearance: {build}, {hair}, {hands}.",
    "{full_name} -- {build}; {hair}; {hands}.",
    "Looking at {full_name}: {build}, {hair}, {hands}.",
    "Notes on {full_name}: {build}, {hair}, {hands}.",
    "{full_name} (notably {build}, with {hair} and {hands}).",
    "{full_name} presents as {build}, with {hair} and {hands}.",
    "{full_name} -- {build} and noticeable, {hair}, {hands}.",
    "{full_name}: {build}; you mark the {hair} and the {hands}.",
    "Reviewing {full_name}: {build}, {hair}, {hands}.",
    "{full_name} stands out: {build}, {hair}, {hands}.",
]

_OBS_VISIBLE_OBJECTS = [
    "You notice: {obj_names}.",
    "Items in view: {obj_names}.",
    "You see {obj_names}.",
    "Visible objects: {obj_names}.",
    "On display here: {obj_names}.",
    "In the room: {obj_names}.",
    "You spot {obj_names}.",
    "Objects of note: {obj_names}.",
    "Items here: {obj_names}.",
    "Visible to you: {obj_names}.",
    "Within sight: {obj_names}.",
    "You catch sight of {obj_names}.",
    "On hand: {obj_names}.",
    "Catalogued in the room: {obj_names}.",
    "Items present: {obj_names}.",
    "Your eye lands on {obj_names}.",
    "Standing out: {obj_names}.",
    "You make out {obj_names}.",
    "What you see: {obj_names}.",
    "You take note of {obj_names}.",
    "In view: {obj_names}.",
    "Objects in the room: {obj_names}.",
    "You can see {obj_names}.",
    "Visible items: {obj_names}.",
    "On display: {obj_names}.",
    "Among the visible: {obj_names}.",
    "Plain to see: {obj_names}.",
    "Objects you observe: {obj_names}.",
    "Recorded objects: {obj_names}.",
    "{obj_names} -- visible from where you stand.",
]

_OBS_EXITS = [
    "Exit leads to {adj_names}.",
    "Exits to: {adj_names}.",
    "Adjoining rooms: {adj_names}.",
    "From here you can reach: {adj_names}.",
    "Doors lead to: {adj_names}.",
    "You can move to: {adj_names}.",
    "Connected to: {adj_names}.",
    "Adjacent rooms: {adj_names}.",
    "Passages to: {adj_names}.",
    "Onward routes: {adj_names}.",
    "Available exits: {adj_names}.",
    "From this room: {adj_names}.",
    "Doorways open onto: {adj_names}.",
    "You can walk to: {adj_names}.",
    "Routes from here: {adj_names}.",
    "This room connects to: {adj_names}.",
    "Egress: {adj_names}.",
    "Onwards: {adj_names}.",
    "Walkable from here: {adj_names}.",
    "Neighbouring rooms: {adj_names}.",
    "You see doors to: {adj_names}.",
    "Reachable: {adj_names}.",
    "From your current spot: {adj_names}.",
    "Adjacent: {adj_names}.",
    "Outbound passages: {adj_names}.",
    "Step through to: {adj_names}.",
    "You can head to: {adj_names}.",
    "Branching off: {adj_names}.",
    "Other rooms within reach: {adj_names}.",
    "Doors here lead to: {adj_names}.",
]

_OBS_WEATHER = [
    "The weather is {weather}.",
    "Outside: {weather}.",
    "Conditions: {weather}.",
    "Weather report: {weather}.",
    "It is {weather} outside.",
    "Currently: {weather}.",
    "The skies show {weather}.",
    "Out of doors, the weather is {weather}.",
    "{weather} prevails outside.",
    "Beyond the walls: {weather}.",
    "The estate is enveloped in {weather}.",
    "Weather: {weather}.",
    "{weather} -- as best you can tell.",
    "Outside conditions: {weather}.",
    "The atmosphere offers {weather}.",
    "Outdoors: {weather}.",
    "You note {weather} outside.",
    "Forecast (as it stands): {weather}.",
    "The grounds lie under {weather}.",
    "Weather conditions read as {weather}.",
    "Through the windows: {weather}.",
    "Above the estate: {weather}.",
    "{weather} marks the night.",
    "{weather} hangs over the grounds.",
    "Beyond: {weather}.",
    "Conditions outdoors: {weather}.",
    "The air outside speaks of {weather}.",
    "Outside the walls: {weather}.",
    "{weather} dominates the outdoors.",
    "From outside drifts the sense of {weather}.",
]


def _pick_template(seed: int, slot: str, *keys: str, n: int) -> int:
    """Stable index selection from (seed, slot, entity-keys). SHA-256 so it
    survives across processes; Python's built-in hash() is salted."""
    payload = f"{seed}|{slot}|" + "|".join(keys)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return int(digest, 16) % n


def _render_obs(seed: int, slot: str, pool: list[str], key: str, **fmt: Any) -> str:
    return pool[_pick_template(seed, slot, key, n=len(pool))].format(**fmt)


# ---------------------------------------------------------------------------
# Action-result paraphrase pools (Tier B). Selected at runtime by
# (seed, slot, action_count) so the same episode never repeats the same
# wrapper twice in a row, but is deterministic given the same seed.
# ---------------------------------------------------------------------------

_ACT_MOVE = [
    "You move to the {loc}.",
    "Now in the {loc}.",
    "Walked to the {loc}.",
    "Headed into the {loc}.",
    "You enter the {loc}.",
    "Crossing into the {loc}.",
    "Stepped into the {loc}.",
    "Arrived at the {loc}.",
    "You proceed to the {loc}.",
    "Now positioned in the {loc}.",
    "You make your way to the {loc}.",
    "Crossed over to the {loc}.",
    "You shift to the {loc}.",
    "Took the route to the {loc}.",
    "You walk to the {loc}.",
    "Now standing in the {loc}.",
    "You head to the {loc}.",
    "Moved across to the {loc}.",
    "You proceed into the {loc}.",
    "Stepped through to the {loc}.",
    "You take up post in the {loc}.",
    "Worked your way to the {loc}.",
    "You drift into the {loc}.",
    "Walked across to the {loc}.",
    "You arrive in the {loc}.",
    "Now within the {loc}.",
    "You go to the {loc}.",
    "Pushed on to the {loc}.",
    "Reached the {loc}.",
    "You head into the {loc}.",
]

_ACT_EXAMINE_PREFIX = [
    "You examine the {obj}. {desc}",
    "Inspecting the {obj}: {desc}",
    "You take a closer look at the {obj}. {desc}",
    "Examining the {obj}: {desc}",
    "Closer inspection of the {obj}: {desc}",
    "You investigate the {obj}. {desc}",
    "Detailed look at the {obj}: {desc}",
    "Up close with the {obj}: {desc}",
    "You scrutinise the {obj}. {desc}",
    "Examination of the {obj} shows: {desc}",
    "You handle the {obj} carefully. {desc}",
    "Looking over the {obj}: {desc}",
    "You study the {obj}. {desc}",
    "Inspection of the {obj}: {desc}",
    "Going over the {obj}: {desc}",
    "Notes on the {obj}: {desc}",
    "You consider the {obj}. {desc}",
    "Close look at the {obj}: {desc}",
    "Surveying the {obj}: {desc}",
    "You take note of the {obj}. {desc}",
    "Picking up the {obj}: {desc}",
    "Reviewing the {obj}: {desc}",
    "You turn the {obj} over. {desc}",
    "Eyes on the {obj}: {desc}",
    "Taking stock of the {obj}: {desc}",
    "You give the {obj} a careful look. {desc}",
    "Scrutiny of the {obj}: {desc}",
    "You appraise the {obj}. {desc}",
    "Once-over of the {obj}: {desc}",
    "You look the {obj} over. {desc}",
]

_ACT_EVIDENCE_TAG = [
    "[Evidence {id}] {desc}",
    "[Clue {id}] {desc}",
    "[Tag {id}] {desc}",
    "[Item {id}] {desc}",
    "[Find {id}] {desc}",
    "[Logged {id}] {desc}",
    "[Record {id}] {desc}",
    "[Discovered: {id}] {desc}",
    "[New evidence {id}] {desc}",
    "[Catalogue {id}] {desc}",
    "[Filed {id}] {desc}",
    "[Marker {id}] {desc}",
    "[ID {id}] {desc}",
    "[E-{id}] {desc}",
    "[Ev {id}] {desc}",
    "Evidence found ({id}): {desc}",
    "Logged: [{id}] {desc}",
    "Catalogued ({id}): {desc}",
    "Recorded as {id}: {desc}",
    "New find -- {id}: {desc}",
    "Note ({id}): {desc}",
    "Tagged {id}: {desc}",
    "Filed under {id}: {desc}",
    "Acquired evidence {id}: {desc}",
    "Picked up [{id}]: {desc}",
    "Logged into evidence ({id}): {desc}",
    "Item {id} added: {desc}",
    "[Evidence ID {id}] {desc}",
    "++ {id} ++ {desc}",
    "Marked {id}: {desc}",
]

_ACT_TAKE = [
    "You take the {obj}.",
    "Picked up the {obj}.",
    "You collect the {obj}.",
    "{obj} taken.",
    "You add the {obj} to your collection.",
    "Bagged the {obj}.",
    "You secure the {obj}.",
    "The {obj} is now in your possession.",
    "You pocket the {obj}.",
    "Acquired the {obj}.",
    "You retrieve the {obj}.",
    "Logged: you have taken the {obj}.",
    "{obj}: collected.",
    "You lift the {obj}.",
    "You take possession of the {obj}.",
    "Stowed the {obj}.",
    "The {obj} is yours.",
    "You scoop up the {obj}.",
    "Added: {obj}.",
    "You take hold of the {obj}.",
    "{obj} now in evidence.",
    "Taken: {obj}.",
    "You gather the {obj}.",
    "Secured the {obj}.",
    "Slipped the {obj} into evidence.",
    "Item taken -- {obj}.",
    "You claim the {obj}.",
    "You take possession: {obj}.",
    "{obj} added to your collection.",
    "You make off with the {obj}.",
]

_ACT_WAIT = [
    "You wait and observe. Time passes.",
    "You stand still. The clock ticks on.",
    "Time passes -- you let it.",
    "You hold position. Time moves on.",
    "Pausing. Time slips by.",
    "You stay where you are. Minutes pass.",
    "Standing by. Time passes.",
    "You wait. The night moves on.",
    "Time elapses while you watch.",
    "You linger. Nothing immediately changes.",
    "You wait and listen.",
    "Time passes; you hold the room.",
    "You bide your time.",
    "You stay put as time moves on.",
    "You let the moment pass.",
    "Standing watch as time advances.",
    "You take a beat. Time slips on.",
    "You wait it out.",
    "You hold still. Minutes go by.",
    "Time advances. You remain.",
    "You stay your hand. Time passes.",
    "You watch and wait.",
    "Pausing here -- time moves forward.",
    "You stand by. Time passes regardless.",
    "Time keeps moving while you wait.",
    "You remain. The hour shifts on.",
    "You take no action. Time runs on.",
    "Holding for now. Time elapses.",
    "You let time pass uninterrupted.",
    "You wait. The estate breathes.",
]

_ACT_INVENTORY_HEADER = [
    "Evidence collected:",
    "Your evidence list:",
    "Currently in evidence:",
    "Catalogued evidence:",
    "Evidence on file:",
    "Items in your collection:",
    "Held evidence:",
    "Logged evidence so far:",
    "Evidence inventory:",
    "Recorded evidence:",
    "Evidence summary:",
    "Collected so far:",
    "Your case file holds:",
    "In your possession:",
    "On the evidence sheet:",
    "Evidence to date:",
    "Recovered evidence:",
    "Held items of evidence:",
    "Items logged:",
    "Catalogue of evidence:",
    "Your evidence locker:",
    "Filed evidence:",
    "Evidence accumulated:",
    "Evidence on hand:",
    "Currently held:",
    "Items in the case file:",
    "Listed evidence:",
    "Recorded clues:",
    "Inventory of evidence:",
    "Tracked evidence:",
]

_ACT_ANALYZE_PREFIX = [
    "You analyze {ev}. {assessment}",
    "Analysis of {ev}: {assessment}",
    "Forensic look at {ev}: {assessment}",
    "Examining {ev} for age: {assessment}",
    "Lab assessment of {ev}: {assessment}",
    "You run analysis on {ev}. {assessment}",
    "Test on {ev} returns: {assessment}",
    "Working through {ev}: {assessment}",
    "Studying {ev} closely: {assessment}",
    "You evaluate {ev}. {assessment}",
    "Detailed analysis of {ev}: {assessment}",
    "Closer scrutiny of {ev}: {assessment}",
    "{ev} -- analyzed: {assessment}",
    "Analytical pass on {ev}: {assessment}",
    "Forensic verdict on {ev}: {assessment}",
    "You probe {ev}. {assessment}",
    "Subjecting {ev} to scrutiny: {assessment}",
    "Lab notes on {ev}: {assessment}",
    "Bench test of {ev}: {assessment}",
    "{ev} examined under lens: {assessment}",
    "Analysis returns -- {ev}: {assessment}",
    "Putting {ev} under analysis: {assessment}",
    "{ev}, on analysis: {assessment}",
    "Findings on {ev}: {assessment}",
    "You investigate {ev}. {assessment}",
    "Forensic study of {ev}: {assessment}",
    "Reading on {ev}: {assessment}",
    "{ev} taken in for analysis: {assessment}",
    "Detail work on {ev}: {assessment}",
    "Analytical findings on {ev}: {assessment}",
]

_ACT_FRESH_FRESH = [
    "This trace appears very recent -- consistent with the time of the murder.",
    "Reading is fresh -- this aligns with the time of the killing.",
    "The trace looks new -- right at the murder window.",
    "Freshly laid -- timing matches the murder.",
    "Recent contact -- consistent with the murder timing.",
    "This is fresh material -- placed at the time of the crime.",
    "The trace is current -- consistent with the murder window.",
    "Very recent contact -- inside the murder window.",
    "Reading: trace is fresh -- matches the time of death.",
    "This trace was laid recently -- consistent with the killing.",
    "The age of the trace puts it at the murder.",
    "Trace appears unmistakably fresh -- consistent with the crime.",
    "Lab confirms: the trace is recent -- aligns with the murder.",
    "Pristine, recent material -- contemporary with the killing.",
    "The trace shows no aging -- it sits at the murder time.",
    "Reads as fresh -- contemporary with the killing.",
    "Evidence is recent -- it lines up with the murder window.",
    "This trace is newly placed -- consistent with the murder.",
    "Under examination, the trace is fresh -- murder-window timing.",
    "Trace freshness confirmed -- time of contact matches the murder.",
    "The trace is current and contemporary with the killing.",
    "Fresh deposit -- placed at or near the murder time.",
    "Trace dating: recent, in the murder window.",
    "This is recent contact -- right at the killing.",
    "Reading the freshness: the trace is contemporary with the murder.",
    "Timestamp on the trace: at the murder.",
    "The trace's age aligns it with the murder.",
    "Verdict: trace is fresh, dated to the killing.",
    "Trace is current -- contemporary with the act.",
    "Forensic dating places this trace at the murder.",
]

_ACT_FRESH_STALE = [
    "This trace is old -- clearly predates the murder by a significant margin.",
    "Reading: the trace is stale -- well before the killing.",
    "The trace is aged -- it predates the murder.",
    "Old material -- laid down well before the killing.",
    "Stale -- deposited substantially before the murder.",
    "The trace is too old to relate to the murder.",
    "Aged contact -- predates the murder window.",
    "This trace was placed long before the murder.",
    "Material is stale -- not contemporary with the killing.",
    "Findings: trace is old -- prior to the murder.",
    "Reads stale -- well outside the murder window.",
    "Lab confirms: trace was laid before the killing.",
    "Trace aging is significant -- predates the murder.",
    "This is old residue -- not the killer's recent passage.",
    "The trace's age places it well before the killing.",
    "Stale deposit -- pre-existing the crime.",
    "Trace is dated -- before the murder window.",
    "Reading: aged trace -- not contemporary with the murder.",
    "Verdict: this trace is old -- it predates the killing.",
    "Old material -- well prior to the murder.",
    "Trace is plainly stale -- predates the killing.",
    "Forensic dating: old -- well before the murder.",
    "This is residual material -- it predates the killing.",
    "Trace is clearly aged -- it sits before the murder.",
    "Reads: stale -- well outside the murder window.",
    "Trace's age rules it out for the murder.",
    "The deposit is old -- not from the killing window.",
    "Findings: trace pre-existed the murder.",
    "This is aged contact -- not connected to the murder timing.",
    "Material is dated -- placed before the killing.",
]

_ACT_FRESH_AMB_RECENT = [
    "Analysis suggests this trace is relatively recent, possibly within the relevant timeframe.",
    "The trace reads as moderately fresh -- it could fall within the murder window.",
    "Findings hint the trace is fairly new -- possibly contemporary with the killing.",
    "The trace's age is unclear, but leans toward recent -- inside the murder window is possible.",
    "Reading: somewhat fresh -- the trace may date to the murder.",
    "Analysis is borderline -- the trace could be from the murder window.",
    "The trace appears reasonably fresh -- it might be contemporary with the killing.",
    "Findings tilt recent -- the trace may align with the murder.",
    "Trace dating: ambiguous, but likely within the relevant window.",
    "The trace seems fresh enough to be relevant -- but not certainly so.",
    "Possibly contemporary with the murder; the dating is not crisp.",
    "The trace might fall in the murder window, but evidence is borderline.",
    "Lab leans toward recent for this trace -- without confirming.",
    "Reads as plausibly fresh -- could be from the killing.",
    "The trace's age allows it to be from the murder, but is not conclusive.",
    "Likely recent, though the reading is ambiguous.",
    "Findings: relatively fresh, possibly within the killing window.",
    "Trace reads young, but with margin -- could be the murder.",
    "Analysis suggests recent, possibly murder-window contact.",
    "The deposit's age is borderline, leaning toward recent.",
    "Tentative reading: the trace could be from the murder.",
    "Trace appears fresh enough to be in the murder window.",
    "Forensic dating is unclear but suggestive of recent contact.",
    "May be fresh enough to count -- the dating is not definitive.",
    "Findings tilt new -- possibly contemporary with the killing.",
    "Reading: borderline-recent -- possibly within the relevant timeframe.",
    "Trace dating is loose, but consistent with the murder window.",
    "The trace's freshness is borderline; murder-window contact is possible.",
    "Looks recent enough to be relevant; certainty is low.",
    "Trace could plausibly fit the murder window.",
]

_ACT_FRESH_AMB_STALE = [
    "Analysis suggests this trace may be older than it first appears.",
    "Findings tilt the trace older than its surface suggests.",
    "Reading: the trace is likely older than it looks.",
    "The trace's age may exceed first impressions.",
    "Despite appearances, the trace is probably aged.",
    "Tentative reading: the trace is older than it seems.",
    "On closer analysis, the trace is older than the surface implies.",
    "The trace probably predates what its appearance suggests.",
    "Forensic dating leans older despite a fresh look.",
    "Reading: aged on closer inspection.",
    "The trace is plausibly older than first appearance.",
    "Closer work suggests the trace pre-dates the surface read.",
    "Findings tilt the trace toward older.",
    "Analysis: probably older than the trace looks.",
    "The trace may have been laid earlier than it appears.",
    "Lab suggests the trace is older than its presentation.",
    "Closer inspection: trace is aged despite first impression.",
    "The trace reads older on detailed analysis.",
    "Verdict: probably older than its surface state.",
    "Trace may be from before the murder window despite looking fresh.",
    "On the bench, the trace dates older than at glance.",
    "Findings: the trace was likely laid earlier than it looks.",
    "Analysis nudges the trace toward older.",
    "Reading: trace older than initial appearance.",
    "Detailed work pushes the trace's age back.",
    "The trace is plausibly older than it first showed.",
    "On reflection, the trace is older than it seems.",
    "Trace dating is probably older than the surface read.",
    "Lab: the trace likely predates first appearance.",
    "Findings push the trace's age beyond first impression.",
]

_ACT_FRESH_NORELEV = [
    "You analyze {ev_name}. No forensically relevant contact traces found.",
    "Analysis of {ev_name}: nothing forensically meaningful.",
    "{ev_name}: no relevant contact traces detected.",
    "Examining {ev_name} -- no forensic markers of interest.",
    "{ev_name} returns no relevant contact data.",
    "You run {ev_name} through analysis. Nothing forensically relevant.",
    "Lab work on {ev_name} comes back blank.",
    "{ev_name}: no usable contact traces.",
    "Analysis fails to surface anything on {ev_name}.",
    "{ev_name} is examined; no forensic relevance found.",
    "{ev_name} carries no contact traces of note.",
    "Findings on {ev_name}: no contact data of interest.",
    "Lab reports {ev_name} is forensically silent.",
    "Inspection of {ev_name} reveals no relevant traces.",
    "{ev_name}: no forensic linkage found.",
    "Your work on {ev_name} returns no usable data.",
    "{ev_name} examined -- nothing forensically tied to the case.",
    "No forensic signal from {ev_name}.",
    "Analysis comes up empty on {ev_name}.",
    "{ev_name} yields nothing forensically relevant.",
    "Lab: no contact relevance on {ev_name}.",
    "{ev_name} carries no forensic markers worth noting.",
    "You analyze {ev_name} -- and find nothing.",
    "{ev_name} comes up clean of forensic links.",
    "No relevant traces present on {ev_name}.",
    "{ev_name}: bench analysis returns nothing.",
    "Analysis of {ev_name} draws a blank.",
    "Forensic look at {ev_name}: nothing of note.",
    "{ev_name} -- no contact traces of forensic value.",
    "Inspection finds nothing on {ev_name}.",
]

_ACT_TALK_WRAPPER = [
    '{char}: "{response}"',
    '{char} replies: "{response}"',
    '{char} says: "{response}"',
    '"{response}" -- {char}.',
    '{char} answers: "{response}"',
    '{char} responds: "{response}"',
    'You hear from {char}: "{response}"',
    '{char} tells you: "{response}"',
    '{char} ({response}).',
    '{char} offers: "{response}"',
    'In response, {char}: "{response}"',
    '{char} -- "{response}"',
    'From {char}: "{response}"',
    '{char} returns: "{response}"',
    '{char} reflects: "{response}"',
    '{char} states: "{response}"',
    'After a moment, {char}: "{response}"',
    '{char} explains: "{response}"',
    '{char} remarks: "{response}"',
    '{char}, after a pause: "{response}"',
    '"{response}" answers {char}.',
    '"{response}" -- says {char}.',
    'Hearing the question, {char}: "{response}"',
    '{char} confides: "{response}"',
    '{char} ventures: "{response}"',
    '"{response}," replies {char}.',
    '"{response}," {char} says.',
    '{char} (in answer): "{response}"',
    '{char}\'s answer: "{response}"',
    '{char} responds with: "{response}"',
]

_ACT_TRAVEL_SUCCESS = [
    "The shortest open route from the {from_room} to the {to_room}{qualifier} takes {steps} step{plural} ({minutes} minutes).",
    "Open route {from_room} -> {to_room}{qualifier}: {steps} step{plural}, {minutes} minutes.",
    "Travel time {from_room} to {to_room}{qualifier}: {steps} step{plural} ({minutes} min).",
    "Best path {from_room} to {to_room}{qualifier} is {steps} step{plural}, taking {minutes} minutes.",
    "From the {from_room} to the {to_room}{qualifier}: {steps} step{plural}, {minutes} minutes total.",
    "Route opens {from_room} -> {to_room}{qualifier} in {steps} step{plural} ({minutes} min).",
    "Estimate: {steps} step{plural} ({minutes} minutes) from the {from_room} to the {to_room}{qualifier}.",
    "Path of {steps} step{plural} ({minutes} min) connects the {from_room} and the {to_room}{qualifier}.",
    "Shortest passage from the {from_room} to the {to_room}{qualifier}: {steps} step{plural}, {minutes} minutes.",
    "Travel: {from_room} -> {to_room}{qualifier} = {steps} step{plural} ({minutes} min).",
    "{steps} step{plural} ({minutes} minutes) from the {from_room} to the {to_room}{qualifier}.",
    "Reachable in {steps} step{plural} ({minutes} minutes): {from_room} -> {to_room}{qualifier}.",
    "From the {from_room}, the {to_room}{qualifier} is {steps} step{plural} ({minutes} min).",
    "It takes {steps} step{plural} ({minutes} minutes) to walk {from_room} to {to_room}{qualifier}.",
    "Open passage: {from_room} -> {to_room}{qualifier}, {steps} step{plural}, {minutes} minutes.",
    "Walking from the {from_room} to the {to_room}{qualifier}: {steps} step{plural} ({minutes} min).",
    "Route ({from_room} -> {to_room}{qualifier}): {steps} step{plural} / {minutes} min.",
    "Path estimate: {steps} step{plural} ({minutes} minutes) {from_room} -> {to_room}{qualifier}.",
    "{from_room} -> {to_room}{qualifier}: shortest open path is {steps} step{plural} ({minutes} minutes).",
    "Minimum travel from the {from_room} to the {to_room}{qualifier}: {steps} step{plural}, {minutes} min.",
    "Logged path {from_room} -> {to_room}{qualifier}: {steps} step{plural} ({minutes} min).",
    "Distance: {steps} step{plural} ({minutes} minutes) from {from_room} to {to_room}{qualifier}.",
    "Open route is {steps} step{plural}, {minutes} minutes ({from_room} -> {to_room}{qualifier}).",
    "Travel computation: {from_room} -> {to_room}{qualifier} -- {steps} step{plural}, {minutes} min.",
    "{from_room} reaches {to_room}{qualifier} in {steps} step{plural} ({minutes} minutes).",
    "Path resolves: {from_room} -> {to_room}{qualifier} = {steps} step{plural} ({minutes} min).",
    "From {from_room} to {to_room}{qualifier} is {steps} step{plural}, equating to {minutes} minutes.",
    "Walk from the {from_room} to the {to_room}{qualifier}: {steps} step{plural} ({minutes} minutes).",
    "Open shortest path: {from_room} -> {to_room}{qualifier}, {steps} step{plural}, {minutes} min.",
    "Routing: {from_room} -> {to_room}{qualifier} in {steps} step{plural} ({minutes} minutes).",
]

_ACT_ROUTE_OPEN = [
    "The passage between the {from_room} and the {to_room} was open at {at_time}.",
    "At {at_time} the passage between the {from_room} and the {to_room} was open.",
    "Status at {at_time}: the {from_room} -- {to_room} passage was open.",
    "{from_room} -- {to_room} passage at {at_time}: open.",
    "Records show the {from_room} -- {to_room} passage open at {at_time}.",
    "Passage from {from_room} to {to_room} at {at_time}: clear.",
    "At {at_time}, the door between the {from_room} and the {to_room} was open.",
    "{from_room} <-> {to_room} at {at_time}: passable.",
    "Open at {at_time}: {from_room} -- {to_room} passage.",
    "{from_room} -- {to_room} was open for transit at {at_time}.",
    "Reachable at {at_time}: {from_room} -- {to_room}.",
    "Confirmed open at {at_time}: passage from the {from_room} to the {to_room}.",
    "{from_room}/{to_room} passage was unblocked at {at_time}.",
    "At {at_time} the {from_room} -- {to_room} corridor was open.",
    "Logs: {from_room} -- {to_room} passage open at {at_time}.",
    "Route {from_room} -- {to_room}: open at {at_time}.",
    "{from_room} -- {to_room} passable at {at_time}.",
    "At {at_time} you could pass from the {from_room} to the {to_room} directly.",
    "Direct route {from_room} -- {to_room}: open at {at_time}.",
    "{at_time}: the corridor between the {from_room} and the {to_room} was clear.",
    "Open transit at {at_time}: {from_room} -- {to_room}.",
    "Passage check: {from_room} -- {to_room} open at {at_time}.",
    "At {at_time} the door between the rooms was open.",
    "Confirmed: {from_room} -- {to_room} unobstructed at {at_time}.",
    "{from_room} and {to_room} were directly connected at {at_time}.",
    "Open passage between the {from_room} and the {to_room} at {at_time}.",
    "{at_time}: clear passage {from_room} -- {to_room}.",
    "At {at_time} the {from_room} -- {to_room} corridor allowed transit.",
    "Routing at {at_time}: {from_room} -- {to_room} open.",
    "Records confirm: {from_room} -- {to_room} passage open at {at_time}.",
]

_ACT_ROUTE_CLOSED = [
    "The direct passage between the {from_room} and the {to_room} was closed at {at_time}: {reason}.",
    "At {at_time} the {from_room} -- {to_room} passage was closed: {reason}.",
    "{from_room} -- {to_room} passage at {at_time}: blocked. Reason: {reason}.",
    "Closed at {at_time}: {from_room} -- {to_room} ({reason}).",
    "Records show {from_room} -- {to_room} passage shut at {at_time}: {reason}.",
    "Status: {from_room} -- {to_room} passage closed at {at_time} ({reason}).",
    "{from_room}/{to_room} sealed at {at_time}. Reason: {reason}.",
    "At {at_time} the corridor between the {from_room} and the {to_room} was blocked: {reason}.",
    "Direct route {from_room} -- {to_room}: closed at {at_time}. {reason}.",
    "{from_room} -- {to_room} barred at {at_time}: {reason}.",
    "Logs: {from_room} -- {to_room} passage closed at {at_time}. {reason}.",
    "{at_time}: {from_room} -- {to_room} passage was unavailable -- {reason}.",
    "Passage check: {from_room} -- {to_room} closed at {at_time} ({reason}).",
    "At {at_time} you could not pass directly from the {from_room} to the {to_room}: {reason}.",
    "Closed corridor at {at_time}: {from_room} -- {to_room}. Reason: {reason}.",
    "{from_room} and {to_room} disconnected at {at_time}. {reason}.",
    "{at_time}: {from_room} -- {to_room} blocked, owing to {reason}.",
    "The door between the {from_room} and the {to_room} was shut at {at_time}: {reason}.",
    "{from_room} -- {to_room}: closed at {at_time} -- {reason}.",
    "Confirmed closed: {from_room} -- {to_room} at {at_time}, {reason}.",
    "At {at_time}, no direct passage from the {from_room} to the {to_room} ({reason}).",
    "{from_room} -- {to_room} corridor was sealed at {at_time}: {reason}.",
    "Routing at {at_time}: {from_room} -- {to_room} closed -- {reason}.",
    "Logs: passage from the {from_room} to the {to_room} blocked at {at_time}. {reason}.",
    "{from_room} -- {to_room} barred at {at_time}; cause: {reason}.",
    "At {at_time}, the {from_room} -- {to_room} passage was not available: {reason}.",
    "Direct route {from_room} -- {to_room} unusable at {at_time} ({reason}).",
    "{at_time}: corridor {from_room} -- {to_room} sealed. {reason}.",
    "{from_room} -- {to_room} passage closed at {at_time}, due to {reason}.",
    "Records: {from_room} -- {to_room} blocked at {at_time}: {reason}.",
]


def _render_action(seed: int, slot: str, pool: list[str], step_key: int, **fmt: Any) -> str:
    """Pick + format an action-result paraphrase. step_key is action_count
    so consecutive actions diversify; same (seed, step) renders the same."""
    return pool[_pick_template(seed, slot, str(step_key), n=len(pool))].format(**fmt)


# ---------------------------------------------------------------------------
# Agent action space
# ---------------------------------------------------------------------------

class AgentAction(Enum):
    MOVE = auto()               # move to adjacent location
    EXAMINE_LOCATION = auto()               # look around current room
    EXAMINE_OBJECT = auto()               # inspect a specific object
    TALK_TO = auto()               # interrogate a character
    ACCUSE = auto()               # make final accusation
    WAIT = auto()               # pass one time step
    CHECK_INVENTORY = auto()               # review collected clues
    TAKE_OBJECT = auto()               # pick up portable object
    ANALYZE = auto()         # temporal assessment of one piece of evidence
    TRAVEL_TIME = auto()     # minimum steps between two rooms (constraint-aware)
    CHECK_ROUTE = auto()     # was a specific passage open at a given clock time?

@dataclass
class ActionResult:
    success: bool = True
    observation: str = ""
    evidence_found: list[str] = field(default_factory=list)   # evidence IDs
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# WorldState (ground truth)
# ---------------------------------------------------------------------------

@dataclass
class WorldState:
    """Complete ground-truth state of the mystery world."""

    seed: int = 0
    config: ComplexityConfig = field(default_factory=ComplexityConfig)

    # --- Entity registries (id -> entity) ---
    locations: dict[str, Location] = field(default_factory=dict)
    characters: dict[str, Character] = field(default_factory=dict)
    objects: dict[str, WorldObject] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)

    # --- Temporal ---
    current_step: int = 0
    weather: str = "clear"
    ground_truth_timeline: list[TimelineEntry] = field(default_factory=list)
    event_log: list[WorldEvent] = field(default_factory=list)
    
    # --- Solution ---
    culprit_id: str = ""
    victim_id: str = ""
    murder_weapon_id: str = ""
    murder_location_id: str = ""   # where the murder was committed
    body_location_id: str = ""     # where the body was found (may differ)
    murder_step: int = 0
    motive: str = ""
    # --- Locard triangle ---
    murder_timestamp: float = 0.0
    freshness_threshold: float = 2.0

    # --- Temporal reasoning ---
    witness_statements: list[WitnessStatement] = field(default_factory=list)
    route_constraints: list[RouteConstraint] = field(default_factory=list)
    anchor_events: dict[str, int] = field(default_factory=dict)


    def get_culprit(self) -> Character | None:
        return self.characters.get(self.culprit_id)


    def get_victim(self) -> Character | None:
        return self.characters.get(self.victim_id)

    
    # --- Serialisation ---
    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "config": self.config.to_dict(),
            "locations": {k: v.to_dict() for k, v in self.locations.items()},
            "characters": {k: v.to_dict() for k, v in self.characters.items()},
            "objects": {k: v.to_dict() for k, v in self.objects.items()},
            "evidence": {k: v.to_dict() for k, v in self.evidence.items()},
            "current_step": self.current_step,
            "weather": self.weather,
            "ground_truth_timeline": [e.to_dict() for e in self.ground_truth_timeline],
            "event_log": [e.to_dict() for e in self.event_log],
            "culprit_id": self.culprit_id,
            "victim_id": self.victim_id,
            "murder_weapon_id": self.murder_weapon_id,
            "murder_location_id": self.murder_location_id,
            "body_location_id": self.body_location_id,
            "murder_step": self.murder_step,
            "motive": self.motive,
            "murder_timestamp": self.murder_timestamp,
            "freshness_threshold": self.freshness_threshold,
            "witness_statements": [w.to_dict() for w in self.witness_statements],
            "route_constraints": [r.to_dict() for r in self.route_constraints],
            "anchor_events": self.anchor_events,
        }


    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    
    @classmethod
    def load(cls, path: str | Path) -> "WorldState":
        d = json.loads(Path(path).read_text())
        ws = cls(
            seed=d["seed"],
            config=ComplexityConfig.from_dict(d["config"]),
            current_step=d["current_step"],
            weather=d["weather"],
            culprit_id=d["culprit_id"],
            victim_id=d["victim_id"],
            murder_weapon_id=d["murder_weapon_id"],
            murder_location_id=d["murder_location_id"],
            body_location_id=d.get("body_location_id", d["murder_location_id"]),
            murder_step=d["murder_step"],
            murder_timestamp=d.get("murder_timestamp", float(d["murder_step"])),
            freshness_threshold=d.get("freshness_threshold", 2.0),
            anchor_events=d.get("anchor_events", {}),
            motive=d["motive"],
        )
        ws.locations = {k: Location.from_dict(v) for k, v in d["locations"].items()}
        ws.characters = {k: Character.from_dict(v) for k, v in d["characters"].items()}
        ws.objects = {k: WorldObject.from_dict(v) for k, v in d["objects"].items()}
        ws.evidence = {k: Evidence.from_dict(v) for k, v in d["evidence"].items()}
        ws.ground_truth_timeline = [TimelineEntry.from_dict(e) for e in d["ground_truth_timeline"]]
        ws.witness_statements = [
            WitnessStatement.from_dict(w) for w in d.get("witness_statements", [])
        ]
        ws.route_constraints = [
            RouteConstraint.from_dict(r) for r in d.get("route_constraints", [])
        ]
        return ws


# ---------------------------------------------------------------------------
# MysteryEnvironment (agent-facing interface)
# ---------------------------------------------------------------------------

class MysteryEnvironment:
    """
    Wraps WorldState with an agent-facing partial-observability interface.

    The agent interacts through ``step(action, **kwargs) -> ActionResult``.
    Observations are rendered as natural-language strings by the narrator.
    """
    def __init__(self, world_state: WorldState):
        self._state = world_state
        self._rng = np.random.default_rng(world_state.seed + 1000)   # offset for event RNG
        self.agent_location_id: str = ""
        self.agent_inventory: list[str] = []  # evidence IDs collected
        self.actions_taken: int = 0
        self.action_history: list[dict[str, Any]] = []
        self.is_solved: bool = False
        self.accusation_correct: bool | None = None
        self._discovered_evidence: set[str] = set()
        self._interviewed_characters: set[str] = set()
        self._last_score_result: dict[str, Any] | None = None
        self._interview_histories: dict[str, list[dict[str, str]]] = {}
        self._examine_total: int = 0
        self._examine_hit: int = 0
        self._revealed_alibi_claims: list[dict[str, str]] = []
        self._npc_responder: NPCResponder | None = None

        # Place agent at a default starting location
        if world_state.locations:
            self.agent_location_id = next(iter(world_state.locations))


    @property
    def state(self) -> WorldState:
        return self._state


    @property
    def budget_remaining(self) -> int:
        return max(0, self._state.config.max_agent_actions - self.actions_taken)

    def set_npc_responder(self, responder: NPCResponder) -> None:
        """Attach an NPC responder for LLM-powered stateful interviews."""
        self._npc_responder = responder
    
    # ------------------------------------------------------------------
    # Observation helpers
    # ------------------------------------------------------------------

    def get_current_location(self) -> Location | None:
        return self._state.locations.get(self.agent_location_id)


    def observe_location(self) -> str:
        """Return a natural-language description of the current location."""
        loc = self.get_current_location()
        if loc is None:
            return "You are nowhere."
        seed = self._state.seed
        parts = [
            _render_obs(
                seed, "loc_opener", _OBS_LOC_OPENER, loc.id,
                loc_name=loc.name, description=loc.description,
            )
        ]
        chars_here = [
            self._state.characters[cid]
            for cid in loc.characters_here
            if cid in self._state.characters and self._state.characters[cid].is_alive
        ]
        dead_here = [
            self._state.characters[cid]
            for cid in loc.characters_here
            if cid in self._state.characters and not self._state.characters[cid].is_alive
        ]
        for d in dead_here:
            parts.append(
                _render_obs(seed, "body_present", _OBS_BODY_PRESENT, d.id,
                            full_name=d.full_name)
            )
        if chars_here:
            names = ", ".join(c.full_name for c in chars_here)
            parts.append(
                _render_obs(seed, "chars_present", _OBS_CHARS_PRESENT, loc.id,
                            names=names)
            )
        for c in chars_here:
            pt = c.physical_traits
            parts.append(
                _render_obs(seed, "physical", _OBS_PHYSICAL, c.id,
                            full_name=c.full_name, build=pt.build,
                            hair=pt.hair, hands=pt.hands)
            )
        visible_objs = [
            self._state.objects[oid]
            for oid in loc.objects_here
            if oid in self._state.objects
        ]
        visible = []
        for obj in visible_objs:
            if obj.evidence_id:
                ev = self._state.evidence.get(obj.evidence_id)
                if ev and ev.state == EvidenceState.HIDDEN:
                    continue
            visible.append(obj)
        if visible:
            obj_names = ", ".join(o.name for o in visible)
            parts.append(
                _render_obs(seed, "visible_objects", _OBS_VISIBLE_OBJECTS, loc.id,
                            obj_names=obj_names)
            )
        adj_names = [
            self._state.locations[aid].name
            for aid in loc.adjacent_ids
            if aid in self._state.locations
        ]
        if adj_names:
            parts.append(
                _render_obs(seed, "exits", _OBS_EXITS, loc.id,
                            adj_names=", ".join(adj_names))
            )
        if loc.weather_exposed:
            parts.append(
                _render_obs(seed, "weather", _OBS_WEATHER, loc.id,
                            weather=self._state.weather.replace("_", " "))
            )
        return " ".join(parts)

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------
    def step(self, action: AgentAction, **kwargs: Any) -> ActionResult:
        """Execute an agent action and advance the world by one time step."""
        if self.is_solved:
            return ActionResult(success=False, observation="The case is already closed.")
        if self.budget_remaining <= 0 and action != AgentAction.ACCUSE:
            return ActionResult(success=False, observation="You have exhausted your action budget. You must ACCUSE now.")
        
        result = self._dispatch_action(action, **kwargs)

        # Record
        self.actions_taken += 1
        self.action_history.append({
            "step": self._state.current_step,
            "action": action.name,
            "kwargs": dict(kwargs),
            "success": result.success,
            "observation": result.observation[:500],
        })

        # Advance world simulation (dynamic events)
        self._state.current_step += 1
        new_events = process_all_events(self._state, self._rng)
        self._state.event_log.extend(new_events)

        return result

    
    def _dispatch_action(self, action: AgentAction, **kwargs: Any) -> ActionResult:
        handlers = {
            AgentAction.MOVE: self._handle_move,
            AgentAction.EXAMINE_LOCATION: self._handle_examine_location,
            AgentAction.EXAMINE_OBJECT: self._handle_examine_object,
            AgentAction.TALK_TO: self._handle_talk,
            AgentAction.ACCUSE: self._handle_accuse,
            AgentAction.WAIT: self._handle_wait,
            AgentAction.CHECK_INVENTORY: self._handle_inventory,
            AgentAction.TAKE_OBJECT: self._handle_take,
            AgentAction.ANALYZE:      self._handle_analyze,
            AgentAction.TRAVEL_TIME:  self._handle_travel_time,
            AgentAction.CHECK_ROUTE:  self._handle_check_route,
        }
        handler = handlers.get(action, self._handle_wait)
        return handler(**kwargs)


    def _handle_analyze(self, evidence_id: str = "", **_: Any) -> ActionResult:
        """Temporal assessment of one piece of evidence. Costs 1 action."""
        if evidence_id not in self._discovered_evidence:
            return ActionResult(False, f"Evidence '{evidence_id}' not in your collection.")
        ev = self._state.evidence.get(evidence_id)
        if ev is None:
            return ActionResult(False, f"Evidence '{evidence_id}' does not exist.")
        seed = self._state.seed
        step = self.actions_taken
        if ev.relevance is None:
            return ActionResult(
                True,
                _render_action(seed, "fresh_norelev", _ACT_FRESH_NORELEV, step, ev_name=ev.name),
            )

        rel = ev.relevance
        freshness = abs(rel.contact_timestamp - self._state.murder_timestamp)
        threshold = self._state.freshness_threshold

        if rel.surface_label == TemporalLabel.CLEARLY_FRESH:
            assessment = _render_action(seed, "fresh_fresh", _ACT_FRESH_FRESH, step)
        elif rel.surface_label == TemporalLabel.CLEARLY_STALE:
            assessment = _render_action(seed, "fresh_stale", _ACT_FRESH_STALE, step)
        else:  # AMBIGUOUS
            if freshness < threshold:
                assessment = _render_action(seed, "fresh_amb_recent", _ACT_FRESH_AMB_RECENT, step)
            else:
                assessment = _render_action(seed, "fresh_amb_stale", _ACT_FRESH_AMB_STALE, step)

        return ActionResult(
            True,
            _render_action(seed, "analyze_prefix", _ACT_ANALYZE_PREFIX, step, ev=ev.name, assessment=assessment),
        )


    def _handle_travel_time(
        self, from_room: str = "", to_room: str = "", at_time: str = "", **_: Any
    ) -> ActionResult:
        """Minimum steps between two rooms, respecting route constraints active at_time.
        If at_time omitted, returns unconstrained minimum. Costs 1 action."""
        from_loc = next(
            (l for l in self._state.locations.values() if l.name.lower() == from_room.lower()),
            None,
        )
        to_loc = next(
            (l for l in self._state.locations.values() if l.name.lower() == to_room.lower()),
            None,
        )
        if from_loc is None:
            return ActionResult(False, f"Unknown room: '{from_room}'.")
        if to_loc is None:
            return ActionResult(False, f"Unknown room: '{to_room}'.")
        if from_loc.id == to_loc.id:
            return ActionResult(True, f"You are already in the {to_room}. No travel needed.")

        active: list[RouteConstraint] = []
        if at_time:
            at_step = _clock_str_to_step(at_time, self._state.config.world_start_hour)
            if at_step is not None:
                active = [
                    rc for rc in self._state.route_constraints
                    if rc.blocked_from_step <= at_step <= rc.blocked_until_step
                ]

        steps = _shortest_path_steps_constrained(
            from_loc.id, to_loc.id, self._state.locations, active
        )
        qualifier = f" at {at_time}" if at_time else ""
        if steps is None:
            return ActionResult(
                True, f"There is no open route from the {from_room} to the {to_room}{qualifier}."
            )
        minutes = steps * self._state.config.step_duration_minutes
        return ActionResult(
            True,
            _render_action(
                self._state.seed, "travel_success", _ACT_TRAVEL_SUCCESS, self.actions_taken,
                from_room=from_room, to_room=to_room, qualifier=qualifier,
                steps=steps, plural="s" if steps != 1 else "", minutes=minutes,
            ),
        )

    def _handle_check_route(
        self, from_room: str = "", to_room: str = "", at_time: str = "", **_: Any
    ) -> ActionResult:
        """Was the direct passage between two rooms open at a given clock time?
        at_time format: '9:30 PM'. Costs 1 action."""
        from_loc = next(
            (l for l in self._state.locations.values() if l.name.lower() == from_room.lower()),
            None,
        )
        to_loc = next(
            (l for l in self._state.locations.values() if l.name.lower() == to_room.lower()),
            None,
        )
        if from_loc is None or to_loc is None:
            return ActionResult(False, f"Unknown room(s): '{from_room}', '{to_room}'.")

        step = _clock_str_to_step(at_time, self._state.config.world_start_hour)
        if step is None:
            return ActionResult(False, f"Could not parse time '{at_time}'. Use format like '9:30 PM'.")

        rc_match = next(
            (rc for rc in self._state.route_constraints
             if rc.blocked_from_step <= step <= rc.blocked_until_step
             and (
                 (rc.from_location_id == from_loc.id and rc.to_location_id == to_loc.id)
                 or (rc.from_location_id == to_loc.id and rc.to_location_id == from_loc.id)
             )),
            None,
        )
        if rc_match:
            return ActionResult(
                True,
                _render_action(
                    self._state.seed, "route_closed", _ACT_ROUTE_CLOSED, self.actions_taken,
                    from_room=from_room, to_room=to_room, at_time=at_time,
                    reason=rc_match.reason,
                ),
            )
        return ActionResult(
            True,
            _render_action(
                self._state.seed, "route_open", _ACT_ROUTE_OPEN, self.actions_taken,
                from_room=from_room, to_room=to_room, at_time=at_time,
            ),
        )
    
    def _handle_move(
        self,
        target_location: str = "",
        **_: Any
    ) -> ActionResult:
        loc = self.get_current_location()
        if loc is None:
            return ActionResult(False, "Cannot move: current location unknown.")
        # Allow moving by name or ID
        target_id = None
        for aid in loc.adjacent_ids:
            adj = self._state.locations.get(aid)
            if adj and (aid == target_location or adj.name.lower() == target_location.lower()):
                target_id = aid
                break
        if target_id is None:
            return ActionResult(False, f"Cannot move to '{target_location}'. Available {', '.join(self._state.locations[a].name for a in loc.adjacent_ids if a in self._state.locations)}.")
        self.agent_location_id = target_id
        obs = self.observe_location()
        prefix = _render_action(
            self._state.seed, "move", _ACT_MOVE, self.actions_taken,
            loc=self._state.locations[target_id].name,
        )
        return ActionResult(True, f"{prefix}\n{obs}")


    def _handle_examine_location(self, **_: Any) -> ActionResult:
        obs = self.observe_location()
        return ActionResult(True, obs)


    def _handle_examine_object(self, object_name: str = "", **_: Any) -> ActionResult:
        loc = self.get_current_location()
        if loc is None:
            return ActionResult(False, "No current location.")
        seed = self._state.seed
        step = self.actions_taken
        for oid in loc.objects_here:
            obj = self._state.objects.get(oid)
            if obj and obj.name.lower() == object_name.lower():
                self._examine_total += 1
                parts = [
                    _render_action(seed, "examine_prefix", _ACT_EXAMINE_PREFIX, step,
                                   obj=obj.name, desc=obj.description)
                ]
                if obj.evidence_id:
                    ev = self._state.evidence.get(obj.evidence_id)
                    if ev and ev.state not in (EvidenceState.HIDDEN, EvidenceState.DESTROYED):
                        self._examine_hit += 1
                        parts.append(
                            _render_action(seed, "evidence_tag", _ACT_EVIDENCE_TAG, step,
                                           id=ev.id, desc=ev.description)
                        )
                        self._discovered_evidence.add(ev.id)
                        return ActionResult(True, " ".join(parts), evidence_found=[ev.id])
                return ActionResult(True, " ".join(parts))
        return ActionResult(False, f"No object called '{object_name}' here.")

    
    def _handle_talk(self, character_name: str = "", question: str = "", **_: Any) -> ActionResult:
        loc = self.get_current_location()
        if loc is None:
            return ActionResult(False, "No current location.")
        for cid in loc.characters_here:
            char = self._state.characters.get(cid)
            if char and char.is_alive and char.full_name.lower() == character_name.lower():
                self._interviewed_characters.add(cid)
                return self._generate_interview(char, question)

        for cid, char in self._state.characters.items():
            if char.full_name.lower() == character_name.lower() and not char.is_alive:
                return ActionResult(False, f"{char.full_name} is dead and cannot be spoken to.")
        return ActionResult(False, f"'{character_name}' is not here or cannot be spoken to.")


    def _generate_interview(self, char: Character, question: str = "") -> ActionResult:
        """Stateful multi-turn interview.

        Uses NPCResponder (LLM) when attached; deterministic fallback otherwise.
        Lying is injected from ground-truth flags -- the LLM does not decide it.
        """
        if not question:
            question = "Where were you at the time of the murder?"
        cid = char.id
        if cid not in self._interview_histories:
            self._interview_histories[cid] = []
        history = self._interview_histories[cid]

        # Alibi provenance: interviewing the culprit reveals their alibi claims
        if char.is_culprit and char.alibi_claims:
            for claim in char.alibi_claims:
                self._revealed_alibi_claims.append({
                    "character": char.full_name,
                    "location": claim.location_name,
                    "time": claim.clock_time_str,
                })

        if self._npc_responder is not None:
            from mystery_world.npc_responder import derive_npc_gloss
            response = self._npc_responder.respond(char, self._state, question, history)
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": response})
            styled = _render_action(
                self._state.seed, "talk_wrapper", _ACT_TALK_WRAPPER, self.actions_taken,
                char=char.full_name, response=response,
            )
            gloss = derive_npc_gloss(char, self._state, question)
            return ActionResult(True, f"{styled}\n[gloss] {gloss}")

        return self._template_interview(char, question, history)

    def _template_interview(
        self, char: Character, question: str, history: list[dict],
    ) -> ActionResult:
        """Deterministic fallback used when no LLM responder is attached."""
        parts = [f'You ask {char.full_name}: "{question}"']
        if char.is_culprit:
            parts.append(f"{char.full_name} seems evasive and avoids answering directly.")
            if char.has_alibi:
                parts.append(f'After a pause they say: "{char.alibi_details}"')
        elif char.has_alibi:
            parts.append(f'{char.full_name} says: "{char.alibi_details}"')
        else:
            parts.append(f"{char.full_name} says they cannot recall anything specific.")
        for rel in char.relationships:
            target = self._state.characters.get(rel.target_id)
            if target:
                if rel.sentiment < -0.3:
                    parts.append(f"They tense up when {target.full_name} is mentioned.")
                elif rel.sentiment > 0.5:
                    parts.append(f"They speak warmly of {target.full_name}.")
        from mystery_world.npc_responder import derive_npc_gloss
        response_text = " ".join(parts[1:])
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": response_text})
        gloss = derive_npc_gloss(char, self._state, question)
        return ActionResult(True, " ".join(parts) + f"\n[gloss] {gloss}")


    def _handle_accuse(
        self,
        suspect_name: str = "",
        weapon_name: str = "",
        location_name: str = "",
        suspect_weapon_evidence: list[str] | None = None,
        weapon_victim_evidence: list[str] | None = None,
        suspect_room_evidence: list[str] | None = None,
        alibi_contradiction: dict[str, Any] | None = None,
        eliminations: dict[str, dict[str, str]] | None = None,
        **_: Any,
    ) -> ActionResult:
        """Final accusation. Ends the episode."""
        self.is_solved = True
        culprit = self._state.get_culprit()
        weapon = self._state.objects.get(self._state.murder_weapon_id)
        murder_loc = self._state.locations.get(self._state.murder_location_id)

        correct_suspect = culprit and culprit.full_name.lower() == suspect_name.lower()
        correct_weapon = weapon and weapon.name.lower() == weapon_name.lower()
        correct_location = murder_loc and murder_loc.name.lower() == location_name.lower()
        self.accusation_correct = correct_suspect and correct_weapon and correct_location

        details = {
            "suspect_correct": correct_suspect,
            "weapon_correct": correct_weapon,
            "location_correct": correct_location,
            "partial_score": sum([correct_suspect, correct_weapon, correct_location]) / 3.0,
        }

        has_scoring = any([
            suspect_weapon_evidence, weapon_victim_evidence,
            suspect_room_evidence, alibi_contradiction,
        ])
        if has_scoring:
            accused_ids = self._resolve_names_to_ids(suspect_name, weapon_name, location_name)
            triangle = {}
            if suspect_weapon_evidence:
                triangle["SUSPECT_WEAPON"] = EdgeArgument(evidence_ids=suspect_weapon_evidence)
            if weapon_victim_evidence:
                triangle["WEAPON_VICTIM"] = EdgeArgument(evidence_ids=weapon_victim_evidence)
            if suspect_room_evidence:
                triangle["SUSPECT_ROOM"] = EdgeArgument(evidence_ids=suspect_room_evidence)

            score = score_accusation(
                accused_ids=accused_ids,
                triangle=triangle,
                state=self._state,
                alibi_contradiction=alibi_contradiction,
                revealed_alibi_claims=self._revealed_alibi_claims,
                eliminations=eliminations,
                discovered_evidence=self._discovered_evidence,
                interviewed_characters=self._interviewed_characters,
                examine_total=self._examine_total,
                examine_hit=self._examine_hit,
            )
            score_dict = score.to_dict()
            self._last_score_result = score_dict
            details["score_result"] = score_dict
            details["triangle_score"] = score.triangle_score
            details["alibi_score"] = score.alibi_score
            details["composite_score"] = score.composite_score

        if self.accusation_correct:
            obs = (f"CORRECT! {culprit.full_name} committed the crime "
                   f"with the {weapon.name} in the {murder_loc.name}.")
        else:
            obs = (f"INCORRECT. The true answer: "
                   f"{culprit.full_name if culprit else '?'} with the "
                   f"{weapon.name if weapon else '?'} in the "
                   f"{murder_loc.name if murder_loc else '?'}.")
        if has_scoring:
            obs += (f" Triangle: {score.triangle_score:.1f}/3."
                    f" Alibi: {score.alibi_score:.2f}."
                    f" Composite: {score.composite_score:.2f}.")
        return ActionResult(True, obs, details=details)

    def _resolve_names_to_ids(
        self, suspect_name: str, weapon_name: str, location_name: str
    ) -> dict[str, str]:
        result = {"suspect": "", "weapon": "", "room": ""}
        for cid, c in self._state.characters.items():
            if c.full_name.lower() == suspect_name.lower():
                result["suspect"] = cid
                break
        for oid, o in self._state.objects.items():
            if o.name.lower() == weapon_name.lower():
                result["weapon"] = oid
                break
        for lid, l in self._state.locations.items():
            if l.name.lower() == location_name.lower():
                result["room"] = lid
                break
        return result


    def _handle_wait(self, **_: Any) -> ActionResult:
        return ActionResult(
            True,
            _render_action(self._state.seed, "wait", _ACT_WAIT, self.actions_taken),
        )


    def _handle_inventory(self, **_: Any) -> ActionResult:
        if not self._discovered_evidence:
            return ActionResult(True, "Your evidence collection is empty.")
        header = _render_action(
            self._state.seed, "inv_header", _ACT_INVENTORY_HEADER, self.actions_taken,
        )
        parts = [header]
        for eid in self._discovered_evidence:
            ev = self._state.evidence.get(eid)
            if ev:
                parts.append(f"- [{eid}] {ev.name} [{ev.evidence_type.name}] ({ev.state.name}): {ev.description}")
        return ActionResult(True, "\n".join(parts))


    def _handle_take(self, object_name: str = "", **_: Any) -> ActionResult:
        loc = self.get_current_location()
        if loc is None:
            return ActionResult(False, "No current location.")
        for oid in loc.objects_here:
            obj = self._state.objects.get(oid)
            if obj and obj.name.lower() == object_name.lower():
                if not obj.portable:
                    return ActionResult(False, f"The {obj.name} cannot be taken.")
                loc.objects_here.remove(oid)
                self.agent_inventory.append(oid)
                return ActionResult(
                    True,
                    _render_action(
                        self._state.seed, "take", _ACT_TAKE, self.actions_taken,
                        obj=obj.name,
                    ),
                )
        return ActionResult(False, f"No object called '{object_name}' here.")


    # ------------------------------------------------------------------
    # Summary for evaluation
    # ------------------------------------------------------------------
    def get_episode_summary(self) -> dict[str, Any]:
        return {
            "seed": self._state.seed,
            "complexity": self._state.config.to_dict(),
            "actions_taken": self.actions_taken,
            "budget": self._state.config.max_agent_actions,
            "is_solved": self.is_solved,
            "accusation_correct": self.accusation_correct,
            "evidence_discovered": list(self._discovered_evidence),
            "total_evidence": len(self._state.evidence),
            "examine_total": self._examine_total,
            "examine_hit": self._examine_hit,
            "characters_interviewed": list(self._interviewed_characters),
            "alibi_claims_revealed": len(self._revealed_alibi_claims),
            "total_characters": len(self._state.characters),
            "steps_elapsed": self._state.current_step,
            "event_count": len(self._state.event_log),
            "action_history": self.action_history,
            "score_result": self._last_score_result,
        }

    # ------------------------------------------------------------------
    # Session save / load (world + agent state + interview transcripts)
    # ------------------------------------------------------------------
    def save_session(self, directory: str | Path) -> Path:
        """
        Save the full session to *directory*:
          world.json          -- full WorldState (can be reloaded with --load)
          session.json        -- agent state + interview transcripts + action log
        Returns the directory path.
        """
        import datetime
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)

        # World state
        self._state.save(directory / "world.json")

        # Agent state
        session = {
            "saved_at": datetime.datetime.now().isoformat(),
            "seed": self._state.seed,
            "agent_location_id": self.agent_location_id,
            "agent_inventory": self.agent_inventory,
            "actions_taken": self.actions_taken,
            "is_solved": self.is_solved,
            "accusation_correct": self.accusation_correct,
            "discovered_evidence": list(self._discovered_evidence),
            "examine_total": self._examine_total,
            "examine_hit": self._examine_hit,
            "interviewed_characters": list(self._interviewed_characters),
            "interview_histories": self._interview_histories,
            "revealed_alibi_claims": self._revealed_alibi_claims,
            "action_history": self.action_history,
        }
        (directory / "session.json").write_text(json.dumps(session, indent=2))
        return directory

    def load_session(self, directory: str | Path) -> None:
        """
        Restore agent state from a previously saved session directory.
        Call this after constructing MysteryEnvironment with the saved world.json.
        """
        directory = Path(directory)
        session = json.loads((directory / "session.json").read_text())
        self.agent_location_id = session["agent_location_id"]
        self.agent_inventory = session["agent_inventory"]
        self.actions_taken = session["actions_taken"]
        self.is_solved = session["is_solved"]
        self.accusation_correct = session["accusation_correct"]
        self._discovered_evidence = set(session["discovered_evidence"])
        self._examine_total = session.get("examine_total", 0)
        self._examine_hit = session.get("examine_hit", 0)
        self._interviewed_characters = set(session["interviewed_characters"])
        self._interview_histories = session["interview_histories"]
        self._revealed_alibi_claims = session.get("revealed_alibi_claims", [])
        self.action_history = session["action_history"]


# ---------------------------------------------------------------------------
# Locard triangle + alibi scoring
# ---------------------------------------------------------------------------

def score_accusation(
    accused_ids: dict[str, str],
    triangle: dict[str, EdgeArgument],
    state: WorldState,
    alibi_contradiction: dict[str, Any] | None = None,
    revealed_alibi_claims: list[dict[str, str]] | None = None,
    eliminations: dict[str, dict[str, str]] | None = None,
    discovered_evidence: set[str] | None = None,
    interviewed_characters: set[str] | None = None,
    examine_total: int = 0,
    examine_hit: int = 0,
) -> ScoreResult:
    """Score an accusation: accusation + triangle F1 + alibi + elimination."""
    result = ScoreResult()
    murder_ts = state.murder_timestamp
    threshold = state.freshness_threshold

    # --- Score 1: Accusation correctness ---
    result.correct_suspect = accused_ids.get("suspect") == state.culprit_id
    result.correct_weapon = accused_ids.get("weapon") == state.murder_weapon_id
    result.correct_room = accused_ids.get("room") == state.murder_location_id
    result.accusation_score = sum([
        result.correct_suspect, result.correct_weapon, result.correct_room
    ]) / 3.0

    # --- Score 2: Locard triangle (precision + recall → F1 per edge) ---
    def _count_available(edge_type: EdgeType) -> int:
        return sum(
            1 for ev in state.evidence.values()
            if not ev.is_red_herring
            and ev.relevance is not None
            and ev.relevance.edge_type == edge_type
            and _relevance_matches_truth(ev.relevance, edge_type, state)
            and abs(ev.relevance.contact_timestamp - murder_ts) < threshold
        )

    def _score_edge(edge_type: EdgeType) -> tuple[float, float, float]:
        arg = triangle.get(edge_type.name)
        total_available = _count_available(edge_type)
        if arg is None or not arg.evidence_ids:
            return (0.0, 0.0, 0.0)
        total_cited = len(arg.evidence_ids)
        correct_fresh = 0
        correct_stale = 0
        for eid in arg.evidence_ids:
            ev = state.evidence.get(eid)
            if ev is None or ev.is_red_herring or ev.relevance is None:
                continue
            rel = ev.relevance
            if rel.edge_type != edge_type:
                continue
            if not _relevance_matches_truth(rel, edge_type, state):
                continue
            if abs(rel.contact_timestamp - murder_ts) < threshold:
                correct_fresh += 1
            else:
                correct_stale += 1
        effective_correct = correct_fresh + 0.5 * correct_stale
        precision = effective_correct / total_cited if total_cited > 0 else 0.0
        recall = correct_fresh / total_available if total_available > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall > 0 else 0.0
        )
        return (precision, recall, f1)

    sw_p, sw_r, sw_f1 = _score_edge(EdgeType.SUSPECT_WEAPON)
    wv_p, wv_r, wv_f1 = _score_edge(EdgeType.WEAPON_VICTIM)
    sr_p, sr_r, sr_f1 = _score_edge(EdgeType.SUSPECT_ROOM)

    result.suspect_weapon_precision = sw_p
    result.suspect_weapon_recall = sw_r
    result.suspect_weapon_score = sw_f1
    result.weapon_victim_precision = wv_p
    result.weapon_victim_recall = wv_r
    result.weapon_victim_score = wv_f1
    result.suspect_room_precision = sr_p
    result.suspect_room_recall = sr_r
    result.suspect_room_score = sr_f1
    result.triangle_score = sw_f1 + wv_f1 + sr_f1

    # --- Score 3: Alibi verification (provenance + evidence_id contradiction) ---
    if alibi_contradiction and revealed_alibi_claims:
        cited_loc = alibi_contradiction.get("claimed_location", "").lower()
        cited_time = alibi_contradiction.get("claimed_time", "").lower()
        cited_ev_ids = alibi_contradiction.get("contradiction_evidence", []) or []
        if not isinstance(cited_ev_ids, list):
            cited_ev_ids = []

        claim_matches = any(
            r["location"].lower() == cited_loc and r["time"].lower() == cited_time
            for r in revealed_alibi_claims
        )

        discovered = discovered_evidence or set()

        def _alibi_ev_valid(eid: str) -> bool:
            if eid not in discovered or eid not in state.evidence:
                return False
            ev = state.evidence[eid]
            if ev.is_red_herring or ev.relevance is None:
                return False
            rel = ev.relevance
            return (
                rel.edge_type == EdgeType.SUSPECT_ROOM
                and state.culprit_id in rel.subject_ids
                and state.murder_location_id in rel.subject_ids
                and abs(rel.contact_timestamp - murder_ts) < threshold
            )

        if claim_matches:
            result.alibi_cited = True
            result.contradiction_found = any(_alibi_ev_valid(eid) for eid in cited_ev_ids)
            result.contradiction_valid = _validate_alibi_contradiction(
                accused_ids.get("suspect", ""), alibi_contradiction, state
            )
            result.alibi_score = sum([
                result.alibi_cited,
                result.contradiction_found,
                result.contradiction_valid,
            ]) / 3.0

    # --- Score 4: Elimination (SUSPECT_ELSEWHERE + corroborator interview) ---
    if eliminations:
        # Characters who appear only as corroborators (witnesses for someone else's
        # alibi) but have no SUSPECT_ELSEWHERE evidence for themselves are not
        # independent elimination targets -- exclude them from the denominator.
        # Mutual corroborators (A witnesses B and B witnesses A) both have SE
        # evidence targeting themselves, so both stay in the denominator.
        corroborator_ids = {
            ev.corroborator_id
            for ev in state.evidence.values()
            if ev.relevance is not None
            and ev.relevance.edge_type == EdgeType.SUSPECT_ELSEWHERE
            and ev.corroborator_id
        }
        se_target_ids = {
            cid
            for ev in state.evidence.values()
            if ev.relevance is not None
            and ev.relevance.edge_type == EdgeType.SUSPECT_ELSEWHERE
            for cid in ev.relevance.subject_ids
        }
        corroborator_only_ids = corroborator_ids - se_target_ids
        total_innocents = sum(
            1 for c in state.characters.values()
            if c.is_alive and not c.is_culprit
            and CharacterRole.SUSPECT in c.roles
            and c.id not in corroborator_only_ids
        )
        correct = 0
        incorrect = 0
        discovered = discovered_evidence or set()
        interviewed = interviewed_characters or set()

        for suspect_name, claim in eliminations.items():
            char = next(
                (c for c in state.characters.values()
                 if c.full_name.lower() == suspect_name.lower()),
                None,
            )
            if char is None or not isinstance(claim, dict):
                continue

            evidence_id = claim.get("evidence_id", "")
            corroborator_name = claim.get("corroborator", "")

            ev_valid = False
            if (
                evidence_id
                and evidence_id in discovered
                and evidence_id in state.evidence
            ):
                ev = state.evidence[evidence_id]
                if (
                    not ev.is_red_herring
                    and ev.relevance is not None
                    and ev.relevance.edge_type == EdgeType.SUSPECT_ELSEWHERE
                    and char.id in ev.relevance.subject_ids
                    and abs(ev.relevance.contact_timestamp - murder_ts) < threshold
                ):
                    ev_valid = True

            corr_valid = False
            if ev_valid and corroborator_name:
                ev = state.evidence[evidence_id]
                if ev.corroborator_id:
                    corr_char = state.characters.get(ev.corroborator_id)
                    if (
                        corr_char is not None
                        and corr_char.full_name.lower() == corroborator_name.lower()
                        and ev.corroborator_id in interviewed
                    ):
                        corr_valid = True

            if ev_valid and corr_valid:
                if char.is_culprit:
                    incorrect += 1
                else:
                    correct += 1
            else:
                if char.is_culprit:
                    incorrect += 1

        result.total_innocents = total_innocents
        result.correct_eliminations = correct
        result.incorrect_eliminations = incorrect
        if total_innocents > 0:
            result.elimination_score = max(
                0.0, (correct - 2 * incorrect) / total_innocents
            )

    # --- Composite ---
    examine_efficiency = examine_hit / max(1, examine_total) if examine_total > 0 else 1.0
    base = (
        0.35 * result.accusation_score
        + 0.35 * (result.triangle_score / 3.0)
        + 0.15 * result.alibi_score
        + 0.15 * result.elimination_score
    )
    result.composite_score = base * (0.8 + 0.2 * examine_efficiency)
    return result


def _relevance_matches_truth(
    rel: EdgeRelevance, edge_type: EdgeType, state: WorldState
) -> bool:
    if edge_type == EdgeType.SUSPECT_WEAPON:
        return state.culprit_id in rel.subject_ids and state.murder_weapon_id in rel.subject_ids
    elif edge_type == EdgeType.WEAPON_VICTIM:
        return state.murder_weapon_id in rel.subject_ids and state.victim_id in rel.subject_ids
    elif edge_type == EdgeType.SUSPECT_ROOM:
        return state.culprit_id in rel.subject_ids and state.murder_location_id in rel.subject_ids
    return False


def _is_blocked(
    from_id: str,
    to_id: str,
    active_constraints: list[RouteConstraint],
) -> bool:
    return any(
        (rc.from_location_id == from_id and rc.to_location_id == to_id)
        or (rc.from_location_id == to_id and rc.to_location_id == from_id)
        for rc in active_constraints
    )


def _shortest_path_steps_constrained(
    from_id: str,
    to_id: str,
    locations: dict[str, Location],
    active_constraints: list[RouteConstraint] | None = None,
) -> int | None:
    """BFS respecting blocked passages. Returns steps or None if unreachable."""
    from collections import deque
    constraints = active_constraints or []
    visited = {from_id}
    queue = deque([(from_id, 0)])
    while queue:
        current_id, steps = queue.popleft()
        loc = locations.get(current_id)
        if loc is None:
            continue
        for adj_id in loc.adjacent_ids:
            if _is_blocked(current_id, adj_id, constraints):
                continue
            if adj_id == to_id:
                return steps + 1
            if adj_id not in visited:
                visited.add(adj_id)
                queue.append((adj_id, steps + 1))
    return None


def _has_path_avoiding(
    from_id: str,
    to_id: str,
    avoid_id: str,
    locations: dict[str, Location],
    active_constraints: list[RouteConstraint] | None = None,
) -> bool:
    """True if a path exists from→to that never enters avoid_id."""
    from collections import deque
    constraints = active_constraints or []
    visited = {from_id, avoid_id}
    queue = deque([from_id])
    while queue:
        current_id = queue.popleft()
        loc = locations.get(current_id)
        if loc is None:
            continue
        for adj_id in loc.adjacent_ids:
            if _is_blocked(current_id, adj_id, constraints):
                continue
            if adj_id == to_id:
                return True
            if adj_id not in visited:
                visited.add(adj_id)
                queue.append(adj_id)
    return False


def _clock_str_to_step(clock_str: str, world_start_hour: int) -> int | None:
    """Parse '9:30 PM' -> step index. Returns None if unparseable."""
    import re
    m = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", clock_str.strip(), re.IGNORECASE)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    meridiem = (m.group(3) or "").upper()
    if meridiem == "PM" and hour != 12:
        hour += 12
    elif meridiem == "AM" and hour == 12:
        hour = 0
    total_minutes = hour * 60 + minute
    start_minutes = world_start_hour * 60
    delta = total_minutes - start_minutes
    if delta < 0:
        delta += 24 * 60
    return delta // 30


def _step_to_clock_str(step: int, world_start_hour: int) -> str:
    """Convert step index to '9:30 PM' string."""
    total_minutes = world_start_hour * 60 + step * 30
    total_minutes %= 24 * 60
    hour, minute = divmod(total_minutes, 60)
    meridiem = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {meridiem}"


def _validate_alibi_contradiction(
    suspect_id: str,
    contradiction: dict[str, Any],
    state: WorldState,
) -> bool:
    """Check that the agent's cited contradiction is logically valid.

    Type A (one alibi claim): suspect claims to be at location X at murder_step,
    but X is not the murder location. Valid when the claim is at murder_step and
    the location differs -- a witness or physical evidence at the crime scene
    disproves the claim.

    Type B (two alibi claims): murder_step falls between the two claims, and every
    open route from the before-location to the after-location passes through the
    murder location at exactly murder_step.
    """
    suspect = state.characters.get(suspect_id)
    if not suspect or not suspect.alibi_claims:
        return False

    murder_step = state.murder_step
    murder_loc_id = state.murder_location_id
    active_constraints = [
        rc for rc in state.route_constraints
        if rc.blocked_from_step <= murder_step <= rc.blocked_until_step
    ]

    def _find_loc(name: str) -> Location | None:
        return next(
            (l for l in state.locations.values() if l.name.lower() == name.lower()),
            None,
        )

    # --- Type A ---
    if len(suspect.alibi_claims) == 1:
        claim = suspect.alibi_claims[0]
        if claim.step != murder_step:
            return False
        claimed_loc = _find_loc(claim.location_name)
        if claimed_loc is None:
            return False
        return claimed_loc.id != murder_loc_id

    # --- Type B ---
    if len(suspect.alibi_claims) >= 2:
        before = min(suspect.alibi_claims, key=lambda a: a.step)
        after = max(suspect.alibi_claims, key=lambda a: a.step)
        if not (before.step < murder_step < after.step):
            return False
        before_loc = _find_loc(before.location_name)
        after_loc = _find_loc(after.location_name)
        if before_loc is None or after_loc is None:
            return False
        # No open path from before→after that avoids the murder location
        can_avoid = _has_path_avoiding(
            before_loc.id, after_loc.id, murder_loc_id,
            state.locations, active_constraints,
        )
        if can_avoid:
            return False
        # Timing: before_step + travel_to_murder == murder_step
        steps_to_murder = _shortest_path_steps_constrained(
            before_loc.id, murder_loc_id, state.locations, active_constraints
        )
        if steps_to_murder is None:
            return False
        return before.step + steps_to_murder == murder_step

    return False
