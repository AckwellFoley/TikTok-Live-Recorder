"""Roster semantics and actual FFmpeg output, without requiring a live room."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
pytest.importorskip("PIL")
from PIL import Image
from core.guest_panel import PanelTimeline, draw_panel, fetch_public_profile, render_panel, roster_from_ticket_notice
from types import SimpleNamespace


def test_profile_resolves_from_html_when_user_api_is_empty():
    payload = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {
        "user": {"id": "123", "uniqueId": "guest", "nickname": "Guest"},
    }}}}
    class Client:
        def get(self, url, **kwargs):
            return SimpleNamespace(
                json=lambda: {},
                text='<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
                     + json.dumps(payload) + '</script>',
            )
    api = SimpleNamespace(http_client=Client(), WEBCAST_URL="https://webcast.tiktok.com",
                          BASE_URL="https://www.tiktok.com")
    assert fetch_public_profile(api, "123")["username"] == "guest"
    with pytest.raises(ValueError, match="did not return"):
        fetch_public_profile(api, "456")


def test_real_ticket_notice_contains_zero_ticket_guests_but_not_viewers():
    events = pytest.importorskip("TikTokLive.events")
    # The relevant fields from the actual example_creator test connection. These
    # participants both have zero tickets, which must not hide their avatars.
    event = events.LinkMicFanTicketMethodEvent.from_dict({
        "fanTicketRoomNotice": {"scoreType": "ALL_FAN_TICKET", "userFanTicket": [
            {"userId": "6920052118524478470"}, {"userId": "7558120665707185165"},
        ]},
    })
    assert roster_from_ticket_notice(event.fan_ticket_room_notice, "6920052118524478470") == [
        "6920052118524478470", "7558120665707185165",
    ]
    event.fan_ticket_room_notice.score_type = 2  # A game-score subset isn't a roster.
    assert roster_from_ticket_notice(event.fan_ticket_room_notice, "6920052118524478470") is None


def test_baseline_joins_departures_and_no_duplicate_host(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("core.guest_panel.time.monotonic", lambda: now[0])
    timeline = PanelTimeline({"id": "1", "name": "Host", "avatar_url": "private-url"})
    timeline.update(["1", "2", "2"], "", "linkmic_fan_ticket")
    now[0] = 110
    timeline.mark_start()
    now[0] = 112
    timeline.update(["1", "2", "3"], "", "linkmic_fan_ticket")
    now[0] = 114
    timeline.update(["1"], "", "linkmic_fan_ticket")
    data = timeline.export()
    assert [(s["time"], s["ids"]) for s in data["snapshots"]] == [
        (0, ["1", "2"]), (2, ["1", "2", "3"]), (4, ["1"]),
    ]
    assert "avatar_url" not in data["profiles"]["1"]


@pytest.mark.parametrize("video_delay", [0.0, 0.1])
def test_rendered_video_shows_guest_only_during_membership_and_keeps_audio(tmp_path, video_delay):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg required for panel rendering integration test")
    original = tmp_path / "original.mp4"
    subprocess.run([
        ffmpeg, "-v", "error", "-itsoffset", str(video_delay),
        "-f", "lavfi", "-i", "color=c=blue:s=320x568:r=30:d=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-fps_mode:v", "passthrough", "-c:a", "aac", "-shortest", str(original),
    ], check=True)
    digest = hashlib.sha256(original.read_bytes()).digest()
    assets = tmp_path / "assets"
    assets.mkdir()
    Image.new("RGB", (160, 160), "red").save(assets / "host.png")
    Image.new("RGB", (160, 160), "lime").save(assets / "guest.png")
    timeline = {"host_id": "1", "profiles": {
        "1": {"name": "Host", "avatar": "host.png"},
        "2": {"name": "Guest", "avatar": "guest.png"},
    }, "snapshots": [
        {"time": 0, "ids": ["1"], "status": ""},
        {"time": 1, "ids": ["1", "2"], "status": ""},
        {"time": 2, "ids": ["1"], "status": ""},
    ]}
    output = render_panel(original, timeline, assets, ffmpeg)
    for timestamp, has_guest in ((0.5, False), (1.5, True), (2.5, False)):
        frame = subprocess.check_output([
            ffmpeg, "-v", "error", "-ss", str(timestamp), "-i", str(output),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ])
        image = Image.frombytes("RGB", (1440, 1280), frame)
        pixels = image.crop((720, 0, 1440, 1280)).get_flattened_data()
        green = sum(1 for r, g, b in pixels if g > 180 and r < 70 and b < 70)
        assert (green > 300) == has_guest
        # The left area remains the source feed, with no guest cards over it.
        left = image.crop((0, 0, 720, 1280))
        assert sum(1 for r, g, b in left.get_flattened_data()
                   if b > 180 and r < 30 and g < 30) > 720 * 1280 * 0.98
    metadata = json.loads(subprocess.check_output([
        ffprobe, "-v", "error", "-show_entries", "stream=codec_type,width,height,start_time,avg_frame_rate:format=duration",
        "-of", "json", str(output),
    ]))
    assert {s["codec_type"] for s in metadata["streams"]} == {"video", "audio"}
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1440, 1280)
    assert video["avg_frame_rate"] == "30/1"
    original_video = json.loads(subprocess.check_output([
        ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=start_time",
        "-of", "json", str(original),
    ]))["streams"][0]
    assert float(video["start_time"]) == pytest.approx(float(original_video["start_time"]), abs=1 / 30)
    assert float(metadata["format"]["duration"]) == pytest.approx(3, abs=0.12)
    def audio_packets(path):
        return subprocess.check_output([
            ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", "copy", "-f", "adts", "-",
        ])
    assert audio_packets(output) == audio_packets(original)
    assert hashlib.sha256(original.read_bytes()).digest() == digest


def test_live_timeline_keeps_slots_when_notices_change_order(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("core.guest_panel.time.monotonic", lambda: now[0])
    timeline = PanelTimeline({"id": "1", "name": "Host"})
    timeline.update(["3", "2"], "", "seed")
    timeline.mark_start()
    now[0] += 1
    timeline.update(["2", "4", "3"], "", "snapshot")
    assert timeline.export(live=True)["snapshots"][-1]["ids"] == ["1", "3", "2", "4"]
    now[0] += 1
    timeline.update(["4", "2"], "", "snapshot")
    assert timeline.export(live=True)["snapshots"][-1]["ids"] == ["1", "2", "4"]
    now[0] += 1
    timeline.update(["3", "4", "2"], "", "snapshot")
    assert timeline.export(live=True)["snapshots"][-1]["ids"] == ["1", "2", "4", "3"]


def test_list_rows_do_not_resize_on_arrival_and_background_is_opaque(tmp_path):
    data = {"host_id": "1", "profiles": {"1": {"name": "Host"}, "2": {"name": "Guest"}}}
    host = draw_panel(data, {"ids": ["1"], "status": ""}, tmp_path, (720, 1280))
    guests = draw_panel(data, {"ids": ["1", "2"], "status": ""}, tmp_path, (720, 1280))
    assert host.crop((24, 128, 238, 342)).tobytes() == guests.crop((24, 128, 238, 342)).tobytes()
    assert guests.getchannel("A").getextrema() == (255, 255)
