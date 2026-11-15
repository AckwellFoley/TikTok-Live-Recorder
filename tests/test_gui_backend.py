import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gui_backend
from core.tiktok_recorder import TikTokRecorder
from gui_backend import RecorderJob, parse_target, safe_message
from utils.custom_exceptions import UserLiveError
from utils.enums import Mode
from utils.recorder_config import RecorderConfig


@pytest.mark.parametrize("target", ["example_creator", "@example_creator", " https://www.tiktok.com/@example_creator/live?lang=en "])
def test_target_normalizes_username_and_live_url(target):
    assert parse_target(target)[0] == "example_creator"


@pytest.mark.parametrize("target", ["", "one,two", "https://evil.example/@user/live", "https://www.tiktok.com@evil.example/@user/live", "https://www.tiktok.com/@user", "user & command"])
def test_invalid_targets_rejected(target):
    with pytest.raises(ValueError):
        parse_target(target)


@pytest.fixture
def ffmpeg_available(monkeypatch):
    monkeypatch.setattr(gui_backend.shutil, "which", lambda name: "C:/ffmpeg/bin/ffmpeg.exe")


def test_job_stops_worker_and_reports_terminal_state(tmp_path, monkeypatch, ffmpeg_available):
    recording = threading.Event()
    received = []
    class Recorder:
        def __init__(self, config):
            self.config = config
        def run(self):
            self.config.event_callback({"type": "state", "state": "recording", "message": "Recording"})
            recording.set()
            self.config.stop_event.wait(3)

    monkeypatch.setattr(gui_backend, "TikTokRecorder", Recorder)
    job = RecorderJob(tmp_path, received.append)
    job.start("@example_creator", tmp_path / "videos")
    assert recording.wait(1)
    assert job.is_running
    with pytest.raises(RuntimeError):
        job.start("other", tmp_path / "videos")
    job.stop()
    job._thread.join(1)
    assert not job.is_running
    assert received[-1]["state"] == "finished"
    assert any(event.get("state") == "stopping" for event in received)


def test_stop_does_not_interrupt_finalization(tmp_path, monkeypatch, ffmpeg_available):
    saving, release = threading.Event(), threading.Event()
    received = []
    class Recorder:
        def __init__(self, config):
            self.config = config
        def run(self):
            self.config.event_callback({"type": "state", "state": "finalizing", "message": "Saving"})
            saving.set()
            release.wait(3)

    monkeypatch.setattr(gui_backend, "TikTokRecorder", Recorder)
    job = RecorderJob(tmp_path, received.append)
    job.start("example_creator", tmp_path / "videos")
    assert saving.wait(1)
    job.stop()
    assert job.is_running
    assert job._state == "finalizing"
    assert received[-1]["type"] == "log"
    release.set()
    job._thread.join(1)
    assert received[-1]["state"] == "finished"


@pytest.mark.parametrize("kwargs", [{"duration": -1}, {"interval": 0}, {"mode": "bad"}, {"duration": "bad"}])
def test_options_validated_before_start(tmp_path, ffmpeg_available, kwargs):
    job = RecorderJob(tmp_path, lambda event: None)
    with pytest.raises(ValueError):
        job.start("example_creator", tmp_path / "videos", **kwargs)
    assert not job.is_running


def test_stop_flushes_stream_and_converts_afterwards(tmp_path, monkeypatch):
    stop = threading.Event()
    received, converted = [], []
    recorder = TikTokRecorder(RecorderConfig(mode=Mode.MANUAL, user="creator",
        stop_event=stop, event_callback=received.append))
    class API:
        def is_room_alive(self, room_id):
            return True
        def download_live_stream(self, url):
            yield b"a" * 5000
            stop.set()
            yield b"b" * 5000
            pytest.fail("Stop should end the stream")
    recorder.tiktok = API()
    output = tmp_path / "video_flv.mp4"
    def convert(path, *args):
        converted.append(Path(path).read_bytes())
    monkeypatch.setattr("core.tiktok_recorder.VideoManagement.convert_flv_to_mp4", convert)
    recorder._record_live_urls("creator", "123", ["stream"], str(output))
    assert converted == [b"a" * 5000 + b"b" * 5000]
    assert [e["state"] for e in received] == ["recording", "finalizing"]


def test_stop_interrupts_automatic_wait_immediately():
    stop, waiting = threading.Event(), threading.Event()
    def callback(event):
        if event.get("state") == "waiting":
            waiting.set()
    recorder = TikTokRecorder(RecorderConfig(mode=Mode.AUTOMATIC, user="creator",
        automatic_interval=5, stop_event=stop, event_callback=callback))
    class API:
        def get_room_id_from_user(self, user):
            return "123"
    recorder.tiktok = API()
    def offline():
        raise UserLiveError("Offline")
    recorder.manual_mode = offline
    thread = threading.Thread(target=recorder.automatic_mode, daemon=True)
    thread.start()
    assert waiting.wait(1)
    stop.set()
    thread.join(1)
    assert not thread.is_alive()


def test_display_messages_remove_signed_url_parameters_and_cookie_values():
    message = safe_message("Failed https://cdn.example/stream.flv?sessionid=secret&token=other cookie=private")
    assert "secret" not in message
    assert "other" not in message
    assert "private" not in message
    assert "https://cdn.example/stream.flv" in message


def test_recording_uses_local_imported_cookies_without_exposing_them(tmp_path, monkeypatch, ffmpeg_available):
    from utils.login_store import save_login_settings
    save_login_settings(tmp_path, {"sessionid_ss": "FAKE_BACKEND_SESSION", "tt-target-idc": "test"})
    used, received = [], []
    class Recorder:
        def __init__(self, config):
            used.append(config.cookies)
        def run(self):
            pass
    monkeypatch.setattr(gui_backend, "TikTokRecorder", Recorder)
    job = RecorderJob(tmp_path, received.append)
    job.start("example_host", tmp_path / "videos")
    job._thread.join(1)
    assert used == [{"sessionid_ss": "FAKE_BACKEND_SESSION", "tt-target-idc": "test"}]
    assert "FAKE_BACKEND_SESSION" not in str(received)
