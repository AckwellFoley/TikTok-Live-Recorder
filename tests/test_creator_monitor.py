"""Creator history and monitoring with fake probes; never uses saved login."""

import json
import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import creator_monitor as module
from creator_monitor import CreatorHistory, CreatorMonitor


def next_event(events, predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while True:
        item = events.get(timeout=max(0.001, deadline - time.monotonic()))
        if predicate(item):
            return item
        if time.monotonic() >= deadline:
            raise AssertionError("Expected monitor event was not delivered")


def test_imports_recorded_creators_and_gui_target_once_then_respects_removals(tmp_path):
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    for name in ("TK_Example_Creator_2026.11.02_03-35-30.mp4",
                 "TK_example_creator_2026.11.02_03-35-30_panel.mp4",
                 "TK_user.name_2026.11.02_03-35-30_flv.mp4",
                 "TK_with_underscore_2026.11.02_03-35-30.mp4",
                 "TK_bad-name_2026.11.02_03-35-30.mp4", "unrelated.mp4"):
        (recordings / name).touch()
    (tmp_path / "gui-settings.json").write_text(json.dumps({"target": "@SavedCreator"}))
    history = CreatorHistory(tmp_path)
    assert history.seed(recordings, ["https://www.tiktok.com/@Example_Creator/live"]) == [
        "example_creator", "savedcreator", "user.name", "with_underscore",
    ]
    history.remove("@EXAMPLE_CREATOR")
    (recordings / "TK_unrequested_2026.11.03_03-35-30.mp4").touch()
    reopened = CreatorHistory(tmp_path)
    assert reopened.seed(recordings, ["@Example_Creator"]) == ["savedcreator", "user.name", "with_underscore"]
    assert json.loads((tmp_path / "creators.json").read_text())["migrated"] is True


def test_history_add_deduplicates_case_and_rejoin_appends(tmp_path):
    history = CreatorHistory(tmp_path)
    history.seed(tmp_path / "missing")
    assert history.add("@First") == "first"
    assert history.add("https://www.tiktok.com/@FIRST/live") == "first"
    history.add("second")
    history.remove("FIRST")
    history.add("first")
    assert CreatorHistory(tmp_path).creators == ["second", "first"]


@pytest.mark.parametrize("target", ["", "@", "bad name", "https://example.com/@user/live",
                                   "http://www.tiktok.com/@user/live", None, ["user"]])
def test_history_rejects_invalid_targets_without_overwriting_file(tmp_path, target):
    history = CreatorHistory(tmp_path)
    history.add("valid")
    original = history.path.read_bytes()
    with pytest.raises(ValueError):
        history.add(target)
    assert history.path.read_bytes() == original


def test_history_save_failure_preserves_previous_atomic_file(tmp_path, monkeypatch):
    history = CreatorHistory(tmp_path)
    history.add("first")
    original = history.path.read_bytes()
    replace = module.os.replace
    def fail_replace(source, destination):
        assert Path(source).parent == tmp_path
        assert Path(destination) == history.path
        assert json.loads(Path(source).read_text())["creators"] == ["first", "second"]
        raise OSError("simulated replace failure")
    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError):
        history.add("second")
    assert history.path.read_bytes() == original
    assert history.creators == ["first"]
    assert not list(tmp_path.glob(".creators-*.tmp"))
    monkeypatch.setattr(module.os, "replace", replace)
    history.add("second")
    assert CreatorHistory(tmp_path).creators == ["first", "second"]


def test_failed_remove_preserves_memory_and_disk_then_can_retry(tmp_path, monkeypatch):
    history = CreatorHistory(tmp_path)
    history.add("first")
    history.add("second")
    original = history.path.read_bytes()
    replace = module.os.replace
    def fail_replace(*args):
        raise OSError("simulated replace failure")
    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError):
        history.remove("first")
    assert history.creators == ["first", "second"]
    assert history.path.read_bytes() == original
    monkeypatch.setattr(module.os, "replace", replace)
    history.remove("first")
    assert CreatorHistory(tmp_path).creators == ["second"]


def test_failed_migration_preserves_marker_and_can_retry(tmp_path, monkeypatch):
    history = CreatorHistory(tmp_path)
    history.add("existing")
    original = history.path.read_bytes()
    replace = module.os.replace
    def fail_replace(*args):
        raise OSError("simulated replace failure")
    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError):
        history.seed(tmp_path / "missing", ["imported"])
    assert history.creators == ["existing"]
    assert history._migrated is False
    assert history.path.read_bytes() == original
    monkeypatch.setattr(module.os, "replace", replace)
    assert history.seed(tmp_path / "missing", ["imported"]) == ["existing", "imported"]
    assert json.loads(history.path.read_text())["migrated"] is True


