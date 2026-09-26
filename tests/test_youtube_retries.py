import json
from types import SimpleNamespace

import pytest

from vaarattu_shorts import highlight_sources, youtube
from vaarattu_shorts.processes import Interrupted, OwnedProcess, ToolError, ToolExitError


def command():
    return ["python", "-m", "yt_dlp", "-f", "bestaudio", "-o", "source.%(ext)s",
            "https://www.youtube.com/watch?v=abc_def-ghI"]


def fail(folder, message="ERROR: unable to download video data: HTTP Error 403: Forbidden"):
    (folder / "download.log").write_text(message, encoding="utf-8")
    raise ToolExitError("An external tool failed")


@pytest.mark.parametrize("runtime", ["node", "deno", None])
def test_runtime_is_enabled_without_suppressing_diagnostics(monkeypatch, runtime):
    monkeypatch.setattr(youtube.shutil, "which", lambda name: f"/tools/{name}" if name == runtime else None)
    args = youtube.yt_args()
    assert "--ignore-config" in args and "--no-warnings" not in args
    if runtime == "node":
        assert args[args.index("--js-runtimes") + 1] == "node:/tools/node"
    else:
        assert "--js-runtimes" not in args


def test_refresh_retries_keep_guards_logs_and_use_fresh_report(settings, monkeypatch):
    folder = settings.work
    calls, sleeps = [], []
    (folder / "download.txt").write_text("stale result", encoding="utf-8")
    monkeypatch.setattr(youtube, "wait_for_download_retry", lambda delay, check: sleeps.append(delay))
    def run(args, config, where, name, check, **kw):
        assert not (where / "download.txt").exists()
        assert kw == {"timeout": 7200, "byte_limit": 100, "watch": folder}
        calls.append(list(args))
        if len(calls) == 1:
            fail(where)
        (where / "download.txt").write_text("fresh result", encoding="utf-8")
    original = command()
    youtube.download(original, settings, folder, lambda: None, audio_only=True, runner=run,
                     timeout=7200, byte_limit=100, watch=folder)
    assert calls == [original, original] and sleeps == [30]
    audit = json.loads((folder / "download-retry.json").read_text())
    assert audit["state"] == "completed" and audit["successful_attempt"] == 2
    assert (folder / audit["attempts"][0]["log"]).is_file()


@pytest.mark.parametrize("audio_only", [True, False])
def test_retries_are_bounded_and_audio_fallback_cannot_mix_partial_files(settings, monkeypatch, audio_only):
    calls, sleeps = [], []
    monkeypatch.setattr(youtube, "wait_for_download_retry", lambda delay, check: sleeps.append(delay))
    def run(args, config, folder, *a, **kw):
        calls.append(list(args))
        fail(folder)
    original = command()
    with pytest.raises(ToolError, match="HTTP 403. Automatic retries exhausted"):
        youtube.download(original, settings, settings.work, lambda: None, audio_only=audio_only, runner=run)
    assert sleeps == [30, 120, 300] and len(calls) == 4
    assert original == command() and calls[:3] == [original] * 3
    assert calls[-1][calls[-1].index("-f") + 1] == ("bestaudio[ext=m4a]" if audio_only else "bestaudio")
    assert calls[-1][calls[-1].index("-o") + 1] == ("source-fallback.%(ext)s" if audio_only else "source.%(ext)s")
    audit = json.loads((settings.work / "download-retry.json").read_text())
    assert audit["state"] == "failed" and audit["retry_at"] is None
    assert len({a["log"] for a in audit["attempts"]}) == 4
    assert all((settings.work / a["log"]).is_file() for a in audit["attempts"])


