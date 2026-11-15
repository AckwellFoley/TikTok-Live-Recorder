import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.guest_panel import GuestPanelCapture, PanelTimeline, render_panel
from core.panel_membership import PanelMembership
from recorder_worker import sanitize_event


def test_comment_clock_and_live_count_follow_the_recording_start(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("core.guest_panel.time.monotonic", lambda: now[0])
    timeline = PanelTimeline({"id": "1", "name": "Host"})
    message = dict(id="old", name="Alice", username="alice", text="Before recording")
    assert not timeline.add_comment(message)
    timeline.mark_start()
    now[0] = 102
    assert not timeline.add_comment(message)
    assert timeline.add_comment(dict(message, id="new", text="Recorded comment"))
    assert timeline.export()["comments"][0]["time"] == 2
    live = timeline.export(live=True)
    assert live["comment_count"] == 1
    assert "comments" not in live
    event = sanitize_event(dict(type="panel", timeline=live, assets="assets"))
    assert event["timeline"]["comment_count"] == 1
    assert "comments" not in event["timeline"]
    timeline.reset_start()
    timeline.mark_start()
    assert not timeline.add_comment(dict(message, id="new"))
    assert timeline.export()["comments"] == []


def test_actual_comment_listener_records_text_without_adding_viewers_to_guest_slots(monkeypatch, tmp_path):
    import TikTokLive
    from TikTokLive.events import CommentEvent
    capture = GuestPanelCapture.__new__(GuestPanelCapture)
    capture.username, capture.room_id = "creator", "123"
    capture.timeline = PanelTimeline({"id": "1", "name": "Host"})
    capture.timeline.mark_start()
    capture.membership = PanelMembership("1", capture._accept_roster)
    from core.speaker_identity import SpeakerIdentity
    capture.speaker_identity = SpeakerIdentity("1")
    capture.stop_event, capture.ready = threading.Event(), threading.Event()
    capture.pending_profiles, capture.profile_attempts = set(), {}
    capture.last_ids, capture.last_comment_preview = [], 0
    capture.assets = tmp_path
    capture.event_callback = lambda event: None

    class Client:
        def __init__(self, **kwargs):
            self.listeners = {}
            self.web = self
        def add_listener(self, event_type, callback):
            self.listeners[event_type] = callback
        async def start(self, **kwargs):
            try:
                event = CommentEvent.from_dict(dict(common=dict(msgId="12345"),
                    user=dict(id="22", displayId="alice", nickname="Alice"), content="Hello from chat"))
                await self.listeners[CommentEvent](event)
                await self.listeners[CommentEvent](event)
            finally:
                capture.stop_event.set()
            return asyncio.create_task(asyncio.sleep(0))
        async def disconnect(self):
            pass
        async def close(self):
            pass

    monkeypatch.setattr(TikTokLive, "TikTokLiveClient", Client)
    asyncio.run(capture._listen())
    saved = capture.timeline.export()
    assert len(saved["comments"]) == 1
    assert saved["comments"][0]["text"] == "Hello from chat"
    assert saved["snapshots"][-1]["ids"] == ["1"]
    # This fake did not emit the library's handshake-complete ConnectEvent.
    assert saved["comment_statuses"][-1]["status"] == "Connecting to comments"


@pytest.mark.parametrize("video_delay", [0.0, 0.1])
def test_three_columns_keep_comments_left_feed_center_guests_right_and_original_audio(tmp_path, video_delay):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg required")
    source = tmp_path / "source.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-itsoffset", str(video_delay),
        "-f", "lavfi", "-i", "color=c=blue:s=320x568:r=30:d=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-fps_mode:v", "passthrough",
        "-c:a", "aac", "-shortest", str(source)], check=True, stdin=subprocess.DEVNULL)
    original_hash = hashlib.sha256(source.read_bytes()).digest()
    assets = tmp_path / "assets"
    assets.mkdir()
    Image.new("RGB", (160, 160), "red").save(assets / "host.png")
    Image.new("RGB", (160, 160), "lime").save(assets / "guest.png")
    timeline = dict(host_id="1", profiles={"1": dict(name="Host", avatar="host.png"),
        "2": dict(name="Guest", avatar="guest.png")},
        snapshots=[dict(time=0, ids=["1", "2"], status="")],
        comments=[dict(time=1, id="message-1", name="Alice", username="alice", text="Hello from the comments panel")],
        comment_statuses=[dict(time=0, status="Connected")])
    output = render_panel(source, timeline, assets, ffmpeg)
    for at, has_comment in ((0.5, False), (1.5, True), (2.5, True)):
        frame = subprocess.check_output([ffmpeg, "-v", "error", "-ss", str(at), "-i", str(output),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        image = Image.frombytes("RGB", (2160, 1280), frame)
        chat_pixels = image.crop((0, 128, 720, 1280)).get_flattened_data()
        bright = sum(r > 180 and g > 180 and b > 180 for r, g, b in chat_pixels)
        assert (bright > 200) == has_comment
        center = image.crop((720, 0, 1440, 1280)).get_flattened_data()
        assert sum(b > 180 and r < 30 and g < 30 for r, g, b in center) > 720 * 1280 * .98
        guests = image.crop((1440, 0, 2160, 1280)).get_flattened_data()
        assert sum(g > 180 and r < 70 and b < 70 for r, g, b in guests) > 1000
    metadata = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_entries",
        "stream=codec_type,width,height,avg_frame_rate,start_time:format=duration", "-of", "json", str(output)]))
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (2160, 1280)
    assert video["avg_frame_rate"] == "30/1"
    assert float(video["start_time"]) == pytest.approx(video_delay, abs=1 / 30)
    assert float(metadata["format"]["duration"]) == pytest.approx(3, abs=.12)
    def audio(path):
        return subprocess.check_output([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0",
                                        "-c:a", "copy", "-f", "adts", "-"])
    assert audio(output) == audio(source)
    assert hashlib.sha256(source.read_bytes()).digest() == original_hash
    assert list(assets.glob("comments-*.png")) == []
