"""Short model-facing pause references and repairs that cannot rewrite valid speech."""
from __future__ import annotations

from typing import Literal, Union

from pydantic import Field, create_model

from . import highlight_edit as source, highlight_episode as episode
from .contracts import Contract
from .llm import ModelAnchorError, ModelOutputError


class Keep(Contract):
    gap_ref: str
    action: Literal["keep"]
    evidence_passage_id: str = Field(description="Copy one retained passage ID, not its speech or an explanation.")
    reason: str = Field(min_length=1, max_length=180)


class Lead(Contract):
    gap_ref: str
    action: Literal["lead_in"]
    seconds: float = Field(ge=1, le=15)
    reason: str = Field(min_length=1, max_length=180)


class Proposal(Contract):
    id: str
    spans: list[source.Span] = Field(max_length=60)
    pauses: list[Keep | Lead] = Field(max_length=40)
    value: int = Field(ge=1, le=4)
    reason: str = Field(min_length=1, max_length=400)


class Batch(Contract):
    scenes: list[Proposal] = Field(max_length=4)


RULES = episode.EDIT_RULES.replace("Use supplied gap IDs.",
    "Copy gap_ref from this scene's pause_options. For keep, evidence_passage_id is a retained speech ID, never a quotation.")


def options(context):
    result = []
    for a, b in zip(context, context[1:]):
        duration = b["speech_start_us"]-a["speech_end_us"]
        if a["asset"] == b["asset"] and duration >= 1500000:
            result.append({"gap_ref": f"g{len(result)+1}", "before_passage_id": a["id"],
                           "after_passage_id": b["id"], "duration_seconds": round(duration/1e6, 2)})
    return result


def canonical(option):
    return f"g{option['before_passage_id']}:{option['after_passage_id']}"


def card(beat, context, previous=None):
    choices = options(context)
    speech = source.speech_rows(context)
    for choice in choices:
        speech = speech.replace(f"gap {canonical(choice)} ", f"gap {choice['gap_ref']} ")
    return {"scene": beat, "speech": speech, "pause_options": choices, "previous_edit": previous}


def pause_type(context, name):
    choices = options(context)
    if not choices:
        return Keep | Lead
    refs = Literal[tuple(c["gap_ref"] for c in choices)]
    ids = Literal[tuple(u["id"] for u in context)]
    keep = create_model(name+"Keep", __base__=Keep, gap_ref=(refs, ...),
                        evidence_passage_id=(ids, Field(description="One supplied retained passage ID. Never quote speech.")))
    lead = create_model(name+"Lead", __base__=Lead, gap_ref=(refs, ...))
    return keep | lead


def schema(pack, contexts):
    variants = []
    for i, entry in enumerate(pack):
        ident = entry["scene"]["id"]
        context = contexts[ident]
        pause = pause_type(context, f"Scene{i}")
        variants.append(create_model(f"Scene{i}", __base__=Proposal, id=(Literal[ident], ...),
            pauses=(list[pause], Field(max_length=40 if options(context) else 0))))
    return create_model("SceneBatch", __base__=Batch,
        scenes=(list[Union[tuple(variants)]], Field(max_length=len(pack))))


def convert(proposal, context):
    if isinstance(proposal, episode.Proposal):
        return proposal
    lookup = {c["gap_ref"]: canonical(c) for c in options(context)}
    pauses = []
    for pause in proposal.pauses:
        ident = lookup.get(pause.gap_ref, pause.gap_ref)
        if pause.action == "keep":
            pauses.append(episode.KeepPause(id=ident, action="keep", evidence=pause.evidence_passage_id, reason=pause.reason))
        else:
            pauses.append(episode.LeadIn(id=ident, action="lead_in", seconds=pause.seconds, reason=pause.reason))
    return episode.Proposal(spans=proposal.spans, pauses=pauses, value=proposal.value, reason=proposal.reason)


