import json
import sys
import asyncio
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.speaker_activity import SpeakerActivity, panel_frames, speaking_payload
from core.panel_membership import PanelMembership


def test_real_nested_grid_format_and_explicit_talk_flag_take_precedence():
    payload = speaking_payload({"app_data": json.dumps({"anchor_link_mic_id": "room_host_0", "grids": [
        {"uid_str": "room_host_0", "talk": 1, "talk_volume": 140, "mute_a": False},
        {"uid_str": "room_guest", "talk": 0, "talk_volume": 17, "mute_a": False},
        {"uid_str": "room_muted", "talk": 1, "talk_volume": 230, "mute_a": True},
    ]})})
    assert payload["states"] == {"room_host_0": True, "room_guest": False, "room_muted": False}
    assert payload["anchor"] == "room_host_0"


def test_mixed_audio_loudness_never_identifies_a_person():
    assert speaking_payload({"loudness": {"sourceMomentary": "-12.0"}}) is None
    assert speaking_payload({"app_data": "invalid"}) is None


def test_linkmic_ids_are_mapped_from_membership_not_guessed_as_profile_ids():
    events = pytest.importorskip("TikTokLive.events")
    membership = PanelMembership("1", lambda *args: None)
    membership.handle(events.LinkLayerEvent.from_dict({"listChangeContent": {"list": {"linkedList": [
        {"user": {"userId": "2"}, "linkmicId": "room_7703"},
    ]}}}))
    assert membership.linkmic_users == {"room_7703": "2"}
    activity = SpeakerActivity()
    activity.add({"infos": [{"uid": "room_7703", "talk": 1}, {"uid": "999", "talk": 1}]}, 1000)
    assert activity.active_users(membership.linkmic_users, "1", ["1", "2"]) == ["2"]
    # Numeric unknown stream identifiers are not silently treated as TikTok IDs.
    assert activity.active_users({}, "1", ["1", "999"]) == []


def test_host_anchor_maps_host_and_departed_guest_does_not_glow():
    activity = SpeakerActivity()
    activity.add({"app_data": {"anchor_link_mic_id": "host-stream", "grids": [
        {"uid_str": "host-stream", "talk": 1}, {"uid_str": "guest-stream", "talk": 1},
    ]}}, 10000)
    assert activity.active_users({"guest-stream": "2"}, "1", ["1"]) == ["1"]


def test_reassigned_stream_identity_is_not_attributed_to_the_wrong_person():
    events = pytest.importorskip("TikTokLive.events")
    tracker = PanelMembership("1", lambda *args: None)
    for uid in ("2", "3"):
        tracker.handle(events.LinkLayerEvent.from_dict({"listChangeContent": {"list": {"linkedList": [
            {"user": {"userId": uid}, "linkmicId": "reassigned"},
        ]}}}))
    assert "reassigned" not in tracker.linkmic_users
    assert "reassigned" in tracker.ambiguous_linkmic_ids


