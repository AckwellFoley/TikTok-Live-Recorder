import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import app_paths
import video_engine
from tests.test_gui import gui_root


def test_frozen_workers_use_console_companion_and_never_python_build_path(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "TikTok Live Recorder.exe"))
    assert app_paths.worker_command() == [str(tmp_path / "recorder-worker.exe"), "--worker"]


def test_portable_data_follows_application_and_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.delenv("TIKTOK_RECORDER_DATA_DIR", raising=False)
    monkeypatch.setattr(app_paths, "application_dir", lambda: tmp_path)
    assert app_paths.data_dir() == tmp_path / "data"
    monkeypatch.setenv("TIKTOK_RECORDER_DATA_DIR", str(tmp_path / "isolated"))
    assert app_paths.data_dir() == tmp_path / "isolated"


def test_archive_extraction_ignores_traversal_and_writes_only_expected_names(tmp_path):
    archive = tmp_path / "engine.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("build/bin/ffmpeg.exe", b"MZffmpeg")
        bundle.writestr("build/bin/ffprobe.exe", b"MZprobe")
        bundle.writestr("../../outside.exe", b"evil")
        bundle.writestr("build/extra.exe", b"unused")
    video_engine.extract_engine(archive, tmp_path / "tools")
    assert sorted(p.name for p in (tmp_path / "tools").iterdir()) == ["ffmpeg.exe", "ffprobe.exe"]
    assert not (tmp_path / "outside.exe").exists()


def test_unverified_download_is_rejected_before_extraction(monkeypatch, tmp_path):
    monkeypatch.setattr(video_engine, "find_ffmpeg", lambda root: None)
    class Response(io.BytesIO):
        url = "https://www.gyan.dev/test.zip"
        headers = {"Content-Length": "5"}
    monkeypatch.setattr(video_engine.urllib.request, "urlopen", lambda *a, **k: Response(b"wrong"))
    with pytest.raises(ValueError, match="checksum"):
        video_engine.ensure_ffmpeg(tmp_path)
    assert not list(tmp_path.rglob("*.exe"))
    assert not list(tmp_path.rglob("*.download"))
    assert not list(tmp_path.rglob("*.lock"))


def test_only_complete_cached_engine_is_selected(monkeypatch, tmp_path):
    monkeypatch.delenv("TIKTOK_RECORDER_FFMPEG", raising=False)
    monkeypatch.setattr(video_engine.shutil, "which", lambda name: None)
    cache = video_engine.cached_engine(tmp_path)
    cache.mkdir(parents=True)
    (cache / "ffmpeg.exe").touch()
    assert video_engine.find_ffmpeg(tmp_path) is None
    (cache / "ffprobe.exe").touch()
    (cache / "installed.json").write_text("{}")
    assert video_engine.find_ffmpeg(tmp_path) == str(cache / "ffmpeg.exe")


def test_new_install_has_no_creator_or_monitoring_history(tmp_path, monkeypatch, gui_root):
    import tkinter as tk
    from gui import RecorderApp
    from tests.test_gui import FakeManager, FakeMonitor
    import gui
    monkeypatch.setattr(gui, "MultiRecorder", FakeManager)
    monkeypatch.setattr(gui, "CreatorMonitor", FakeMonitor)
    # Keep one Tcl interpreter per GUI test process; each app gets a fresh window
    # and data directory. Native CTk startup is checked in separate processes.
    root = tk.Toplevel(gui_root)
    root.withdraw()
    try:
        app = RecorderApp(root, tmp_path)
        assert app.target.get() == ""
        assert not app.monitoring.get()
        assert app.manager.max_concurrent == 1
        assert app.history.creators == []
    finally:
        root.destroy()
