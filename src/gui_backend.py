"""Threaded recorder controller for the desktop UI.

Callbacks carry plain dictionaries and can occur on the worker or event-feed
threads. A UI must enqueue these events before touching its widgets.
"""

import logging
import re
import shutil
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from core.tiktok_recorder import TikTokRecorder
from utils.enums import Mode
from utils.logger_manager import logger
from utils.recorder_config import RecorderConfig
from utils.login_store import load_login_settings
from video_engine import find_ffmpeg, ensure_ffmpeg


def parse_target(target):
    target = str(target).strip()
    if target.startswith("https://"):
        parts = urlsplit(target)
        if parts.hostname not in ("tiktok.com", "www.tiktok.com"):
            raise ValueError("Enter a TikTok username or a full TikTok LIVE link.")
        match = re.fullmatch(r"/@([A-Za-z0-9._]+)/live/?", parts.path)
        if not match or parts.username or parts.password or parts.port not in (None, 443):
            raise ValueError("Use a LIVE link such as https://www.tiktok.com/@creator/live.")
        return match.group(1), "https://www.tiktok.com/@" + match.group(1) + "/live"
    match = re.fullmatch(r"@?([A-Za-z0-9._]+)", target)
    if not match:
        raise ValueError("Enter one TikTok username, for example @example_creator.")
    return match.group(1), None


def safe_message(value):
    """Keep useful log text without exposing cookie values or signed URLs."""
    message = str(value).replace("\r", " ").strip()
    def clean_url(match):
        try:
            parts = urlsplit(match.group(0))
            return urlunsplit((parts.scheme, parts.netloc.split("@")[-1], parts.path, "", ""))
        except ValueError:
            return "[URL]"
    message = re.sub(r"https?://[^\s]+", clean_url, message)
    message = re.sub(r"(?i)(sessionid(?:_ss)?|authorization|access[_-]?key|token|cookie)\s*[:=]\s*[^\s,;]+",
                     r"\1=[redacted]", message)
    return message[:1200]


class _EventLogHandler(logging.Handler):
    def __init__(self, emit):
        super().__init__(logging.INFO)
        self.emit_event = emit

    def emit(self, record):
        self.emit_event({"type": "log", "message": safe_message(record.getMessage())})


class RecorderJob:
    def __init__(self, root_dir, on_event):
        self.root_dir = Path(root_dir).resolve()
        self.on_event = on_event
        self._thread = None
        self._stop_event = threading.Event()
        self._state = "finished"
        self._lock = threading.RLock()

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def _emit(self, event):
        if event.get("type") == "state":
            self._state = event.get("state", self._state)
        if "message" in event:
            event = dict(event, message=safe_message(event["message"]))
        try:
            self.on_event(event)
        except Exception:
            # Display failures must never interrupt recording or conversion.
            logger.debug("GUI event consumer failed", exc_info=True)

    def start(self, target, output, mode="manual", panel=True, duration=0, interval=5):
        with self._lock:
            if self.is_running:
                raise RuntimeError("A recording is already running.")
            username, url = parse_target(target)
            if mode not in ("manual", "automatic"):
                raise ValueError("Choose Record now or Monitor automatically.")
            try:
                duration, interval = int(duration), int(interval)
            except (TypeError, ValueError) as exc:
                raise ValueError("Duration and check interval must be whole numbers.") from exc
            if duration < 0:
                raise ValueError("Duration cannot be negative. Use 0 for no time limit.")
            if interval < 1:
                raise ValueError("The check interval must be at least one minute.")
            if not str(output).strip():
                raise ValueError("Choose a folder for saved recordings.")
            folder = Path(output).expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
            if not folder.is_dir():
                raise ValueError("Choose a folder, not a file, for recordings.")
            ffmpeg = find_ffmpeg(self.root_dir)
            self._stop_event = threading.Event()
            config = RecorderConfig(
                mode=Mode.MANUAL if mode == "manual" else Mode.AUTOMATIC,
                user=None if url else username, url=url, output=str(folder),
                automatic_interval=interval, duration=duration or None,
                panel=bool(panel), ffmpeg_path=ffmpeg,
                stop_event=self._stop_event, event_callback=self._emit,
            )
            self._state = "connecting"
            self._thread = threading.Thread(target=self._run, args=(config,), daemon=True,
                                            name="TikTok recorder")
            self._thread.start()

    def _run(self, config):
        handler = _EventLogHandler(self._emit)
        logger.addHandler(handler)
        result = {"type": "state", "state": "finished", "message": "Recording finished. Saved videos are ready."}
        try:
            self._emit({"type": "state", "state": "connecting", "message": "Connecting to TikTok..."})
            config.cookies = load_login_settings(self.root_dir)
            if not config.ffmpeg_path:
                config.ffmpeg_path = ensure_ffmpeg(self.root_dir,
                    progress=lambda message: self._emit({"type": "state", "state": "connecting", "message": message}),
                    stop_event=self._stop_event)
            TikTokRecorder(config).run()
            if self._stop_event.is_set():
                result["message"] = "Stopped. Any recorded video has been saved."
        except Exception as exc:
            result = {"type": "state", "state": "error", "message": safe_message(exc)}
        finally:
            logger.removeHandler(handler)
            self._emit(result)

    def stop(self):
        if not self.is_running:
            return
        self._stop_event.set()
        if self._state == "finalizing":
            self._emit({"type": "log", "message": "Finishing the MP4. Please wait for saving to complete."})
        else:
            self._emit({"type": "state", "state": "stopping", "message": "Stopping and saving the current recording..."})
