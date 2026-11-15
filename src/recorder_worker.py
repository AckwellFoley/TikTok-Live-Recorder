"""One isolated recording, controlled by newline-delimited JSON on stdin.

The first line contains ordinary recording options and the installation root.
Cookies are loaded by RecorderJob inside this process, never sent over IPC.
Subsequent {"command": "stop"} lines, or parent stdin EOF, request a safe stop.
"""

import contextlib
import json
import logging
import sys
import threading
import time
from pathlib import Path


TERMINAL_STATES = {"finished", "error"}
OPTION_KEYS = {"root_dir", "target", "output", "mode", "panel", "duration", "interval"}


def sanitize_event(event):
    from gui_backend import safe_message

    if not isinstance(event, dict) or event.get("type") not in ("state", "log", "panel"):
        return None
    kind = event["type"]
    if kind in ("state", "log"):
        result = {"type": kind, "message": safe_message(event.get("message", ""))}
        if kind == "state":
            state = event.get("state")
            if state not in {"connecting", "recording", "waiting", "finalizing", "finished", "error", "stopping"}:
                return None
            result["state"] = state
        if isinstance(event.get("path"), str):
            result["path"] = event["path"]
        return result
    timeline = event.get("timeline")
    if not isinstance(timeline, dict):
        return None
    # Explicit public-display fields prevent accidental transmission of raw
    # HTTP responses, login settings, or signed avatar URLs.
    profiles = {}
    raw_profiles = timeline.get("profiles") or {}
    if not isinstance(raw_profiles, dict):
        return None
    for uid, profile in list(raw_profiles.items())[:128]:
        if isinstance(profile, dict):
            profiles[str(uid)] = {key: profile[key] for key in ("id", "username", "name", "avatar")
                                  if isinstance(profile.get(key), str)}
    clean = {key: timeline[key] for key in ("host_id", "speaking_indicators", "active_speakers")
             if key in timeline}
    clean["snapshots"] = [{key: snapshot[key] for key in ("time", "ids", "status", "source", "active_speakers")
                           if key in snapshot}
                          for snapshot in timeline.get("snapshots", [])[-128:] if isinstance(snapshot, dict)]
    clean["profiles"] = profiles
    for key in ("speaker_mapped_count", "speaker_participant_count"):
        value = timeline.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 128:
            clean[key] = value
    slots = timeline.get("panel_slots")
    if isinstance(slots, (int, float)) and not isinstance(slots, bool) and 1 <= slots <= 128 and float(slots).is_integer():
        clean["panel_slots"] = int(slots)
    comment_count = timeline.get("comment_count")
    if isinstance(comment_count, int) and not isinstance(comment_count, bool) and comment_count >= 0:
        clean["comment_count"] = comment_count
    return {"type": "panel", "timeline": clean, "assets": str(event.get("assets", ""))}


def run_worker(stdin, stdout, job_factory=None, poll_interval=0.05):
    """Run the IPC lifecycle; injectable streams/jobs allow no-network tests."""
    if job_factory is None:
        from gui_backend import RecorderJob
        job_factory = RecorderJob
    lock = threading.RLock()
    terminal = None
    channel_broken = False
    stopped = False
    job = None

    def request_stop():
        nonlocal stopped
        with lock:
            if stopped:
                return
            stopped = True
            current = job
        if current is not None:
            current.stop()

    def send(event):
        nonlocal terminal, channel_broken
        clean = sanitize_event(event)
        if clean is None:
            return
        broken = False
        with lock:
            if channel_broken or terminal is not None:
                return
            if clean.get("state") in TERMINAL_STATES:
                terminal = clean["state"]
            try:
                stdout.write(json.dumps(clean, ensure_ascii=True) + "\n")
                stdout.flush()
            except (OSError, ValueError):
                channel_broken = broken = True
        if broken:
            request_stop()

    def commands():
        try:
            while True:
                line = stdin.readline(4097)
                if not line:
                    break
                if len(line) > 4096:
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict) and value.get("command") == "stop":
                    request_stop()
        finally:
            request_stop()

    try:
        line = stdin.readline(32769)
        if not line or len(line) > 32768:
            raise ValueError("Recording worker received no valid start options.")
        options = json.loads(line)
        if not isinstance(options, dict) or set(options) - OPTION_KEYS:
            raise ValueError("Recording worker received unsupported start options.")
        root = options.pop("root_dir", str(Path(__file__).resolve().parents[1]))
        job = job_factory(root, send)
        job.start(**options)
        threading.Thread(target=commands, name="Recorder commands", daemon=True).start()
        while job.is_running:
            time.sleep(poll_interval)
        if terminal is None:
            send({"type": "state", "state": "finished" if stopped else "error",
                  "message": "Stopped. Saved video is ready." if stopped else "Recording worker ended without a result."})
    except Exception as exc:
        send({"type": "state", "state": "error", "message": str(exc)})
        request_stop()
        while job is not None and job.is_running:
            time.sleep(poll_interval)
    return 0 if terminal == "finished" else 1


def main():
    wire = sys.stdout
    # Only sanitized JSON uses the original stdout. Console/file handlers from
    # the legacy logger must not race on one shared rotating log across jobs.
    with contextlib.redirect_stdout(sys.stderr):
        from gui_backend import RecorderJob
        logger = logging.getLogger("logger")
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        return run_worker(sys.stdin, wire, RecorderJob)


if __name__ == "__main__":
    raise SystemExit(main())
