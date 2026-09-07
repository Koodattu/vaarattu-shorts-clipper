from vaarattu_shorts.contracts import Word
from vaarattu_shorts.render import captions


def test_active_word_only_and_neutral_pause(tmp_path):
    words = [
        Word(id="a", text="Hyvää", start_us=10000000, end_us=10500000),
        Word(id="b", text="päivää", start_us=10700000, end_us=11200000),
    ]
    captions(tmp_path, words, 10000000, 12000000)
    ass = (tmp_path / "captions.ass").read_text("utf-8")
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 3
    yellow, white = r"{\1c&H0046C7FF&}", r"{\1c&H00FFFFFF&}"
    assert f"{yellow}Hyvää{white} päivää" in events[0]
    assert "0:00:00.00,0:00:00.50" in events[0]
    assert yellow not in events[1] and "0:00:00.50,0:00:00.70" in events[1]
    assert f"Hyvää {yellow}päivää{white}" in events[2]
    assert all(r"{\fs86}" in event for event in events)
    assert yellow not in (tmp_path / "captions.srt").read_text("utf-8")


def test_overlaps_have_one_phrase_and_one_highlight_at_a_time(tmp_path):
    words = [
        Word(id="a", text="Ääni", start_us=0, end_us=1000000),
        Word(id="b", text="jatkuu", start_us=500000, end_us=1500000),
        Word(id="c", text="hyvää päivää", start_us=1500000, end_us=2000000),
    ]
    flags = captions(tmp_path, words, 0, 2000000)
    events = [
        line
        for line in (tmp_path / "captions.ass").read_text("utf-8").splitlines()
        if line.startswith("Dialogue:")
    ]
    assert any("overlap" in flag for flag in flags)
    assert all(event.count(r"{\1c&H0046C7FF&}") == 1 for event in events)
    assert all(a.split(",")[2] == b.split(",")[1] for a, b in zip(events, events[1:]))
    assert "hyvää päivää" in events[-1]


def test_caption_lines_and_long_compounds(tmp_path):
    words = [
        Word(id=str(i), text=text, start_us=i * 500000, end_us=(i + 1) * 500000)
        for i, text in enumerate(
            ["Tällainen", "päivä", "on", "mukava", "erittäinpitkäyhdyssanatestiesimerkki"]
        )
    ]
    flags = captions(tmp_path, words, 0, 2500000)
    ass = (tmp_path / "captions.ass").read_text("utf-8")
    assert r"\N" in ass
    assert any("shorter line" in flag for flag in flags)
    assert "erittäinpitkäyhdyssanatestiesimerkki" in ass
