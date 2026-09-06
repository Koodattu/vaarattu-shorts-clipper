from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .contracts import video_id
from .processes import run_tool


def yt_args():
    return [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--no-playlist",
        "--no-warnings",
        "--no-progress",
        "--retries",
        "2",
        "--fragment-retries",
        "2",
        "--socket-timeout",
        "30",
    ]


def metadata(settings, value, folder, check=lambda: None):
    vod_id = video_id(value)
    out = folder / "metadata.raw.json"
    run_tool(
        [*yt_args(), "--skip-download", "--dump-single-json", f"https://www.youtube.com/watch?v={vod_id}"],
        settings,
        folder,
        "metadata",
        check,
        stdout=out,
        timeout=180,
    )
    info = json.loads(out.read_text("utf-8"))
    if info.get("id") != vod_id or info.get("channel_id") != settings.youtube_channel_id:
        raise ValueError("Select a video from the configured YouTube channel.")
    if info.get("live_status") in {"is_live", "is_upcoming", "post_live"} or not info.get("duration"):
        raise ValueError("This video is not yet available as a complete recording.")
    # Signed format URLs and private extractor fields do not belong in durable metadata.
    result = {key: info.get(key) for key in ("id", "channel_id", "title", "upload_date", "duration", "fps")}
    out.unlink()
    return result


def probe(settings, source: Path, folder: Path, check=lambda: None):
    out = folder / "probe.json"
    run_tool(
        [settings.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", source],
        settings,
        folder,
        "probe",
        check,
        stdout=out,
        timeout=120,
    )
    return json.loads(out.read_text("utf-8"))


def acquire(settings, vod_id, folder, check, interval=None):
    folder.mkdir(parents=True, exist_ok=True)
    args = [
        *yt_args(),
        "--ffmpeg-location",
        str(Path(shutil.which(settings.ffmpeg) or settings.ffmpeg).resolve()),
        "--paths",
        str(folder),
        "-o",
        "source.%(ext)s",
        "--print-to-file",
        "after_move:filepath",
        str(folder / "download.txt"),
    ]
    if interval is None:
        args += ["-f", "bestaudio", "--max-filesize", str(int(settings.max_download_gb * 1e9))]
    else:
        start, end = interval
        args += [
            "-f",
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "--download-sections",
            f"*{start:.6f}-{end:.6f}",
            "--force-keyframes-at-cuts",
            "--downloader-args",
            "ffmpeg_o:-c:v libx264 -preset fast -crf 18 -c:a aac -b:a 192k -f matroska",
            "--merge-output-format",
            "mkv",
            "--remux-video",
            "mkv",
        ]
    args += [f"https://www.youtube.com/watch?v={vod_id}"]
    run_tool(
        args,
        settings,
        folder,
        "download",
        check,
        timeout=7200,
        byte_limit=int(settings.max_download_gb * 1e9),
        watch=folder,
    )
    report = folder / "download.txt"
    if not report.exists():
        raise ValueError("The downloader did not produce a media file.")
    source = Path(report.read_text("utf-8").strip().splitlines()[-1]).resolve()
    if not source.is_relative_to(folder.resolve()) or not source.is_file():
        raise ValueError("The downloader returned an invalid output location.")
    info = probe(settings, source, folder, check)
    types = {s["codec_type"] for s in info["streams"]}
    if "audio" not in types or (interval is None and "video" in types):
        raise ValueError("The selected source does not contain the expected media streams.")
    if interval is not None and "video" not in types:
        raise ValueError("The selected section has no video stream.")
    return source


def pcm(settings, source, output, start, duration, check, rate=16000):
    temporary = output.with_name(output.stem + ".partial.wav")
    run_tool(
        [
            settings.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            *(["-ss", f"{start:.6f}"] if start is not None else []),
            "-i",
            source,
            "-t",
            f"{duration:.6f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-c:a",
            "pcm_s16le",
            temporary,
        ],
        settings,
        output.parent,
        "decode",
        check,
    )
    os.replace(temporary, output)
