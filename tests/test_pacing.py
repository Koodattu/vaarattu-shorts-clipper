import json

import pytest

from vaarattu_shorts import pacing, render
from vaarattu_shorts.contracts import Word


def word(start, end, name="speech"):
    return Word(id=f"{start}-{end}", start_us=round(start * 1e6), end_us=round(end * 1e6), text=name)


def test_only_long_quiet_internal_gaps_are_removed_and_all_words_survive():
    words = [word(100, 102), word(108, 110), word(111, 112)]
    result = pacing.plan(100000000, 113000000, words, [(102200000, 107800000), (110000000, 111000000)])
    assert result["removed"] == [[102700000, 107300000]]
    assert result["output_duration_us"] == 8400000
    mapped = pacing.retime(words, result)
    assert [(w.start_us, w.end_us) for w in mapped] == [(0, 2000000), (3400000, 5400000), (6400000, 7400000)]
    assert [w.id for w in mapped] == [w.id for w in words]
    assert words[1].start_us == 108000000
    graph = pacing.filters(result, 99000000)
    assert "trim=start=1.000000:duration=13.000000" in graph
    assert "4.600000*gte(T,7.300000)" in graph
    assert "concat=n=2:v=0:a=1" in graph  # No A/V concat padding at every seam.
    assert "afade=t=in:d=0.005" in graph


@pytest.mark.parametrize(
    "quiet,issues",
    [
        ([], []),
        ([(2000000, 3900000)], []),
        ([(2000000, 8000000)], [{"start_us": 4000000, "end_us": 5000000}]),
    ],
)
def test_short_or_noisy_or_uncertain_gaps_stay_intact(quiet, issues):
    result = pacing.plan(0, 10000000, [word(0, 2), word(8, 10)], quiet, issues)
    assert result["removed"] == []
    assert result["output_duration_us"] == 10000000


def test_nested_words_and_untranscribed_audio_are_protected():
    words = [word(0, 9), word(1, 2), word(10, 12)]
    assert pacing.plan(0, 12000000, words, [(2000000, 10000000)])["removed"] == []
    # Speech/laughter missing from ASR divides the quiet interval; neither short fragment qualifies.
    assert (
        pacing.plan(0, 6000000, [word(0, 1), word(5, 6)], [(1000000, 2500000), (3500000, 5000000)])["removed"]
        == []
    )
    with pytest.raises(ValueError, match="remove speech"):
        pacing.retime([word(2, 5)], pacing.plan(0, 8000000, [word(0, 1), word(7, 8)], [(1000000, 7000000)]))


def test_silence_log_uses_clip_relative_seconds_and_ignores_unclosed_tail():
    assert pacing.quiet_intervals(
        "silence_start: 2.2\nsilence_end: 7.8 | silence_duration: 5.6\nsilence_start: 9", 100000000, 110000000
    ) == [(102200000, 107800000)]


def test_nvenc_paced_render_checks_edited_duration_and_preserves_source_words(settings, monkeypatch):
    folder = settings.work / "paced"
    commands = []

    def run(args, settings, cwd, name, check):
        commands.append((name, [str(a) for a in args]))
        (cwd / f"{name}.log").write_text("silence_start: 2.2\nsilence_end: 7.8" if name == "silence" else "")
        if name == "render":
            (cwd / "short.mp4").write_bytes(b"fixture")

    def probe(settings, path, cwd, check):
        if path.name == "short.mp4":
            return {
                "format": {"duration": "7.4"},
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1080,
                        "height": 1920,
                        "pix_fmt": "yuv420p",
                        "duration": "7.4",
                    },
                    {"codec_type": "audio", "duration": "7.4"},
                ],
            }
        return {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}

    monkeypatch.setattr(render, "run_tool", run)
    monkeypatch.setattr(pacing, "run_tool", run)
    monkeypatch.setattr(render, "probe", probe)
    words = [word(101, 103), word(109, 111)]
    body = {
        "start_us": 101000000,
        "end_us": 113000000,
        "title": "Test",
        "trim_silence": True,
        "video_encoder": "h264_nvenc",
        "words": [w.model_dump() for w in words],
    }
    layout = {
        "name": "test",
        "calibrated": True,
        "solo_host": True,
        "camera": {"x": 0, "y": 0, "width": 0.3, "height": 0.3},
        "gameplay": {"x": 0.3, "y": 0.3, "width": 0.7, "height": 0.7},
    }
    result = render.render_clip(
        settings,
        settings.work / "source.mkv",
        {"origin_us": 100000000, "section_duration": 20},
        body,
        layout,
        words,
        folder,
        lambda: None,
    )
    assert result["output_duration_us"] == 7400000
    assert result["words"] == body["words"]
    assert result["video_encoder"] == "h264_nvenc"
    assert any("Shortened 4.6s" in f for f in result["flags"])
    args = dict(commands)["render"]
    assert "h264_nvenc" in args and "-cq" in args and "-crf" not in args
    detection = dict(commands)["silence"]
    assert "-ac" not in detection and "-50dB:d=2" in detection[detection.index("-af") + 1]
    captions = json.loads((folder / "captions.words.json").read_text("utf-8"))
    assert captions[1]["start_us"] == 3400000
    assert json.loads((folder / "captions.source.words.json").read_text("utf-8")) == body["words"]
