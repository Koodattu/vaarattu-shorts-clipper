import pytest

from vaarattu_shorts.render import captions
from vaarattu_shorts.transcribe import merge_chunks


def chunk(offset, start, stop, words):
    return (
        {
            "offset_us": round(offset * 1e6),
            "core_start_us": round(start * 1e6),
            "core_end_us": round(stop * 1e6),
        },
        {"words": [{"start": a - offset, "end": b - offset, "text": text} for a, b, text in words]},
    )


def test_shared_phrase_repairs_220ms_seam_without_retiming_or_losing_fillers():
    left = chunk(
        0,
        0,
        10,
        [
            (5.3, 5.5, "asiasta"),
            (5.5, 6.34, "tässä,"),
            (6.42, 6.48, "kun"),
            (6.48, 7.44, "nimi"),
            (7.44, 7.56, "ihan"),
            (7.56, 8.08, "katso"),
            (8.12, 8.22, "kun"),
            (8.22, 10.54, "pelaaja"),
            (10.54, 10.98, "pelas"),
        ],
    )
    right_words = [
        (5.3, 5.5, "asiasta"),
        (5.5, 6.32, "tässä"),
        (6.42, 6.5, "kun"),
        (6.5, 7.42, "nimi"),
        (7.42, 7.44, "niin"),
        (7.44, 7.56, "ihan"),
        (7.56, 8.06, "katsoi"),
        (8.06, 8.2, "kun"),
        (8.2, 9.02, "pelaaja"),
        (9.02, 9.2, "tuota"),
        (9.2, 9.56, "noin"),
        (10.32, 10.98, "pelasi"),
        (10.98, 11.08, "sitä"),
        (11.08, 11.6, "peliä"),
        (11.6, 11.8, "niin"),
        (11.9, 12.1, "niin"),
    ]
    right = chunk(5, 10, 20, right_words)
    result = merge_chunks([left, right], 20000000)
    assert [w.text for w in result] == ["asiasta", "tässä,", "kun"] + [w[2] for w in right_words[3:]]
    original = {
        (round(w["start"] * 1e6) + c["offset_us"], round(w["end"] * 1e6) + c["offset_us"], w["text"])
        for c, r in (left, right)
        for w in r["words"]
    }
    assert all((w.start_us, w.end_us, w.text) in original for w in result)
    assert all(a.end_us <= b.start_us for a, b in zip(result, result[1:]))
    assert len({w.id for w in result}) == len(result)


def test_conflicting_seam_without_shared_phrase_preserves_owned_speech_for_review():
    issues = []
    words = merge_chunks(
        [chunk(0, 0, 10, [(9, 10.5, "vasen")]), chunk(5, 10, 20, [(10.2, 11, "oikea")])],
        20000000,
        issues,
    )
    assert [w.text for w in words] == ["vasen", "oikea"]
    assert issues[0] == {
        "kind": "chunk_seam_conflict",
        "chunk_indices": [0, 1],
        "start_us": 5000000,
        "end_us": 15000000,
    }


def test_matching_phrase_at_different_time_is_not_an_anchor():
    left = chunk(0, 0, 10, [(5, 5.2, "a"), (5.2, 5.4, "b"), (5.4, 5.6, "c"), (9, 10.5, "vasen")])
    right = chunk(5, 10, 20, [(7, 7.2, "a"), (7.2, 7.4, "b"), (7.4, 7.6, "c"), (10.2, 11, "oikea")])
    issues = []
    words = merge_chunks([left, right], 20000000, issues)
    assert [w.text for w in words] == ["a", "b", "c", "vasen", "oikea"]
    assert issues[0]["kind"] == "chunk_seam_conflict"


def test_overlap_inside_one_chunk_is_not_hidden_by_seam_repair():
    issues = []
    words = merge_chunks([chunk(0, 0, 10, [(1, 2, "sana"), (1.5, 2.5, "toinen")])], 10000000, issues)
    assert len(words) == 2
    assert issues[0]["overlap_us"] == 500000


def test_small_decoder_segment_overlap_preserves_words_and_times_and_is_audited(tmp_path):
    source = chunk(1195, 1200, 2400, [(1763.79, 1764.07, "oma"), (1763.84, 1764.84, "näkemys")])
    source[1]["segments"] = [
        {"start": 527.15, "end": 569.07},
        {"start": 568.84, "end": 618.96},
    ]
    issues = []
    result = merge_chunks([source], 2400000000, issues)
    assert [(w.text, w.start_us, w.end_us) for w in result] == [
        ("oma", 1763790000, 1764070000),
        ("näkemys", 1763840000, 1764840000),
    ]
    assert issues == [
        {
            "kind": "word_overlap",
            "chunk_indices": [0],
            "word_ids": [w.id for w in result],
            "start_us": 1763790000,
            "end_us": 1764840000,
            "overlap_us": 230000,
        }
    ]
    flags = captions(tmp_path, result, 1763500000, 1765000000)
    assert any("timestamps overlap" in flag for flag in flags)
    assert "näkemys" in (tmp_path / "captions.srt").read_text("utf-8")
    assert not any(
        "timestamps overlap" in flag for flag in captions(tmp_path, result, 1764070000, 1765000000)
    )


@pytest.mark.parametrize(
    "start,end,segments",
    [
        (1.77, 3, []),  # Small overlap without segment-boundary evidence.
        (1.77, 3, [{"start": 0, "end": 2.1}, {"start": 1.77, "end": 4}]),
        (1.69, 3, [{"start": 0, "end": 2}, {"start": 1.69, "end": 4}]),  # Over 300 ms.
        (1.77, 1.9, [{"start": 0, "end": 2}, {"start": 1.77, "end": 4}]),  # Nested word.
    ],
)
def test_unexplained_large_or_nested_overlap_is_recorded_without_retiming(start, end, segments):
    source = chunk(0, 0, 10, [(1, 2, "oma"), (start, end, "näkemys")])
    source[1]["segments"] = segments
    issues = []
    words = merge_chunks([source], 10000000, issues)
    assert [(w.start_us, w.end_us) for w in words] == [
        (1000000, 2000000),
        (round(start * 1e6), round(end * 1e6)),
    ]
    assert issues[0]["kind"] == "word_overlap"
    assert issues[0]["end_us"] == round(max(2, end) * 1e6)


def test_nested_overlaps_track_longest_active_word_and_keep_repeated_words():
    issues = []
    words = merge_chunks(
        [
            chunk(
                0,
                0,
                20,
                [(1, 10, "pitkä"), (2, 3, "niin"), (2.1, 3.1, "niin"), (5, 6, "sana"), (12, 13, "loppu")],
            )
        ],
        20000000,
        issues,
    )
    assert [w.text for w in words] == ["pitkä", "niin", "niin", "sana", "loppu"]
    assert len(issues) == 3
    assert all(issue["start_us"] == 1000000 and issue["end_us"] == 10000000 for issue in issues)


def test_same_word_from_overlapping_chunks_is_still_deduplicated():
    issues = []
    words = merge_chunks(
        [chunk(0, 0, 10, [(9.7, 10.1, "sana")]), chunk(5, 10, 20, [(9.8, 10.2, "sana")])],
        20000000,
        issues,
    )
    assert [w.text for w in words] == ["sana"]
    assert not issues
