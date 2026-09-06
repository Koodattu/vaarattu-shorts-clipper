import numpy as np
import pytest

from vaarattu_shorts.contracts import Candidate, Word
from vaarattu_shorts.discover import resolve, windows
from vaarattu_shorts.render import captions, correlate, stamp
from vaarattu_shorts.stream_data import boost, rank_matches, recording_date
from vaarattu_shorts.transcribe import merge_chunks
from vaarattu_shorts.youtube import acquire


def word(i, start, end, text="sana"):
    return Word(id=f"w_{i}", start_us=start, end_us=end, text=text)


def test_chunk_offsets_and_ownership_keep_repeated_words():
    chunks = [
        (
            {"offset_us": 0, "core_start_us": 0, "core_end_us": 2000000},
            {"words": [{"start": 1, "end": 1.4, "text": "niin"}, {"start": 1.5, "end": 1.9, "text": "niin"}]},
        ),
        (
            {"offset_us": 1000000, "core_start_us": 2000000, "core_end_us": 4000000},
            {
                "words": [
                    {"start": 0.5, "end": 0.9, "text": "niin"},
                    {"start": 2, "end": 2.5, "text": "kyllä"},
                ]
            },
        ),
    ]
    result = merge_chunks(chunks, 4000000)
    assert [w.text for w in result] == ["niin", "niin", "kyllä"]
    assert result[-1].start_us == 3000000


def test_alignment_finds_offset_and_rejects_silence_and_repetition():
    rng = np.random.default_rng(1)
    ref = rng.normal(0, 1000, 24000)
    query = ref[4321:8321] * 0.7 + 10
    offset, score = correlate(ref, query)
    assert offset == pytest.approx(4321 / 2000)
    assert score > 0.99
    with pytest.raises(ValueError):
        correlate(np.zeros(24000), np.zeros(4000))
    with pytest.raises(ValueError, match="ambiguous"):
        correlate(np.tile(query, 4), query)


def test_caption_source_clock_and_finnish_escape(tmp_path):
    words = [
        word(1, 10000000, 10500000, "Älä"),
        word(2, 10600000, 11000000, "{poista}\\N"),
        word(3, 13000000, 14000000, "ulkopuoli"),
    ]
    captions(tmp_path, words, 9900000, 12000000)
    srt = (tmp_path / "captions.srt").read_text("utf-8")
    ass = (tmp_path / "captions.ass").read_text("utf-8")
    assert "00:00:00,100" in srt and "Älä" in srt and "ulkopuoli" not in srt
    assert "{poista}" not in ass and "｛poista｝" in ass
    assert stamp(3661999000) == "01:01:01,999"


def test_anchor_order_and_unknown_ids():
    candidate = Candidate(
        outcome="accept",
        start_word_id="w_1",
        end_word_id="w_2",
        idea_word_id="w_1",
        category="opinion",
        summary_fi="Ajatus",
        title_fi="Otsikko",
        flags=[],
        reason="OK",
        scores=dict(standalone=4, opening=4, substance=4, payoff=4, fidelity=4),
    )
    assert resolve(candidate, [word(1, 0, 1000000), word(2, 2000000, 3000000)]) == (0, 3000000)
    with pytest.raises(ValueError):
        resolve(candidate, [word(2, 0, 1000000)])
    with pytest.raises(ValueError):
        resolve(
            candidate.model_copy(update={"idea_word_id": "w_2", "end_word_id": "w_1"}),
            [word(1, 0, 1000000), word(2, 2000000, 3000000)],
        )


def test_window_coverage_includes_late_speech_and_silence():
    class Evaluator:
        discovery_budget = 7000

        def check(self):
            pass

        def request_size(self, system, prompt, schema):
            return 100

    result = list(windows([word(1, 800000000, 801000000)], 900000000, Evaluator()))
    assert [(a, b) for a, b, _ in result] == [(0, 360000000), (360000000, 720000000), (720000000, 900000000)]
    assert result[-1][2][0].id == "w_1"


def test_stream_date_not_substring_or_upload_date():
    assert recording_date("29.7.2026 - same") != recording_date("9.7.2026 - same")
    streams = [{"id": 254, "startTime": "2026-07-09T14:51:00Z", "segments": [{"title": "same"}]}]
    assert rank_matches({"title": "9.7.2026 - same", "upload_date": "20260716"}, streams)[0]["id"] == 254
    assert rank_matches({"title": "29.7.2026 - same"}, streams) == []
    assert boost(0, 100, {"status": "timing_unconfirmed", "points": []}) == 0


def test_download_is_audio_only_and_resolves_ffmpeg_path(settings, monkeypatch):
    executable = settings.root / "tools" / "ffmpeg.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"not executed")
    monkeypatch.setattr("vaarattu_shorts.youtube.shutil.which", lambda _: str(executable))
    captured = []
    folder = settings.work / "audio"

    def fake_tool(args, settings, folder, name, check, **kwargs):
        captured.extend(args)
        output = folder / "source.opus"
        output.write_bytes(b"not media")
        (folder / "download.txt").write_text(str(output), encoding="utf-8")

    monkeypatch.setattr("vaarattu_shorts.youtube.run_tool", fake_tool)
    monkeypatch.setattr("vaarattu_shorts.youtube.probe", lambda *_: {"streams": [{"codec_type": "audio"}]})
    assert acquire(settings, "abc_def-ghI", folder, lambda: None).name == "source.opus"
    assert captured[captured.index("--ffmpeg-location") + 1] == str(executable.resolve())
    assert captured[captured.index("-f") + 1] == "bestaudio"
    assert "--no-playlist" in captured and "--download-sections" not in captured
