from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import catalog, discover
from .context_repair import ContextRequest
from .contracts import MAX_CLIP_US, MIN_CLIP_US, EditRequest, LayoutSave, RunRequest, Word
from .llm import PROVIDERS, codex_settings
from .models import CATALOG, model_path
from .pipeline import preflight
from .storage import BusyError, Store


def public_clip(clip):
    body = clip["body"]
    return {
        "id": clip["id"],
        "revision": clip["revision"],
        "run_id": clip["run_id"],
        "review_status": clip.get("review_status", "unreviewed"),
        "review_note": clip.get("review_note", ""),
        **{
            k: body.get(k)
            for k in (
                "status",
                "title",
                "start_us",
                "end_us",
                "flags",
                "review_notes",
                "review_rank",
                "words",
                "source_url",
                "selection",
                "layout",
                "trim_silence",
                "video_encoder",
                "output_duration_us",
                "pacing",
                "previous_revision",
                "context_request",
            )
        },
        "has_preview": bool(body.get("folder") and (Path(body["folder"]) / "short.mp4").exists()),
        "has_source": bool(body.get("section") and Path(body["section"]).exists()),
        "section_origin_us": body.get("mapping", {}).get("origin_us", 0),
        "caption_coverage": (
            {k: body["caption_transcript"][k] for k in ("profile", "start_us", "end_us")}
            if body.get("caption_transcript") else None
        ),
    }


