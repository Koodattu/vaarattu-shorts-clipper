"""Independent, resumable landscape highlight jobs and rendering."""
from __future__ import annotations

import json
import re
import shutil
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from . import highlight_edit as editing, highlight_sources as sources, render, transcribe, youtube
from .contracts import Contract
from .llm import Evaluator
from .pipeline import Pipeline, check_space
from .llm import local_server
from .processes import Interrupted, ToolError, run_tool
from .storage import Store, atomic_json, digest


class Start(Contract):
    manifest_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    target_minutes: int = Field(default=12, ge=5, le=20)
    provider: Literal["local", "gemini", "openai", "codex", "zai", "deepseek", "meta"] = "codex"
    local_model: Literal["gemma4-31b", "gemma4-26b-a4b"] = "gemma4-31b"
    context_size: Literal[16384, 32768] = 32768
    budget_usd: float = Field(default=0, ge=0, le=100)
    discovery_reasoning: Literal["low", "medium"] = "low"
    verification_reasoning: Literal["low", "medium"] = "medium"
    video_encoder: Literal["h264_nvenc", "libx264"] = "h264_nvenc"
    final_transcription: Literal[False] = False

    @model_validator(mode="after")
    def budget(self):
        if self.provider not in {"local", "codex"} and self.budget_usd <= 0:
            raise ValueError("Set an API spending limit for the selected provider.")
        if self.provider == "codex":
            self.budget_usd = 0
        return self


def store_for(settings):
    return Store(settings.work / "highlights" / "state.sqlite3")


