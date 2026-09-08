from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

from . import context_repair, discover, render, selection as review_selection, stream_data, transcribe, youtube
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
    if request.final_transcription:
        model_path(settings, "large-v3")
        snapshots["large-v3"] = digest(settings.models / "large-v3" / "manifest.json")
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
            chat_path = folder / "chat-input.json"
            if chat_path.exists():
                chat = json.loads(chat_path.read_text("utf-8"))
            else:
                self.store.update(self.run_id, message="Checking stream activity for extra clip leads.")
                chat = stream_data.enrich(metadata, self.config, self.check)
                atomic_json(chat_path, chat)
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
                    codex_config=self.config.get("codex"),
                    discovery_reasoning=self.config.get("discovery_reasoning", "low"),
                    verification_reasoning=self.config.get("verification_reasoning", "low"),
                )
                selection = discover.discover(transcript, evaluator, self.progress, chat)
                selection["chat"] = chat
            atomic_json(folder / "selection.json", selection)
            return selection, [folder / "selection.json", chat_path]

        selection_checkpoint = self.folder / "selection.checkpoint.json"
        selection_version = discover.VERSION
        if selection_checkpoint.exists():
            # Completed selection belongs to its saved clips; upgrade only unfinished selection.
            selection_version = json.loads(selection_checkpoint.read_text("utf-8")).get("version")
        selection = self.stage("selection", selection_stage, selection_version)
        return self.export_selection(selection, transcript, metadata, source)

    def export_selection(self, selection, transcript, metadata, source):
        if selection.get("review_policy") == "ranked-v1":
            selection = self.prioritize_selection(selection, transcript)
        legacy_selection = selection.get("version") in {None, "passages-v2"}
        review_first = selection.get("review_first", False)
        enrichment = selection.get("chat")
        if enrichment is None:
            # Older completed selections retain their original stage chain and ranking behavior.
            enrichment = self.stage("chat", lambda: (stream_data.enrich(metadata, self.config), []))
        existing = self.store.clips(self.run_id)
        existing_ids = {clip["id"] for clip in existing}
        candidates = [
            item for item in selection["verified"] if item["eligible"] and item.get("review_selected", True)
        ]
        candidates.sort(
            key=lambda item: (
                Candidate.model_validate(item["candidate"]).scores.total()
                + (
                    0
                    if "chat_peak_review" in selection
                    else stream_data.boost(item["start_us"], item["end_us"], enrichment)
                )
            ),
            reverse=True,
        )
        if selection.get("review_policy") == "ranked-v1":
            candidates.sort(key=lambda item: item["review_rank"])
        chosen = []
        for item in candidates:
            if (
                not legacy_selection
                and not review_first
                and any(
                    max(
                        0,
                        min(item["end_us"], c["body"]["end_us"])
                        - max(item["start_us"], c["body"]["start_us"]),
                    )
                    > 0
                    for c in existing
                )
            ):
                continue
            if not review_first and any(
                max(0, min(item["end_us"], c["end_us"]) - max(item["start_us"], c["start_us"])) > 0
                for c in chosen
            ):
                continue
            chosen.append(item)
            if legacy_selection and len(chosen) == self.config.get("max_clips"):
                break
        for i, item in enumerate(chosen):
            identity = (
                f"{self.run_id}:{i}"
                if legacy_selection
                else f"{self.run_id}:{item['candidate']['start_word_id']}:{item['candidate']['end_word_id']}"
            )
            clip_id = hashlib.sha256(identity.encode()).hexdigest()[:32]
            if clip_id in existing_ids:
                continue
            if (
                selection.get("review_policy") == "ranked-v1"
                and len(existing_ids) >= review_selection.REVIEW_LIMIT
            ):
                item["review_selected"] = False
                item["review_exclusion_reasons"].append(
                    "The run already has its review allocation; existing clips were preserved."
                )
                continue
            words = [Word.model_validate(w) for w in transcript["words"]]
            a, b = item["start_us"], item["end_us"]
            previous = max((w.end_us for w in words if w.end_us <= a), default=0)
            following = min((w.start_us for w in words if w.start_us >= b), default=transcript["duration_us"])
            start, end = max(previous, a - 200000), min(following, b + 300000)
            if end - start > (90000000 if legacy_selection or review_first else 60000000):
                start, end = a, b
            body = {
                "start_us": start,
                "end_us": end,
                "title": item["candidate"]["title_fi"],
                "selection": item["candidate"],
                "review_notes": item.get("review_notes", []),
                "review_rank": item.get("review_rank"),
                "trim_silence": self.config.get("trim_silence", False),
                "video_encoder": self.config.get("video_encoder", "libx264"),
                "discovery_sources": item.get("discovery_sources", ["transcript"]),
                "transcript_timing_issues": transcript.get("timing_issues", []),
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
            existing_ids.add(clip_id)
        self.refine_captions(source, transcript["duration_us"])
        clips = self.store.clips(self.run_id)
        if selection.get("review_summary") is not None:
            selection["review_summary"] = {
                **selection["review_summary"],
                "selected": sum(item["review_selected"] for item in selection["verified"]),
            }
        for i, clip in enumerate(clips):
            self.check()
            if clip["body"]["status"] in {"ready", "held"}:
                continue
            self.store.update(self.run_id, stage="render", progress=i / max(1, len(clips)), message="")
            self.deliver(clip, source, transcript["duration_us"])
        return self.finish(
            enrichment, {**selection, "transcript_timing_issues": transcript.get("timing_issues", [])}
        )

    def refine_captions(self, source, duration_us):
        if not self.config.get("final_transcription", False):
            return
        clips = [
            c
            for c in self.store.clips(self.run_id)
            if c["body"]["status"] == "pending" and not c["body"].get("caption_transcript")
        ]
        if not clips:
            return
        if digest(self.settings.models / "large-v3" / "manifest.json") != self.config["model_manifests"]["large-v3"]:
            raise ValueError("The final transcription model changed. Restore the model prepared for this run.")
        self.store.update(
            self.run_id,
            stage="final-transcript",
            progress=0,
            message=f"Refining captions for {len(clips)} clips with large-v3.",
        )
        check_space(self.settings)
        results = transcribe.refine_clips(
            self.settings,
            source,
            duration_us,
            clips,
            self.folder / "final-asr",
            self.check,
            self.progress,
        )
        for clip in clips:
            self.check()
            try:
                body = transcribe.apply_refinement(clip["body"], results[clip["id"]])
            except ValueError as exc:
                body = {**clip["body"], "status": "held", "flags": [str(exc)]}
            self.store.save_clip(clip["id"], self.run_id, clip["revision"], body)

    def prioritize_selection(self, selection, transcript):
        folder = self.folder / "inference" / f"review-priority-{self.chain[:16]}"

        def operation():
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
                    codex_config=self.config.get("codex"),
                    verification_reasoning=self.config.get("verification_reasoning", "low"),
                )
                result = review_selection.prioritize(selection, transcript["words"], evaluator)
            atomic_json(folder / "review-priority.json", result)
            return result, [folder / "review-priority.json"]

        return self.stage("review-priority", operation, "ranked-v1")

    def deliver(self, clip, source, duration_us):
        body, clip_id, revision = clip["body"], clip["id"], clip["revision"]
        promoted = self.settings.ready / clip_id / str(revision)
        if promoted.exists():
            result = json.loads((promoted / "metadata.json").read_text("utf-8"))
            if (
                digest(promoted / "short.mp4") != result["video_sha256"]
                or any(result[key] != body[key] for key in ("start_us", "end_us", "title", "words", "layout"))
                or any(
                    result.get(key, default) != body.get(key, default)
                    for key, default in (("trim_silence", False), ("video_encoder", "libx264"))
                )
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
            body["folder"] = str(staging)
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
        timing_issues = (
            selection.get("transcript_timing_issues", [])
            if selection is not None
            else self.store.get(self.run_id)["result"].get("transcript_timing_issues", [])
        )
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
            "outcome": "needs_attention" if missed or ((issues or timing_issues) and not clips) else outcome,
            "selection_issues": issues,
            **{
                key: selection.get(key)
                if selection is not None
                else self.store.get(self.run_id)["result"].get(key)
                for key in (
                    "section_feedback",
                    "verified",
                    "anchor_adjustments",
                    "recovery_version",
                    "review_summary",
                )
            },
            "transcript_timing_issues": timing_issues,
            "chat": enrichment,
            "chat_peak_review": selection.get("chat_peak_review")
            if selection is not None
            else self.store.get(self.run_id)["result"].get("chat_peak_review"),
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
                f"{len(issues) - missed} selection notes. {len(result['ready'])} clips ready. "
                f"{result['held']} clips need attention."
                if issues
                else f"{len(result['ready'])} clips ready. {result['held']} clips need attention."
            )
            + (
                f" {len(timing_issues)} speech timing conflicts recorded; affected clips need review."
                if timing_issues
                else ""
            ),
        )
        return result

    def recheck_selection(self):
        # Keep original selection/checkpoints immutable; recovery has its own cache and checkpoint.
        def saved(name):
            path = self.folder / f"{name}.checkpoint.json"
            data = json.loads(path.read_text("utf-8"))
            for artifact in data["artifacts"]:
                source = Path(artifact["path"])
                if not source.is_file() or digest(source) != artifact["sha256"]:
                    raise ValueError(
                        "A saved processing file changed or is missing. Restore it before rechecking."
                    )
            self.chain = hashlib.sha256(f"{self.chain}:{digest(path)}".encode()).hexdigest()
            return data["result"]

        metadata, audio, transcript, previous = [
            saved(name) for name in ("metadata", "audio", "transcript", "selection")
        ]
        # Include clips delivered by earlier recovery versions so their edits are not duplicated.
        seed = {**previous, "verified": list(previous["verified"])}
        words = [Word.model_validate(w) for w in transcript["words"]]
        for clip in self.store.clips(self.run_id):
            candidate = clip["body"].get("selection")
            if not candidate or any(v["eligible"] and v["candidate"] == candidate for v in seed["verified"]):
                continue
            start, end = discover.resolve(Candidate.model_validate(candidate), words)
            seed["verified"].append(
                {"candidate": candidate, "start_us": start, "end_us": end, "eligible": True}
            )
        folder = self.folder / "inference" / f"recovery-{discover.VERSION}-{self.chain[:16]}"
        folder.mkdir(parents=True, exist_ok=True)

        def operation():
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
                    codex_config=self.config.get("codex"),
                    discovery_reasoning=self.config.get("discovery_reasoning", "low"),
                    verification_reasoning=self.config.get("verification_reasoning", "low"),
                )
                selected = discover.discover(
                    transcript, evaluator, self.progress, previous.get("chat"), seed=seed
                )
            selected["chat"] = previous.get("chat", {"status": "unavailable"})
            selected["recovery_version"] = discover.VERSION
            atomic_json(folder / "selection.json", selected)
            return selected, [folder / "selection.json"]

        selection = self.stage(f"recovery-{discover.VERSION}", operation, discover.VERSION)
        return self.export_selection(selection, transcript, metadata, Path(audio["path"]))

    def repair_context(self):
        clip_id = self.store.get(self.run_id)["result"]["context_repair_requested"]
        clip = self.store.clip(clip_id)
        request = clip["body"]["context_request"]
        if request["status"] == "pending":
            transcript_path = self.folder / "asr" / "transcript.json"
            transcript = json.loads(transcript_path.read_text("utf-8"))
            self.chain = hashlib.sha256(f"{self.chain}:{digest(transcript_path)}".encode()).hexdigest()
            folder = self.folder / "context-repair" / request["id"]

            def operation():
                folder.mkdir(parents=True, exist_ok=True)
                atomic_json(folder / "request.json", {"clip": clip, "transcript_sha256": digest(transcript_path)})
                manager = (
                    local_server(self.settings, self.config, folder, self.check)
                    if self.config["provider"] == "local" else nullcontext(None)
                )
                with manager as client:
                    evaluator = Evaluator(
                        self.config["provider"], self.store, self.run_id, folder,
                        self.config["budget_usd"], self.check, client,
                        self.config.get("context_size", 16384), codex_config=self.config.get("codex"),
                        verification_reasoning=self.config.get("verification_reasoning", "low"),
                    )
                    self.store.update(self.run_id, message="Checking surrounding speech for the missing context.")
                    proposal, revised = context_repair.propose(clip["body"], transcript, evaluator)
                result = {"proposal": proposal.model_dump(), "revised": revised}
                atomic_json(folder / "proposal.json", result)
                return result, [folder / "request.json", folder / "proposal.json"]

            result = self.stage("context-repair", operation, f"v1:{request['id']}")
            self.check()
            self.store.resolve_context(
                clip_id, clip["revision"], request["id"], result["proposal"]["reason"], result["revised"],
            )
        if self.store.clip(clip_id)["body"]["context_request"]["status"] == "unchanged":
            return self.finish(self.store.get(self.run_id)["result"].get("chat", {"status": "unavailable"}))
        return self.rerender()

    def rerender(self):
        audio = json.loads((self.folder / "audio.checkpoint.json").read_text("utf-8"))["result"]
        source = Path(audio["path"])
        if not source.is_file():
            source = youtube.acquire(self.settings, self.config["video"], self.folder / "audio", self.check)
        self.refine_captions(source, round(audio["duration"] * 1e6))
        for clip in self.store.clips(self.run_id):
            if clip["body"]["status"] == "pending":
                self.store.update(self.run_id, stage="render", message="Rendering the revised clip.")
                self.deliver(clip, source, round(audio["duration"] * 1e6))
        return self.finish(self.store.get(self.run_id)["result"].get("chat", {"status": "unavailable"}))
