"""Preview and render score-floor changes using saved editorial decisions."""
import copy
import json
import time

from . import highlight_episode as editing
from .storage import atomic_json, digest


def snapshot(settings, store, run_id, revision):
    from .highlights import revision_folder, run_folder
    run = store.get(run_id)
    folder = revision_folder(settings, run_id, revision)
    if run["result"].get("revision", 1) != revision or not (folder / "plan.json").is_file():
        raise ValueError("The saved edit changed or is not ready. Refresh this recording.")
    plan_hash = digest(folder / "plan.json")
    plan = json.loads((folder / "plan.json").read_text("utf-8"))
    if "scene_pool" not in plan or "rankings" not in plan:
        raise ValueError("This older draft has no saved scene scores. Rebuild it before adjusting the score floor.")
    transcripts = {}
    for source in run["config"]["manifest"]["sources"]:
        path = run_folder(settings, run_id) / source["asset"] / "asr" / "transcript.json"
        if not path.is_file():
            raise ValueError("The saved transcript is missing. Restore it before adjusting the score floor.")
        transcripts[source["asset"]] = json.loads(path.read_text("utf-8"))
    items = editing.units(transcripts)
    # Saved rendered ranges are authoritative for existing scenes, including older compilers.
    spans = {}
    for span in plan["retained"]:
        spans.setdefault(span["sequence"], []).append(span)
    for scene in plan["scene_pool"]:
        ident = scene["beat"]["id"]
        if ident not in spans:
            spans[ident] = editing.compiled(scene, items)
    return run, plan, plan_hash, items, spans


def select(plan, items, spans, floor):
    if not isinstance(floor, int) or not 0 <= floor <= 100:
        raise ValueError("Choose a minimum score from 0 to 100.")
    selected, decisions = editing.assemble(plan["scene_pool"], plan["rankings"], items, floor, spans)
    retained = editing.timeline(selected, items, spans)
    return selected, decisions, retained


def summary(selected, retained, floor, original_ids):
    ids = {s["beat"]["id"] for s in selected}
    frames = sum(max(1, round((s["end_us"]-s["start_us"])/1e6*30)) for s in retained)
    return {"score_floor": floor, "scenes": len(ids), "sections": len(retained), "duration": frames/30,
            "added": len(ids-original_ids), "removed": len(original_ids-ids)}


def preview(settings, store, run_id, revision):
    _, plan, plan_hash, items, spans = snapshot(settings, store, run_id, revision)
    original = {s["sequence"] for s in plan["retained"]}
    previews = []
    for floor in range(101):
        selected, _, retained = select(plan, items, spans, floor)
        previews.append(summary(selected, retained, floor, original))
    return {"revision": revision, "plan_hash": plan_hash,
            "current_floor": plan.get("final_score_floor", 60), "previews": previews}


def queue(settings, store, run_id, revision, floor, expected_hash):
    from .highlights import revision_folder
    run, plan, plan_hash, items, spans = snapshot(settings, store, run_id, revision)
    if plan_hash != expected_hash:
        raise ValueError("The edit changed after this preview. Refresh before rendering.")
    selected, decisions, retained = select(plan, items, spans, floor)
    if not retained:
        raise ValueError("No scenes meet this score floor. Lower it before rendering.")
    if retained == plan["retained"]:
        raise ValueError("This score floor keeps the same edit. There is no new video to render.")
    new_plan = copy.deepcopy(plan)
    selected_ids = {s["beat"]["id"] for s in selected}
    new_plan.update(sequences=selected, selection=decisions, retained=retained,
                    duration=editing.seconds(retained), final_score_floor=floor,
                    issues=[i for i in plan.get("issues", []) if i.get("sequence") in selected_ids],
                    metrics={**plan.get("metrics", {}), "selected_scenes": len(selected), "retained_ranges": len(retained)})
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = store.unpack(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
        result = current["result"]
        if current["state"] != "completed" or result.get("revision", 1) != revision:
            raise ValueError("Wait for this recording to finish, then refresh before rendering a new draft.")
        if digest(revision_folder(settings, run_id, revision) / "plan.json") != expected_hash:
            raise ValueError("The edit changed after this preview. Refresh before rendering.")
        next_revision = max([revision, *[h["revision"] for h in result.get("history", [])]])+1
        folder = revision_folder(settings, run_id, next_revision)
        atomic_json(folder / "plan.json", new_plan)
        result = {**result, "mode": "selection", "revision": next_revision, "parent_revision": revision,
                  "approved_revision": None, "has_draft": False, "has_final": False, "review": "unreviewed",
                  "revision_started": time.time(), "duration": 0, "warnings": [], "issues": [], "metrics": {},
                  "final_score_floor": floor, "plan_sha256": digest(folder / "plan.json"),
                  "selection_preview": summary(selected, retained, floor, {s["sequence"] for s in plan["retained"]})}
        db.execute("UPDATE runs SET state='queued',intent='',result=?,stage='selection',progress=0,message=?,updated=? WHERE id=?",
                   (json.dumps(result), "New score floor selected. Waiting to render the saved edit.", time.time(), run_id))
    return {"id": run_id, "revision": next_revision}
