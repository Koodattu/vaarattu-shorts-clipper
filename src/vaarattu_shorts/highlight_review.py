"""Bounded editorial patches on verified speech, with explicit resolution records."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Literal

from pydantic import Field

from . import highlight_edit as source, highlight_episode as episode
from .contracts import Contract
from .llm import ModelAnchorError, ModelOutputError


class Decision(Contract):
    sequence: str
    verdict: Literal["acceptable", "change", "unresolved"]
    remove: list[source.Span] = Field(max_length=30)
    restore: list[source.Span] = Field(max_length=12)
    pauses: list[episode.Pause] = Field(max_length=20)
    protect: list[source.Span] = Field(max_length=20, description="Necessary setup/payoff passages which must survive this and later changes.")
    reason: str = Field(min_length=1, max_length=300)


class Decisions(Contract):
    scenes: list[Decision] = Field(max_length=12)


class Check(Contract):
    sequence: str
    verdict: Literal["resolved", "unresolved"]
    reason: str = Field(min_length=1, max_length=300)


class Checks(Contract):
    scenes: list[Check] = Field(max_length=12)


class Duplicates(Contract):
    duplicates: list[episode.Duplicate] = Field(max_length=24)


def fingerprint(scene):
    return hashlib.sha256(json.dumps(scene, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def passage_ids(spans, items):
    result = set()
    for span in spans:
        a, b = source.bounds(source.Span.model_validate(span), items)
        result.update(u["id"] for u in items[a:b+1])
    return result


def patch(scene, decision, items, selected):
    """Apply all instructions atomically; never guess an anchor or rewrite speech."""
    old = copy.deepcopy(scene)
    spans = episode.compiled(scene, items)
    kept = passage_ids([{"first": s["first"], "last": s["last"]} for s in spans], items)
    removed = passage_ids([s.model_dump() for s in decision.remove], items)
    protected = passage_ids([s.model_dump() for s in decision.protect], items)
    required = set()
    for context in scene.get("required_context", []):
        required.update(passage_ids([{"first": context["first"], "last": context["last"]}], items))
    required.update(scene.get("protected_passages", []))
    if not removed <= kept:
        raise ModelAnchorError("Remove only speech in the current output, not reference-only passages.")
    if removed & (protected | required):
        raise ModelAnchorError("Do not remove protected setup or payoff.")
    result = copy.deepcopy(scene)
    if decision.restore:
        additions = [episode.ContextAddition(sequence=decision.sequence, reason=decision.reason, **s.model_dump())
                     for s in decision.restore]
        result = episode.add_context(result, additions, items, selected, reference=old)
    available = passage_ids(result["edit"]["spans"], items) - removed
    if not protected <= available:
        raise ModelAnchorError("Protect only passages retained by this edit.")
    restored = passage_ids([s.model_dump() for s in decision.restore], items)
    if restored & removed:
        raise ModelAnchorError("Do not restore and remove the same passage.")
    ranges = []
    for position, u in enumerate(items):
        if u["id"] not in available:
            continue
        if ranges and ranges[-1][1]+1 == position and items[ranges[-1][1]]["asset"] == u["asset"]:
            ranges[-1][1] += 1
        else:
            ranges.append([position, position])
    result["edit"]["spans"] = [{"first": items[a]["id"], "last": items[b]["id"]} for a, b in ranges]
    base = source.compile_edit(source.Edit(spans=[source.Span(**s) for s in result["edit"]["spans"]], gaps=[], reason=decision.reason), items, allowance=float("inf"))
    allowed_gaps = {f"g{a['id']}:{b['id']}" for a, b in zip(items, items[1:])
                    if a["asset"] == b["asset"] and b["speech_start_us"]-a["speech_end_us"] >= 1500000
                    and any(s["asset"] == a["asset"] and s["start_us"] <= a["speech_end_us"] < b["speech_start_us"] <= s["end_us"] for s in base)}
    gaps = {g["id"]: g for g in result["edit"]["gaps"]}
    seen = set()
    for pause in decision.pauses:
        if pause.id not in allowed_gaps or pause.evidence not in available:
            raise ModelAnchorError("Change only a retained gap with retained passage evidence.")
        if pause.action != "lead_in" and pause.seconds != 0:
            raise ModelAnchorError("Use seconds=0 for keep and shorten.")
        if pause.id in seen:
            raise ModelAnchorError("Change each pause once.")
        seen.add(pause.id)
        gaps[pause.id] = pause.model_dump()
    result["edit"]["gaps"] = list(gaps.values())
    if not available:
        result["edit"].update(value=1, gaps=[])
    result["protected_passages"] = sorted(required | protected | restored)
    retained = episode.compiled(result, items)
    others = [s for seq in selected if seq["beat"]["id"] != scene["beat"]["id"] for s in episode.compiled(seq, items)]
    if any(a["asset"] == b["asset"] and a["start_us"] < b["end_us"] and b["start_us"] < a["end_us"] for a in retained for b in others):
        raise ModelAnchorError("The changed scene overlaps another selected scene.")
    return result


def measured_change(before, after, items, decision):
    """Report compiled timing differences, rather than asking the reviewer to infer them."""
    def clock(scene):
        positions, cursor = {}, 0
        for span in episode.compiled(scene, items):
            for u in source.sequence_items(scene["beat"], items):
                if span["start_us"] <= u["speech_start_us"] and u["speech_end_us"] <= span["end_us"]:
                    positions[u["id"]] = (cursor+u["speech_start_us"]-span["start_us"], cursor+u["speech_end_us"]-span["start_us"])
            cursor += span["end_us"]-span["start_us"]
        return positions, cursor/1e6
    old, old_seconds = clock(before)
    new, new_seconds = clock(after)
    pauses = []
    for pause in decision.pauses:
        left, right = pause.id[1:].split(":")
        if left in old and right in old and left in new and right in new:
            pauses.append({"id": pause.id, "before_seconds": round((old[right][0]-old[left][1])/1e6, 3),
                           "after_seconds": round((new[right][0]-new[left][1])/1e6, 3)})
    return {"before_seconds": round(old_seconds, 3), "after_seconds": round(new_seconds, 3),
            "removed_passages": sorted(old.keys()-new.keys()), "restored_passages": sorted(new.keys()-old.keys()), "pauses": pauses}


def cards_for(selected, items, targets, records):
    cards, cursor, previous_end = [], 0, None
    by_id = {u["id"]: u for u in items}
    for i, scene in enumerate(selected):
        spans = episode.compiled(scene, items)
        evidence = episode.scene_evidence(scene, items, True, cursor, previous_end)
        cursor += round(episode.seconds(spans)*1e6)
        previous_end = cursor-(spans[-1]["end_us"]-by_id[spans[-1]["last"]]["speech_end_us"])
        ident = scene["beat"]["id"]
        if ident not in targets:
            continue
        neighbors = []
        for j in [i-1, i+1]:
            if 0 <= j < len(selected):
                other = episode.scene_evidence(selected[j], items)
                neighbors.append({"sequence": selected[j]["beat"]["id"], "side": "before" if j<i else "after",
                                  "speech": (other[-1]["speech"][-600:] if j<i else other[0]["speech"][:600])})
        cards.append({"sequence": ident, "output_ranges": evidence, "neighbors": neighbors,
                      "pause_recovery": scene.get("pause_recovery"),
                      "pause_overrides": [{k: v for k, v in gap.items() if k != "reason"} for gap in scene["edit"]["gaps"]], "protected_passages": scene.get("protected_passages", []),
                      "previous_decisions": [{k: v for k, v in r.items() if k != "before_scene"} for r in records if r["sequence"] == ident][-2:]})
    return cards


REVIEW_RULES = episode.RULES + """
Review each supplied scene exactly once. Judge the actual output clock and retained speech, not
source time gaps. Reference-only speech is excluded. Neighbors are context, not editable here.
Judge long retained pauses independently of the previous editor. A following reaction alone does not
justify a long wait: require speech-supported anticipation or comic timing, otherwise shorten the gap.
Check pause_recovery fallback_gaps explicitly: these pauses were kept because instructions could not
be verified, not because an editor established their value.
Check that a reaction still has its necessary question/setup; do not assume a missing setup is intentional.
Return acceptable when the scene works. Do not invent objections or remove purposeful repetition,
emotional emphasis or natural short pauses. Return change only for a concrete improvement:
remove whole supplied output passage ranges, restore only offered reference passages, or change
an existing retained gap. Pause IDs contain both passage IDs and a colon; evidence is a retained
passage ID, never a quotation. shorten uses seconds=0; lead_in uses 1-15 and the following reaction.
List necessary setup/payoff in protect. Do not remove protected speech. State why the proposed
change preserves meaning. Consider previous decisions; do not undo an earlier cut just to express
another preference. If a prior cut damaged meaning, report unresolved rather than oscillating.
For acceptable/unresolved return empty remove, restore, pauses and protect. For change supply the
correction directly; do not write instructions requiring a second editor. No new speech or timestamps.
"""


def review(selected, items, evaluator, targets, records, key, verify=False):
    cards = cards_for(selected, items, targets, records)
    system = (episode.RULES + """