def test_polling_distinguishes_live_offline_and_safe_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(CreatorMonitor, "PACE_SECONDS", 0.02)
    events = queue.Queue()
    def probe(creator):
        if creator == "failed":
            raise RuntimeError("Denied cookie=test-secret https://tiktok.com/live?token=secret")
        return creator == "liveuser"
    monitor = CreatorMonitor(tmp_path, events.put, probe=probe)
    monitor.start(["@LiveUser", "offlineuser", "failed"])
    try:
        live = next_event(events, lambda e: e["state"] == "live")
        offline = next_event(events, lambda e: e["state"] == "offline")
        error = next_event(events, lambda e: e["state"] == "error")
        assert (live["creator"], live["live"], live["type"]) == ("liveuser", True, "presence")
        assert offline["live"] is False
        assert error["live"] is None
        assert "secret" not in error["message"]
        assert isinstance(error["checked_at"], float)
    finally:
        monitor.stop()


@pytest.mark.parametrize("action", ["remove", "stop"])
def test_in_flight_removed_or_stopped_checks_cannot_publish_stale_result(tmp_path, action):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    events = queue.Queue()
    def probe(creator):
        entered.set()
        release.wait(2)
        finished.set()
        return True
    monitor = CreatorMonitor(tmp_path, events.put, probe=probe)
    monitor.start(["creator"])
    assert entered.wait(1)
    assert events.get(timeout=1)["state"] == "checking"
    before = time.monotonic()
    if action == "remove":
        monitor.set_creators([])
    else:
        monitor.stop()
    assert time.monotonic() - before < 0.2
    release.set()
    assert finished.wait(1)
    try:
        with pytest.raises(queue.Empty):
            events.get(timeout=0.1)
    finally:
        monitor.stop()


def test_refreshes_are_coalesced_and_checks_stay_sequential_and_paced(tmp_path, monkeypatch):
    monkeypatch.setattr(CreatorMonitor, "PACE_SECONDS", 0.06)
    events = queue.Queue()
    first_entered, release = threading.Event(), threading.Event()
    calls = []
    def probe(creator):
        calls.append((creator, time.monotonic()))
        if len(calls) == 1:
            first_entered.set()
            release.wait(2)
        return True
    monitor = CreatorMonitor(tmp_path, events.put, interval=60, probe=probe)
    monitor.start(["one", "two"])
    assert first_entered.wait(1)
    for _ in range(10):
        monitor.refresh()
    release.set()
    try:
        for _ in range(4):
            next_event(events, lambda e: e["state"] == "live")
        assert [name for name, _ in calls] == ["one", "two", "one", "two"]
        assert all(b[1] - a[1] >= 0.045 for a, b in zip(calls, calls[1:]))
        with pytest.raises(queue.Empty):
            events.get(timeout=0.1)
    finally:
        monitor.stop()


def test_default_cycle_reloads_login_and_closes_owned_sessions_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(CreatorMonitor, "PACE_SECONDS", 0.01)
    events = queue.Queue()
    cycles = []
    def fake_login(root):
        cycles.append(root)
        return {"sessionid_ss": "test-sentinel"}
    class Session:
        closed = False
        def close(self):
            self.closed = True
    instances = []
    class Api:
        def __init__(self, **kwargs):
            assert kwargs["cookies"] == {"sessionid_ss": "test-sentinel"}
            self.http_client, self._http_client_stream = Session(), Session()
            instances.append(self)
        def get_room_id_from_user(self, creator):
            return "123"
        def is_room_alive(self, room):
            return True
    monkeypatch.setattr(module, "load_login_settings", fake_login)
    monkeypatch.setattr(module, "TikTokAPI", Api)
    monitor = CreatorMonitor(tmp_path, events.put)
    monitor.start(["creator"])
    try:
        next_event(events, lambda e: e["state"] == "live")
        monitor.refresh()
        next_event(events, lambda e: e["state"] == "live")
        assert len(cycles) == 2
    finally:
        monitor.stop()
    # The result callback precedes the cycle's finally block; wait for cleanup.
    monitor._thread.join(1) if monitor._thread is not None else None
    assert all(api.http_client.closed and api._http_client_stream.closed for api in instances)


def test_presence_session_bounds_requests_and_does_not_turn_restrictions_into_offline():
    class Response:
        def __init__(self, code):
            self.code = code
        def raise_for_status(self):
            pass
        def json(self):
            return {"status_code": self.code}
    calls = []
    class Session:
        def get(self, url, **kwargs):
            calls.append(kwargs)
            return Response(4003110)
    session = module._PresenceSession(Session())
    with pytest.raises(ValueError, match="restricted"):
        session.get("https://www.tiktok.com/api-live/user/room/")
    assert calls[-1]["timeout"] == 15
    # API's alive=True + restricted info is still a positive presence result.
    assert session.get("https://webcast.tiktok.com/webcast/room/info/").json()["status_code"] == 4003110
