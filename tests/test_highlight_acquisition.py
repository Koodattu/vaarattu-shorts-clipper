import json
from pathlib import Path

import numpy as np
import pytest

from vaarattu_shorts import highlight_sources as sources, render
from vaarattu_shorts.processes import Interrupted, ToolError


@pytest.mark.parametrize("fault", [None, "acquire", "align", "coverage", "decode"])
def test_copy_acquisition_is_verified_and_falls_back_only_on_failure(settings, monkeypatch, fault):
    calls = []
    def acquire(settings, source, folder, check, interval, *, stream_copy):
        calls.append(("acquire", stream_copy))
        folder.mkdir(parents=True, exist_ok=True)
        if fault == "acquire" and stream_copy:
            raise ToolError("Copy failed")
        path = folder / "source.mkv"
        path.write_bytes(b"media")
        return path
    def align(settings, audio, path, start, folder, check, **kwargs):
        copied = folder.name == "copy"
        calls.append(("align", copied))
        if fault == "align" and copied:
            raise ValueError("Unverified timing")
        return {"origin_us": 20000000 if copied and fault == "coverage" else 0, "section_duration": 40}
    def decode(args, settings, folder, *a, **kw):
        calls.append(("decode", folder.name == "copy"))
        if fault == "decode" and folder.name == "copy":
            raise ToolError("Broken first frame")
        assert "-xerror" in args
    monkeypatch.setattr(sources, "acquire", acquire)
    monkeypatch.setattr(render, "align", align)
    monkeypatch.setattr(sources, "run_tool", decode)
    path, mapping = sources.acquire_aligned(settings, {}, settings.work / "section", lambda: None,
        (0, 40), settings.work / "audio.opus", [{"start_us": 10000000, "end_us": 30000000}])
    assert path.parent.name == ("copy" if fault is None else "encoded")
    assert mapping["origin_us"] == 0
    assert [c for c in calls if c[0] == "acquire"] == ([("acquire", True)] if fault is None else [("acquire", True), ("acquire", False)])
    audit = json.loads((settings.work / "section/acquisition.json").read_text('utf-8'))
    assert audit["attempts"][-1]["status"] == "verified"


def test_cancellation_never_launches_encoding_fallback(settings, monkeypatch):
    calls = []
    def acquire(*a, **kw):
        calls.append(kw["stream_copy"])
        raise Interrupted("pause")
    monkeypatch.setattr(sources, "acquire", acquire)
    with pytest.raises(Interrupted):
        sources.acquire_aligned(settings, {}, settings.work, lambda: None, (0, 40), Path('audio'), [])
    assert calls == [True]


def test_both_acquisition_failures_propagate(settings, monkeypatch):
    def acquire(*a, **kw):
        a[2].mkdir(parents=True, exist_ok=True)
        raise ToolError("Network unavailable")
    monkeypatch.setattr(sources, "acquire", acquire)
    with pytest.raises(ToolError, match="Network unavailable"):
        sources.acquire_aligned(settings, {}, settings.work, lambda: None, (0, 40), Path('audio'), [])
    audit = json.loads((settings.work / 'acquisition.json').read_text('utf-8'))
    assert [a['status'] for a in audit['attempts']] == ['failed', 'failed']


@pytest.mark.parametrize("copy_media", [False, True])
def test_copy_flag_changes_only_highlight_acquisition_when_requested(settings, monkeypatch, copy_media):
    commands = []
    def run(args, settings, folder, *a, **kw):
        commands.append(args)
        path = folder / 'source.mkv'
        path.write_bytes(b'media')
        (folder / 'download.txt').write_text(str(path), encoding='utf-8')
    monkeypatch.setattr(sources, 'run_tool', run)
    monkeypatch.setattr(sources.youtube, 'probe', lambda *a: {'streams': [{'codec_type': 'audio'}, {'codec_type': 'video'}]})
    sources.acquire(settings, {'url': 'https://www.twitch.tv/videos/123'}, settings.work / 'section', lambda: None, (10, 40), stream_copy=copy_media)
    args = commands[0]
    assert ('--force-keyframes-at-cuts' in args) == (not copy_media)
    assert ('ffmpeg_o:-c copy -f matroska' in args) == copy_media
    assert args[args.index('--download-sections')+1] == '*10.000000-40.000000'


def test_dense_alignment_skips_redundant_matches_and_reuses_reference_decode(settings, monkeypatch):
    import wave
    rate = 2000
    rng = np.random.default_rng(19)
    full = rng.integers(-3000, 3000, 800*rate, dtype=np.int16)
    section = full[50*rate:750*rate].copy()
    # Coarse far anchors are silent; the first separated dense sample remains usable.
    for position in [692, 175, 350, 525]:
        section[position*rate:(position+6)*rate] = 0
    paths = settings.work / 'audio.opus', settings.work / 'section.mkv'
    decodes = []
    def pcm(settings, path, output, start, duration, check, rate):
        decodes.append((path, start))
        data = section if path == paths[1] else full
        a = round((start or 0)*rate)
        with wave.open(str(output), 'wb') as f:
            f.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
            f.writeframes(data[a:a+round(duration*rate)].tobytes())
    monkeypatch.setattr(render, 'pcm', pcm)
    monkeypatch.setattr(render, 'probe', lambda *a: {'format': {'duration': '700'}})
    result = render.align(settings, *paths, 50, settings.work, lambda: None, 660)
    assert result['origin_us'] == 50000000
    assert len(result['anchors']) == 2
    assert result['anchors'][-1]['section_seconds']-result['anchors'][0]['section_seconds'] >= 231
    assert len(decodes) == 3  # one section decode and two reference windows
