"""Bounded concurrent recordings with one independent process per creator."""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from gui_backend import parse_target, safe_message
from app_paths import worker_command


TERMINAL_STATES = {"finished", "error"}


class ProcessRecorderJob:
    """RecorderJob-compatible process adapter; no login data crosses its pipes."""

    def __init__(self, root_dir, on_event):
        self.root_dir = Path(root_dir).resolve()
        self.on_event = on_event
        self.process = None
        self.reader = None
        self._terminal = False
        self._write_lock = threading.Lock()

    @property
    def is_running(self):
        return self.process is not None and (self.process.poll() is None or
               (self.reader is not None and self.reader.is_alive()))

    def _emit(self, event):
        if self._terminal:
            return
        if event.get("type") == "state" and event.get("state") in TERMINAL_STATES:
            self._terminal = True
        self.on_event(event)

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # Never forward unsolicited plaintext output.
                if isinstance(event, dict):
                    self._emit(event)
            code = self.process.wait()
            if not self._terminal:
                self._emit({"type": "state", "state": "error",
                            "message": f"Recording worker exited unexpectedly (code {code})."})
        except Exception:
            self._emit({"type": "state", "state": "error", "message": "Recording worker connection ended unexpectedly."})
        finally:
            for stream in (self.process.stdin, self.process.stdout):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def start(self, target, output, mode="manual", panel=True, duration=0, interval=5):
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        self.process = subprocess.Popen(
            worker_command(), cwd=str(self.root_dir),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.reader = threading.Thread(target=self._read, name="Recorder events", daemon=True)
        options = {"root_dir": str(self.root_dir), "target": target, "output": str(output),
                   "mode": mode, "panel": panel, "duration": duration, "interval": interval}
        try:
            self.reader.start()
            with self._write_lock:
                self.process.stdin.write(json.dumps(options, ensure_ascii=True) + "\n")
                self.process.stdin.flush()
        except Exception:
            # A spawned worker may already be waiting for options. EOF requests
            # its safe shutdown even when startup IPC fails before a stop command
            # can be sent. Keep the reader alive to drain the terminal result.
            with self._write_lock:
                try:
                    self.process.stdin.close()
                except (OSError, ValueError):
                    pass
            raise

    def stop(self):
        if self.process is None:
            return
        with self._write_lock:
            try:
                self.process.stdin.write('{"command":"stop"}\n')
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass  # The reader reports the terminal result or crash.


class MultiRecorder:
    def __init__(self, root_dir, on_event, max_concurrent=4, job_factory=None):
        self.root_dir = Path(root_dir).resolve()
        self.on_event = on_event
        self.job_factory = job_factory or ProcessRecorderJob
        self._lock = threading.RLock()
        self._jobs = {}
        self._stopping_all = False
        self._queue_serial = 0
        self.max_concurrent = max_concurrent

    @property
    def max_concurrent(self):
        return self._max_concurrent

    @max_concurrent.setter
    def max_concurrent(self, value):
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Concurrent recordings must be a whole number from 1 to 12.") from exc
        if isinstance(value, bool) or not 1 <= number <= 12 or (isinstance(value, float) and value != number):
            raise ValueError("Concurrent recordings must be a whole number from 1 to 12.")
        with self._lock:
            self._max_concurrent = number

    @property
    def active_count(self):
        with self._lock:
            return sum(bool(record["slot"]) for record in self._jobs.values())

    @property
    def is_running(self):
        with self._lock:
            return any(record["slot"] or record["queued"] for record in self._jobs.values())

    def _emit(self, record, event):
        tagged = dict(event, creator=record["creator"], session_id=record["session_id"])
        if "message" in tagged:
            tagged["message"] = safe_message(tagged["message"])
        try:
            self.on_event(tagged)
        except Exception:
            pass  # A display error must never interrupt another recording.

    def _receive(self, creator, session_id, event):
        with self._lock:
            record = self._jobs.get(creator)
            if record is None or record["session_id"] != session_id or record["terminal"]:
                return
            if not isinstance(event, dict) or event.get("type") not in ("state", "panel", "log"):
                return
            if event["type"] == "state":
                state = event.get("state")
                if state not in {"connecting", "recording", "waiting", "finalizing", "finished", "error", "stopping"}:
                    return
                if state == "recording" and record["state"] != "recording":
                    record["started_at"] = time.monotonic()
                    record["ended_at"] = None
                elif state in ("connecting", "waiting"):
                    record["started_at"] = None
                    record["ended_at"] = None
                elif state in ("finalizing", "stopping", "finished", "error"):
                    if record["started_at"] is not None and record["ended_at"] is None:
                        record["ended_at"] = time.monotonic()
                record["state"] = state
                record["message"] = safe_message(event.get("message", ""))
                if state in TERMINAL_STATES:
                    record["terminal"] = True
            self._emit(record, event)

    def start(self, target, output, mode="manual", panel=True, duration=0, interval=5):
        creator = parse_target(target)[0].lower()
        if mode not in ("manual", "automatic"):
            raise ValueError("Choose Record now or Monitor automatically.")
        try:
            duration, interval = int(duration), int(interval)
        except (TypeError, ValueError) as exc:
            raise ValueError("Duration and interval must be whole numbers.") from exc
        if duration < 0 or interval < 1:
            raise ValueError("Duration must be zero or more; interval must be at least one minute.")
        if not str(output).strip():
            raise ValueError("Choose a folder for saved recordings.")
        folder = Path(output).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        with self._lock:
            old = self._jobs.get(creator)
            if old and (old["slot"] or old["queued"]):
                raise ValueError(f"@{creator} is already recording or queued.")
            self._queue_serial += 1
            record = {"creator": creator, "session_id": uuid.uuid4().hex, "state": "queued",
                      "started_at": None, "ended_at": None, "cancelled": False,
                      "queued": True, "slot": False, "job": None,
                      "terminal": False, "stop_requested": False, "message": "Waiting for a recording slot.",
                      "queue_order": self._queue_serial,
                      "options": {"target": creator, "output": str(folder), "mode": mode,
                                  "panel": bool(panel), "duration": duration, "interval": interval}}
            self._jobs[creator] = record
            self._emit(record, {"type": "state", "state": "queued", "message": record["message"]})
            self._launch_queued()
        return creator

    def _launch_queued(self):
        if self._stopping_all:
            return
        while self.active_count < self.max_concurrent:
            record = min((r for r in self._jobs.values() if r["queued"]),
                         key=lambda r: r["queue_order"], default=None)
            if record is None:
                return
            record.update(queued=False, slot=True, state="connecting")
            creator, session_id = record["creator"], record["session_id"]
            callback = lambda event, c=creator, s=session_id: self._receive(c, s, event)
            try:
                job = self.job_factory(self.root_dir, callback)
                record["job"] = job
                if record["stop_requested"]:
                    self._cancel(record)
                    continue
                job.start(**record["options"])
                if record["stop_requested"]:
                    job.stop()
            except Exception as exc:
                self._receive(creator, session_id, {"type": "state", "state": "error", "message": str(exc)})
                job = record["job"]
                if job is None or not job.is_running:
                    record.update(slot=False, job=None)

    def _cancel(self, record):
        record.update(queued=False, slot=False, job=None, state="finished", terminal=True,
                      stop_requested=True, cancelled=True, message="Queued recording cancelled.")
        self._emit(record, {"type": "state", "state": "finished", "message": record["message"], "cancelled": True})

    def stop(self, creator):
        creator = parse_target(creator)[0].lower()
        with self._lock:
            record = self._jobs.get(creator)
            if not record or (not record["queued"] and not record["slot"]):
                return False
            record["stop_requested"] = True
            if record["queued"]:
                self._cancel(record)
            elif record["job"] is not None:
                if record["state"] not in TERMINAL_STATES and record["state"] != "finalizing":
                    self._receive(creator, record["session_id"], {"type": "state", "state": "stopping",
                                                                "message": "Stopping and saving this recording..."})
                try:
                    record["job"].stop()
                except Exception as exc:
                    self._emit(record, {"type": "log", "message": f"Stop request failed: {safe_message(exc)}"})
            return True

    def stop_all(self):
        with self._lock:
            # Cancel every queued item first. Reentrant completion/poll callbacks
            # must not launch a queued recording while active jobs are stopping.
            previous = self._stopping_all
            self._stopping_all = True
            try:
                for record in list(self._jobs.values()):
                    if record["queued"]:
                        self._cancel(record)
                for record in list(self._jobs.values()):
                    if record["slot"]:
                        self.stop(record["creator"])
            finally:
                self._stopping_all = previous

    def poll(self):
        with self._lock:
            for record in list(self._jobs.values()):
                job = record["job"]
                if not record["slot"] or job is None:
                    continue
                if not job.is_running:
                    if not record["terminal"]:
                        self._receive(record["creator"], record["session_id"], {
                            "type": "state", "state": "finished" if record["stop_requested"] else "error",
                            "message": "Stopped." if record["stop_requested"] else "Recording worker exited without a result.",
                        })
                    record.update(slot=False, job=None)
            self._launch_queued()

    def snapshot(self):
        with self._lock:
            return [{key: record[key] for key in ("creator", "session_id", "state", "started_at", "ended_at", "queued", "cancelled", "message")}
                    for record in self._jobs.values()]
