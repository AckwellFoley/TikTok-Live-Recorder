import io
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from multi_backend import MultiRecorder, ProcessRecorderJob
from recorder_worker import run_worker, sanitize_event


class FakeJob:
    def __init__(self, root, callback):
        self.callback = callback
        self.is_running = False
        self.started = False
        self.stops = 0

    def start(self, **options):
        self.options = options
        self.is_running = self.started = True
        self.callback({"type": "state", "state": "recording", "message": "Recording"})

    def stop(self):
        self.stops += 1
        self.callback({"type": "state", "state": "finalizing", "message": "Saving"})

    def finish(self, state="finished", exit=True):
        self.callback({"type": "state", "state": state, "message": "Result"})
        if exit:
            self.is_running = False


def manager(tmp_path, cap=2, consumer=None):
    jobs, events = [], []
    def factory(root, callback):
        job = FakeJob(root, callback)
        jobs.append(job)
        return job
    def callback(event):
        events.append(event)
        if consumer:
            consumer(event)
    return MultiRecorder(tmp_path, callback, cap, factory), jobs, events


def test_cap_duplicate_creator_and_slot_held_until_process_exit(tmp_path):
    multi, jobs, events = manager(tmp_path)
    assert multi.start("@Alice", tmp_path) == "alice"
    multi.start("bob", tmp_path)
    multi.start("carol", tmp_path)
    assert multi.active_count == 2 and len(jobs) == 2
    assert multi.snapshot()[-1]["queued"]
    with pytest.raises(ValueError, match="already"):
        multi.start("https://www.tiktok.com/@ALICE/live", tmp_path)
    # A finished callback is not enough: its process still owns the slot.
    jobs[0].finish(exit=False)
    multi.poll()
    assert len(jobs) == 2
    jobs[0].is_running = False
    multi.poll()
    assert len(jobs) == 3 and jobs[2].options["target"] == "carol"
    assert multi.snapshot()[0]["state"] == "finished"
    assert all("creator" in e and "session_id" in e for e in events)


def test_queued_cancel_never_creates_worker_and_stop_all_cancels_before_reentrant_poll(tmp_path):
    multi, jobs, _ = manager(tmp_path, cap=1)
    multi.start("alice", tmp_path)
    multi.start("bob", tmp_path)
    assert multi.stop("bob")
    assert multi.snapshot()[1]["cancelled"]
    multi.start("carol", tmp_path)
    original_stop = jobs[0].stop
    def stop_and_poll():
        original_stop()
        jobs[0].finish()
        multi.poll()
    jobs[0].stop = stop_and_poll
    multi.stop_all()
    multi.poll()
    assert len(jobs) == 1
    assert not multi.is_running
    assert multi.snapshot()[2]["cancelled"]


def test_stop_all_blocks_launch_during_queued_cancellation_callbacks(tmp_path):
    multi, jobs, _ = manager(tmp_path, cap=1)
    for creator in ("alice", "bob", "carol"):
        multi.start(creator, tmp_path)
    jobs[0].finish()
    previous_callback = multi.on_event
    def reentrant(event):
        previous_callback(event)
        if event.get("cancelled"):
            multi.poll()
    multi.on_event = reentrant
    multi.stop_all()
    assert len(jobs) == 1 and not multi.is_running


def test_stop_during_factory_callback_prevents_worker_launch(tmp_path):
    jobs = []
    multi = None
    def callback(event):
        if event.get("state") == "connecting":
            multi.stop_all()
    def factory(root, on_event):
        job = FakeJob(root, on_event)
        jobs.append(job)
        on_event({"type": "state", "state": "connecting"})
        return job
    multi = MultiRecorder(tmp_path, callback, 1, factory)
    multi.start("alice", tmp_path)
    assert len(jobs) == 1 and not jobs[0].started and not multi.is_running


def test_stale_callbacks_cannot_modify_new_session_for_same_creator(tmp_path):
    multi, jobs, received = manager(tmp_path)
    multi.start("alice", tmp_path)
    first_session = multi.snapshot()[0]["session_id"]
    jobs[0].finish()
    multi.poll()
    multi.start("ALICE", tmp_path)
    second = multi.snapshot()[0]
    assert second["session_id"] != first_session
    count = len(received)
    jobs[0].callback({"type": "state", "state": "error", "message": "Stale"})
    jobs[0].callback({"type": "panel", "timeline": {"profiles": {}}, "assets": "old"})
    assert len(received) == count
    assert multi.snapshot()[0]["state"] == "recording"


