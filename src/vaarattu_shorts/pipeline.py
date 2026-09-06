from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

from . import discover, render, stream_data, transcribe, youtube
from .contracts import CHANNEL_ID, Candidate, RunRequest, Word
from .llm import Evaluator, check_provider, local_server
from .models import model_path
from .processes import Interrupted, ToolError
from .storage import atomic_json, digest


def preflight(settings, request: RunRequest):
    check_provider(request.provider)
    model_path(settings, "turbo")
    if importlib.util.find_spec("faster_whisper") is None:
        raise ValueError("Install the local ASR extra before starting: uv sync --extra asr.")
    for tool in (settings.ffmpeg, settings.ffprobe):
        if not shutil.which(tool) and not (settings.root / tool).is_file():
            raise ValueError("FFmpeg and FFprobe must be installed before starting.")
    if request.provider == "local":
        model_path(settings, request.local_model)
        if not shutil.which(settings.llama_server) and not (settings.root / settings.llama_server).is_file():
            raise ValueError("Set the installed llama-server path in config.toml.")
    snapshots = {"turbo": digest(settings.models / "turbo" / "manifest.json")}
    if request.provider == "local":
        snapshots[request.local_model] = digest(settings.models / request.local_model / "manifest.json")
    return snapshots


def check_space(settings):
    if shutil.disk_usage(settings.root).free < settings.min_free_gb * 1e9:
        raise ValueError("Free disk space is below the configured minimum. Completed work is saved.")


