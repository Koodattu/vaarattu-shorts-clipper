"""Screen by quality, edit worthwhile scenes, then assemble a chronological episode."""
from __future__ import annotations

import copy
import json
from typing import Literal

from pydantic import Field

from . import highlight_edit as source
from .contracts import Contract
from .llm import ModelAnchorError, ModelOutputError

VERSION = "episode-v4"
FINAL_SCORE_FLOOR = 75
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


def reference_context(scene, items):
    retained = compiled(scene, items)
    positions = {u["id"]: i for i, u in enumerate(items)}
    kept = {u["id"] for u in items if any(s["asset"] == u["asset"] and s["start_us"] <= u["speech_start_us"]
            and u["speech_end_us"] <= s["end_us"] for s in retained)}
    nearby = {}
    for span in retained:
        a, b = positions[span["first"]], positions[span["last"]]
        for u in items[max(0, a-2):a]+items[b+1:b+3]:
            if u["asset"] == span["asset"] and u["id"] not in kept:
                nearby[u["id"]] = {"id": u["id"], "text": u["text"]}
    return list(nearby.values())


def scene_evidence(scene, items, with_context=False, output_start_us=0, previous_speech_end_us=None):
    """Present the compiled output clock; source gaps never masquerade as pauses."""
    context = source.sequence_items(scene["beat"], items)
    result, cursor, previous_end = [], output_start_us, previous_speech_end_us
    for span in compiled(scene, items):
        speech = [u for u in context if span["start_us"] <= u["speech_start_us"] and u["speech_end_us"] <= span["end_us"]]
        rows = []
        for u in speech:
            start = cursor+u["speech_start_us"]-span["start_us"]
            end = cursor+u["speech_end_us"]-span["start_us"]
            pause = max(0, start-previous_end)/1e6 if previous_end is not None else (start-cursor)/1e6
            rows.append(f"{u['id']} output {start/1e6:.2f}-{end/1e6:.2f}s pause_before={pause:.2f}s: {u['text']}")
            previous_end = end
        duration = span["end_us"]-span["start_us"]
        result.append({"first": span["first"], "last": span["last"],
                       "output_start_seconds": cursor/1e6, "output_end_seconds": (cursor+duration)/1e6,
                       "duration_seconds": duration/1e6, "cut_before": "jump_cut" if result else "scene_start",
                       "speech": "\n".join(rows)})
        cursor += duration
    if with_context and result:
        result[0]["reference_only_not_in_video"] = reference_context(scene, items)
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
Output timestamps and pause_before describe the actual edited scene. [CUT] is a jump cut, not waiting.
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
                      "retained_ranges": len(spans), "speech": excerpt, "abbreviated": len(speech)>2400})
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


def assemble(pool, rankings, items, floor=FINAL_SCORE_FLOOR, compiled_spans=None):
    if compiled_spans is None:
        compiled_spans = {s["beat"]["id"]: compiled(s, items) for s in pool}
    lookup = {s["beat"]["id"]: s for s in pool}
    picked, decisions, used = [], [], []
    for rating in sorted(rankings, key=lambda r: -r["score"]):
        seq = lookup[rating["id"]]
        spans = compiled_spans[rating["id"]]
        length = seconds(spans)
        reason = "selected"
        if seq.get("excluded_as_duplicate_of"):
            reason = "repeated content kept in "+seq["excluded_as_duplicate_of"]
        elif rating["score"] < floor or seq["edit"]["value"] < 2 or not spans:
            reason = "below editorial threshold"
        elif any(a["asset"] == b["asset"] and a["start_us"] < b["end_us"] and b["start_us"] < a["end_us"] for a in spans for b in used):
            reason = "overlapping selected footage"
        else:
            picked.append(seq)
            used.extend(spans)
        decisions.append({**rating, "edited_seconds": round(length, 2), "decision": reason})
    order = {u["asset"]: i for i, u in enumerate(items)}
    picked.sort(key=lambda s: (order[compiled_spans[s["beat"]["id"]][0]["asset"]],
                               compiled_spans[s["beat"]["id"]][0]["start_us"]))
    return picked, decisions