Verify the previous corrections and affected joins in each supplied edited scene exactly once.
Use the output clock, retained speech, neighbors and previous decisions. Check whether the specific
problem is resolved without damaging necessary setup/payoff. measured_change is computed from the
actual compiled before/after timelines: use it to determine whether the correction happened.
A shortened gap normally retains about 0.8 seconds of speech margins; a nonzero pause does NOT mean
the shortening failed. Do not demand zero pauses or invent another shortening target.
Do not search for stylistic tweaks.
Return resolved or unresolved with a concrete reason. Reference-only speech is not in the video.
""") if verify else REVIEW_RULES
    schema = Checks if verify else Decisions
    packs, current = [], []
    for card in cards:
        if current and (len(current) == 12 or evaluator.request_size(system, json.dumps({"scenes": current+[card]}, ensure_ascii=False), schema) > evaluator.discovery_budget):
            packs.append(current)
            current = []
        current.append(card)
    if current:
        packs.append(current)
    lookup = {s["beat"]["id"]: s for s in selected}
    def request_pack(job):
        index, pack = job
        ids = {c["sequence"] for c in pack}
        def validate(result):
            actual = [s.sequence for s in result.scenes]
            if len(actual) != len(ids) or set(actual) != ids:
                raise ModelAnchorError("Return a verdict for every supplied scene exactly once.")
        def validate_decision(decision):
            if verify:
                return
            changes = decision.remove or decision.restore or decision.pauses or decision.protect
            if (decision.verdict == "change") != bool(changes):
                raise ModelAnchorError("Only change verdicts may contain edits, and must contain a correction. For acceptable/unresolved all edit lists must be empty.")
            if decision.verdict == "change":
                patch(lookup[decision.sequence], decision, items, selected)
        evaluator.report(f"{'Checking corrections' if verify else 'Reviewing scenes'}: batch {index+1} of {len(packs)}.")
        try:
            results = source.request(evaluator, system, {"scenes": pack}, schema, f"{key}-{index}", validate).scenes
        except ModelOutputError:
            return [Check(sequence=c["sequence"], verdict="unresolved", reason="The review response could not be verified.") for c in pack]
        verified = []
        for decision in results:
            try:
                validate_decision(decision)
            except ValueError as exc:
                card = next(c for c in pack if c["sequence"] == decision.sequence)
                def validate_repair(result):
                    if len(result.scenes) != 1 or result.scenes[0].sequence != card["sequence"]:
                        raise ModelAnchorError("Return this scene exactly once.")
                    validate_decision(result.scenes[0])
                try:
                    decision = source.request(evaluator, system, {"scenes": [card], "previous_decision": decision.model_dump(),
                        "correction_needed": str(exc)}, schema, f"{key}-repair-{card['sequence']}", validate_repair).scenes[0]
                except ModelOutputError:
                    decision = Check(sequence=card["sequence"], verdict="unresolved", reason="The suggested correction could not be verified; the previous edit was kept.")
            verified.append(decision)
        return verified
    return [s for batch in source.parallel_requests(evaluator, request_pack, list(enumerate(packs))) for s in batch]


def duplicates(selected, items, evaluator):
    if len(selected) < 2:
        return []
    index = [{"sequence": s["beat"]["id"], "summary": s["edit"]["reason"],
              "sample": " ".join(r["speech"] for r in episode.scene_evidence(s, items))[:500]} for s in selected]
    system = episode.RULES + """
