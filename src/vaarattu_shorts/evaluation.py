"""Explicit, isolated comparisons; never queue production clips or change their reviews."""

from __future__ import annotations

import csv
import json
import sqlite3
import time
import uuid
from pathlib import Path

from . import discover, render
from .contracts import Candidate, Word
from .llm import Evaluator
from .processes import ToolError
from .storage import Store, atomic_json, digest


def saved_record(settings, table, record_id):
    if table not in {"runs", "clips"}:
        raise ValueError("Choose a saved run or clip.")
    with sqlite3.connect((settings.work / "state.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
    if row is None:
        raise ValueError("That saved run or clip was not found.")
    return dict(row)


def comparison_folder(settings, name):
    folder = settings.work / "evaluations" / f"{name}-{uuid.uuid4().hex[:12]}"
    folder.mkdir(parents=True)
    return folder


def compare_reasoning(settings, run_id, sections, stage="discovery", budget=None):
    saved = saved_record(settings, "runs", run_id)
    config = json.loads(saved["config"])
    if config["provider"] not in {"openai", "codex"}:
        raise ValueError("Low/medium comparisons require an OpenAI or Codex run.")
    path = settings.work / "runs" / run_id / "transcript.checkpoint.json"
    transcript = json.loads(path.read_text("utf-8"))["result"]
    regions = [
        {
            "start_us": n * discover.CORE_US,
            "end_us": min((n + 1) * discover.CORE_US, transcript["duration_us"]),
        }
        for n in sorted(set(sections))
    ]
    if not regions or any(r["start_us"] < 0 or r["start_us"] >= r["end_us"] for r in regions):
        raise ValueError("Choose section numbers within this recording (the first is 0).")
    seed = None
    if stage == "verification":
        selection_path = path.with_name("selection.checkpoint.json")
        selection = json.loads(selection_path.read_text("utf-8"))["result"]
        starts = {w["id"]: w["start_us"] for w in transcript["words"]}
        proposals = [
            c
            for c in selection["proposals"]
            if any(r["start_us"] <= starts.get(c["idea_word_id"], -1) < r["end_us"] for r in regions)
        ]
        if not proposals:
            raise ValueError(
                "These sections have no saved proposals; compare discovery or choose other sections."
            )
        seed = {
            "proposals": proposals,
            "verified": [],
            "coverage": [[r["start_us"], r["end_us"]] for r in regions],
        }
    cap = config.get("budget_usd", 0) if budget is None else budget
    if not 0 <= cap <= 100 or (config["provider"] == "openai" and cap <= 0):
        raise ValueError("Choose a combined API spending limit above zero and at most $100.")
    folder = comparison_folder(settings, f"reasoning-{stage}")
    atomic_json(folder / "transcript.json", transcript)
    atomic_json(
        folder / "manifest.json",
        {
            "source_run": run_id,
            "transcript_sha256": digest(path),
            "sections": regions,
            "stage": stage,
            "selection_version": discover.VERSION,
            "budget_usd": None if config["provider"] == "codex" else cap,
        },
    )
    store = Store(folder / "state.sqlite3")
    evaluation_run = store.admit({**config, "budget_usd": cap}, "comparison")
    results = []
    for effort in ("low", "medium"):
        output = folder / effort
        output.mkdir()
        evaluator = Evaluator(
            config["provider"],
            store,
            evaluation_run,
            output,
            cap,
            lambda: None,
            codex_config=config.get("codex"),
            discovery_reasoning=effort if stage == "discovery" else "low",
            verification_reasoning=effort if stage == "verification" else "low",
        )
        started = time.monotonic()
        selected = discover.discover(transcript, evaluator, lambda _: None, seed=seed, regions=regions)
        atomic_json(output / "selection.json", selected)
        results.append(
            {
                "effort": effort,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "proposals": len(selected["proposals"]),
                "reviewable": sum(v["eligible"] for v in selected["verified"]),
                "issues": selected["issues"],
            }
        )
        # A human review sheet; non-publication is deliberately not classified as generation failure.
        with (output / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "start_seconds",
                    "end_seconds",
                    "title",
                    "model_reason",
                    "would_post",
                    "cut_defect",
                    "needs_context",
                    "notes",
                ]
            )
            for item in selected["verified"]:
                c = Candidate.model_validate(item["candidate"])
                writer.writerow(
                    [item["start_us"] / 1e6, item["end_us"] / 1e6, c.title_fi, c.reason, "", "", "", ""]
                )
    atomic_json(folder / "comparison.json", {"results": results, "usage": store.usage(evaluation_run)})
    store.update(evaluation_run, state="completed")
    return folder


def benchmark_render(settings, clip_id):
    clip = saved_record(settings, "clips", clip_id)
    body = json.loads(clip["body"])
    section = Path(body.get("section") or "__missing__")
    if not section.is_file() or not body.get("mapping") or digest(section) != body.get("section_sha256"):
        raise ValueError("This comparison needs the clip's unchanged, aligned source section.")
    folder = comparison_folder(settings, "encoders")
    results = []
    for encoder in ("libx264", "h264_nvenc"):
        output = folder / encoder
        output.mkdir()
        try:
            result = render.render_clip(
                settings,
                section,
                body["mapping"],
                {**body, "video_encoder": encoder},
                body["layout"],
                [Word.model_validate(w) for w in body["words"]],
                output,
                lambda: None,
            )
            results.append(
                {
                    "encoder": encoder,
                    "status": "passed",
                    **{
                        k: result[k] for k in ("encode_seconds", "video_bytes", "output_duration_us", "flags")
                    },
                }
            )
        except (ValueError, ToolError) as exc:
            results.append({"encoder": encoder, "status": "failed", "reason": str(exc)})
    atomic_json(
        folder / "comparison.json", {"source_clip": clip_id, "revision": clip["revision"], "results": results}
    )
    return folder