def test_gui_expiry_does_not_lose_the_recorded_silence_transition(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("core.speaker_activity.time.monotonic", lambda: now[0])
    activity = SpeakerActivity()
    activity.add({"infos": [{"uid": "a", "talk": 1}]}, 10000)
    now[0] = 2.0
    assert activity.expire()
    assert activity.current == []
    activity.add({"infos": [{"uid": "a", "talk": 0}]}, 11000)
    result = activity.export({"a": "1"}, "1", 10000)
    assert result[:2] == [{"time": 0, "ids": ["1"]}, {"time": 1, "ids": []}]


def test_stale_metadata_expires_and_out_of_order_data_cannot_relight():
    activity = SpeakerActivity()
    activity.add({"infos": [{"uid": "a", "talk": 1}]}, 10000)
    activity.add({"infos": [{"uid": "a", "talk": 1}]}, 13000)
    assert not activity.add({"infos": [{"uid": "a", "talk": 0}]}, 12000)
    assert activity.export({"a": "1"}, "1", 10000) == [
        {"time": 0, "ids": ["1"]}, {"time": 1.25, "ids": []},
        {"time": 3, "ids": ["1"]}, {"time": 4.25, "ids": []},
    ]


def test_same_speaker_relights_gui_after_wall_clock_expiry(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("core.speaker_activity.time.monotonic", lambda: now[0])
    activity = SpeakerActivity()
    payload = {"infos": [{"uid": "a", "talk": 1}]}
    activity.add(payload, 10000)
    now[0] = 2
    assert activity.expire()
    assert activity.add(payload, 10100)
    assert activity.current == ["a"]


def test_live_preview_clears_without_another_stream_chunk(tmp_path):
    from core.guest_panel import GuestPanelCapture, PanelTimeline
    capture = GuestPanelCapture.__new__(GuestPanelCapture)
    capture.stop_event, capture.ready = threading.Event(), threading.Event()
    capture.timeline = PanelTimeline({"id": "1", "name": "Host"})
    capture.timeline.linkmic_users = {"a": "1"}
    capture.timeline.activity.add({"infos": [{"uid": "a", "talk": 1}]}, 1000)
    capture.assets = tmp_path
    received = []
    capture.event_callback = received.append
    async def idle_feed():
        capture.ready.set()
        while not capture.stop_event.is_set():
            await asyncio.sleep(0.05)
    capture._listen = idle_feed
    capture.start()
    deadline = time.monotonic() + 2.5
    try:
        while time.monotonic() < deadline and not any(not e["timeline"]["active_speakers"] for e in received):
            time.sleep(0.05)
        assert received[0]["timeline"]["active_speakers"] == ["1"]
        assert received[-1]["timeline"]["active_speakers"] == []
    finally:
        capture.stop()


def test_roster_and_speech_are_combined_without_highlighting_absent_people():
    data = {"snapshots": [
        {"time": 0, "ids": ["1", "2"], "status": ""},
        {"time": 2, "ids": ["1"], "status": ""},
    ], "speaker_updates": [{"time": 1, "ids": ["2"]}, {"time": 3, "ids": ["1"]}]}
    frames = panel_frames(data, 4)
    assert [(f["time"], f["active_speakers"]) for f in frames] == [
        (0, []), (1, ["2"]), (2, []), (3, ["1"]),
    ]


@pytest.mark.parametrize("with_host_id", [True, False])
def test_saved_replay_preserves_guest_order_across_shuffles_departures_and_rejoins(with_host_id):
    data = {"snapshots": [
        {"time": 0, "ids": ["1", "2", "3"], "status": ""},
        {"time": 1, "ids": ["3", "1", "2"], "status": ""},
        {"time": 2, "ids": ["5", "3", "2", "4", "1"], "status": ""},
        {"time": 3, "ids": ["4", "1", "2", "5"], "status": ""},
        {"time": 4, "ids": ["3", "5", "2", "1", "4"], "status": ""},
    ], "speaker_updates": [
        {"time": 1.6, "ids": ["3"]},
        {"time": 3.6, "ids": ["2", "3"]},
    ]}
    if with_host_id:
        data["host_id"] = "1"
    frames = panel_frames(data, 5)
    assert [(f["time"], f["ids"], f["active_speakers"]) for f in frames] == [
        (0, ["1", "2", "3"], []),
        (1.6, ["1", "2", "3"], ["3"]),
        (2, ["1", "2", "3", "5", "4"], ["3"]),
        (3, ["1", "2", "5", "4"], []),
        (3.6, ["1", "2", "5", "4"], ["2"]),
        (4, ["1", "2", "5", "4", "3"], ["2", "3"]),
    ]
    # Rendering an old timeline must not rewrite its source snapshots.
    assert data["snapshots"][1]["ids"] == ["3", "1", "2"]


def test_rendered_speaking_border_switches_person_and_clears_on_silence(tmp_path):
    from core.guest_panel import draw_panel
    data = {"host_id": "1", "profiles": {"1": {"name": "Host"}, "2": {"name": "Guest"}}}
    def green_in_slot(active, slot):
        image = draw_panel(data, {"ids": ["1", "2"], "status": "", "active_speakers": active}, tmp_path, (720, 1280))
        region = image.crop((24 + slot * 228, 128, 238 + slot * 228, 342))
        return sum(1 for r, g, b, a in region.get_flattened_data() if g > 180 and g > r * 1.5 and g > b * 1.2)
    assert green_in_slot(["1"], 0) > 500
    assert green_in_slot(["1"], 1) == 0
    assert green_in_slot(["2"], 1) > 500
    assert green_in_slot([], 0) == 0


def test_fresh_audio_reports_take_precedence_over_stale_layout_flags():
    activity = SpeakerActivity()
    activity.add({"infos": [{"uid": "nick", "talk": 1}]}, 10000)
    assert not activity.add({"app_data": {"grids": [{"uid_str": "nick", "talk": 0}]}}, 10000)
    assert activity.current == ["nick"]
    activity.add({"infos": [{"uid": "nick", "talk": 0}]}, 10200)
    assert activity.current == []
    activity.add({"app_data": {"grids": [{"uid_str": "nick", "talk": 1}]}}, 12000)
    assert activity.current == ["nick"]


def test_sibling_join_identifier_maps_to_the_named_guest():
    from types import SimpleNamespace
    membership = PanelMembership("1", lambda *args: None)
    membership.remember_links(SimpleNamespace(join_room_direct_content=SimpleNamespace(
        joiner=SimpleNamespace(user_id=22), joiner_link_mic_id="channel_nick")))
    assert membership.linkmic_users == {"channel_nick": "22"}
