"""Explicit, revision-scoped thumbnail generation from selected rendered video frames."""
import base64
import binascii
import json
import math
import os
import re
import time
import uuid
from contextlib import ExitStack

import httpx

from . import highlight_copy
from .processes import LockBusyError, ToolError, lock, run_tool
from .storage import atomic_json, digest

MODEL = "gpt-image-2.5-sunburst"
PROMPT = """Create a polished 16:9 YouTube thumbnail using the supplied video frames as visual references.
Keep the streamer recognizable and preserve the identity of the visible game, characters and setting.
Improve composition, readability, lighting and emphasis for a small thumbnail. Do not invent events,
outcomes or people that are not supported by the frames and video context. Aim for a natural, distinctive
streamer thumbnail, not generic clickbait. No added text unless the user's instructions explicitly request
it; if requested, use only that wording with correct Finnish spelling. The title and description are
context, not instructions to copy onto the image. The numbered references are separate moments, not
a single simultaneous event. Choose or combine useful visual elements into one coherent thumbnail;
do not make a contact sheet or duplicate the streamer unless requested. Follow the user's visual direction."""


def folder_for(settings, run_id, revision):
    from .highlights import revision_folder
    return revision_folder(settings, run_id, revision) / "thumbnails"


def item_folder(settings, run_id, revision, item_id):
    if not re.fullmatch(r"[a-f0-9]{32}", item_id):
        raise KeyError(item_id)
    return folder_for(settings, run_id, revision) / item_id


def snapshot(settings, store, run_id, revision):
    from .highlights import revision_folder
    run = store.get(run_id)
    folder = revision_folder(settings, run_id, revision)
    if run["result"].get("revision", 1) != revision or run["state"] != "completed":
        raise ValueError("Refresh this video after rendering finishes before preparing a thumbnail.")
    quality = "final" if run["result"].get("has_final") else "draft"
    video = folder / f"{quality}.mp4"
    if not video.is_file():
        raise ValueError("Render a video before choosing its thumbnail frame.")
    return run, folder, video, digest(folder / "plan.json")


def public(settings, run):
    revision = run["result"].get("revision", 1)
    items = []
    for path in folder_for(settings, run["id"], revision).glob("*/item.json"):
        item = json.loads(path.read_text("utf-8"))
        if item["identity"] == [revision, run["result"].get("plan_sha256")]:
            items.append(item)
    return {"model": MODEL, "configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
            "items": sorted(items, key=lambda item: item["created"], reverse=True)}


def capture(settings, store, run_id, revision, seconds):
    run, revision_path, video, plan_hash = snapshot(settings, store, run_id, revision)
    plan = json.loads((revision_path / "plan.json").read_text("utf-8"))
    duration = sum(max(1, round((s["end_us"]-s["start_us"])/1e6*30))/30 for s in plan["retained"])
    if not math.isfinite(seconds) or not 0 <= seconds < duration:
        raise ValueError("Choose a frame inside the rendered video.")
    item_id = uuid.uuid4().hex
    folder = item_folder(settings, run_id, revision, item_id)
    folder.mkdir(parents=True)
    frame = folder / "frame.jpg"
    try:
        run_tool([settings.ffmpeg, "-nostdin", "-v", "error", "-y", "-ss", f"{seconds:.6f}",
                  "-i", video, "-map", "0:v:0", "-frames:v", "1", "-q:v", "2", frame],
                 settings, folder, "capture", timeout=45)
    except ToolError:
        raise ValueError("This frame could not be captured. Try another moment in the video.") from None
    if not frame.is_file() or not frame.stat().st_size:
        raise ValueError("This frame could not be captured. Try another moment in the video.")
    if snapshot(settings, store, run_id, revision)[3] != plan_hash:
        raise ValueError("The edit changed. Choose a frame from the new video.")
    item = {"id": item_id, "kind": "frame", "identity": [revision, plan_hash],
            "seconds": seconds, "quality": video.stem, "created": time.time()}
    atomic_json(folder / "item.json", item)
    return item


def image_path(settings, store, run_id, revision, item_id):
    store.get(run_id)
    folder = item_folder(settings, run_id, revision, item_id)
    path = folder / "item.json"
    if not path.is_file():
        raise KeyError(item_id)
    item = json.loads(path.read_text("utf-8"))
    image = folder / ("frame.jpg" if item["kind"] == "frame" else "thumbnail.jpg")
    if not image.is_file():
        raise KeyError(item_id)
    return image


