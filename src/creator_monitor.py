"""Saved creator history and paced, asynchronous LIVE presence checks.

Callbacks run on a worker thread; GUI consumers must enqueue their events.
An injected probe accepts one canonical creator name and returns a bool, or
raises when status cannot be established. No error is converted to offline.
"""

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from core.tiktok_api import TikTokAPI
from gui_backend import parse_target, safe_message
from utils.login_store import load_login_settings


RECORDING_NAME = re.compile(
    r"^TK_([A-Za-z0-9._]+)_\d{4}\.\d{2}\.\d{2}_\d{2}-\d{2}-\d{2}"
    r"(?:_(?:panel|flv))?\.mp4$", re.IGNORECASE,
)


def _creator(target):
    if not isinstance(target, str):
        raise ValueError("Enter a TikTok username or a full TikTok LIVE link.")
    return parse_target(target)[0].lower()


def _creators(targets):
    if isinstance(targets, str):
        targets = [targets]
    return list(dict.fromkeys(_creator(target) for target in targets))


class CreatorHistory:
    """Persist explicit choices; historical recordings seed only once."""

    def __init__(self, root_dir):
        self.root_dir = Path(root_dir)
        self.path = self.root_dir / "creators.json"
        self._creators = []
        self._migrated = False
        self._lock = threading.RLock()
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict) or not isinstance(data.get("creators"), list):
                    raise ValueError()
                self._creators = _creators(data["creators"])
                self._migrated = data.get("migrated") is True
            except (OSError, ValueError, TypeError) as exc:
                raise ValueError("The saved creator list could not be read.") from exc

    @property
    def creators(self):
        with self._lock:
            return list(self._creators)

    def seed(self, recording_folder, targets=()):
        """Import recording filenames and the old GUI target on first use."""
        with self._lock:
            if self._migrated:
                return self.creators
            incoming = [targets] if isinstance(targets, str) else list(targets)
            try:
                settings = json.loads((self.root_dir / "gui-settings.json").read_text(encoding="utf-8-sig"))
                if isinstance(settings, dict) and settings.get("target"):
                    incoming.append(settings["target"])
            except (OSError, ValueError, UnicodeError):
                pass
            folder = Path(recording_folder)
            if folder.is_dir():
                for path in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
                    match = RECORDING_NAME.fullmatch(path.name)
                    if path.is_file() and match:
                        incoming.append(match.group(1))
            creators = list(self._creators)
            for target in incoming:
                try:
                    creator = _creator(target)
                except (TypeError, ValueError):
                    continue  # Ignore invalid legacy targets and unrelated files.
                if creator not in creators:
                    creators.append(creator)
            self._commit(creators, migrated=True)
            return self.creators

    def add(self, target):
        creator = _creator(target)
        with self._lock:
            if creator not in self._creators:
                self._commit(self._creators + [creator])
        return creator

    def remove(self, creator):
        creator = _creator(creator)
        with self._lock:
            if creator in self._creators:
                self._commit([uid for uid in self._creators if uid != creator])

    def _commit(self, creators, migrated=None):
        previous = self._creators, self._migrated
        self._creators = creators
        if migrated is not None:
            self._migrated = migrated
        try:
            self.save()
        except Exception:
            self._creators, self._migrated = previous
            raise

    def save(self):
        with self._lock:
            self.root_dir.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "migrated": self._migrated, "creators": self._creators}
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.root_dir,
                    prefix=".creators-", suffix=".tmp", delete=False,
                ) as file:
                    temporary = Path(file.name)
                    json.dump(payload, file, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return self.path


class _PresenceSession:
    """Bound requests and retain access errors otherwise mistaken for offline."""

    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        return getattr(self.session, name)

    def get(self, url, **kwargs):
        kwargs.setdefault("timeout", 15)
        response = self.session.get(url, **kwargs)
        response.raise_for_status()
        parts = urlsplit(url)
        host = parts.hostname or ""
        if host == "tiktok.com" or host.endswith(".tiktok.com"):
            try:
                data = response.json()
            except ValueError:
                return response  # Upstream identifies challenge HTML/invalid JSON.
            if isinstance(data, dict):
                code = data.get("status_code", data.get("statusCode", 0))
                if code not in (None, 0, "0"):
                    # is_room_alive first obtained an explicit alive=True from
                    # check_alive. Its info endpoint can be restricted while
                    # the creator is still demonstrably LIVE.
                    if str(code) == "4003110" and parts.path == "/webcast/room/info/":
                        return response
                    raise ValueError("TikTok restricted this status check. Check your login or try again later.")
        return response


def _close_api(api):
    seen = set()
    for name in ("http_client", "_http_client_stream"):
        session = getattr(api, name, None)
        if isinstance(session, _PresenceSession):
            session = session.session
        if session is None or id(session) in seen:
            continue
        seen.add(id(session))
        try:
            session.close()
        except Exception:
            pass


class CreatorMonitor:
    """One sequential worker; updates/stop invalidate in-flight results."""

    PACE_SECONDS = 1.0

    def __init__(self, root_dir, on_event, interval=60, probe=None):
        self.root_dir = Path(root_dir)
        self.on_event = on_event
        self.interval = float(interval)
        if not 0 < self.interval < float("inf"):
            raise ValueError("The LIVE check interval must be positive.")
        self.probe = probe
        self._condition = threading.Condition(threading.RLock())
        self._creators = []
        self._generation = 0
        self._refresh_serial = 0
        self._running = False
        self._thread = None
        self._next_request = 0.0

    @property
    def is_running(self):
        with self._condition:
            return self._running

    def _launch_locked(self):
        self._thread = threading.Thread(target=self._run, name="Creator LIVE checks", daemon=True)
        self._thread.start()

    def start(self, creators):
        creators = _creators(creators)
        with self._condition:
            self._creators = creators
            self._generation += 1
            self._running = True
            if self._thread is None or not self._thread.is_alive():
                self._launch_locked()
            self._condition.notify_all()

    def set_creators(self, creators):
        creators = _creators(creators)
        with self._condition:
            if creators != self._creators:
                self._creators = creators
                self._generation += 1
                self._condition.notify_all()

    def refresh(self):
        with self._condition:
            self._refresh_serial += 1
            self._condition.notify_all()

    def stop(self):
        with self._condition:
            self._running = False
            self._generation += 1
            self._condition.notify_all()

    def _emit(self, creator, generation, state, live=None, message=""):
        with self._condition:
            if not self._running or generation != self._generation or creator not in self._creators:
                return
            event = {"type": "presence", "creator": creator, "state": state,
                     "live": live, "checked_at": time.time(), "message": safe_message(message)}
            try:
                self.on_event(event)
            except Exception:
                pass  # A UI consumer cannot terminate polling.

    def _turn(self, generation):
        with self._condition:
            while self._running and generation == self._generation:
                delay = self._next_request - time.monotonic()
                if delay <= 0:
                    self._next_request = time.monotonic() + self.PACE_SECONDS
                    return True
                self._condition.wait(delay)
            return False

    def _new_api(self):
        # Called only on the worker, once per cycle, so imported login changes
        # take effect without reopening the app. Each cycle owns its sessions.
        api = TikTokAPI(proxy=None, cookies=load_login_settings(self.root_dir))
        api.http_client = _PresenceSession(api.http_client)
        return api

    def _check(self, api, creator):
        if self.probe is not None:
            live = self.probe(creator)
        else:
            room_id = api.get_room_id_from_user(creator)
            live = api.is_room_alive(room_id) if room_id else False
        if not isinstance(live, bool):
            raise ValueError("TikTok did not return a LIVE status.")
        return live

    def _run(self):
        try:
            while True:
                with self._condition:
                    while self._running and not self._creators:
                        self._condition.wait()
                    if not self._running:
                        return
                    creators = list(self._creators)
                    generation, refresh = self._generation, self._refresh_serial
                started = time.monotonic()
                api, setup_error = None, None
                try:
                    if self.probe is None:
                        api = self._new_api()
                except Exception as exc:
                    setup_error = exc
                try:
                    for creator in creators:
                        if not self._turn(generation):
                            break
                        self._emit(creator, generation, "checking", message="Checking LIVE status...")
                        with self._condition:
                            if not self._running or self._generation != generation:
                                break
                        try:
                            if setup_error is not None:
                                raise setup_error
                            live = self._check(api, creator)
                        except Exception as exc:
                            self._emit(creator, generation, "error", message=exc)
                        else:
                            self._emit(creator, generation, "live" if live else "offline", live,
                                       "Live now" if live else "Not live")
                finally:
                    if api is not None:
                        _close_api(api)
                with self._condition:
                    while (self._running and self._generation == generation
                           and self._refresh_serial == refresh):
                        remaining = started + self.interval - time.monotonic()
                        if remaining <= 0:
                            break
                        self._condition.wait(remaining)
        finally:
            # If start() raced with the worker exiting after stop(), ensure its
            # fresh request gets a worker without overlapping network probes.
            with self._condition:
                if self._thread is threading.current_thread():
                    self._thread = None
                    if self._running:
                        self._launch_locked()
