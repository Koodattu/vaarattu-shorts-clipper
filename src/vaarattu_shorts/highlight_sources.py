"""Source manifests for landscape highlights; shorts retain their YouTube adapter."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from . import catalog, youtube
from .contracts import video_id
from .processes import run_tool
from .storage import digest

PART = re.compile(r"\s*\((?:part|osa)\s+(\d+)\s*/\s*(\d+)\)\s*$", re.I)


def source_url(value):
    value = value.strip()
    parsed = urlparse(value)
    if parsed.hostname in {"twitch.tv", "www.twitch.tv", "m.twitch.tv"}:
        match = re.fullmatch(r"/videos/(\d+)/?", parsed.path)
        if parsed.scheme not in {"https", "http"} or not match or parsed.username or parsed.port:
            raise ValueError("Use a completed Twitch VOD URL, such as https://www.twitch.tv/videos/123456.")
        return "twitch", match[1], f"https://www.twitch.tv/videos/{match[1]}"
    try:
        identity = video_id(value)
    except ValueError:
        raise ValueError("Enter a YouTube video or a completed Twitch VOD URL.") from None
    return "youtube", identity, f"https://www.youtube.com/watch?v={identity}"


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


def resolve(settings, store, urls, folder):
    normalized = list(dict.fromkeys(source_url(u)[2] for u in urls))
    if not 1 <= len(normalized) <= 30:
        raise ValueError("Enter between one and 30 source URLs.")
    first = metadata(settings, normalized[0], folder / "0")
    if len(normalized) == 1 and first["provider"] == "youtube" and part_info(first["title"]):
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
    if len(sources) > 1:
        if any(s["provider"] != "youtube" or s["owner"] != first["owner"] for s in sources):
            raise ValueError("Combine only YouTube parts of the same recording; use one Twitch VOD URL.")
        sources = ordered_parts(sources)
    elif sources[0]["provider"] == "youtube":
        ordered_parts(sources)
    for i, source in enumerate(sources):
        source["asset"] = f"s{i}"
    return {"title": PART.sub("", sources[0]["title"]).strip(), "sources": sources,
            "duration": sum(s["duration"] for s in sources),
            "notes": (["Part boundaries stay separate; footage is never joined across an unverified seam."]
                      if len(sources) > 1 else [])}


def acquire(settings, source, folder, check, interval=None):
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
                 "--download-sections", f"*{start:.6f}-{end:.6f}", "--force-keyframes-at-cuts",
                 "--downloader-args", "ffmpeg_o:-c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k -f matroska",
                 "--merge-output-format", "mkv", "--remux-video", "mkv"]
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


def reusable_transcript(settings, source, manifests):
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
