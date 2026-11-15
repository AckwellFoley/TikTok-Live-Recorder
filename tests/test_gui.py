import json
import sys
import time
from pathlib import Path

import pytest
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import gui


class FakeManager:
    def __init__(self, root_dir, on_event, max_concurrent=4):
        self.max_concurrent = max_concurrent
        self.jobs = {}
        self.options = []
        self.stopped = []

    @property
    def active_count(self):
        return sum(job["state"] not in ("finished", "error", "queued") for job in self.jobs.values())

    @property
    def is_running(self):
        return any(job["state"] not in ("finished", "error") for job in self.jobs.values())

    def start(self, **options):
        creator = options["target"].lstrip("@").lower()
        if creator in self.jobs and self.jobs[creator]["state"] not in ("finished", "error"):
            raise RuntimeError("Already recording this creator.")
        self.options.append(options)
        self.jobs[creator] = dict(creator=creator, state="connecting", started_at=None, session_id=creator)
        return creator

    def stop(self, creator):
        self.stopped.append(creator)
        if creator in self.jobs:
            self.jobs[creator]["state"] = "stopping"

    def stop_all(self):
        for creator in self.jobs:
            self.stop(creator)

    def snapshot(self):
        return list(self.jobs.values())

    def poll(self):
        pass


class FakeMonitor:
    def __init__(self, root_dir, on_event, interval=60):
        self.creators = []
        self.running = False

    def start(self, creators):
        self.creators = list(creators)
        self.running = True

    def set_creators(self, creators):
        self.creators = list(creators)

    def refresh(self):
        pass

    def stop(self):
        self.running = False


@pytest.fixture(scope="session")
def gui_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(monkeypatch, tmp_path, gui_root):
    monkeypatch.setattr(gui, "MultiRecorder", FakeManager)
    monkeypatch.setattr(gui, "CreatorMonitor", FakeMonitor)
    root = tk.Toplevel(gui_root)
    root.withdraw()
    result = gui.RecorderApp(root, tmp_path)
    yield result
    result.monitor.stop()
    try:
        root.destroy()
    except tk.TclError:
        pass


def test_new_session_controls_remain_available_during_other_recordings(app):
    app.target.set("@alice")
    app.duration.set("30")
    app.start()
    app.manager.jobs["alice"].update(state="recording", started_at=time.monotonic())
    app.target.set("bob")
    app.start()
    assert [options["target"] for options in app.manager.options] == ["alice", "bob"]
    assert app.manager.options[0]["duration"] == 30
    assert app.manager.options[0]["panel"] is True
    assert not app.start_button.instate(["disabled"])
    assert not app.target_entry.instate(["disabled"])
    assert app.login_button.instate(["disabled"])
    app.stop()
    assert set(app.manager.stopped) == {"alice", "bob"}


def test_record_selected_starts_every_selected_creator_and_stop_is_individual(app):
    for creator in ("alice", "bob"):
        app.history.add(creator)
    app._refresh_creators()
    app.creators.selection_set(("alice", "bob"))
    app._record_selected()
    assert [item["target"] for item in app.manager.options] == ["alice", "bob"]
    app.creators.selection_set("alice")
    app._stop_selected()
    assert app.manager.stopped == ["alice"]
    assert app.manager.jobs["bob"]["state"] == "connecting"


def test_automatic_watch_waits_without_a_slot_and_starts_only_when_live(app):
    app.mode.set("Monitor automatically")
    app.target.set("alice")
    app.start()
    assert app.manager.active_count == 0
    app._event(dict(type="presence", creator="alice", state="offline", live=False, checked_at=time.time()))
    app._start_due_automatic()
    assert app.manager.options == []
    app._event(dict(type="presence", creator="alice", state="live", live=True, checked_at=time.time()))
    app._start_due_automatic()
    assert app.manager.options[0]["target"] == "alice"
    assert app.manager.options[0]["mode"] == "manual"
    app._start_due_automatic()
    assert len(app.manager.options) == 1
    app.stop()
    assert app.auto_record == {}


