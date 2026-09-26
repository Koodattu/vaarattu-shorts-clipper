"""Highlight source manifests and shared Twitch acquisition."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

from . import catalog, youtube
from .contracts import source_url
from .processes import run_tool
from .storage import digest

PART = re.compile(r"\s*\((?:part|osa)\s+(\d+)\s*/\s*(\d+)\)\s*$", re.I)


def part_info(title):
    match = PART.search(title)
    if not match:
        return None
    index, total = map(int, match.groups())
    if not 1 <= index <= total <= 30:
        raise ValueError("This part numbering is ambiguous. Use recordings numbered Part 1/N through Part N/N.")
    return re.sub(r"\s+", " ", title[:match.start()].strip()).casefold(), index, total


def ordered_parts(videos):
    if len(videos) == 1 and not part_info(videos[0]["title"]):
        return videos
    parsed = [part_info(v["title"]) for v in videos]
    if any(p is None for p in parsed) or len({(p[0], p[2]) for p in parsed}) != 1:
        raise ValueError("These videos do not form one matching multipart recording.")
    total = parsed[0][2]
    if sorted(p[1] for p in parsed) != list(range(1, total + 1)):
        raise ValueError("Some recording parts are missing or duplicated. Add all part URLs, one per line.")
    return [v for _, v in sorted(zip([p[1] for p in parsed], videos), key=lambda item: item[0])]


def metadata(settings, value, folder, check=lambda: None):
    provider, identity, url = source_url(value)
    if provider == "youtube":
        info = youtube.metadata(settings, identity, folder, check)
        owner = info["channel_id"]
    else:
        folder.mkdir(parents=True, exist_ok=True)
        raw = folder / "metadata.raw.json"
        try:
            run_tool([*youtube.yt_args(), "--skip-download", "--dump-single-json", url],
                     settings, folder, "metadata", check, stdout=raw, timeout=180)
            info = json.loads(raw.read_text("utf-8"))
        finally:
            raw.unlink(missing_ok=True)
        if str(info.get("id", "")).removeprefix("v") != identity:
            raise ValueError("Twitch returned a different recording.")
        if info.get("live_status") not in {"was_live", "not_live"} or not info.get("duration"):
            raise ValueError("Wait until this Twitch recording is complete before processing it.")
        owner = info.get("uploader_id")
    return {"provider": provider, "id": identity, "url": url, "owner": owner,
            "title": info.get("title") or identity, "duration": float(info["duration"]),
            "upload_date": info.get("upload_date")}


def resolve(settings, store, urls, folder, *, collection=False, match_parts=True):
    normalized = list(dict.fromkeys(source_url(u)[2] for u in urls))
    if not 1 <= len(normalized) <= 30:
        raise ValueError("Enter between one and 30 source URLs.")
    first = metadata(settings, normalized[0], folder / "0")
    if match_parts and len(normalized) == 1 and first["provider"] == "youtube" and part_info(first["title"]):
        base, _, total = part_info(first["title"])
        for attempt in range(6):
            matches = [v for v in store.videos(settings.youtube_channel_id)
                       if PART.search(v["title"])
                       and re.sub(r"\s+", " ", PART.sub("", v["title"]).strip()).casefold() == base
                       and part_info(v["title"])[2] == total]
            if len(matches) >= total:
                normalized = [v["url"] for v in ordered_parts(matches)]
                break
            saved = store.channel(settings.youtube_channel_id)
            if attempt == 5 or (saved and not saved.get("next_page_token")):
                raise ValueError("Could not find every recording part. Add the missing part URLs, one per line.")
            catalog.fetch_page(settings, store, older=bool(saved))
    sources = [first if u == first["url"] else metadata(settings, u, folder / str(i + 1))
               for i, u in enumerate(normalized)]
    if len(sources) > 1 and not collection:
        if any(s["provider"] != "youtube" or s["owner"] != first["owner"] for s in sources):
            raise ValueError("Combine only YouTube parts of the same recording; use one Twitch VOD URL.")
        sources = ordered_parts(sources)
    elif not collection and sources[0]["provider"] == "youtube":
        ordered_parts(sources)
    for i, source in enumerate(sources):
        source["asset"] = f"s{i}"
    return {"title": (f"Highlights from {len(sources)} recordings" if collection and len(urls) > 1
                       else PART.sub("", sources[0]["title"]).strip()), "sources": sources,
            "duration": sum(s["duration"] for s in sources),
            "notes": (["Recordings stay separate during analysis and are assembled in the order shown."]
                      if len(sources) > 1 else [])}


def select_manifest(manifest, segments=None, title=""):
    if segments is None:
        return {**manifest, "title": title.strip() or manifest["title"]}
    lookup = {s["asset"]: s for s in manifest["sources"]}
    selected, seen = [], set()
    for segment in segments:
        asset, start, end = segment["asset"], segment["start_us"], segment["end_us"]
        if asset not in lookup or asset in seen:
            raise ValueError("Choose each loaded recording at most once.")
        source = lookup[asset]
        if not 0 <= start < end <= round(source["duration"]*1e6):
            raise ValueError("Each selected range must start before its end and stay inside the recording.")
        seen.add(asset)
        selected.append({**source, "selection_start_us": start, "selection_end_us": end})
    if not selected:
        raise ValueError("Include at least one recording range.")
    return {**manifest, "title": title.strip() or manifest["title"], "sources": selected,
            "duration": sum(s["selection_end_us"]-s["selection_start_us"] for s in selected)/1e6}


def selection_bounds(source):
    return source.get("selection_start_us", 0), source.get("selection_end_us", round(source["duration"]*1e6))


def selected_transcript(transcript, source, offset_us=0):
    """Keep original VOD timestamps; only fully included words can reach the editor."""
    if "selection_start_us" not in source and not offset_us:
        return transcript
    start, end = selection_bounds(source)
    def shift(item):
        return {**item, "start_us": item["start_us"]+offset_us, "end_us": item["end_us"]+offset_us}
    words = [shift(w) for w in transcript["words"]
             if start <= w["start_us"]+offset_us and w["end_us"]+offset_us <= end]
    issues = [shift(i) for i in transcript.get("timing_issues", [])
              if i["end_us"]+offset_us > start and i["start_us"]+offset_us < end]
    coverage = [[max(start, a+offset_us), min(end, b+offset_us)]
                for a, b in transcript.get("coverage", [[0, transcript["duration_us"]]])
                if b+offset_us > start and a+offset_us < end]
    return {**transcript, "words": words, "timing_issues": issues, "coverage": coverage,
            "duration_us": round(source["duration"]*1e6), "selection_start_us": start,
            "selection_end_us": end, "asr_offset_us": offset_us}


def validate_selection(plan, sources):
    lookup = {s["asset"]: selection_bounds(s) for s in sources}
    for span in plan["retained"]:
        if span["asset"] not in lookup:
            raise ValueError("The edit references a recording outside this project.")
        start, end = lookup[span["asset"]]
        if not start <= span["start_us"] < span["end_us"] <= end:
            raise ValueError("The edit reaches outside a selected recording range. Rebuild this draft.")


def acquire(settings, source, folder, check, interval=None, *, stream_copy=False):
    folder.mkdir(parents=True, exist_ok=True)
    args = [*youtube.yt_args(), "--ffmpeg-location",
            str(Path(shutil.which(settings.ffmpeg) or settings.ffmpeg).resolve()),
            "--paths", str(folder), "-o", "source.%(ext)s", "--print-to-file",
            "after_move:filepath", str(folder / "download.txt")]
    if interval is None:
        args += ["-f", "bestaudio/best", "-x", "--audio-format", "opus",
                 "--max-filesize", str(int(settings.max_download_gb * 1e9))]
    else:
        start, end = interval
        args += ["-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
                 "--download-sections", f"*{start:.6f}-{end:.6f}"]
        if stream_copy:
            args += ["--no-force-keyframes-at-cuts", "--downloader-args", "ffmpeg_o:-c copy -f matroska"]
        else:
            args += ["--force-keyframes-at-cuts", "--downloader-args",
                     "ffmpeg_o:-c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k -f matroska"]
        args += ["--merge-output-format", "mkv", "--remux-video", "mkv"]
    if source_url(source["url"])[0] == "youtube":
        youtube.download([*args, source["url"]], settings, folder, check, audio_only=interval is None,
                         runner=run_tool, timeout=14400,
                         byte_limit=int(settings.max_download_gb * 1e9), watch=folder)
    else:
        run_tool([*args, source["url"]], settings, folder, "download", check, timeout=14400,
                 byte_limit=int(settings.max_download_gb * 1e9), watch=folder)
    report = folder / "download.txt"
    if not report.is_file():
        raise ValueError("The downloader did not produce a media file.")
    path = Path(report.read_text("utf-8").strip().splitlines()[-1]).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ValueError("The downloaded media location is invalid.")
    info = youtube.probe(settings, path, folder, check)
    streams = {s["codec_type"] for s in info["streams"]}
    if "audio" not in streams or (interval is not None and "video" not in streams):
        raise ValueError("The recording is missing the required picture or audio.")
    return path


def acquire_aligned(settings, source, folder, check, interval, audio, spans):
    """Try packet-copy acquisition; encode only when its timing or decoding fails."""
    from . import render
    from .processes import ToolError
    from .storage import atomic_json
    import time

    start, end = interval
    timings = []
    for copy_media in (True, False):
        where = folder / ("copy" if copy_media else "encoded")
        started = time.monotonic()
        try:
            path = acquire(settings, source, where, check, interval, stream_copy=copy_media)
            acquired = time.monotonic()
            mapping = render.align(settings, Path(audio), path, start, where, check,
                                   clip_duration=max(6, end-start-40))
            # Acquisition boundaries can move to an earlier keyframe. The verified clock
            # must cover every retained cut; padding itself need not be frame-exact.
            origin = mapping["origin_us"]
            limit = origin+round(mapping["section_duration"]*1e6)
            needed = [s for s in spans if round(start*1e6) <= s["start_us"] and s["end_us"] <= round(end*1e6)]
            if not needed or any(s["start_us"] < origin or s["end_us"] > limit for s in needed):
                raise ValueError("The acquired footage does not cover the required cuts.")
            # Copying packets is fast but may retain a broken keyframe boundary.
            run_tool([settings.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", path,
                      "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
                     settings, where, "decode-check", check, timeout=7200)
            timings.append({"mode": "copy" if copy_media else "encoded", "download_seconds": round(acquired-started, 3),
                            "verification_seconds": round(time.monotonic()-acquired, 3), "status": "verified"})
            atomic_json(folder / "acquisition.json", {"attempts": timings})
            return path, mapping
        except (ToolError, ValueError):
            check()
            timings.append({"mode": "copy" if copy_media else "encoded", "elapsed_seconds": round(time.monotonic()-started, 3),
                            "status": "failed"})
            atomic_json(folder / "acquisition.json", {"attempts": timings})
            if not copy_media:
                raise


def _shorts_transcript(settings, source, manifests):
    """Find verified full-source artifacts; callers copy them before depending on them."""
    database = settings.work / "state.sqlite3"
    if source["provider"] != "youtube" or not database.is_file():
        return None
    with sqlite3.connect(database.as_uri()+"?mode=ro", uri=True) as db:
        rows = db.execute("SELECT id,config FROM runs WHERE json_extract(config,'$.video')=? ORDER BY created DESC",
                          (source["id"],)).fetchall()
    for identity, raw in rows:
        config = json.loads(raw)
        if config.get("model_manifests", {}).get("turbo") != manifests.get("turbo"):
            continue
        folder = settings.work / "runs" / identity
        try:
            audio = json.loads((folder / "audio.checkpoint.json").read_text("utf-8"))
            transcript = json.loads((folder / "transcript.checkpoint.json").read_text("utf-8"))
            if abs(audio["result"]["duration"]-source["duration"]) > 3:
                continue
            artifacts = audio["artifacts"]+transcript["artifacts"]
            if not artifacts or any(not Path(a["path"]).is_file() or digest(Path(a["path"])) != a["sha256"] for a in artifacts):
                continue
            return {"audio": audio["result"], "transcript": transcript["result"]}
        except (OSError, ValueError, KeyError):
            continue
    return None


def reusable_transcript(settings, source, manifests):
    reused = _shorts_transcript(settings, source, manifests)
    if reused:
        return reused
    database = settings.work / "highlights" / "state.sqlite3"
    if not database.is_file():
        return None
    with sqlite3.connect(database.as_uri()+"?mode=ro", uri=True) as db:
        rows = db.execute("SELECT id,config FROM runs WHERE state='completed' ORDER BY created DESC").fetchall()
    audio_only = None
    start, end = selection_bounds(source)
    for identity, raw in rows:
        config = json.loads(raw)
        for old in config.get("manifest", {}).get("sources", []):
            if (old["provider"], old["id"]) != (source["provider"], source["id"]):
                continue
            folder = settings.work / "highlights" / "runs" / identity
            try:
                audio = json.loads((folder / f"audio-{old['asset']}.checkpoint.json").read_text("utf-8"))
                if abs(audio["result"]["duration"]-source["duration"]) > 3:
                    continue
                if not audio["artifacts"] or any(not Path(a["path"]).is_file() or digest(Path(a["path"])) != a["sha256"] for a in audio["artifacts"]):
                    continue
                audio_only = audio_only or {"audio": audio["result"], "transcript": None}
                if config.get("model_manifests", {}).get("turbo") != manifests.get("turbo"):
                    continue
                saved = json.loads((folder / f"transcript-{old['asset']}.checkpoint.json").read_text("utf-8"))
                transcript = saved["result"]
                coverage = transcript.get("coverage", [[0, transcript["duration_us"]]])
                if not any(a <= start < end <= b for a, b in coverage):
                    continue
                if not saved["artifacts"] or any(not Path(a["path"]).is_file() or digest(Path(a["path"])) != a["sha256"] for a in saved["artifacts"]):
                    continue
                return {"audio": audio["result"], "transcript": transcript}
            except (OSError, ValueError, KeyError):
                continue
    return audio_only
