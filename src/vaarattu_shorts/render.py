from __future__ import annotations

import json
import os
import wave

import numpy as np

from .contracts import MAX_CLIP_US, MIN_CLIP_US, Layout
from .processes import run_tool
from .storage import atomic_json, digest
from .youtube import pcm, probe


def read_wave(path):
    with wave.open(str(path), "rb") as wav:
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float64)


def correlate(reference, query, rate=2000):
    """Normalized valid cross correlation; reject silence and ambiguous repeated audio."""
    query = query - query.mean()
    energy = float(np.dot(query, query))
    if len(reference) < len(query) or energy < len(query) * 100:
        raise ValueError("Audio timing could not be verified from a silent or short sample.")
    n = len(reference) + len(query) - 1
    nfft = 1 << (n - 1).bit_length()
    convolution = np.fft.irfft(np.fft.rfft(reference, nfft) * np.fft.rfft(query[::-1], nfft), nfft)
    numerator = convolution[len(query) - 1 : len(reference)]
    total = np.r_[0, np.cumsum(reference)]
    square = np.r_[0, np.cumsum(reference * reference)]
    count = len(query)
    local_energy = square[count:] - square[:-count] - (total[count:] - total[:-count]) ** 2 / count
    scores = numerator / np.sqrt(np.maximum(1, local_energy) * energy)
    index = int(np.argmax(scores))
    best = float(scores[index])
    second = scores.copy()
    second[max(0, index - rate // 4) : index + rate // 4 + 1] = -1
    if best < 0.65 or float(second.max(initial=-1)) > best * 0.98:
        raise ValueError("The section's audio timing is ambiguous. This clip needs alignment review.")
    return index / rate, best


def align(settings, source_audio, section, requested_start, folder, check):
    info = probe(settings, section, folder, check)
    duration = float(info["format"]["duration"])
    if duration < 14:
        raise ValueError("The downloaded section is too short for two timing checks.")
    # Decode the short section once. Seeking a sparse video index can shift the audio sample.
    decoded = folder / "alignment.wav"
    pcm(settings, section, decoded, None, duration, check, rate=2000)
    waveform = read_wave(decoded)
    decoded.unlink()
    duration = len(waveform) / 2000
    origins, samples, failures = [], [], []
    positions = [2.0, duration - 8.0, duration * 0.25, duration * 0.5, duration * 0.75]
    for i, position in enumerate(dict.fromkeys(positions)):
        check()
        if not 0 <= position <= duration - 6:
            continue
        expected = requested_start + position
        reference_start = max(0, expected - 30)
        ref = folder / f"reference-{i}.wav"
        pcm(settings, source_audio, ref, reference_start, 66, check, rate=2000)
        reference = read_wave(ref)
        ref.unlink()
        query = waveform[round(position * 2000) : round((position + 6) * 2000)]
        try:
            offset, score = correlate(reference, query)
        except ValueError as exc:
            failures.append({"section_seconds": position, "reason": str(exc)})
            continue
        origin = reference_start + offset - position
        origins.append(origin)
        samples.append(
            {"section_seconds": position, "source_seconds": origin + position, "correlation": score}
        )
        if max(origins) - min(origins) > 0.08:
            break
        if max(s["section_seconds"] for s in samples) - min(s["section_seconds"] for s in samples) >= max(
            6, duration * 0.35
        ):
            break
    atomic_json(folder / "alignment.json", {"anchors": samples, "unusable_samples": failures})
    if len(origins) >= 2 and max(origins) - min(origins) > 0.08:
        raise ValueError("The section and full audio drift apart. This clip needs alignment review.")
    if len(origins) < 2 or max(s["section_seconds"] for s in samples) - min(
        s["section_seconds"] for s in samples
    ) < max(6, duration * 0.35):
        raise ValueError(
            "Could not find two clear, separated audio matches. Retry or review this clip's timing."
        )
    return {
        "origin_us": round(sum(origins) / len(origins) * 1e6),
        "anchors": samples,
        "section_duration": duration,
    }


def caption_cues(words, start_us, end_us):
    selected = [w for w in words if w.start_us >= start_us and w.end_us <= end_us]
    cues, group = [], []
    for word in selected:
        text = " ".join(w.text for w in [*group, word])
        if group and (
            len(text) > 30
            or len(group) >= 4
            or word.start_us - group[-1].end_us > 450000
            or word.end_us - group[0].start_us > 3000000
        ):
            cues.append(group)
            group = []
        group.append(word)
    if group:
        cues.append(group)
    result = []
    for group in cues:
        text = " ".join(w.text for w in group)
        middle = None
        # Two short lines prevent long Finnish compounds from disappearing off the canvas.
        if len(text) > 20 and len(group) > 1:
            options = [
                (abs(len(" ".join(w.text for w in group[:i])) - len(" ".join(w.text for w in group[i:]))), i)
                for i in range(1, len(group))
            ]
            _, middle = min(options)
            text = " ".join(w.text for w in group[:middle]) + "\n" + " ".join(w.text for w in group[middle:])
        result.append(
            {
                "start_us": group[0].start_us - start_us,
                "end_us": max(w.end_us for w in group) - start_us,
                "text": text,
                "words": group,
                "line_break": middle,
            }
        )
    return result


def stamp(us, ass=False):
    units = us // (10000 if ass else 1000)
    per_second = 100 if ass else 1000
    seconds, fraction = divmod(units, per_second)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return (
        f"{hours:01d}:{minutes:02d}:{seconds:02d}.{fraction:02d}"
        if ass
        else f"{hours:02d}:{minutes:02d}:{seconds:02d},{fraction:03d}"
    )


def captions(folder, words, start, end):
    cues = caption_cues(words, start, end)
    srt, ass = (
        [],
        [
            """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,86,&H00FFFFFF,&H0046C7FF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,90,90,360,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        ],
    )
    flags = []
    selected = [w for w in words if w.start_us >= start and w.end_us <= end]
    if any(a.end_us - b.start_us > 150000 for a, b in zip(selected, selected[1:])):
        flags.append("Speech timestamps overlap slightly; inspect this clip's caption timing.")
    for i, cue in enumerate(cues, 1):
        duration = (cue["end_us"] - cue["start_us"]) / 1e6
        if duration <= 0 or max(map(len, cue["text"].splitlines())) > 28:
            flags.append("Captions need a shorter line or timing adjustment.")
        if duration > 0 and len(cue["text"].replace("\n", " ")) / duration > 30:
            flags.append("Captions are too fast to read comfortably; inspect this excerpt.")
        srt.append(f"{i}\n{stamp(cue['start_us'])} --> {stamp(cue['end_us'])}\n{cue['text']}\n")
    # One event per timing interval avoids stacking duplicate phrases during overlapping ASR words.
    boundaries = sorted(
        {max(0, (t - start) // 10000) * 10000 for w in selected for t in (w.start_us, w.end_us)}
    )
    for left, right in zip(boundaries, boundaries[1:]):
        active = [c for c in cues if c["start_us"] // 10000 * 10000 <= left < c["end_us"] // 10000 * 10000]
        if not active:
            continue
        cue = active[-1]
        spoken = [
            w
            for w in cue["words"]
            if (w.start_us - start) // 10000 * 10000 <= left < (w.end_us - start) // 10000 * 10000
        ]
        current = spoken[-1] if spoken else None
        # Conservative width allowance for exceptional Finnish compounds; normal phrases stay large.
        size = min(86, max(1, int(880 / (max(map(len, cue["text"].splitlines())) * 0.65))))
        tokens = []
        for index, word in enumerate(cue["words"]):
            escaped = word.text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
            escaped = "".join(c for c in escaped if ord(c) >= 32)
            if index:
                tokens.append(r"\N" if index == cue["line_break"] else " ")
            tokens.append(
                (r"{\1c&H0046C7FF&}" + escaped + r"{\1c&H00FFFFFF&}") if word is current else escaped
            )
        text = "".join(tokens)
        ass.append(
            f"Dialogue: 0,{stamp(left, True)},{stamp(right, True)},Default,,0,0,0,,{{\\fs{size}}}{text}\n"
        )
    if not cues:
        flags.append("No caption words fall inside this excerpt.")
    (folder / "captions.srt").write_text("\n".join(srt), encoding="utf-8")
    (folder / "captions.ass").write_text("".join(ass), encoding="utf-8")
    atomic_json(
        folder / "captions.words.json",
        [w.model_dump() for w in words if w.start_us >= start and w.end_us <= end],
    )
    return sorted(set(flags))


def crop(rect, width, height):
    x, y = int(rect.x * width) // 2 * 2, int(rect.y * height) // 2 * 2
    w, h = int(rect.width * width) // 2 * 2, int(rect.height * height) // 2 * 2
    if w < 32 or h < 32 or x + w > width or y + h > height:
        raise ValueError("The saved crop is too small or falls outside this source.")
    return f"crop={w}:{h}:{x}:{y}"


def render_clip(settings, section, mapping, body, layout, words, folder, check):
    folder.mkdir(parents=True, exist_ok=True)
    start, end = body["start_us"], body["end_us"]
    local_start = (start - mapping["origin_us"]) / 1e6
    duration = (end - start) / 1e6
    if (
        local_start < 0
        or end - start < MIN_CLIP_US
        or end - start > MAX_CLIP_US
        or local_start + duration > mapping["section_duration"] + 0.05
    ):
        raise ValueError("The chosen boundaries fall outside the verified section or 2–90 second range.")
    layout = Layout.model_validate(layout)
    info = probe(settings, section, folder, check)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    video_start = float(video.get("start_time", 0)) - float(info.get("format", {}).get("start_time", 0))
    if video_start > local_start + 0.05:
        raise ValueError("The picture starts after the chosen clip boundary. Retry the section download.")
    width, height = video["width"], video["height"]
    flags = captions(folder, words, start, end)
    if any(
        issue["start_us"] < end and issue["end_us"] > start
        for issue in body.get("transcript_timing_issues", [])
    ):
        flags.append("Transcription is uncertain in this section; inspect its speech, cut and captions.")
    if not layout.calibrated or not layout.solo_host:
        flags.append("Check the source layout and primary speaker before publishing.")
    camera, gameplay = crop(layout.camera, width, height), crop(layout.gameplay, width, height)
    filters = (
        f"[0:v]trim=start={local_start:.6f}:duration={duration:.6f},setpts=PTS-STARTPTS,split=2[c][g];"
        f"[c]{camera},scale=1080:608:force_original_aspect_ratio=increase,"
        "crop=1080:608,setsar=1[cam];"
        f"[g]{gameplay},scale=1080:1312:force_original_aspect_ratio=increase,crop=1080:1312,setsar=1[game];"
        "[cam][game]vstack,subtitles=filename=captions.ass,fps=30,format=yuv420p[v];"
        f"[0:a]atrim=start={local_start:.6f}:duration={duration:.6f},asetpts=PTS-STARTPTS,aresample=48000[a]"
    )
    (folder / "filters.txt").write_text(filters, encoding="utf-8")
    run_tool(
        [
            settings.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            section,
            "-filter_complex_script",
            "filters.txt",
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "short.mp4",
        ],
        settings,
        folder,
        "render",
        check,
    )
    output = folder / "short.mp4"
    run_tool(
        [settings.ffmpeg, "-nostdin", "-v", "error", "-i", output, "-f", "null", "-"],
        settings,
        folder,
        "decode-check",
        check,
    )
    final = probe(settings, output, folder, check)
    streams = {s["codec_type"]: s for s in final["streams"]}
    v, a = streams.get("video", {}), streams.get("audio", {})
    if (v.get("width"), v.get("height"), v.get("pix_fmt")) != (1080, 1920, "yuv420p") or not a:
        raise ValueError("The rendered video does not match the delivery format.")
    if abs(float(final["format"]["duration"]) - duration) > 0.15:
        raise ValueError("The rendered video duration does not match the excerpt.")
    if abs(float(v.get("duration", duration)) - float(a.get("duration", duration))) > 0.1:
        raise ValueError("Audio and video durations disagree.")
    if abs(float(v.get("start_time", 0)) - float(a.get("start_time", 0))) > 0.05:
        raise ValueError("The rendered picture and audio do not start together.")
    # Detect cuts in the source picture. A calibrated crop is unsafe across unknown layout changes.
    scene_log = folder / "scene.log"
    run_tool(
        [
            settings.ffmpeg,
            "-nostdin",
            "-v",
            "info",
            "-ss",
            f"{local_start:.6f}",
            "-i",
            section,
            "-t",
            str(duration),
            "-an",
            "-vf",
            "select='gt(scene,0.45)',showinfo",
            "-f",
            "null",
            "-",
        ],
        settings,
        folder,
        "scene",
        check,
    )
    if "pts_time:" in scene_log.read_text("utf-8", errors="replace") and not body.get("reviewed"):
        flags.append("A scene change needs a layout check.")
    run_tool(
        [
            settings.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            output,
            "-vf",
            f"fps=1/{max(1, duration / 3):.3f},scale=270:480,tile=3x1",
            "-frames:v",
            "1",
            "contact.jpg",
        ],
        settings,
        folder,
        "contact",
        check,
    )
    (folder / "title.txt").write_text(body["title"], encoding="utf-8")
    metadata = {
        **body,
        "mapping": mapping,
        "layout": layout.model_dump(),
        "flags": sorted(set([*flags, *body.get("selection", {}).get("flags", [])])),
        "status": "ready",
        "video_sha256": digest(output),
    }
    atomic_json(folder / "metadata.json", metadata)
    return metadata


def promote(settings, clip_id, revision, staging):
    target = settings.ready / clip_id / str(revision)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        old = json.loads((target / "metadata.json").read_text("utf-8"))
        if digest(target / "short.mp4") != old["video_sha256"]:
            raise ValueError("An existing export failed its checksum check.")
        return target
    os.replace(staging, target)
    return target