def test_creator_presence_and_panels_do_not_overwrite_each_other(app):
    app.monitoring.set(True)
    for creator in ("alice", "bob"):
        app.history.add(creator)
    app.selected_creator.set("alice")
    timeline = {"host_id": "1", "profiles": {"1": {"name": "Host"}},
                "snapshots": [{"ids": ["1", "2"], "status": ""}]}
    app._event(dict(type="panel", creator="alice", timeline=timeline, assets=str(app.root_dir)))
    assert app.guest_text.get() == "1"
    app._event(dict(type="presence", creator="bob", state="live", live=True, checked_at=time.time()))
    app._event(dict(type="presence", creator="alice", state="error", live=None, checked_at=time.time()))
    app._refresh_creators()
    assert app.creators.item("bob", "values")[1] == "LIVE"
    assert app.creators.item("alice", "values")[1] == "Check failed"
    assert app.guest_text.get() == "1"
    app.selected_creator.set("bob")
    app._choose_creator()
    assert app.guest_text.get() == "—"
    assert app.panel_data is None


def test_invalid_time_limit_shows_error_before_start(app, monkeypatch):
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kwargs: errors.append(args))
    app.duration.set("not a number")
    app.start()
    assert errors
    assert not app.manager.is_running


def test_recordings_list_excludes_unfinished_raw_capture(app):
    (app.root_dir / "recordings").mkdir()
    for name in ("video_flv.mp4", "video.mp4", "video_panel.mp4"):
        (app.root_dir / "recordings").mkdir(exist_ok=True)
        (app.root_dir / "recordings" / name).write_bytes(b"video")
    app._scan_recordings()
    assert {p.name for p in app.recent_paths.values()} == {"video.mp4", "video_panel.mp4"}


def test_login_error_gives_user_a_clear_import_action(app):
    app._event(dict(type="state", state="error", message="TikTok requires login; refresh cookies."))
    assert app.status_text.get() == "Login needed"
    assert "TikTok login" in app.notice.get()


def test_gui_import_keeps_credentials_out_of_settings_and_log(app):
    from utils.login_store import load_login_settings
    path = app.root_dir / "export.json"
    path.write_text(json.dumps({"format": "tiktok-recorder-cookies", "version": 1,
        "site": "https://www.tiktok.com", "cookies": [{"name": "sessionid_ss",
        "value": "FAKE_GUI_TEST_SESSION", "domain": ".tiktok.com", "path": "/"}]}), encoding="utf-8")
    app._import_login(path)
    app._save_settings()
    assert load_login_settings(app.root_dir)["sessionid_ss"] == "FAKE_GUI_TEST_SESSION"
    assert "not yet verified" in app.login_text.get()
    assert "FAKE_GUI_TEST_SESSION" not in app.settings_path.read_text(encoding="utf-8")
    assert "FAKE_GUI_TEST_SESSION" not in app.log.get("1.0", "end")


def test_login_import_waits_for_all_recording_sessions(app):
    app.manager.start(target="alice")
    with pytest.raises(RuntimeError, match="Finish the current recording"):
        app._import_login(app.root_dir / "does-not-exist.json")


def test_removed_creator_results_are_ignored(app):
    app.history.add("alice")
    app.history.remove("alice")
    app._event(dict(type="presence", creator="alice", state="live", live=True, checked_at=time.time()))
    assert "alice" not in app.presence


def test_elapsed_time_freezes_after_session_finishes(app, monkeypatch):
    monkeypatch.setattr(gui.time, "monotonic", lambda: 999)
    assert app._elapsed(10, 75) == "00:01:05"


def test_generic_failure_updates_idle_badge(app):
    app._event(dict(type="state", state="error", message="This creator has ended their LIVE."))
    assert app.status_text.get() == "Needs attention"


def test_rearmed_automatic_watch_overrides_old_cancelled_label(app):
    app.history.add("alice")
    app.manager.jobs["alice"] = dict(creator="alice", state="finished", cancelled=True, started_at=None)
    app.auto_record["alice"] = {}
    app._refresh_creators()
    assert app.creators.item("alice", "values")[2] == "Watching LIVE"