def test_restarted_creator_queues_behind_previously_queued_creator(tmp_path):
    multi, jobs, _ = manager(tmp_path, cap=1)
    for creator in ("alice", "bob", "carol"):
        multi.start(creator, tmp_path)
    jobs[0].finish()
    multi.poll()
    assert jobs[1].options["target"] == "bob"
    multi.start("alice", tmp_path)
    jobs[1].finish()
    multi.poll()
    assert jobs[2].options["target"] == "carol"
    assert next(row for row in multi.snapshot() if row["creator"] == "alice")["queued"]


def test_lower_cap_keeps_existing_jobs_and_increase_advances_queue_on_poll(tmp_path):
    multi, jobs, _ = manager(tmp_path, cap=2)
    for creator in ("alice", "bob", "carol"):
        multi.start(creator, tmp_path)
    multi.max_concurrent = 1
    assert multi.active_count == 2 and not any(job.stops for job in jobs)
    multi.poll()
    assert len(jobs) == 2
    multi.max_concurrent = 3
    multi.poll()
    assert len(jobs) == 3


@pytest.mark.parametrize("limit", [0, 13, -1, 1.5, True, "bad", float("inf")])
def test_cap_validation(tmp_path, limit):
    with pytest.raises(ValueError):
        MultiRecorder(tmp_path, lambda e: None, limit)