class Pipeline:
    def __init__(self, settings, store, run_id, stopping=lambda: False):
        self.settings, self.store, self.run_id, self.stopping = settings, store, run_id, stopping
        self.config = store.get(run_id)["config"]
        # A resumed run retains its original channel even if .env changes later.
        self.settings = replace(
            settings,
            youtube_channel_id=self.config.get("channel_id", CHANNEL_ID),
            asr_batch_size=self.config.get("asr_batch_size", 0),
            asr_flash_attention=self.config.get("asr_flash_attention", False),
        )
        self.folder = settings.work / "runs" / run_id
        self.folder.mkdir(parents=True, exist_ok=True)
        self.chain = hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()

    def check(self):
        if self.stopping():
            raise Interrupted("pause")
        run = self.store.get(self.run_id)
        if run["intent"]:
            raise Interrupted(run["intent"])

    def progress(self, value):
        self.store.update(self.run_id, progress=min(1, max(0, value)))

    def stage(self, name, operation, version=None):
        self.check()
        if version is not None:
            self.chain = hashlib.sha256(f"{self.chain}:{name}:{version}".encode()).hexdigest()
        self.store.update(self.run_id, stage=name, progress=0, message="")
        checkpoint = self.folder / f"{name}.checkpoint.json"
        if checkpoint.exists():
            cached = json.loads(checkpoint.read_text("utf-8"))
            if cached.get("input_chain") == self.chain and all(
                Path(item["path"]).is_file() and digest(Path(item["path"])) == item["sha256"]
                for item in cached["artifacts"]
            ):
                self.chain = digest(checkpoint)
                return cached["result"]
        check_space(self.settings)
        result, artifacts = operation()
        self.check()
        atomic_json(
            checkpoint,
            {
                "input_chain": self.chain,
                "version": version,
                "result": result,
                "artifacts": [{"path": str(p), "sha256": digest(p)} for p in artifacts],
            },
        )
        self.chain = digest(checkpoint)
        return result

    def execute(self):
        for key, expected in self.config["model_manifests"].items():
            if digest(self.settings.models / key / "manifest.json") != expected:
                raise ValueError("A selected model changed since this run was created. Start a new run.")
        metadata = self.stage(
            "metadata",
            lambda: (youtube.metadata(self.settings, self.config["video"], self.folder, self.check), []),
        )

        def audio_stage():
            source = youtube.acquire(self.settings, metadata["id"], self.folder / "audio", self.check)
            probe = youtube.probe(self.settings, source, self.folder / "audio", self.check)
            duration = float(probe["format"]["duration"])
            if abs(duration - metadata["duration"]) > 3:
                raise ValueError(
                    "The downloaded audio duration differs from the VOD. Source timing needs review."
                )
            return {"path": str(source), "duration": duration}, [source]

        audio = self.stage("audio", audio_stage)
        source = Path(audio["path"])

        def transcript_stage():
            result = transcribe.transcribe(
                self.settings,
                source,
                audio["duration"],
                "turbo",
                self.folder / "asr",
                self.check,
                self.progress,
            )
            return result, [self.folder / "asr" / "transcript.json"]

        transcript = self.stage("transcript", transcript_stage)

        def selection_stage():
            folder = self.folder / "inference" / self.chain[:16]
            folder.mkdir(parents=True, exist_ok=True)
            manager = (
                local_server(self.settings, self.config, folder, self.check)
                if self.config["provider"] == "local"
                else nullcontext(None)
            )
            with manager as client:
                evaluator = Evaluator(
                    self.config["provider"],
                    self.store,
                    self.run_id,
                    folder,
                    self.config["budget_usd"],
                    self.check,
                    client,
                    self.config.get("context_size", 16384),
                )
                selection = discover.discover(transcript, evaluator, self.progress)
            atomic_json(folder / "selection.json", selection)
            return selection, [folder / "selection.json"]

        selection_checkpoint = self.folder / "selection.checkpoint.json"
        selection_version = discover.VERSION
        if selection_checkpoint.exists():
            # Completed selection belongs to its saved clips; upgrade only unfinished selection.
            selection_version = json.loads(selection_checkpoint.read_text("utf-8")).get("version")
        selection = self.stage("selection", selection_stage, selection_version)
        enrichment = self.stage("chat", lambda: (stream_data.enrich(metadata, self.config), []))
        existing_ids = {clip["id"] for clip in self.store.clips(self.run_id)}
        candidates = [item for item in selection["verified"] if item["eligible"]]
        candidates.sort(
            key=lambda item: (
                Candidate.model_validate(item["candidate"]).scores.total()
                + stream_data.boost(item["start_us"], item["end_us"], enrichment)
            ),
            reverse=True,
        )
        chosen = []
        for item in candidates:
            if any(
                max(0, min(item["end_us"], c["end_us"]) - max(item["start_us"], c["start_us"])) > 0
                for c in chosen
            ):
                continue
            chosen.append(item)
            if len(chosen) == self.config["max_clips"]:
                break
        for i, item in enumerate(chosen):
            clip_id = hashlib.sha256(f"{self.run_id}:{i}".encode()).hexdigest()[:32]
            if clip_id in existing_ids:
                continue
            words = [Word.model_validate(w) for w in transcript["words"]]
            a, b = item["start_us"], item["end_us"]
            previous = max((w.end_us for w in words if w.end_us <= a), default=0)
            following = min((w.start_us for w in words if w.start_us >= b), default=transcript["duration_us"])
            start, end = max(previous, a - 200000), min(following, b + 300000)
            if end - start > 90000000:
                start, end = a, b
            body = {
                "start_us": start,
                "end_us": end,
                "title": item["candidate"]["title_fi"],
                "selection": item["candidate"],
                "layout": self.config["layout"],
                "source_id": metadata["id"],
                "source_title": metadata["title"],
                "source_url": f"https://www.youtube.com/watch?v={metadata['id']}",
                "source_date": metadata["upload_date"],
                "status": "pending",
                "reviewed": False,
                "words": [w.model_dump() for w in words if w.start_us >= start and w.end_us <= end],
            }
            self.store.save_clip(clip_id, self.run_id, 1, body)
        for i, clip in enumerate(self.store.clips(self.run_id)):
            self.check()
            if clip["body"]["status"] in {"ready", "held"}:
                continue
            self.store.update(self.run_id, stage="render", progress=i / max(1, self.config["max_clips"]))
            self.deliver(clip, source, transcript["duration_us"])
        return self.finish(enrichment, selection)

    def deliver(self, clip, source, duration_us):
        body, clip_id, revision = clip["body"], clip["id"], clip["revision"]
        promoted = self.settings.ready / clip_id / str(revision)
        if promoted.exists():
            result = json.loads((promoted / "metadata.json").read_text("utf-8"))
            if digest(promoted / "short.mp4") != result["video_sha256"] or any(
                result[key] != body[key] for key in ("start_us", "end_us", "title", "words", "layout")
            ):
                raise ValueError(
                    "A previous export differs from this revision. It was preserved for inspection."
                )
            self.store.save_clip(clip_id, self.run_id, revision, {**result, "folder": str(promoted)})
            return
        folder = self.folder / "sections" / clip_id / str(revision)
        folder.mkdir(parents=True, exist_ok=True)
        # Reuse a verified downloaded section when edits stay inside it.
        mapping, section = body.get("mapping"), Path(body["section"]) if body.get("section") else None
        try:
            if (
                not mapping
                or not section
                or not section.exists()
                or not body.get("section_sha256")
                or (section and section.exists() and digest(section) != body.get("section_sha256"))
                or not (
                    mapping["origin_us"] <= body["start_us"]
                    and body["end_us"] <= mapping["origin_us"] + round(mapping["section_duration"] * 1e6)
                )
            ):
                start = max(0, body["start_us"] / 1e6 - 10)
                end = min(duration_us / 1e6, body["end_us"] / 1e6 + 10)
                section = youtube.acquire(self.settings, body["source_id"], folder, self.check, (start, end))
                mapping = render.align(self.settings, source, section, start, folder, self.check)
            body.update({"section": str(section), "section_sha256": digest(section), "mapping": mapping})
            self.store.save_clip(clip_id, self.run_id, revision, body)
            check_space(self.settings)
            staging = self.settings.ready / ".staging" / clip_id / str(revision)
            words = [Word.model_validate(w) for w in body["words"]]
            result = render.render_clip(
                self.settings, section, mapping, body, body["layout"], words, staging, self.check
            )
            self.check()
            if result["status"] == "ready":
                folder = render.promote(self.settings, clip_id, revision, staging)
            else:
                folder = staging
            result["folder"] = str(folder)
            self.store.save_clip(clip_id, self.run_id, revision, result)
        except ValueError as exc:
            self.store.save_clip(
                clip_id, self.run_id, revision, {**body, "status": "held", "flags": [str(exc)]}
            )
        except ToolError:
            self.store.save_clip(
                clip_id,
                self.run_id,
                revision,
                {
                    **body,
                    "status": "held",
                    "flags": ["This clip's media stage failed. Check its local log and retry."],
                },
            )

    def finish(self, enrichment, selection=None):
        self.check()
        clips = self.store.clips(self.run_id)
        issues = (
            selection.get("issues", [])
            if selection is not None
            else self.store.get(self.run_id)["result"].get("selection_issues", [])
        )
        missed = sum(issue["reason"] == "section_unreadable" for issue in issues)
        outcome = (
            "no_candidates"
            if not clips
            else "completed"
            if any(c["body"]["status"] == "ready" for c in clips)
            else "needs_attention"
        )
        result = {
            "run_id": self.run_id,
            "video_id": self.config["video"],
            "coverage": "partial" if missed else "complete",
            "outcome": "needs_attention" if missed or (issues and not clips) else outcome,
            "selection_issues": issues,
            "chat": enrichment,
            "ready": [
                {"clip_id": c["id"], "revision": c["revision"], "folder": c["body"].get("folder")}
                for c in clips
                if c["body"]["status"] == "ready"
            ],
            "held": sum(c["body"]["status"] == "held" for c in clips),
        }
        atomic_json(self.settings.ready / "runs" / f"{self.run_id}.json", result)
        self.store.update(
            self.run_id,
            state="completed",
            stage="complete",
            progress=1,
            result=result,
            intent="",
            message=(
                f"Finished with {missed} speech sections that could not be evaluated and "
                f"{len(issues) - missed} discarded suggestions. {len(result['ready'])} clips ready."
                if issues
                else ""
            ),
        )
        return result

    def rerender(self):
        audio = json.loads((self.folder / "audio.checkpoint.json").read_text("utf-8"))["result"]
        source = Path(audio["path"])
        if not source.is_file():
            source = youtube.acquire(self.settings, self.config["video"], self.folder / "audio", self.check)
        for clip in self.store.clips(self.run_id):
            if clip["body"]["status"] == "pending":
                self.deliver(clip, source, round(audio["duration"] * 1e6))
        return self.finish(self.store.get(self.run_id)["result"].get("chat", {"status": "unavailable"}))