Find potential repeated scenes in this compact episode overview. Similar topics alone are not duplicates.
Keep distinct reactions, jokes and payoffs. Return only clear candidates for a detailed comparison.
Never drop a scene named as keep. Empty duplicates is valid.
"""
    if evaluator.request_size(system, json.dumps({"scenes": index}, ensure_ascii=False), Duplicates) > evaluator.discovery_budget:
        index = [{"sequence": c["sequence"], "summary": c["summary"]} for c in index]
    # For unusually long episodes, compare overlapping index windows rather than truncate scenes.
    packs, current = [], []
    for card in index:
        if current and evaluator.request_size(system, json.dumps({"scenes": current+[card]}, ensure_ascii=False), Duplicates) > evaluator.discovery_budget:
            packs.append(current)
            current = current[-2:] if len(current)>2 else []
        current.append(card)
    if current:
        packs.append(current)
    found = []
    for i, pack in enumerate(packs):
        ids = {c["sequence"] for c in pack}
        def validate(result):
            dropped = {d.drop for d in result.duplicates}
            if len(dropped) != len(result.duplicates) or any(d.drop not in ids or d.keep not in ids or d.keep in dropped for d in result.duplicates):
                raise ModelAnchorError("Use supplied IDs; keep a different scene which is not dropped. Drop each scene once.")
        result = source.request(evaluator, system, {"scenes": pack}, Duplicates, f"episode-duplicates-{i}", validate)
        for pair in result.duplicates:
            pair_scenes = [s for s in selected if s["beat"]["id"] in {pair.drop, pair.keep}]
            evidence = [{"sequence": s["beat"]["id"], "output": episode.scene_evidence(s, items)} for s in pair_scenes]
            def confirm(result):
                if any(d.drop != pair.drop or d.keep != pair.keep for d in result.duplicates) or len(result.duplicates)>1:
                    raise ModelAnchorError("Confirm only the supplied pair, or return an empty list.")
            checked = source.request(evaluator, episode.RULES+"\nConfirm a duplicate only from this full edited evidence. Keep both if either adds a distinct joke, setup or payoff.",
                {"candidate": pair.model_dump(), "scenes": evidence}, Duplicates, f"episode-duplicate-check-{pair.drop}-{pair.keep}", confirm)
            found.extend(checked.duplicates)
    dropped = {d.drop for d in found}
    return list({d.drop: d for d in found if d.keep not in dropped}.values())


def finish(pool, rankings, items, evaluator, floor, warnings, progress):
    records, attempts, reviewed, seen = [], {}, set(), {}
    selected, decisions = episode.assemble(pool, rankings, items, floor)
    # A single global check avoids resending the whole episode with every local review.
    try:
        pairs = duplicates(selected, items, evaluator)
    except ModelOutputError:
        pairs = []
        warnings.append("The episode repetition check could not be verified.")
    for pair in pairs:
        scene = next(s for s in pool if s["beat"]["id"] == pair.drop)
        scene["excluded_as_duplicate_of"] = pair.keep
        records.append({"sequence": pair.drop, "status": "verified", "reason": pair.reason, "duplicate_of": pair.keep})
    selected, decisions = episode.assemble(pool, rankings, items, floor)
    targets = {s["beat"]["id"] for s in selected}
    cycle = 0
    while targets:
        current = {s["beat"]["id"]: s for s in selected}
        checks = {i for i in targets if attempts.get(i, 0)>=2}
        changes = targets-checks
        results = review(selected, items, evaluator, changes, records, f"episode-polish-{cycle}") if changes else []
        if checks:
            results += review(selected, items, evaluator, checks, records, f"episode-verify-{cycle}", verify=True)
        changed = set()
        for decision in results:
            ident = decision.sequence
            scene = current[ident]
            record = {"sequence": ident, "reason": decision.reason, "before": fingerprint(scene)}
            reviewed.add(ident)
            if decision.verdict in {"acceptable", "resolved"}:
                record["status"] = "verified"
            elif decision.verdict == "unresolved":
                record["status"] = "unresolved"
            else:
                attempts[ident] = attempts.get(ident, 0)+1
                try:
                    edited = patch(scene, decision, items, selected)
                    before = passage_ids(scene["edit"]["spans"], items)
                    restored = passage_ids([s.model_dump() for s in decision.restore], items)
                    removed_before = set().union(*(set(r.get("removed", [])) for r in records if r["sequence"] == ident))
                    new_hash = fingerprint(edited)
                    if restored & removed_before or new_hash in seen.setdefault(ident, {fingerprint(scene)}):
                        record.update(status="declined", reason="This correction reverses an earlier edit or makes no change. "+decision.reason)
                    else:
                        seen[ident].add(new_hash)
                        record.update(status="applied", after=new_hash, before_scene=copy.deepcopy(scene), measured_change=measured_change(scene, edited, items, decision), removed=sorted(before-passage_ids(edited["edit"]["spans"], items)), patch=decision.model_dump())
                        scene.clear()
                        scene.update(edited)
                        changed.add(ident)
                except ModelAnchorError as exc:
                    record.update(status="unresolved", reason=str(exc))
            records.append(record)
        if changed:
            rankings = [r for r in rankings if r["id"] not in changed]+episode.rank_scenes([s for s in pool if s["beat"]["id"] in changed], items, evaluator, warnings)
        old_selected = selected
        selected, decisions = episode.assemble(pool, rankings, items, floor)
        ids = {s["beat"]["id"] for s in selected}
        restored_duplicate = False
        for scene in pool:
            if scene.get("excluded_as_duplicate_of") and scene["excluded_as_duplicate_of"] not in ids:
                scene.pop("excluded_as_duplicate_of")
                restored_duplicate = True
        if restored_duplicate:
            selected, decisions = episode.assemble(pool, rankings, items, floor)
            ids = {s["beat"]["id"] for s in selected}
        targets = (changed & ids) | (ids-reviewed)
        # A changed join affects the adjacent scene even when its own speech is unchanged.
        for sequence in (old_selected, selected):
            for i, scene in enumerate(sequence):
                if scene["beat"]["id"] in changed:
                    targets.update(sequence[j]["beat"]["id"] for j in [i-1, i+1] if 0<=j<len(sequence) and sequence[j]["beat"]["id"] in ids)
        # Neighbor checks are verification, not another invitation to rewrite unchanged scenes.
        neighbor_checks = targets-changed-(ids-reviewed)
        if neighbor_checks:
            for check in review(selected, items, evaluator, neighbor_checks, records, f"episode-joins-{cycle}", verify=True):
                records.append({"sequence": check.sequence, "status": "verified" if check.verdict=="resolved" else "unresolved", "reason": check.reason})
            targets -= neighbor_checks
        progress(0.85)
        cycle += 1
    latest = {r["sequence"]: r for r in records}
    selected_ids = {s["beat"]["id"] for s in selected}
    issues = [{"sequence": i, "instruction": r["reason"]} for i, r in latest.items() if i in selected_ids and r["status"] != "verified"]
    return selected, decisions, rankings, records, issues


def summary(plan):
    if plan.get("version") != "episode-v5":
        return None
    latest = {r["sequence"]: r for r in plan.get("review_history", [])}
    selected = {s["beat"]["id"] for s in plan["sequences"]}
    statuses = [latest[i]["status"] if i in latest else "not_reviewed" for i in selected]
    return {"verified": statuses.count("verified"), "unresolved": sum(s in {"unresolved", "applied", "declined"} for s in statuses),
            "not_reviewed": statuses.count("not_reviewed"),
            "corrections": sum(r["status"] == "applied" for r in plan.get("review_history", []))}
