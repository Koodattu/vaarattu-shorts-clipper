"""Reject real render timing faults while accepting measured mux clock rounding."""
import copy

import pytest

from vaarattu_shorts.highlights import validate_render


def probe():
    # Metadata from the 30,270-frame draft rejected by the old exact-string check.
    return {"streams": [
        {"codec_type": "video", "width": 1280, "height": 720, "pix_fmt": "yuv420p",
         "r_frame_rate": "30/1", "avg_frame_rate": "181620000/6053999", "nb_frames": "30270",
         "duration": "1008.999833", "start_time": "0.000000"},
        {"codec_type": "audio", "duration": "1009.000000", "start_time": "0.000000"}],
        "format": {"duration": "1009.000000"}}


def test_actual_draft_timestamp_rounding_passes_without_modifying_metadata():
    info = probe()
    before = copy.deepcopy(info)
    validate_render(info, 1280, 720, 30270, 110)
    assert info == before


@pytest.mark.parametrize("rate", ["30/1", "30", "60/2"])
def test_equivalent_rational_frame_rates_pass(rate):
    info = probe()
    info["streams"][0].update(avg_frame_rate=rate, r_frame_rate=rate, duration="1009")
    validate_render(info, 1280, 720, 30270, 110)


@pytest.mark.parametrize("field,value", [
    ("avg_frame_rate", "30000/1001"), ("avg_frame_rate", "25/1"), ("avg_frame_rate", "60/1"),
    ("avg_frame_rate", "0/0"), ("avg_frame_rate", "N/A"), ("avg_frame_rate", "nan"),
    ("r_frame_rate", "60/1"), ("nb_frames", "30269"), ("nb_frames", "30271"), ("nb_frames", "N/A"),
])
def test_wrong_rates_missing_or_extra_frames_are_rejected(field, value):
    info = probe()
    info["streams"][0][field] = value
    with pytest.raises(ValueError, match="frame count and 30 fps"):
        validate_render(info, 1280, 720, 30270, 110)


def test_even_many_cuts_cannot_hide_one_millisecond_of_drift():
    info = probe()
    info["streams"][0]["avg_frame_rate"] = "30270000/1009002"  # Two milliseconds too long.
    with pytest.raises(ValueError, match="frame count and 30 fps"):
        validate_render(info, 1280, 720, 30270, 100000)


@pytest.mark.parametrize("fault", ["wrong_size", "no_audio", "audio_offset", "audio_duration", "overall_duration", "missing_count"])
def test_picture_audio_and_frame_count_checks_remain_required(fault):
    info = probe()
    if fault == "wrong_size":
        info["streams"][0]["height"] = 1080
    elif fault == "no_audio":
        info["streams"].pop()
    elif fault == "audio_offset":
        info["streams"][1]["start_time"] = "0.2"
    elif fault == "audio_duration":
        info["streams"][1]["duration"] = "1008"
    elif fault == "overall_duration":
        info["format"]["duration"] = "1010"
    else:
        info["streams"][0].pop("nb_frames")
    with pytest.raises(ValueError):
        validate_render(info, 1280, 720, 30270, 110)