def timeline(sequences, items, compiled_spans=None):
    spans = [{**span, "sequence": s["beat"]["id"]} for s in sequences
             for span in (compiled_spans[s["beat"]["id"]] if compiled_spans is not None else compiled(s, items))]
    order = {u["asset"]: i for i, u in enumerate(items)}
    spans.sort(key=lambda s: (order[s["asset"]], s["start_us"]))
    cursor = 0
    for i, span in enumerate(spans):
        if i and spans[i-1]["asset"] == span["asset"] and spans[i-1]["end_us"] > span["start_us"]:
            raise ModelAnchorError("Two scenes overlap in the assembled episode.")
        span["output_start_us"] = cursor
        cursor += span["end_us"]-span["start_us"]
    return spans


class ContextAddition(source.Span):
    sequence: str
    reason: str = Field(min_length=1, max_length=250)


class Duplicate(Contract):
    drop: str
    keep: str
    reason: str = Field(min_length=1, max_length=250)


class EpisodeReview(Contract):
    issues: list[source.Issue] = Field(max_length=24)
    context: list[ContextAddition] = Field(max_length=24)
    duplicates: list[Duplicate] = Field(max_length=24)


def add_context(scene, additions, items, selected, reference=None):
    """Add only offered reference passages, never a whole lower-ranked scene."""
    result = copy.deepcopy(scene)
    allowed = {u["id"] for u in reference_context(reference or scene, items)}
    ranges = [source.bounds(source.Span(**s), items) for s in scene["edit"]["spans"]]
    for addition in additions:
        a, b = source.bounds(addition, items)
        if not all(u["id"] in allowed for u in items[a:b+1]):
            raise ModelAnchorError("Context additions must use only the supplied reference-only passages.")
        if items[a]["asset"] != items[ranges[0][0]]["asset"]:
            raise ModelAnchorError("Context must come from the same source part.")
        ranges.append((a, b))
    merged = []
    for a, b in sorted(ranges):
        if merged and a <= merged[-1][1]+1:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    first, last = items[merged[0][0]]["id"], items[merged[-1][1]]["id"]
    result["beat"].update(first=first, last=last, context_first=first, context_last=last)
    result["edit"]["spans"] = [{"first": items[a]["id"], "last": items[b]["id"]} for a, b in merged]
    spans = compiled(result, items)
    others = [s for seq in selected if seq["beat"]["id"] != scene["beat"]["id"] for s in compiled(seq, items)]
    if any(a["asset"] == b["asset"] and a["start_us"] < b["end_us"] and b["start_us"] < a["end_us"] for a in spans for b in others):
        raise ModelAnchorError("The requested context is already retained in another scene; do not duplicate footage.")
    result["required_context"] = scene.get("required_context", [])+[a.model_dump() for a in additions]
    return result