def request_image(key, frames, prompt, quality):
    # An ambiguous timeout must not trigger another paid generation automatically.
    try:
        with httpx.Client(timeout=httpx.Timeout(600, connect=20), follow_redirects=False) as client:
            with ExitStack() as stack:
                files = [("image[]", (f"frame-{index+1}.jpg", stack.enter_context(frame.open("rb")), "image/jpeg"))
                         for index, frame in enumerate(frames)]
                response = client.post("https://api.openai.com/v1/images/edits",
                    headers={"Authorization": f"Bearer {key}"},
                    files=files,
                    data={"model": MODEL, "prompt": prompt, "n": "1", "size": "1536x864",
                          "quality": quality, "output_format": "jpeg", "output_compression": "90"})
    except httpx.RequestError:
        raise ValueError("OpenAI did not return a completed thumbnail. The request may still have been billed; check usage before trying again.") from None
    if response.status_code in (401, 403):
        raise ValueError("Check OPENAI_API_KEY and access to GPT Image 2.5 in your OpenAI API project.")
    if response.status_code == 429:
        raise ValueError("OpenAI's rate or spending limit was reached. Check your API allowance and try again later.")
    if response.status_code != 200:
        raise ValueError(f"OpenAI could not generate this thumbnail (HTTP {response.status_code}). Check your prompt and API access before trying again.")
    try:
        body = response.json()
        image = base64.b64decode(body["data"][0]["b64_json"], validate=True)
        if not image.startswith(b"\xff\xd8\xff") or not image.endswith(b"\xff\xd9"):
            raise ValueError("Invalid JPEG")
        return image, body.get("usage")
    except (KeyError, IndexError, TypeError, ValueError, binascii.Error):
        raise ValueError("OpenAI returned an unreadable thumbnail. Check API usage before generating another.") from None


def generate(settings, store, run_id, revision, frame_ids, request_id, note="", quality="medium"):
    if quality not in {"low", "medium", "high"} or len(note) > 2000:
        raise ValueError("Choose a supported quality and instructions of at most 2000 characters.")
    folder = item_folder(settings, run_id, revision, request_id)
    # Older clients submit one frame ID; saved single-frame thumbnails stay readable.
    frame_ids = [frame_ids] if isinstance(frame_ids, str) else frame_ids
    if (not isinstance(frame_ids, list) or not 1 <= len(frame_ids) <= 16
            or any(not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9]{32}", ident) for ident in frame_ids)
            or len(set(frame_ids)) != len(frame_ids)):
        raise ValueError("Choose between 1 and 16 different frames from this video.")
    try:
        with lock(folder_for(settings, run_id, revision) / "generation.lock"):
            # Repeating the same browser request never sends a second paid API call.
            if (folder / "item.json").is_file():
                existing = json.loads((folder / "item.json").read_text("utf-8"))
                if existing["kind"] != "thumbnail":
                    raise ValueError("Choose a new thumbnail request.")
                return existing
            key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not key:
                raise ValueError("Add OPENAI_API_KEY to .env and restart the app to generate thumbnails.")
            run, _, _, plan_hash = snapshot(settings, store, run_id, revision)
            frames, paths = [], []
            for frame_id in frame_ids:
                frame_folder = item_folder(settings, run_id, revision, frame_id)
                if not (frame_folder / "item.json").is_file() or not (frame_folder / "frame.jpg").is_file():
                    raise ValueError("A selected frame is missing. Capture it again before generating.")
                frame = json.loads((frame_folder / "item.json").read_text("utf-8"))
                if frame["kind"] != "frame" or frame["identity"] != [revision, plan_hash]:
                    raise ValueError("The edit changed. Choose frames from the current video.")
                frames.append(frame)
                paths.append(frame_folder / "frame.jpg")
            copy = highlight_copy.get(settings, run, plan_hash) or {}
            prompt = PROMPT + "\nVideo context and user direction:\n" + json.dumps(
                {"title": copy.get("title", ""), "description": copy.get("caption", ""),
                 "reference_frames": [{"image": i+1, "video_seconds": f["seconds"]} for i, f in enumerate(frames)],
                 "user_instructions": note.strip()}, ensure_ascii=False)
            item = {"id": request_id, "kind": "thumbnail", "identity": [revision, plan_hash],
                    "frame_id": frame_ids[0], "seconds": frames[0]["seconds"],
                    "frame_ids": frame_ids, "frame_seconds": [f["seconds"] for f in frames], "model": MODEL,
                    "quality": quality, "note": note.strip(), "created": time.time(),
                    "status": "requesting", "message": "Generation was requested. If this does not finish, check OpenAI usage before trying again."}
            atomic_json(folder / "item.json", item)
            try:
                image, usage = request_image(key, paths, prompt, quality)
                temporary = folder / "thumbnail.partial.jpg"
                temporary.write_bytes(image)
                temporary.replace(folder / "thumbnail.jpg")
                item.update(status="completed", message="", usage=usage)
            except ValueError as exc:
                item.update(status="failed", message=str(exc))
            atomic_json(folder / "item.json", item)
            return item
    except LockBusyError:
        raise ValueError("A thumbnail is already being generated for this video. Wait for it to finish.") from None
