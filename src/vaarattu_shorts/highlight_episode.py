"""Screen by quality, edit worthwhile scenes, then assemble a chronological episode."""
from __future__ import annotations

import copy
import json
from typing import Literal

from pydantic import Field

from . import highlight_edit as source
from .contracts import Contract
from .llm import ModelAnchorError, ModelOutputError

VERSION = "episode-v3"
units = source.units
RULES = source.RULES


class Pause(Contract):
    id: str
    action: Literal["keep", "shorten", "lead_in"]
    evidence: str = Field(description="Retained passage ID supporting this decision; especially the reaction after a gameplay lead-in.")
    seconds: float = Field(ge=0, le=15, description="For lead_in only: seconds before the reaction to retain. Otherwise use zero.")
    reason: str = Field(min_length=1, max_length=180)


class SceneEdit(Contract):
    spans: list[source.Span] = Field(max_length=60)
    gaps: list[Pause] = Field(max_length=100)
    value: int = Field(ge=1, le=4, description="Quality AFTER editing: 1 discard, 2 enjoyable supporting material, 3 strong, 4 exceptional. Entertaining commentary need not have a punchline.")
    reason: str = Field(min_length=1, max_length=400)


class KeepPause(Contract):
    id: str
    action: Literal["keep"]
    evidence: str = Field(description="Retained passage supporting this pause.")
    reason: str = Field(min_length=1, max_length=180)


class LeadIn(Contract):
    id: str
    action: Literal["lead_in"]
    seconds: float = Field(ge=1, le=15, description="Seconds of gameplay before the following retained reaction.")
    reason: str = Field(min_length=1, max_length=180)


class Proposal(Contract):
    spans: list[source.Span] = Field(max_length=60)
    pauses: list[KeepPause | LeadIn] = Field(max_length=40)
    value: int = Field(ge=1, le=4)
    reason: str = Field(min_length=1, max_length=400)


class NamedProposal(Proposal):
    id: str


class EditBatch(Contract):
    scenes: list[NamedProposal] = Field(max_length=4)


def verified_proposal(proposal, items):
    # The following passage is encoded in a supplied gap ID, not generated separately.
    gaps = [Pause(id=p.id, action=p.action,
                  evidence=p.evidence if isinstance(p, KeepPause) else p.id.split(":")[-1],
                  seconds=p.seconds if isinstance(p, LeadIn) else 0, reason=p.reason)
            for p in proposal.pauses]
    edit = SceneEdit(spans=proposal.spans, gaps=gaps, value=proposal.value, reason=proposal.reason)
    compile_scene(edit, items)
    return edit


class Rating(Contract):
    id: str
    score: int = Field(ge=0, le=100)
    reason: str = Field(min_length=1, max_length=180)


class Ratings(Contract):
    scenes: list[Rating] = Field(max_length=24)