def critique(sequences, items, evaluator, iteration, warnings):
    system = RULES + """
Review the actual assembled OUTPUT timeline. Output times, pause_before and jump_cut markers describe
what viewers will see; never infer waiting from source passage IDs. reference_only_not_in_video is
excluded speech, not part of the edit. Ask to add it only when essential for setup, meaning or payoff.
Request minimal complete passage ranges in context; never restore a whole weak scene for atmosphere.
Use issues for specific filler/repetition cuts INSIDE strong scenes, preserving necessary setup/payoff.
Only remove speech in output_ranges; reference-only passages are already excluded.
The episode_index covers all selected scenes, including those outside this detailed batch. In duplicates,
drop a redundant scene only when the named keep scene expresses the same point without losing a distinct
joke, reaction, setup or payoff. Similar topics alone are not duplicates. Only drop scenes in this batch.
Do not drop a scene referenced as keep in this response. Reference supplied scene IDs only.
Give actionable passage/gap instructions; do not ask a transcript-only editor to watch unseen gameplay.
No need to fill time or reach a scene count. Empty lists are valid."""
    cards, cursor, previous_end = [], 0, None
    by_id = {u["id"]: u for u in items}
    for seq in sequences:
        evidence = scene_evidence(seq, items, with_context=True, output_start_us=cursor, previous_speech_end_us=previous_end)
        cards.append({"sequence": seq["beat"]["id"], "output_ranges": evidence})
        spans = compiled(seq, items)
        cursor += round(seconds(spans)*1e6)
        previous_end = cursor-(spans[-1]["end_us"]-by_id[spans[-1]["last"]]["speech_end_us"])
    index = [{"sequence": c["sequence"], "output_start": c["output_ranges"][0]["output_start_seconds"],
              "summary": next(s["edit"]["reason"] for s in sequences if s["beat"]["id"] == c["sequence"]),
              "speech_sample": c["output_ranges"][0]["speech"][:120]+" [...] "+c["output_ranges"][-1]["speech"][-120:]} for c in cards]
    def payload(pack):
        return {"episode_index": index, "assembled": pack}
    if cards and evaluator.request_size(system, json.dumps(payload([max(cards, key=lambda c: len(json.dumps(c)))]), ensure_ascii=False), EpisodeReview) > evaluator.discovery_budget:
        index = [{"sequence": c["sequence"], "summary": c["summary"][:100]} for c in index]
    packs, current = [], []
    for card in cards:
        if current and evaluator.request_size(system, json.dumps(payload(current+[card]), ensure_ascii=False), EpisodeReview) > evaluator.discovery_budget:
            packs.append(current)
            current = []
        current.append(card)
    if current:
        packs.append(current)
    combined = EpisodeReview(issues=[], context=[], duplicates=[])
    all_ids = {s["beat"]["id"] for s in sequences}
    for i, pack in enumerate(packs):
        ids = {p["sequence"] for p in pack}
        def validate(result):
            if any(x.sequence not in ids for x in [*result.issues, *result.context]):
                raise ModelAnchorError("Reference scenes from this detailed batch for edits and context.")
            for ident in {x.sequence for x in result.context}:
                seq = next(s for s in sequences if s["beat"]["id"] == ident)
                add_context(seq, [a for a in result.context if a.sequence == ident], items, sequences)
            dropped = {d.drop for d in result.duplicates}
            if any(d.drop not in ids or d.keep not in all_ids or d.keep in dropped for d in result.duplicates):
                raise ModelAnchorError("Drop only a scene in this batch and keep a different supplied scene that is not dropped.")
        result = source.request(evaluator, system, payload(pack), EpisodeReview, f"episode-critic-{iteration}-{i}", validate,
            recovery=lambda _: EpisodeReview(issues=[], context=[], duplicates=[]), warnings=warnings,
            warning="Part of the pacing review could not be verified. Check the draft's joins and context before approving.")
        combined.issues.extend(result.issues)
        combined.context.extend(result.context)
        combined.duplicates.extend(result.duplicates)
    # Cross-batch conflicts cannot remove both versions of an idea.
    dropped = {d.drop for d in combined.duplicates}
    combined.duplicates = [d for d in combined.duplicates if d.keep not in dropped]
    return combined


