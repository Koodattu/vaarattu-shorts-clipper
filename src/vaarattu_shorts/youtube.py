from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

from .contracts import video_id
from .processes import ToolError, ToolExitError, run_tool
from .storage import atomic_json


def yt_args():
    args = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--retries",
        "2",
        "--fragment-retries",
        "2",
        "--socket-timeout",
        "30",
    ]
    # Node is not enabled by yt-dlp automatically. Prefer its default Deno when present.
    if not shutil.which("deno") and (node := shutil.which("node")):
        args += ["--js-runtimes", f"node:{node}"]
    return args


DOWNLOAD_RETRY_DELAYS = (30, 120, 300)


def download_failure(log):
    """Classify diagnostics without exposing signed URLs or arbitrary tool output."""
    if not log.is_file():
        return "YouTube download failed", False, None
    with log.open("rb") as handle:
        handle.seek(max(0, log.stat().st_size - 65536))
        tail = handle.read().decode("utf-8", errors="replace")
    # Warnings can mention 403 on an unused format; only inspect the final error.
    errors = re.findall(r"^(?!WARNING:).*(?:ERROR:|Error opening input|HTTP error \d{3}|Server returned \d{3}).*$", tail, re.MULTILINE)
    detail = "\n".join(errors[-3:]).lower()
    if any(text in detail for text in ("private video", "video unavailable", "has been removed",
                                       "sign in", "login required", "members-only", "age-restricted")):
        return "YouTube recording is unavailable or requires account access", False, None
    statuses = re.findall(r"(?:http(?: error)?|server returned)\s+(\d{3})", detail)
    if statuses:
        status = int(statuses[-1])
        retry = status in {403, 408, 429, 500, 502, 503, 504}
        return f"YouTube download returned HTTP {status}", retry, status
    if any(text in detail for text in ("timed out", "timeout", "connection reset", "connection aborted",
                                       "temporary failure in name resolution", "remote end closed")):
        return "YouTube download encountered a temporary network failure", True, None
    if "requested format is not available" in detail:
        return "YouTube did not offer the requested media format", False, None
    return "YouTube download failed", False, None


def wait_for_download_retry(seconds, check):
    deadline = time.monotonic() + seconds
    while True:
        check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def download(args, settings, folder, check, *, audio_only=False, runner=None, **kwargs):
    """Refresh extraction after transient failures; never retry cancellation or resource limits."""
    runner = runner or run_tool
    args = list(args)
    token = uuid.uuid4().hex[:12]
    attempts = []
    fallback = False
    report = folder / "download.txt"
    for attempt in range(len(DOWNLOAD_RETRY_DELAYS) + 1):
        check()
        # yt-dlp appends this report; an unsuccessful attempt must not reuse a stale path.
        report.unlink(missing_ok=True)
        try:
            runner(args, settings, folder, "download", check, **kwargs)
        except ToolExitError as exc:
            check()
            log = folder / "download.log"
            reason, retryable, status = download_failure(log)
            saved_log = f"download-{token}-attempt-{attempt + 1}.log"
            if log.is_file():
                shutil.copyfile(log, folder / saved_log)
            retry = retryable and attempt < len(DOWNLOAD_RETRY_DELAYS)
            delay = DOWNLOAD_RETRY_DELAYS[attempt] if retry else 0
            attempts.append({"attempt": attempt + 1, "reason": reason, "log": saved_log,
                             "audio_fallback": fallback, "retry_after_seconds": delay})
            atomic_json(folder / "download-retry.json", {
                "state": "waiting" if retry else "failed", "attempts": attempts,
                "retry_at": time.time() + delay if retry else None})
            if not retry:
                ending = " Automatic retries exhausted." if retryable else ""
                raise ToolError(f"{reason}.{ending} See download.log in this run's media folder.") from exc
            # Keep normal format selection for two refreshed attempts. On persistent 403,
            # try a different audio container in its own file (never resume mixed formats).
            if audio_only and status == 403 and attempt == len(DOWNLOAD_RETRY_DELAYS) - 1:
                args[args.index("-f") + 1] = "bestaudio[ext=m4a]"
                args[args.index("-o") + 1] = "source-fallback.%(ext)s"
                fallback = True
            wait_for_download_retry(delay, check)
        else:
            atomic_json(folder / "download-retry.json", {
                "state": "completed", "attempts": attempts, "successful_attempt": attempt + 1,
                "audio_fallback": fallback})
            return


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
    download(
        args,
        settings,
        folder,
        check,
        audio_only=interval is None,
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