def run_folder(settings, run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise KeyError(run_id)
    return settings.work / "highlights" / "runs" / run_id


def revision_folder(settings, run_id, revision):
    if revision < 1:
        raise ValueError("Choose a saved video revision.")
    return run_folder(settings, run_id) / "revisions" / str(revision)


def control(store, run_id, action, revision, guidance="", restore=None):
    if action in {"pause", "resume", "cancel"}:
        store.control(run_id, action)
        return
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        run = store.unpack(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
        result = run["result"]
        if run["state"] != "completed" or revision != result.get("revision"):
            raise ValueError("Refresh this highlight video before changing it; processing may still be running.")
        result = dict(result)
        if action == "approve":
            if not result.get("has_draft"):
                raise ValueError("Review a rendered draft before approving it.")
            result.update(mode="final", approved_revision=revision)
            state = "queued"
        elif action == "revise":
            if not guidance.strip() or len(guidance) > 2000:
                raise ValueError("Describe the changes in 1-2000 characters.")
            result.update(mode="draft", revision=max([revision, *[h["revision"] for h in result.get("history", [])]])+1, parent_revision=revision,
                          guidance=guidance.strip(), approved_revision=None, has_draft=False, has_final=False, review="unreviewed")
            state = "queued"
        elif action == "restore":
            saved = next((r for r in result.get("history", []) if r["revision"] == restore), None)
            if not saved or not saved.get("has_draft"):
                raise ValueError("Choose an earlier rendered draft.")
            result.update(saved, mode="draft", approved_revision=None, has_final=False, review="unreviewed")
            state = "completed"
        elif action == "reject":
            result.update(review="rejected", approved_revision=None)
            state = "completed"
        else:
            raise ValueError("This highlight action is unavailable.")
        db.execute("UPDATE runs SET state=?,intent='',result=?,message='',updated=? WHERE id=?",
                   (state, json.dumps(result), time.time(), run_id))


def public(settings, store, run):
    result = run["result"]
    revision = result.get("revision", 1)
    folder = revision_folder(settings, run["id"], revision)
    return {"id": run["id"], "title": run["config"]["manifest"]["title"],
            "state": run["state"], "stage": run["stage"], "progress": run["progress"],
            "message": run["message"], "revision": revision,
            "created": run["created"], "sources": run["config"]["manifest"]["sources"],
            "has_draft": bool(result.get("has_draft") and (folder / "draft.mp4").is_file()),
            "has_final": bool(result.get("has_final") and (folder / "final.mp4").is_file()),
            "duration": result.get("duration", 0), "issues": result.get("issues", []),
            "review": result.get("review"), "history": result.get("history", []),
            "shorter_than_target": result.get("shorter_than_target", False),
            "usage": {k: v for k, v in store.usage(run["id"]).items() if k != "requests"}}


def render_video(settings, plan, media, output, encoder, check, progress):
    """Encode pieces with PCM audio, then encode AAC once to avoid seam padding accumulation."""
    output.parent.mkdir(parents=True, exist_ok=True)
    final = output.stem == "final"
    width, height, bitrate = (1920, 1080, "10M") if final else (1280, 720, "4M")
    pieces, total_frames = [], 0
    work = output.parent / (output.stem+"-pieces")
    work.mkdir(exist_ok=True)
    for i, span in enumerate(plan["retained"]):
        check()
        check_space(settings)
        match = next(m for m in media if m["asset"] == span["asset"]
                     and m["start_us"] <= span["start_us"] and m["end_us"] >= span["end_us"])
        local = (span["start_us"]-match["mapping"]["origin_us"])/1e6
        duration = (span["end_us"]-span["start_us"])/1e6
        if local < 0 or local+duration > match["mapping"]["section_duration"]+0.05:
            raise ValueError("The highlight cut falls outside the verified source section.")
        frames = max(1, round(duration*30))
        duration = frames/30
        total_frames += frames
        piece = work / f"part-{i:04d}.mov"
        filters = (f"[0:v]fps=30,scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,"
                   f"tpad=stop_mode=clone:stop_duration=0.1,trim=end_frame={frames},setpts=N/(30*TB)[v];"
                   f"[0:a]atrim=duration={duration:.9f},asetpts=PTS-STARTPTS,aresample=48000,"
                   f"aformat=sample_fmts=s16:channel_layouts=stereo,apad,atrim=duration={duration:.9f},"
                   f"afade=t=in:d=0.005,afade=t=out:st={max(0, duration-0.005):.9f}:d=0.005[a]")
        encoding = (["-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "20", "-b:v", bitrate,
                     "-maxrate", "16M" if final else "6M", "-bufsize", "32M" if final else "12M"]
                    if encoder == "h264_nvenc" else ["-preset", "medium", "-crf", "20" if final else "24"])
        run_tool([settings.ffmpeg, "-nostdin", "-v", "error", "-y", "-ss", f"{local:.9f}",
                  "-i", match["path"], "-filter_complex", filters, "-map", "[v]", "-map", "[a]",
                  "-c:v", encoder, *encoding, "-profile:v", "high", "-c:a", "pcm_s16le", "-video_track_timescale", "30000",
                  "-t", f"{duration:.9f}", piece], settings, work, f"piece-{i}", check, timeout=7200)
        pieces.append(piece)
        progress((i+1)/(len(plan["retained"])+1))
    if not pieces:
        raise ValueError("There are no retained passages to render.")
    listing = work / "concat.txt"
    listing.write_text("\n".join(f"file '{p.name}'" for p in pieces), encoding="utf-8")
    temporary = output.with_name(output.stem+".partial.mp4")
    run_tool([settings.ffmpeg, "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "1",
              "-i", listing, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
              "-video_track_timescale", "30000", "-movflags", "+faststart", temporary], settings, work, "assemble", check, timeout=7200)
    run_tool([settings.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", temporary, "-f", "null", "-"],
             settings, work, "decode-check", check, timeout=7200)
    info = youtube.probe(settings, temporary, work, check)
    streams = {s["codec_type"]: s for s in info["streams"]}
    v, a = streams.get("video", {}), streams.get("audio", {})
    if (v.get("width"), v.get("height"), v.get("pix_fmt")) != (width, height, "yuv420p") or not a:
        raise ValueError("The highlight render does not match the requested picture and audio format.")
    if v.get("avg_frame_rate") not in {"30/1", "30"}:
        raise ValueError("The highlight video is not 30 frames per second.")
    duration = total_frames/30
    if (abs(float(info["format"]["duration"])-duration) > 0.15
            or abs(float(v.get("duration", duration))-float(a.get("duration", duration))) > 0.1
            or abs(float(v.get("start_time", 0))-float(a.get("start_time", 0))) > 0.05):
        raise ValueError("The highlight picture and sound do not match the edit timing.")
    check()
    temporary.replace(output)
    for piece in pieces:
        piece.unlink()
    progress(1)
    return {"duration": duration, "frames": total_frames, "sha256": digest(output), "bytes": output.stat().st_size}


class Highlights(Pipeline):
    def __init__(self, settings, store, run_id, stopping=lambda: False):
        super().__init__(settings, store, run_id, stopping, folder=run_folder(settings, run_id))

    def execute(self):
        result = self.store.get(self.run_id)["result"]
        if result.get("mode") == "final":
            return self.render_final(result)
        for key, expected in self.config["model_manifests"].items():
            if digest(self.settings.models / key / "manifest.json") != expected:
                raise ValueError("A selected model changed. Start a new highlight run.")
        transcripts, audio = {}, {}
        for source in self.config["manifest"]["sources"]:
            asset = source["asset"]
            reuse = None
            if not (self.folder / f"transcript-{asset}.checkpoint.json").is_file():
                reuse = sources.reusable_transcript(self.settings, source, self.config["model_manifests"])

            def acquire_audio():
                if reuse:
                    old = Path(reuse["audio"]["path"])
                    path = self.folder / asset / "audio" / ("source"+old.suffix)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(old, path)
                    return {**reuse["audio"], "path": str(path)}, [path]
                path = sources.acquire(self.settings, source, self.folder / asset / "audio", self.check)
                info = youtube.probe(self.settings, path, path.parent, self.check)
                duration = float(info["format"]["duration"])
                if abs(duration-source["duration"]) > 3:
                    raise ValueError("Downloaded audio differs from the recording duration. Source timing needs review.")
                return {"path": str(path), "duration": duration}, [path]

            audio[asset] = self.stage(f"audio-{asset}", acquire_audio)

            def transcript():
                folder = self.folder / asset / "asr"
                if reuse:
                    result = reuse["transcript"]
                    atomic_json(folder / "transcript.json", result)
                else:
                    result = transcribe.transcribe(self.settings, Path(audio[asset]["path"]), audio[asset]["duration"],
                                                  "turbo", folder, self.check, self.progress)
                return result, [folder / "transcript.json"]

            transcripts[asset] = self.stage(f"transcript-{asset}", transcript)
        items = editing.units(transcripts)
        result = self.store.get(self.run_id)["result"]
        revision = result.get("revision", 1)
        folder = revision_folder(self.settings, self.run_id, revision)
        folder.mkdir(parents=True, exist_ok=True)

        def editing_stage():
            inference = folder / "inference"
            inference.mkdir(exist_ok=True)
            manager = local_server(self.settings, self.config, inference, self.check) if self.config["provider"] == "local" else nullcontext(None)
            parent = result.get("parent_revision")
            previous = (json.loads((revision_folder(self.settings, self.run_id, parent) / "plan.json").read_text("utf-8"))
                        if parent else None)
            with manager as client:
                evaluator = Evaluator(self.config["provider"], self.store, self.run_id, inference,
                    self.config["budget_usd"], self.check, client, self.config["context_size"],
                    codex_config=self.config.get("codex"), discovery_reasoning=self.config["discovery_reasoning"],
                    verification_reasoning=self.config["verification_reasoning"])
                plan = editing.plan(items, evaluator, self.progress, self.config["target_minutes"]*60,
                                    previous, result.get("guidance", ""))
            atomic_json(folder / "plan.json", plan)
            return plan, [folder / "plan.json"]

        plan = self.stage(f"edit-{revision}", editing_stage, editing.VERSION)
        media = []
        for source in self.config["manifest"]["sources"]:
            asset = source["asset"]
            spans = [s for s in plan["retained"] if s["asset"] == asset]
            windows = []
            for span in spans:
                start, end = max(0, span["start_us"]/1e6-20), min(source["duration"], span["end_us"]/1e6+20)
                if windows and start <= windows[-1][1]+30:
                    windows[-1][1] = max(windows[-1][1], end)
                else:
                    windows.append([start, end])
            for i, (start, end) in enumerate(windows):
                def section():
                    where = self.folder / "sections" / f"{asset}-{start:.3f}-{end:.3f}"
                    path = sources.acquire(self.settings, source, where, self.check, (start, end))
                    mapping = render.align(self.settings, Path(audio[asset]["path"]), path, start, where,
                                           self.check, clip_duration=max(6, end-start-40))
                    record = {"asset": asset, "path": str(path), "start_us": round(start*1e6),
                              "end_us": round(end*1e6), "mapping": mapping}
                    return record, [path]
                media.append(self.stage(f"media-{revision}-{asset}-{i}", section))
        atomic_json(folder / "media.json", [{**m, "sha256": digest(Path(m["path"]))} for m in media])
        mode = "draft"
        rendered = {}
        if plan["retained"]:
            def output():
                record = render_video(self.settings, plan, media, folder / f"{mode}.mp4",
                                      self.config["video_encoder"], self.check, self.progress)
                atomic_json(folder / f"{mode}.json", record)
                return record, [folder / f"{mode}.mp4", folder / f"{mode}.json"]
            rendered = self.stage(f"render-{revision}-{mode}", output)
        return self.finish(result, plan, rendered, mode)

    def render_final(self, result):
        revision = result["revision"]
        if result.get("approved_revision") != revision:
            raise ValueError("Approve the current draft before rendering the final video.")
        folder = revision_folder(self.settings, self.run_id, revision)
        self.store.update(self.run_id, message="Checking the approved edit and its saved source footage.")
        for name in ("plan", "media"):
            path = folder / f"{name}.json"
            if not path.is_file() or digest(path) != result.get(f"{name}_sha256"):
                raise ValueError("The approved edit or its source records changed. Review a new draft before exporting.")
        plan = json.loads((folder / "plan.json").read_text("utf-8"))
        media = json.loads((folder / "media.json").read_text("utf-8"))
        for item in media:
            self.check()
            if not Path(item["path"]).is_file() or digest(Path(item["path"])) != item["sha256"]:
                raise ValueError("Source footage for the approved draft is missing or changed. Create a new draft.")

        def output():
            record = render_video(self.settings, plan, media, folder / "final.mp4",
                                  self.config["video_encoder"], self.check, self.progress)
            atomic_json(folder / "final.json", record)
            return record, [folder / "final.mp4", folder / "final.json"]

        rendered = self.stage(f"render-{revision}-final", output)
        return self.finish(result, plan, rendered, "final")

    def finish(self, result, plan, rendered, mode):
        revision = result.get("revision", 1)
        folder = revision_folder(self.settings, self.run_id, revision)
        history = [h for h in result.get("history", []) if h["revision"] != revision]
        current = {"revision": revision, "duration": rendered.get("duration", plan["duration"]),
                   "has_draft": bool(plan["retained"]), "has_final": mode == "final" and bool(rendered),
                   "issues": plan["issues"], "shorter_than_target": plan["shorter_than_target"],
                   "parent_revision": result.get("parent_revision"), "guidance": result.get("guidance", ""),
                   "plan_sha256": digest(folder / "plan.json"), "media_sha256": digest(folder / "media.json")}
        history.append(current)
        result = {**result, **current, "history": history, "review": "approved" if mode == "final" else "unreviewed"}
        self.store.update(self.run_id, result=result, state="completed", stage="completed", progress=1,
                          message=("Final video ready." if mode == "final" else "Draft ready for review.")
                          if rendered else "No worthwhile highlight sequences were found.")
        return result


def run_job(settings, store, run_id, stopping):
    try:
        Highlights(settings, store, run_id, stopping).execute()
    except Interrupted as exc:
        store.update(run_id, state="cancelled" if exc.action == "cancel" else "paused", intent="",
                     message="Completed highlight stages are saved.")
    except (ValueError, ToolError) as exc:
        store.update(run_id, state="failed", intent="", message=str(exc))
    except Exception as exc:
        atomic_json(run_folder(settings, run_id) / "error.json", {"type": type(exc).__name__})
        store.update(run_id, state="failed", intent="",
                     message="Highlight processing stopped unexpectedly. Completed stages are saved; retry to continue.")
