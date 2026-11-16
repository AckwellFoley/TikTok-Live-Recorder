from dataclasses import dataclass
from threading import Event
from typing import Callable

from utils.enums import Mode


@dataclass
class RecorderConfig:
    mode: Mode
    url: str | None = None
    user: str | None = None
    room_id: str | None = None
    automatic_interval: int = 5
    cookies: dict | None = None
    proxy: str | None = None
    output: str | None = None
    duration: int | None = None
    use_telegram: bool = False
    bitrate: str | None = None
    ffmpeg_path: str | None = None
    panel: bool = False
    guests: bool | None = None
    comments: bool | None = None
    layout: str = "combined"
    stop_event: Event | None = None
    event_callback: Callable[[dict], None] | None = None