def compile_scene(edit, items):
    """Shorten only verified spaces between words; retain natural short pauses."""
    base = source.Edit(spans=edit.spans, gaps=[], reason=edit.reason)
    ranges = source.compile_edit(base, items, allowance=float("inf"))
    kept = {u["id"] for u in items if any(s["start_us"] <= u["speech_start_us"]
            and u["speech_end_us"] <= s["end_us"] for s in ranges)}
    gaps, supplied = {}, set()
    for left, right in zip(items, items[1:]):
        if left["asset"] != right["asset"]:
            continue
        a, b = left["speech_end_us"], right["speech_start_us"]
        ident = f"g{left['id']}:{right['id']}"
        if b-a >= 1500000:
            supplied.add(ident)
            if any(s["start_us"] <= a < b <= s["end_us"] for s in ranges):
                gaps[ident] = (left, right)
    decisions = {}
    for gap in edit.gaps:
        if gap.id not in supplied:
            raise ModelAnchorError(f"Unknown gap {gap.id}; use a supplied gap ID.")
        # Removing speech removes its intervening pause too. This annotation has no effect.
        if gap.id not in gaps:
            continue
        if gap.id in decisions or gap.evidence not in kept:
            raise ModelAnchorError("Use each retained gap once, with evidence from a retained passage.")
        if gap.action == "lead_in" and (gap.seconds < 1 or gap.evidence != gaps[gap.id][1]["id"]):
            raise ModelAnchorError("A gameplay lead-in needs 1-15 seconds and the following passage as reaction evidence.")
        decisions[gap.id] = gap
    cuts = []
    for ident, (left, right) in gaps.items():
        gap = decisions.get(ident)
        duration = right["speech_start_us"]-left["speech_end_us"]
        action = gap.action if gap else ("shorten" if duration >= 2500000 else "keep")
        unsafe = left["unsafe"] or right["unsafe"] or left.get("gap_unsafe", False)
        if unsafe:
            if gap and action != "keep":
                raise ModelAnchorError(f"Keep {ident}: word timing around this gap is uncertain.")
            continue
        if action == "keep":
            continue
        before = round(gap.seconds*1e6) if action == "lead_in" else 400000
        a, b = left["speech_end_us"]+400000, right["speech_start_us"]-before
        if b > a:
            cuts.append((left["asset"], a, b))
    retained = []
    for span in ranges:
        cursor = span["start_us"]
        for asset, a, b in cuts:
            if asset == span["asset"] and cursor < a < b < span["end_us"]:
                retained.append({**span, "start_us": cursor, "end_us": a})
                cursor = b
        retained.append({**span, "start_us": cursor})
    # A split interval must identify its own speech, not the original enclosing range.
    for span in retained:
        speech = [u for u in items if u["asset"] == span["asset"]
                  and span["start_us"] <= u["speech_start_us"] and u["speech_end_us"] <= span["end_us"]]
        if not speech:
            raise ModelAnchorError("Every retained interval must include grounded speech or its reaction.")
        span.update(first=speech[0]["id"], last=speech[-1]["id"])
    return retained


def seconds(spans):
    return sum(s["end_us"]-s["start_us"] for s in spans)/1e6


def compiled(scene, items):
    return compile_scene(SceneEdit.model_validate(scene["edit"]), source.sequence_items(scene["beat"], items))


def scene_evidence(scene, items, with_context=False):
    context = source.sequence_items(scene["beat"], items)
    result = []
    for span in compiled(scene, items):
        speech = [u for u in context if span["start_us"] <= u["speech_start_us"] and u["speech_end_us"] <= span["end_us"]]
        result.append({"first": span["first"], "last": span["last"],
                       "duration_seconds": round((span["end_us"]-span["start_us"])/1e6, 2),
                       "speech": source.speech_rows(speech)})
        if with_context:
            first = next(i for i, u in enumerate(context) if u["id"] == span["first"])
            last = next(i for i, u in enumerate(context) if u["id"] == span["last"])
            result[-1].update(preceding_source=source.speech_rows(context[max(0, first-2):first]),
                              following_source=source.speech_rows(context[last+1:last+3]))
    return result


EDIT_RULES = RULES + """
Edit each supplied scene independently into enjoyable commentary, personality, exchanges and reactions.
Remove repetitive explanations, routine coordination, abandoned tangents and waiting. Preserve setup,
meaning, qualifications and payoff; retain complete thoughts, not staccato word-level fragments.
Long verified speech gaps are shortened automatically, with speech margins. Do not describe ordinary
shortening. In pauses, list only exceptions: keep a speech-supported anticipation/comic pause, or a
1-15 second lead_in before the following reaction. Use supplied gap IDs. Both sides of a pause must
remain in a retained range. Omitted pauses use the automatic policy. Empty pauses is usually appropriate.
Do not claim to see gameplay or preserve waiting solely for unknown visuals. Value AFTER editing:
1 discard, 2 enjoyable supporting material, 3 strong, 4 exceptional. Empty spans discard a scene.
Human guidance is preference, not evidence. Give a brief reason. Return every supplied scene once."""