def create_app(settings):
    settings.initialize()
    store = Store(settings.work / "state.sqlite3")
    token = secrets.token_urlsafe(32)
    catalog_lock = threading.Lock()
    app = FastAPI(title="Vaarattu Shorts", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    static = Path(__file__).parent / "static"

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        parsed = urlparse("http://" + host)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            return JSONResponse({"detail": "This app is available only on this computer."}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in {
            f"http://127.0.0.1:{settings.port}",
            f"http://localhost:{settings.port}",
        }:
            return JSONResponse({"detail": "Request origin is not allowed."}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            if not secrets.compare_digest(request.headers.get("x-local-token", ""), token):
                return JSONResponse({"detail": "Reload this page before making changes."}, status_code=403)
            size = request.headers.get("content-length", "0")
            if not size.isdigit() or int(size) > 2_000_000:
                return JSONResponse({"detail": "This request is too large."}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data: https://i.ytimg.com; media-src 'self'; "
            "style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(BusyError)
    async def busy(_, exc):
        return JSONResponse(
            {"detail": "Finish or cancel this clip's run before editing it.", "run_id": str(exc)}, status_code=409
        )

    @app.exception_handler(KeyError)
    async def missing(_, exc):
        return JSONResponse({"detail": "This item was not found."}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/api/status")
    def status():
        models = {}
        for name in CATALOG:
            try:
                model_path(settings, name)
                models[name] = True
            except (ValueError, OSError, KeyError):
                models[name] = False
        return {
            "token": token,
            "max_concurrent_jobs": store.concurrency(),
            "models": models,
            "providers": {
                key: {
                    "model": os.environ.get("CODEX_MODEL", spec["model"])
                    if key == "codex"
                    else spec["model"],
                    "configured": not spec["key"] or bool(os.environ.get(spec["key"])),
                    "available": not spec.get("unavailable"),
                    "reason": spec.get("unavailable", ""),
                }
                for key, spec in PROVIDERS.items()
            },
            "output": str(settings.ready),
            "model_cache": str(settings.models),
            "asr": {"batch_size": settings.asr_batch_size, "flash_attention": settings.asr_flash_attention},
        }

    @app.post("/api/concurrency")
    def concurrency(max_concurrent_jobs: int = Body(embed=True, ge=1, le=4)):
        store.set_concurrency(max_concurrent_jobs)
        return {"max_concurrent_jobs": store.concurrency()}

    @app.get("/api/videos")
    def videos():
        channel = store.channel(settings.youtube_channel_id)
        return {
            "channel_id": settings.youtube_channel_id,
            "channel_title": channel["title"] if channel else "",
            "configured": bool(os.environ.get("YOUTUBE_API_KEY", "").strip()),
            "fetched_at": channel["fetched_at"] if channel else None,
            "has_older": bool(channel and channel.get("next_page_token")),
            "videos": store.videos(settings.youtube_channel_id),
        }

    @app.post("/api/videos/{action}")
    def fetch_videos(action: str):
        if action not in {"refresh", "older"}:
            raise HTTPException(404, "Action not found.")
        if not catalog_lock.acquire(blocking=False):
            raise HTTPException(409, "Channel videos are already being fetched.")
        try:
            catalog.fetch_page(settings, store, older=action == "older")
            return videos()
        finally:
            catalog_lock.release()

    @app.get("/api/layouts")
    def layouts():
        return store.layouts()

    @app.post("/api/layouts", status_code=201)
    def add_layout(layout: LayoutSave):
        screenshot = None
        if layout.screenshot is not None:
            try:
                image = base64.b64decode(layout.screenshot.data.split(",", 1)[1], validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("This screenshot could not be saved. Choose it again.") from None
            if not image.startswith(b"\xff\xd8\xff") or not image.endswith(b"\xff\xd9"):
                raise ValueError("This screenshot could not be saved. Choose it again.")
            screenshot = (layout.screenshot.name, image, layout.screenshot.width, layout.screenshot.height)
        return {"id": store.add_layout(layout.model_dump(exclude={"screenshot"}), screenshot)}

    @app.get("/api/layouts/{layout_id}/screenshot")
    def layout_screenshot(layout_id: str):
        return Response(store.layout_frame(layout_id), media_type="image/jpeg")

    @app.delete("/api/layouts/{layout_id}")
    def delete_layout(layout_id: str):
        store.delete_layout(layout_id)
        return {"deleted": layout_id}

    @app.get("/api/runs")
    def runs():
        return store.runs()

    @app.post("/api/runs", status_code=202)
    def start_run(body: RunRequest, idempotency_key: str = Header(min_length=8, max_length=100)):
        manifests = preflight(settings, body)
        layout = store.layout(body.layout_id)
        config = {
            **body.model_dump(),
            "layout": layout,
            "model_manifests": manifests,
            "pipeline_version": 1,
            "channel_id": settings.youtube_channel_id,
            "asr_batch_size": settings.asr_batch_size,
            "asr_flash_attention": settings.asr_flash_attention,
            **({"codex": codex_settings()} if body.provider == "codex" else {}),
        }
        return {"id": store.admit(config, idempotency_key)}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        usage = store.usage(run_id)
        run = store.get(run_id)
        return {
            **run,
            "can_recheck": run["state"] == "completed"
            and run["result"].get("recovery_version") != discover.VERSION
            and (settings.work / "runs" / run_id / "selection.checkpoint.json").is_file(),
            "usage": {k: v for k, v in usage.items() if k != "requests"},
            "clips": [public_clip(c) for c in store.clips(run_id)],
        }

    @app.get("/api/runs/{run_id}/usage")
    def get_usage(run_id: str):
        report = store.usage(run_id)
        return JSONResponse(
            report, headers={"Content-Disposition": f'attachment; filename="usage-{report["run_id"]}.json"'}
        )

    @app.post("/api/runs/{run_id}/{action}")
    def control(run_id: str, action: str):
        if action not in {"pause", "resume", "cancel", "recheck"}:
            raise HTTPException(404, "Action not found.")
        if action == "recheck":
            if not (settings.work / "runs" / run_id / "selection.checkpoint.json").is_file():
                raise ValueError("This run has no saved selection to recheck.")
            if store.get(run_id)["result"].get("recovery_version") == discover.VERSION:
                raise ValueError("Saved exclusions have already been rechecked with these selection rules.")
        store.control(run_id, action)
        return {"ok": True}

    @app.get("/api/clips")
    def clip_gallery():
        clips = []
        for clip in store.clips():
            if clip["body"].get("status") not in {"ready", "held"}:
                continue
            item = public_clip(clip)
            if item["has_preview"]:
                clips.append({k: v for k, v in item.items() if k not in {"words", "layout", "selection"}})
        return clips

    @app.post("/api/clips/{clip_id}/review")
    def review_clip(
        clip_id: str,
        expected_revision: int = Body(ge=1),
        status: Literal["unreviewed", "approved", "not_approved"] = Body(),
        note: str | None = Body(default=None, max_length=2000),
    ):
        store.review_clip(clip_id, expected_revision, status, note)
        return public_clip(store.clip(clip_id))

    @app.get("/api/clips/{clip_id}")
    def get_clip(clip_id: str):
        return public_clip(store.clip(clip_id))

    def queue_render(clip_id, expected_revision, layout_id=None, trim_silence=None, video_encoder=None):
        clip = store.clip(clip_id)
        if clip["body"]["status"] not in {"held", "ready"}:
            raise ValueError("Wait for this clip to finish before rendering it again.")
        body = {**clip["body"], "status": "pending", "flags": [], "folder": None}
        if public_clip(clip)["has_preview"]:
            body["previous_revision"] = clip["revision"]
        if layout_id is not None:
            body["layout"] = store.layout(layout_id)
        if trim_silence is not None:
            body["trim_silence"] = trim_silence
        if video_encoder is not None:
            body["video_encoder"] = video_encoder
        revision = store.queue_edit(clip_id, expected_revision, body)
        return {"revision": revision, "run_id": clip["run_id"]}

    @app.post("/api/clips/{clip_id}/retry", status_code=202)
    def retry_clip(clip_id: str, expected_revision: int = Body(embed=True, ge=1)):
        return queue_render(clip_id, expected_revision)

    @app.post("/api/clips/{clip_id}/context", status_code=202)
    def request_context(clip_id: str, request: ContextRequest):
        clip = store.clip(clip_id)
        if not (settings.work / "runs" / clip["run_id"] / "asr" / "transcript.json").is_file():
            raise ValueError("The saved recording transcript is missing; restore it before requesting context.")
        return store.queue_context(clip_id, request.model_dump())

    @app.post("/api/clips/{clip_id}/rerender", status_code=202)
    def rerender_clip(
        clip_id: str,
        expected_revision: int = Body(ge=1),
        layout_id: str | None = Body(default=None, min_length=1, max_length=100),
        trim_silence: bool | None = Body(default=None),
        video_encoder: Literal["libx264", "h264_nvenc"] | None = Body(default=None),
    ):
        return queue_render(clip_id, expected_revision, layout_id, trim_silence, video_encoder)

    @app.post("/api/clips/{clip_id}/edit", status_code=202)
    def edit(clip_id: str, edit: EditRequest):
        clip = store.clip(clip_id)
        original = clip["body"]
        transcript_path = settings.work / "runs" / clip["run_id"] / "asr" / "transcript.json"
        transcript = json.loads(transcript_path.read_text("utf-8"))
        if (
            not MIN_CLIP_US <= edit.end_us - edit.start_us <= MAX_CLIP_US
            or edit.end_us > transcript["duration_us"]
        ):
            raise ValueError("Choose a 3–90 second interval within this VOD.")
        captions = original.get("caption_transcript", transcript)
        if original.get("caption_transcript") and not (
            captions["start_us"] <= edit.start_us and edit.end_us <= captions["end_us"]
        ):
            raise ValueError(
                f"Keep this edit within its transcribed section "
                f"({captions['start_us'] / 1e6:.2f}–{captions['end_us'] / 1e6:.2f} seconds)."
            )
        canonical = [Word.model_validate(w) for w in captions["words"]]
        if any(
            (w.start_us < edit.start_us < w.end_us) or (w.start_us < edit.end_us < w.end_us)
            for w in canonical
        ):
            raise ValueError("Move the boundary into a pause so no spoken word is cut.")
        selected = [w for w in canonical if w.start_us >= edit.start_us and w.end_us <= edit.end_us]
        submitted = {w.id: w for w in edit.words}
        known = {w.id: w for w in canonical}
        if len(submitted) != len(edit.words) or not submitted.keys() <= known.keys():
            raise ValueError("Caption edits must reference original word IDs.")
        for key, word in submitted.items():
            old = known[key]
            if word.start_us != old.start_us or word.end_us != old.end_us or not word.text.strip():
                raise ValueError("Edit caption text without changing its source timing or omitting words.")
        words = [submitted.get(w.id, w).model_dump() for w in selected]
        if not words:
            raise ValueError("The chosen interval contains no caption words.")
        if (
            edit.start_us != original["start_us"]
            or edit.end_us != original["end_us"]
            or words != original["words"]
        ) and not edit.reviewed:
            raise ValueError("Confirm the edited excerpt preserves the original meaning before rerendering.")
        body = {
            **original,
            "start_us": edit.start_us,
            "end_us": edit.end_us,
            "title": edit.title,
            "words": words,
            "layout": store.layout(edit.layout_id),
            "reviewed": edit.reviewed,
            "status": "pending",
            "flags": [],
            "folder": None,
            "trim_silence": edit.trim_silence if edit.trim_silence is not None else original.get("trim_silence", False),
            "video_encoder": edit.video_encoder or original.get("video_encoder", "libx264"),
            "previous_revision": clip["revision"] if public_clip(clip)["has_preview"] else original.get("previous_revision"),
        }
        revision = store.queue_edit(clip_id, edit.expected_revision, body)
        return {"revision": revision, "run_id": clip["run_id"]}

    @app.get("/api/artifacts/{clip_id}/{kind}")
    def artifact(clip_id: str, kind: str, revision: int | None = None):
        clip = store.clip(clip_id)
        body = clip["body"]
        if revision is not None and revision != clip["revision"]:
            if not 1 <= revision < clip["revision"]:
                raise HTTPException(404, "This preview is not available.")
            folder = settings.ready / clip_id / str(revision)
            if not (folder / "metadata.json").is_file():
                folder = settings.ready / ".staging" / clip_id / str(revision)
            metadata = folder / "metadata.json"
            if not metadata.is_file():
                raise HTTPException(404, "This preview is not available.")
            body = {**json.loads(metadata.read_text("utf-8")), "folder": str(folder)}
        names = {"video": "short.mp4", "captions": "captions.srt", "contact": "contact.jpg"}
        if kind == "source":
            path = Path(body.get("section") or "__missing__").resolve()
        elif kind in names and body.get("folder"):
            path = (Path(body["folder"]) / names[kind]).resolve()
        else:
            raise HTTPException(404, "This preview is not available.")
        if not path.is_relative_to(settings.work.resolve()) or not path.is_file():
            raise HTTPException(404, "This preview is not available.")
        return FileResponse(path)

    @app.post("/api/output/open")
    def open_output():
        if os.name != "nt":
            return {"path": str(settings.ready)}
        os.startfile(settings.ready)
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
