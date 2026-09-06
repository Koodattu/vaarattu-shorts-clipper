import pytest

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


def test_conflicting_seam_without_shared_phrase_still_fails():
    with pytest.raises(ValueError, match="no reliable shared phrase"):
        merge_chunks(
            [chunk(0, 0, 10, [(9, 10.5, "vasen")]), chunk(5, 10, 20, [(10.2, 11, "oikea")])], 20000000
        )


def test_matching_phrase_at_different_time_is_not_an_anchor():
    left = chunk(0, 0, 10, [(5, 5.2, "a"), (5.2, 5.4, "b"), (5.4, 5.6, "c"), (9, 10.5, "vasen")])
    right = chunk(5, 10, 20, [(7, 7.2, "a"), (7.2, 7.4, "b"), (7.4, 7.6, "c"), (10.2, 11, "oikea")])
    with pytest.raises(ValueError, match="no reliable shared phrase"):
        merge_chunks([left, right], 20000000)


def test_overlap_inside_one_chunk_is_not_hidden_by_seam_repair():
    with pytest.raises(ValueError, match="Overlapping speech"):
        merge_chunks([chunk(0, 0, 10, [(1, 2, "sana"), (1.5, 2.5, "toinen")])], 10000000)