def compact(beat, items, evaluator, key, guidance="", previous=None, warnings=None):
    context = source.sequence_items(beat, items)
    def validate(proposal):
        # Explain field mistakes before the compiler reports a combined anchor error.
        # Keep this in repair validation so existing verified batch caches remain reusable.
        passage_ids = {u["id"] for u in context}
        gap_ids = [f"g{a['id']}:{b['id']}" for a, b in zip(context, context[1:])
                   if a["asset"] == b["asset"] and b["speech_start_us"]-a["speech_end_us"] >= 1500000]
        for pause in proposal.pauses:
            if pause.id not in gap_ids:
                raise ModelAnchorError(f"Use a complete supplied gap ID, including both passage IDs and the colon. Available IDs: {', '.join(gap_ids)}.")
            if isinstance(pause, KeepPause) and pause.evidence not in passage_ids:
                raise ModelAnchorError(f"For {pause.id}, evidence must be a retained passage ID such as {pause.id.split(':')[-1]}, not quoted speech or an explanation. Put explanations in reason.")
        verified_proposal(proposal, context)
    proposal = source.request(evaluator, EDIT_RULES, {"scene": beat, "speech": source.speech_rows(context),
        "guidance": guidance, "previous_edit": previous}, Proposal, key, validate,
        retry_delays=(5,))
    return verified_proposal(proposal, context)


def edit_scenes(beats, items, evaluator, key, guidance, warnings, previous=None):
    """Cache batches, validate each scene, and repair only the invalid/missing scene."""
    previous = previous or {}
    cards = [{"scene": beat, "speech": source.speech_rows(source.sequence_items(beat, items)),
              "previous_edit": previous.get(beat["id"])} for beat in beats]
    packs, current = [], []
    for card in cards:
        if current and (len(current) == 4 or evaluator.request_size(EDIT_RULES,
                json.dumps({"scenes": current+[card], "guidance": guidance}, ensure_ascii=False), EditBatch) > evaluator.discovery_budget):
            packs.append(current)
            current = []
        current.append(card)
    if current:
        packs.append(current)
    result, failures = [], []
    for i, pack in enumerate(packs):
        evaluator.report(f"Editing shortlisted scenes: batch {i+1} of {len(packs)} ({len(pack)} scenes).")
        batch = source.request(evaluator, EDIT_RULES, {"scenes": pack, "guidance": guidance}, EditBatch,
            f"{key}-{i}", recovery=lambda _: EditBatch(scenes=[]), warnings=warnings,
            warning="A scene batch could not be read; its scenes will be checked individually.", retry_delays=(5,))
        known = {c["scene"]["id"] for c in pack}
        if any(p.id not in known for p in batch.scenes):
            warnings.append("An edit referenced an unknown scene; that extra suggestion was ignored.")
        for card in pack:
            beat = card["scene"]
            proposals = [p for p in batch.scenes if p.id == beat["id"]]
            try:
                if len(proposals) != 1:
                    raise ModelAnchorError("Return this scene exactly once.")
                value = verified_proposal(proposals[0], source.sequence_items(beat, items))
            except ValueError as exc:
                evaluator.report(f"Repairing one scene edit ({beat['id']}); other verified edits are saved.")
                try:
                    value = compact(beat, items, evaluator, f"{key}-repair-{beat['id']}",
                                    guidance+"\nCorrect this edit problem: "+str(exc), card["previous_edit"], warnings)
                except ModelOutputError:
                    # Pause instead of silently producing a technically depleted episode.
                    failures.append(beat["id"])
                    if len(failures) >= 3:
                        raise ModelOutputError("Several scene edits could not be verified. Processing is paused to avoid losing useful scenes. Completed requests are saved.") from None
                    continue
            result.append({"beat": beat, "edit": value.model_dump()})
    if failures:
        raise ModelOutputError(f"An edit for scene {', '.join(failures)} could not be verified. Processing is paused; other edits are saved. Resume to retry the affected scene.")
    return result