def repair(proposal, context, evaluator, key, warnings):
    """Check speech first; any remaining failure is confined to pause annotations."""
    proposal = convert(proposal, context)
    base = episode.SceneEdit(spans=proposal.spans, gaps=[], value=proposal.value, reason=proposal.reason)
    episode.compile_scene(base, context)  # Invalid speech anchors must never use the pause fallback.
    good, bad, problems, seen = {}, {}, [], {}
    for index, pause in enumerate(proposal.pauses):
        try:
            if pause.id in seen:
                if pause == seen[pause.id]:
                    continue
                raise ModelAnchorError("Conflicting decisions for the same pause.")
            episode.verified_proposal(proposal.model_copy(update={"pauses": [pause]}), context)
            seen[pause.id] = pause
            good[index] = pause
        except ModelAnchorError as exc:
            bad[index] = pause
            problems.append({"slot": index, "problem": str(exc)})
    if not bad:
        return episode.verified_proposal(proposal.model_copy(update={"pauses": list(good.values())}), context), None

    pause = pause_type(context, "Repair")
    slot = create_model("PauseSlot", __base__=Contract, slot=(Literal[tuple(bad)], ...), pause=(pause, ...))
    response = create_model("PauseRepair", __base__=Contract, repairs=(list[slot], Field(min_length=len(bad), max_length=len(bad))))
    def apply(result):
        slots = [r.slot for r in result.repairs]
        if len(slots) != len(bad) or set(slots) != set(bad):
            raise ModelAnchorError("Repair every requested slot exactly once.")
        combined = dict(good)
        for entry in result.repairs:
            wire = Proposal(id="repair", spans=proposal.spans, pauses=[entry.pause], value=proposal.value, reason=proposal.reason)
            combined[entry.slot] = convert(wire, context).pauses[0]
        result = proposal.model_copy(update={"pauses": [combined[i] for i in sorted(combined)]})
        return episode.verified_proposal(result, context)
    system = source.RULES+"""
Repair only the listed invalid pause instructions. Speech ranges and valid pauses are locked.
Copy gap_ref from pause_options and evidence_passage_id from retained speech IDs. Correct all reported
problems together. Keep a supported pause or request a 1-15 second lead-in before its reaction.
Do not invent references, rewrite speech, or change other pauses. Return each requested slot once.
"""
    payload = {"original_proposal": proposal.model_dump(), "problems": problems,
               "speech": card({"id": "repair"}, context)["speech"], "pause_options": options(context)}
    try:
        result = source.request(evaluator, system, payload, response, key+"-pauses", apply, retry_delays=())
        return apply(result), {"repaired_instructions": len(bad), "fallback_gaps": []}
    except ModelOutputError:
        # Unknown references could denote any retained pause. Preserve them all rather than guess.
        choices = options(context)
        known = {canonical(c) for c in choices}
        uncertain = {p.id for p in bad.values()}
        if not uncertain <= known:
            uncertain = known
        kept = []
        for choice in choices:
            ident = canonical(choice)
            if ident not in uncertain:
                continue
            keep = episode.KeepPause(id=ident, action="keep", evidence=choice["after_passage_id"],
                                     reason="Pause instruction could not be verified; retained for final review.")
            # Removed speech also removes the pause; those annotations have no effect.
            kept.append(keep)
        remaining = [p for p in good.values() if p.id not in uncertain]
        value = episode.verified_proposal(proposal.model_copy(update={"pauses": remaining+kept}), context)
        ranges = source.compile_edit(source.Edit(spans=value.spans, gaps=[], reason=value.reason), context, allowance=float("inf"))
        by_id = {u["id"]: u for u in context}
        retained = [canonical(c) for c in choices if canonical(c) in uncertain and any(
            r["start_us"] <= by_id[c["before_passage_id"]]["speech_end_us"] < by_id[c["after_passage_id"]]["speech_start_us"] <= r["end_us"] for r in ranges)]
        warnings.append(f"Scene {key.rsplit('-repair-', 1)[-1]}: {len(retained)} uncertain pauses were kept for final review after a failed pause repair.")
        return value, {"repaired_instructions": 0, "fallback_gaps": retained, "problems": problems}