def plan(items, evaluator, progress, previous=None, guidance="", final_score_floor=FINAL_SCORE_FLOOR):
    if not 0 <= final_score_floor <= 100:
        raise ValueError("Choose a final score from 0 to 100.")
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
        replacements = edit_scenes([s["beat"] for s in pool if s["beat"]["id"] in active], items, evaluator,
                                  "episode-edit", guidance, warnings, {s["beat"]["id"]: s["edit"] for s in pool})
        pool = [s for s in pool if s["beat"]["id"] not in active]+replacements
    else:
        pool = edit_scenes(candidates, items, evaluator, "episode-edit", guidance, warnings)
    progress(0.65)
    rankings = rank_scenes(pool, items, evaluator, warnings)
    history, seen, issues = [], set(), []
    for iteration in range(2):
        selected, decisions = assemble(pool, rankings, items, final_score_floor)
        retained = timeline(selected, items)
        fingerprint = json.dumps(retained, sort_keys=True)
        if fingerprint in seen:
            break
        seen.add(fingerprint)
        evaluator.report(f"Reviewing episode pacing and joins: pass {iteration+1} of 2.")
        review = critique(selected, items, evaluator, iteration, warnings)
        issues = [i.model_dump() for i in review.issues]
        issues.extend({"sequence": a.sequence, "instruction": f"Needed context {a.first}-{a.last}: {a.reason}"} for a in review.context)
        issues.extend({"sequence": d.drop, "instruction": f"Repeated by {d.keep}: {d.reason}"} for d in review.duplicates)
        history.append({"iteration": iteration, "issues": issues, "selected": [s["beat"]["id"] for s in selected],
                        "edits": [copy.deepcopy(s["edit"]) for s in selected], "duration": seconds(retained),
                        "context": [a.model_dump() for a in review.context], "duplicates": [d.model_dump() for d in review.duplicates]})
        if not issues or iteration == 1:
            break
        revised = []
        for i, seq in enumerate(selected):
            ident = seq["beat"]["id"]
            if any(d.drop == ident for d in review.duplicates):
                continue
            notes = [x.instruction for x in review.issues if x.sequence == ident]
            additions = [a for a in review.context if a.sequence == ident]
            before = copy.deepcopy(seq)
            if notes:
                try:
                    seq["edit"] = compact(seq["beat"], items, evaluator, f"episode-revise-{i}",
                        guidance+"\n"+"\n".join(notes), seq["edit"], warnings).model_dump()
                except ModelOutputError:
                    warnings.append(f"Scene {seq['beat']['id']} kept its last verified edit; the pacing change could not be verified.")
            if additions and seq["edit"]["spans"]:
                try:
                    seq.update(add_context(seq, additions, items, selected, reference=before))
                except ModelAnchorError:
                    warnings.append(f"Scene {ident} needs a context check; its requested addition conflicted with another retained scene.")
            if notes or additions:
                revised.append(seq)
        if revised:
            ids = {s["beat"]["id"] for s in revised}
            rankings = [r for r in rankings if r["id"] not in ids]+rank_scenes(revised, items, evaluator, warnings)
        scores = {r["id"]: r["score"] for r in rankings}
        lookup = {s["beat"]["id"]: s for s in pool}
        for duplicate in review.duplicates:
            keep = lookup[duplicate.keep]
            if scores[duplicate.keep] >= final_score_floor and keep["edit"]["value"] >= 2 and compiled(keep, items):
                lookup[duplicate.drop]["edit"].update(spans=[], gaps=[], value=1)
                lookup[duplicate.drop]["excluded_as_duplicate_of"] = duplicate.keep
                for rating in rankings:
                    if rating["id"] == duplicate.drop:
                        rating.update(score=0, reason=duplicate.reason)
        progress(0.85)
    if not retained and warnings:
        raise ModelOutputError("No verified episode edit could be produced. Completed work is saved; resume to try again.")
    progress(1)
    return {"version": VERSION, "beats": beats, "screening": screening, "scene_pool": pool, "rankings": rankings,
            "selection": decisions, "sequences": selected, "retained": retained, "duration": seconds(retained),
            "review_history": history, "issues": issues, "warnings": list(dict.fromkeys(warnings)),
            "guidance": guidance, "final_score_floor": final_score_floor,
            "metrics": {"mapped_scenes": len(beats), "eligible_scenes": len(candidates),
                        "edited_scenes": len(pool), "selected_scenes": len(selected),
                        "retained_ranges": len(retained)}}