def rank_scenes(pool, items, evaluator, warnings):
    """Rate edited evidence in bounded batches, keeping every decision for assembly/refill."""
    system = RULES + """
Rate EVERY edited scene for an enjoyable condensed stream episode. This is not
final selection: do not pick a handful to fill a runtime. Score independently on a shared 0-100 scale:
0-39 unusable/routine, 40-59 weak or mostly dependent on unseen gameplay, 60-74 worthwhile supporting
commentary/personality, 75-89 strong, 90-100 exceptional. Judge the actual retained speech, runtime,
repetition and long gaps, not an earlier model's enthusiasm. Concise ordinary entertaining moments count.
An expensive long excerpt needs more substance. Return each supplied ID exactly once with a brief
concrete reason; no quota. Excerpts marked abbreviated are incomplete evidence, not continuous speech."""
    cards, rankings = [], []
    for seq in pool:
        evidence = scene_evidence(seq, items)
        if not evidence or seq["edit"]["value"] < 2:
            rankings.append({"id": seq["beat"]["id"], "score": 0, "reason": "Discarded during scene editing."})
            continue
        speech = "\n[CUT]\n".join(s["speech"] for s in evidence)
        excerpt = speech if len(speech) <= 2400 else speech[:900]+"\n[...]\n"+speech[len(speech)//2-300:len(speech)//2+300]+"\n[...]\n"+speech[-900:]
        spans = compiled(seq, items)
        cards.append({"id": seq["beat"]["id"], "edited_seconds": round(seconds(spans), 2),
                      "retained_ranges": len(spans), "speech": excerpt, "abbreviated": len(speech)>2400,
                      "preserved_pauses": [g for g in seq["edit"]["gaps"] if g["action"] != "shorten"]})
    return rankings + rate_cards(cards, evaluator, system, "episode-rank", "Comparing edited scenes", warnings)


def rate_cards(cards, evaluator, system, key, label, warnings):
    rankings = []
    packs, current = [], []
    for card in cards:
        if current and (len(current) == 24 or evaluator.request_size(system, json.dumps({"scenes": current+[card]}, ensure_ascii=False), Ratings) > evaluator.discovery_budget):
            packs.append(current)
            current = []
        current.append(card)
    if current:
        packs.append(current)
    for i, pack in enumerate(packs):
        ids = {c["id"] for c in pack}
        def validate(result):
            actual = [r.id for r in result.scenes]
            if len(actual) != len(ids) or set(actual) != ids:
                raise ModelAnchorError("Rate every supplied scene ID exactly once, including weak scenes.")
        def recovery(result):
            known = {r.id: r for r in result.scenes if r.id in ids} if result else {}
            if set(known) != ids:
                raise ModelOutputError("Scene ranking is incomplete after retries. Processing is paused; completed requests are saved.")
            return Ratings(scenes=[known[c["id"]] for c in pack])
        evaluator.report(f"{label}: batch {i+1} of {len(packs)}.")
        result = source.request(evaluator, system, {"scenes": pack}, Ratings, f"{key}-{i}", validate,
            recovery=recovery, warnings=warnings, warning="Scene ranking needed recovery; only supplied scene IDs were retained.")
        rankings.extend(r.model_dump() for r in result.scenes)
    return rankings


def screen_scenes(beats, items, evaluator, warnings):
    system = RULES + """
Rate every source scene's potential AFTER removing filler and waiting. This is an inexpensive first
assessment, not a final edit. 0-39 routine/unusable, 40-59 weak or dependent on unseen visuals,
60-74 worthwhile personality/commentary, 75-89 strong, 90-100 exceptional. Do not reward raw duration.
Judge supplied speech, not the discovery reason. Ordinary entertaining exchanges count. Return every
ID exactly once with a brief concrete reason. Abbreviated excerpts are incomplete evidence."""
    cards = []
    for beat in beats:
        a, b = source.bounds(source.Span(first=beat["first"], last=beat["last"]), items)
        speech = source.speech_rows(items[a:b+1])
        excerpt = speech if len(speech) <= 3000 else speech[:1100]+"\n[...]\n"+speech[len(speech)//2-400:len(speech)//2+400]+"\n[...]\n"+speech[-1100:]
        cards.append({"id": beat["id"], "speech": excerpt, "abbreviated": len(speech)>3000})
    return rate_cards(cards, evaluator, system, "episode-screen", "Assessing source scenes", warnings)


def assemble(pool, rankings, items):
    lookup = {s["beat"]["id"]: s for s in pool}
    picked, decisions, used = [], [], []
    for rating in sorted(rankings, key=lambda r: -r["score"]):
        seq = lookup[rating["id"]]
        spans = compiled(seq, items)
        length = seconds(spans)
        reason = "selected"
        if rating["score"] < 60 or seq["edit"]["value"] < 2 or not spans:
            reason = "below editorial threshold"
        elif any(a["asset"] == b["asset"] and a["start_us"] < b["end_us"] and b["start_us"] < a["end_us"] for a in spans for b in used):
            reason = "overlapping selected footage"
        else:
            picked.append(seq)
            used.extend(spans)
        decisions.append({**rating, "edited_seconds": round(length, 2), "decision": reason})
    order = {u["asset"]: i for i, u in enumerate(items)}
    picked.sort(key=lambda s: (order[compiled(s, items)[0]["asset"]], compiled(s, items)[0]["start_us"]))
    return picked, decisions


def timeline(sequences, items):
    spans = [{**span, "sequence": s["beat"]["id"]} for s in sequences for span in compiled(s, items)]
    order = {u["asset"]: i for i, u in enumerate(items)}
    spans.sort(key=lambda s: (order[s["asset"]], s["start_us"]))
    cursor = 0
    for i, span in enumerate(spans):
        if i and spans[i-1]["asset"] == span["asset"] and spans[i-1]["end_us"] > span["start_us"]:
            raise ModelAnchorError("Two scenes overlap in the assembled episode.")
        span["output_start_us"] = cursor
        cursor += span["end_us"]-span["start_us"]
    return spans


def critique(sequences, items, evaluator, iteration, warnings):
    system = RULES + """
Review the pacing and joins of this assembled episode, in source order.
Look for repeated thoughts, abrupt topic changes, unfinished setups, missing reactions, staccato edits
and long sections with little substance. Use only supplied speech and timings. Give actionable retained
passage/gap instructions or recommend discarding a scene; never ask another transcript-only pass to watch
video or verify unseen gameplay. Do not demand a change solely to increase cut count. No issues is valid."""
    packs, current, issues = [], [], []
    for seq in sequences:
        card = {"sequence": seq["beat"]["id"], "retained_ranges": scene_evidence(seq, items, with_context=True),
                "pause_decisions": seq["edit"]["gaps"]}
        if current and evaluator.request_size(system, json.dumps({"assembled": current+[card]}, ensure_ascii=False), source.Review) > evaluator.discovery_budget:
            packs.append(current)
            current = [current[-1]] if len(current)>1 else []
            # Do not let overlap itself overflow a context window.
            if current and evaluator.request_size(system, json.dumps({"assembled": current+[card]}, ensure_ascii=False), source.Review) > evaluator.discovery_budget:
                current = []
        current.append(card)
    if current:
        packs.append(current)
    for i, pack in enumerate(packs):
        ids = {p["sequence"] for p in pack}
        def validate(result):
            if any(x.sequence not in ids for x in result.issues):
                raise ModelAnchorError("Reference supplied scene IDs only.")
        result = source.request(evaluator, system, {"assembled": pack}, source.Review, f"episode-critic-{iteration}-{i}", validate,
            recovery=lambda result: source.Review(issues=[x for x in result.issues if x.sequence in ids] if result else []),
            warnings=warnings, warning="Part of the pacing review was unavailable. Check the draft's joins and pacing before approving.")
        for issue in result.issues:
            if issue.model_dump() not in issues:
                issues.append(issue.model_dump())
    return issues


def plan(items, evaluator, progress, previous=None, guidance=""):
    warnings = []
    reuse = previous and previous.get("version") == VERSION
    beats = copy.deepcopy(previous["beats"]) if reuse else source.discover(items, evaluator, lambda p: progress(p*0.25), warnings)
    positions = {u["id"]: i for i, u in enumerate(items)}
    for left, right in zip(beats, beats[1:]):
        if items[positions[left["last"]]]["asset"] == items[positions[right["first"]]]["asset"]:
            middle = (positions[left["last"]]+positions[right["first"]])//2
            left["context_last"] = items[middle]["id"]
            right["context_first"] = items[middle+1]["id"]
    screening = copy.deepcopy(previous["screening"]) if reuse else screen_scenes(beats, items, evaluator, warnings)
    progress(0.35)
    eligible = {r["id"] for r in screening if r["score"] >= 60}
    candidates = [b for b in beats if b["id"] in eligible]
    pool = copy.deepcopy(previous["scene_pool"]) if reuse else []
    if reuse:
        active = {s["beat"]["id"] for s in previous["sequences"]}
        replacements = edit_scenes([b for b in beats if b["id"] in active], items, evaluator,
                                  "episode-edit", guidance, warnings, {s["beat"]["id"]: s["edit"] for s in pool})
        pool = [s for s in pool if s["beat"]["id"] not in active]+replacements
    else:
        pool = edit_scenes(candidates, items, evaluator, "episode-edit", guidance, warnings)
    progress(0.65)
    rankings = rank_scenes(pool, items, evaluator, warnings)
    history, seen, issues = [], set(), []
    for iteration in range(2):
        selected, decisions = assemble(pool, rankings, items)
        retained = timeline(selected, items)
        fingerprint = json.dumps(retained, sort_keys=True)
        if fingerprint in seen:
            break
        seen.add(fingerprint)
        evaluator.report(f"Reviewing episode pacing and joins: pass {iteration+1} of 2.")
        issues = critique(selected, items, evaluator, iteration, warnings)
        history.append({"iteration": iteration, "issues": issues, "selected": [s["beat"]["id"] for s in selected],
                        "edits": [copy.deepcopy(s["edit"]) for s in selected], "duration": seconds(retained)})
        if not issues or iteration == 1:
            break
        revised = []
        for i, seq in enumerate(selected):
            notes = [x["instruction"] for x in issues if x["sequence"] == seq["beat"]["id"]]
            if notes:
                try:
                    seq["edit"] = compact(seq["beat"], items, evaluator, f"episode-revise-{i}",
                        guidance+"\n"+"\n".join(notes), seq["edit"], warnings).model_dump()
                except ModelOutputError:
                    warnings.append(f"Scene {seq['beat']['id']} kept its last verified edit; the pacing change could not be verified.")
                revised.append(seq)
        if revised:
            ids = {s["beat"]["id"] for s in revised}
            rankings = [r for r in rankings if r["id"] not in ids]+rank_scenes(revised, items, evaluator, warnings)
        progress(0.85)
    if not retained and warnings:
        raise ModelOutputError("No verified episode edit could be produced. Completed work is saved; resume to try again.")
    progress(1)
    return {"version": VERSION, "beats": beats, "screening": screening, "scene_pool": pool, "rankings": rankings,
            "selection": decisions, "sequences": selected, "retained": retained, "duration": seconds(retained),
            "review_history": history, "issues": issues, "warnings": list(dict.fromkeys(warnings)),
            "guidance": guidance,
            "metrics": {"mapped_scenes": len(beats), "eligible_scenes": len(candidates),
                        "edited_scenes": len(pool), "selected_scenes": len(selected),
                        "retained_ranges": len(retained)}}
