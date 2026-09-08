import json

import pytest

from vaarattu_shorts import pacing, render
from vaarattu_shorts.contracts import Word


def word(start, end, name="speech"):
    return Word(id=f"{start}-{end}", start_us=round(start * 1e6), end_us=round(end * 1e6), text=name)


def test_long_transcript_gaps_are_removed_and_all_words_survive():
    words = [word(100, 102), word(108, 110), word(111, 112)]
    result = pacing.plan(100000000, 113000000, words)
    assert result["removed"] == [[102300000, 107700000]]
    assert result["output_duration_us"] == 7600000
    mapped = pacing.retime(words, result)
    assert [(w.start_us, w.end_us) for w in mapped] == [(0, 2000000), (2600000, 4600000), (5600000, 6600000)]
    assert [w.id for w in mapped] == [w.id for w in words]
    assert words[1].start_us == 108000000
    graph = pacing.filters(result, 99000000)
    assert "trim=start=1.000000:duration=13.000000" in graph
    assert "5.400000*gte(T,7.700000)" in graph
    assert "concat=n=2:v=0:a=1" in graph
    assert "afade=t=in:d=0.005" in graph


@pytest.mark.parametrize("issues,enabled", [([{"start_us": 4000000, "end_us": 5000000}], True), ([], False)])
def test_uncertain_gaps_or_disabled_trimming_stay_intact(issues, enabled):
    result = pacing.plan(0, 10000000, [word(0, 2), word(8, 10)], issues, enabled=enabled)
    assert result["removed"] == []
    assert result["output_duration_us"] == 10000000


def test_short_gaps_nested_words_and_minimum_length_are_protected():
    assert pacing.plan(0, 4000000, [word(0, 2), word(3.49, 4)])["removed"] == []
    words = [word(0, 9), word(1, 2), word(10, 12)]
    assert pacing.plan(0, 12000000, words)["removed"] == []
    assert pacing.plan(0, 10000000, [word(0, 0.1), word(9.9, 10)])["removed"] == []
    with pytest.raises(ValueError, match="remove speech"):
        pacing.retime([word(2, 5)], pacing.plan(0, 10000000, [word(0, 2), word(8, 10)]))


def test_multiple_gaps_keep_padding_and_retime_repeated_words():
    words = [word(0, 2, "niin"), word(4, 6, "niin"), word(10, 12)]
    result = pacing.plan(0, 12000000, words)
    assert result["removed"] == [[2300000, 3700000], [6300000, 9700000]]
    assert result["output_duration_us"] == 7200000
    assert [w.start_us for w in pacing.retime(words, result)] == [0, 2600000, 5200000]
    assert result["method"] == "transcript_gaps"


def test_nvenc_paced_render_checks_edited_duration_and_preserves_source_words(settings, monkeypatch):
    folder = settings.work / "paced"
    commands = []

    def run(args, settings, cwd, name, check):
        commands.append((name, [str(a) for a in args]))
        (cwd / f"{name}.log").write_text("")
        if name == "render":
            (cwd / "short.mp4").write_bytes(b"fixture")

    def probe(settings, path, cwd, check):
        if path.name == "short.mp4":
            return {
                "format": {"duration": "6.6"},
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1080,
                        "height": 1920,
                        "pix_fmt": "yuv420p",
                        "duration": "6.6",
                    },
                    {"codec_type": "audio", "duration": "6.6"},
                ],
            }
        return {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}

    monkeypatch.setattr(render, "run_tool", run)
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
    assert result["output_duration_us"] == 6600000
    assert result["words"] == body["words"]
    assert result["video_encoder"] == "h264_nvenc"
    assert any("Shortened 5.4s" in f for f in result["flags"])
    args = dict(commands)["render"]
    assert "h264_nvenc" in args and "-cq" in args and "-crf" not in args
    assert "silence" not in dict(commands), "Transcript pacing does not use an audio volume detector"
    captions = json.loads((folder / "captions.words.json").read_text("utf-8"))
    assert captions[1]["start_us"] == 2600000
    assert json.loads((folder / "captions.source.words.json").read_text("utf-8")) == body["words"]