@pytest.mark.parametrize("detail,retry,status", [
    ("ERROR: HTTP Error 403: Forbidden", True, 403),
    ("ERROR: HTTP Error 408: Request timeout", True, 408),
    ("ERROR: HTTP Error 429: Too many requests", True, 429),
    ("ERROR: HTTP Error 500: Server error", True, 500),
    ("ERROR: HTTP Error 502: Bad gateway", True, 502),
    ("ERROR: HTTP Error 503: Unavailable", True, 503),
    ("ERROR: HTTP Error 504: Gateway timeout", True, 504),
    ("ERROR: HTTP Error 404: Not found", False, 404),
    ("[https @ a] HTTP error 403 Forbidden\nERROR: ffmpeg exited with code 1", True, 403),
    ("ERROR: connection reset by peer", True, None),
    ("ERROR: request timed out", True, None),
    ("ERROR: Private video; HTTP Error 403", False, None),
    ("WARNING: HTTP Error 403\nERROR: No space left on device", False, None),
    ("ERROR: Requested format is not available", False, None),
])
def test_safe_failure_classification(tmp_path, detail, retry, status):
    path = tmp_path / "download.log"
    path.write_text(detail + " https://example.test/?secret=do-not-expose", encoding="utf-8")
    reason, actual_retry, actual_status = youtube.download_failure(path)
    assert (actual_retry, actual_status) == (retry, status)
    assert "secret" not in reason and "https" not in reason


@pytest.mark.parametrize("message", ["ERROR: Private video", "ERROR: No space left on device",
                                     "ERROR: Requested format is not available"])
def test_terminal_errors_do_not_retry(settings, monkeypatch, message):
    monkeypatch.setattr(youtube, "wait_for_download_retry", lambda *a: pytest.fail("Must not wait"))
    calls = []
    def run(args, config, folder, *a, **kw):
        calls.append(args)
        fail(folder, message)
    with pytest.raises(ToolError):
        youtube.download(command(), settings, settings.work, lambda: None, runner=run)
    assert len(calls) == 1


@pytest.mark.parametrize("error", [Interrupted("pause"), Interrupted("cancel"),
    ToolError("This stage exceeded its time limit"), ToolError("Download exceeded the size limit")])
def test_interruption_and_resource_limits_never_retry_even_with_403_log(settings, monkeypatch, error):
    monkeypatch.setattr(youtube, "wait_for_download_retry", lambda *a: pytest.fail("Must not wait"))
    def run(*a, **kw):
        (settings.work / "download.log").write_text("ERROR: HTTP Error 403")
        raise error
    with pytest.raises(type(error)) as caught:
        youtube.download(command(), settings, settings.work, lambda: None, runner=run)
    assert caught.value is error


def test_backoff_checks_cancellation_instead_of_blocking_for_minutes(monkeypatch):
    now = [0.0]
    sleeps = []
    monkeypatch.setattr(youtube.time, "monotonic", lambda: now[0])
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(youtube.time, "sleep", sleep)
    def check():
        if now[0] >= 0.4:
            raise Interrupted("cancel")
    with pytest.raises(Interrupted):
        youtube.wait_for_download_retry(300, check)
    assert max(sleeps) <= 0.2 and now[0] < 1
    youtube.wait_for_download_retry(1, lambda: None)
    assert now[0] >= 1.4


def test_tool_exit_is_distinct_from_resource_limits(tmp_path):
    process = OwnedProcess.__new__(OwnedProcess)
    process.proc = SimpleNamespace(poll=lambda: 1, returncode=1)
    process.log = tmp_path / "log"
    with pytest.raises(ToolExitError):
        process.wait()


@pytest.mark.parametrize("highlights", [False, True])
def test_acquisition_really_recovers_and_probes_downloaded_media(settings, monkeypatch, highlights):
    adapter = highlight_sources if highlights else youtube
    calls = []
    monkeypatch.setattr(youtube, "wait_for_download_retry", lambda *a: None)
    def run(args, config, folder, *a, **kw):
        calls.append(list(args))
        if len(calls) < 4:
            fail(folder)
        output = folder / "source-fallback.m4a"
        output.write_bytes(b"media")
        (folder / "download.txt").write_text(str(output), encoding="utf-8")
    monkeypatch.setattr(adapter, "run_tool", run)
    probed = []
    def probe(config, path, folder, check):
        probed.append(path)
        return {"streams": [{"codec_type": "audio"}]}
    monkeypatch.setattr(youtube, "probe", probe)
    source = {"url": command()[-1]} if highlights else "abc_def-ghI"
    path = adapter.acquire(settings, source, settings.work, lambda: None)
    assert path.name == "source-fallback.m4a" and probed == [path] and len(calls) == 4


def test_successful_resume_clears_old_retry_status(settings):
    (settings.work / "download-retry.json").write_text('{"state":"failed"}')
    youtube.download(command(), settings, settings.work, lambda: None, runner=lambda *a, **kw: None)
    assert json.loads((settings.work / "download-retry.json").read_text())["state"] == "completed"