def test_finalizing_freezes_timer_but_keeps_slot(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("multi_backend.time.monotonic", lambda: now[0])
    multi, jobs, _ = manager(tmp_path)
    multi.start("alice", tmp_path)
    assert multi.snapshot()[0]["started_at"] == 100
    now[0] = 120
    jobs[0].callback({"type": "state", "state": "finalizing"})
    assert multi.snapshot()[0]["ended_at"] == 120
    now[0] = 130
    jobs[0].finish()
    multi.poll()
    assert multi.snapshot()[0]["ended_at"] == 120


def test_failed_job_does_not_stop_other_creators_or_block_queue(tmp_path):
    multi, jobs, _ = manager(tmp_path)
    for creator in ("alice", "bob", "carol"):
        multi.start(creator, tmp_path)
    jobs[0].finish("error")
    multi.poll()
    assert multi.snapshot()[0]["state"] == "error"
    assert jobs[1].is_running and jobs[1].stops == 0
    assert len(jobs) == 3


def test_worker_eof_requests_graceful_stop_and_emits_terminal_once(tmp_path):
    made = []
    class Job(FakeJob):
        def stop(self):
            self.stops += 1
            self.callback({"type": "state", "state": "finalizing", "message": "Saving"})
            self.finish()
            self.finish("error")  # Late duplicate must not become a second result.
    def factory(root, callback):
        made.append(Job(root, callback))
        return made[-1]
    options = {"root_dir": str(tmp_path), "target": "alice", "output": str(tmp_path)}
    stdout = io.StringIO()
    result = run_worker(io.StringIO(json.dumps(options) + "\n"), stdout, factory, poll_interval=0.001)
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert result == 0 and made[0].stops == 1
    assert [e["state"] for e in events if e.get("state") in ("finished", "error")] == ["finished"]


def test_worker_rejects_secret_options_without_echoing_values(tmp_path):
    stdout = io.StringIO()
    result = run_worker(io.StringIO('{"cookies":{"sessionid_ss":"sentinel-secret"}}\n'), stdout,
                        lambda *args: pytest.fail("Must not create a job"))
    assert result == 1
    assert "sentinel-secret" not in stdout.getvalue()
    assert len(stdout.getvalue().splitlines()) == 1


def test_worker_crash_without_terminal_becomes_one_error(tmp_path):
    released = threading.Event()
    class Input:
        first = True
        def readline(self, size=-1):
            if self.first:
                self.first = False
                return json.dumps({"target": "alice", "output": str(tmp_path)}) + "\n"
            released.wait(2)
            return ""
    class Job(FakeJob):
        def start(self, **kwargs):
            self.is_running = False
    stdout = io.StringIO()
    try:
        result = run_worker(Input(), stdout, Job, poll_interval=0.001)
    finally:
        released.set()
    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert result == 1 and len(events) == 1 and events[0]["state"] == "error"


def test_process_crash_reports_terminal_once(tmp_path):
    events = []
    job = ProcessRecorderJob(tmp_path, events.append)
    job.process = SimpleNamespace(stdout=io.StringIO('{"type":"log","message":"hello"}\n'),
                                  stdin=io.StringIO(), wait=lambda: 7)
    job._read()
    assert [e["state"] for e in events if e.get("state") in ("finished", "error")] == ["error"]
    assert "code 7" in events[-1]["message"]


def test_real_worker_process_reports_validation_error_without_network(tmp_path):
    terminal = threading.Event()
    events = []
    def callback(event):
        events.append(event)
        if event.get("state") in ("finished", "error"):
            terminal.set()
    job = ProcessRecorderJob(tmp_path, callback)
    # Fails input validation before a RecorderJob thread starts, so no login
    # file is loaded and no TikTok network request can be made.
    job.start("invalid target with spaces", tmp_path)
    assert terminal.wait(5)
    job.reader.join(2)
    assert not job.is_running
    assert [e["state"] for e in events if e.get("state") in ("finished", "error")] == ["error"]
    assert "username" in events[-1]["message"]


def test_worker_sanitizes_logs_and_public_panel_fields():
    event = sanitize_event({"type": "log", "message": "https://cdn.example/a?token=sentinel-secret cookie=private"})
    assert "sentinel-secret" not in event["message"] and "private" not in event["message"]
    event = sanitize_event({"type": "panel", "assets": "assets", "timeline": {
        "host_id": "1", "sessionid": "sentinel-secret", "snapshots": [{"ids": ["1"], "cookie": "private"}],
        "profiles": {"1": {"id": "1", "name": "Host", "avatar_url": "signed-secret"}},
    }})
    text = json.dumps(event)
    assert "sentinel-secret" not in text and "private" not in text and "signed-secret" not in text


@pytest.mark.parametrize("slots,expected", [(8, 8), (4.0, 4), (True, None), (1.5, None), (0, None), (float("inf"), None)])
def test_worker_preserves_only_valid_panel_slot_counts(slots, expected):
    event = sanitize_event({"type": "panel", "timeline": {"panel_slots": slots, "profiles": {}}})
    assert event["timeline"].get("panel_slots") == expected


def test_spawned_worker_receives_eof_if_start_options_write_fails(tmp_path, monkeypatch):
    class Pipe:
        closed = False
        def write(self, value):
            raise OSError("Simulated startup write failure")
        def close(self):
            self.closed = True
    pipe = Pipe()
    process = SimpleNamespace(stdin=pipe, stdout=io.StringIO(), poll=lambda: None)
    monkeypatch.setattr("multi_backend.subprocess.Popen", lambda *args, **kwargs: process)
    class Reader:
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass
        def is_alive(self):
            return True
    monkeypatch.setattr("multi_backend.threading.Thread", Reader)
    job = ProcessRecorderJob(tmp_path, lambda event: None)
    with pytest.raises(OSError, match="startup write"):
        job.start("alice", tmp_path)
    assert pipe.closed


@pytest.mark.parametrize("fraction", [None, float("nan"), float("inf"), -1, 2, True, "50%"])
def test_invalid_saving_progress_is_indeterminate(fraction):
    clean = sanitize_event(dict(type="state", state="finalizing", message="Encoding", progress=fraction))
    assert clean["progress"] is None


def test_saving_progress_survives_ipc_and_snapshot(tmp_path):
    multi, jobs, _ = manager(tmp_path)
    multi.start("alice", tmp_path)
    event = sanitize_event(dict(type="state", state="finalizing", message="Encoding combined video", progress=.42))
    jobs[0].callback(event)
    assert multi.snapshot()[0]["progress"] == .42
    assert multi.snapshot()[0]["message"] == "Encoding combined video"
    jobs[0].finish()
    assert multi.snapshot()[0]["progress"] is None
