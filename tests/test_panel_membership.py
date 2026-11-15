import sys
import asyncio
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
events = pytest.importorskip("TikTokLive.events")
from core.panel_membership import PanelMembership, stable_panel_ids
from core.guest_panel import PanelTimeline
from core.guest_panel import GuestPanelCapture


def seed(tracker, ids, stamp=100):
    event = events.LinkMicFanTicketMethodEvent.from_dict({"fanTicketRoomNotice": {
        "scoreType": "ALL_FAN_TICKET", "eventTime": str(stamp),
        "userFanTicket": [{"userId": str(uid)} for uid in ids],
    }})
    tracker.tickets(event.fan_ticket_room_notice)


def layer(**fields):
    return events.LinkLayerEvent.from_dict({"channelId": "7", **fields})


def test_stable_order_keeps_survivors_and_appends_multiple_new_guests():
    current = ["1", "2", "3"]
    incoming = ["5", "3", "1", "4", "2", "5"]
    assert stable_panel_ids(current, incoming, "1") == ["1", "2", "3", "5", "4"]
    assert current == ["1", "2", "3"]
    assert incoming == ["5", "3", "1", "4", "2", "5"]


def test_stable_order_compacts_departures_and_appends_rejoins():
    ids = stable_panel_ids([], [3, 2, 1], "1")
    assert ids == ["1", "3", "2"]
    ids = stable_panel_ids(ids, [2, 1], "1")
    assert ids == ["1", "2"]
    ids = stable_panel_ids(ids, [3, 1, 2], "1")
    assert ids == ["1", "2", "3"]
    assert stable_panel_ids(ids, ["invalid", 0, None, 2, 2], "1") == ["1", "2"]


def test_membership_snapshots_cannot_reshuffle_survivors_or_restore_stale_departures():
    published = []
    tracker = PanelMembership("1", lambda ids, source: published.append(list(ids)))
    def snapshot(version, ids):
        tracker.handle(layer(version=str(version), listChangeContent={"list": {
            "linkedList": [{"user": {"userId": str(uid)}} for uid in ids],
        }}))
    snapshot(1, [2, 3])
    snapshot(2, [4, 3, 2, 5])
    assert tracker.ids == ["1", "2", "3", "4", "5"]
    tracker.handle(layer(version="3", leaveContent={"leaver": {"userId": "3"}}))
    assert tracker.ids == ["1", "2", "4", "5"]
    snapshot(2, [5, 3, 4, 2])
    snapshot(3, [5, 3, 4, 2])
    assert tracker.ids == ["1", "2", "4", "5"]
    assert "3" in tracker.removed
    snapshot(4, [5, 2, 4])
    assert tracker.ids == ["1", "2", "4", "5"]
    tracker.handle(layer(version="5", joinDirectContent={"joiner": {"user": {"userId": "3"}}}))
    assert tracker.ids == ["1", "2", "4", "5", "3"]
    assert "3" not in tracker.removed
    assert published[0] == ["1", "2", "3"]


def test_departures_stay_removed_through_cached_scores_partial_updates_and_reconnect():
    tracker = PanelMembership("1", lambda *args: None)
    seed(tracker, [1, 2, 3])
    tracker.handle(layer(version="3", leaveContent={"leaver": {"userId": "2"}}))
    assert tracker.ids == ["1", "3"]
    # Reconnecting uses the same membership state. Cached notices mustn't revive 2.
    seed(tracker, [1, 2, 3], stamp=101)
    tracker.handle(events.LinkStateEvent.from_dict({"stateType": "STATE_AUDIO_MUTE",
        "userStates": [{"user": {"userId": "2"}}]}))
    assert tracker.ids == ["1", "3"]
    tracker.handle(layer(version="2", listChangeContent={"list": {
        "linkedList": [{"user": {"userId": "2"}}]}}))
    assert tracker.ids == ["1", "3"]
    for fields in ({}, {"version": "3"}):
        tracker.handle(layer(**fields, listChangeContent={"list": {
            "linkedList": [{"user": {"userId": "2"}}, {"user": {"userId": "3"}}]}}))
        assert tracker.ids == ["1", "3"]
    tracker.handle(layer(version="4", joinDirectContent={"joiner": {"user": {"userId": "2"}}}))
    assert tracker.ids == ["1", "3", "2"]


def test_invite_and_waiting_list_never_add_guests_but_linked_snapshot_does():
    tracker = PanelMembership("1", lambda *args: None)
    tracker.handle(layer(version="1", inviteContent={"invitee": {"userId": "4"}}))
    assert tracker.ids == ["1"]
    tracker.handle(layer(version="2", listChangeContent={"list": {
        "linkedList": [{"user": {"userId": "2"}}, {"user": {"userId": "3"}}],
        "appliedList": [{"user": {"userId": "4"}}],
        "invitedList": [{"user": {"userId": "5"}}],
    }}))
    assert tracker.ids == ["1", "2", "3"]
    # Updating just one user's mute state doesn't remove the others.
    tracker.handle(events.LinkStateEvent.from_dict({"stateType": "STATE_AUDIO_MUTE",
        "userStates": [{"user": {"userId": "3"}}]}))
    assert tracker.ids == ["1", "2", "3"]


def test_empty_roster_kick_finish_and_legacy_departure():
    tracker = PanelMembership("1", lambda *args: None)
    seed(tracker, [1, 2, 3])
    tracker.handle(layer(version="1", kickOutContent={"offliner": {"userId": "2"}}))
    assert tracker.ids == ["1", "3"]
    tracker.handle(events.LinkEvent.from_dict({"version": "1", "leaveContent": {"userId": "3"}}))
    assert tracker.ids == ["1"]
    tracker.handle(layer(version="2", joinDirectContent={"joiner": {"user": {"userId": "4"}}}))
    tracker.handle(layer(version="3", listChangeContent={"list": {"linkedList": []}}))
    assert tracker.ids == ["1"]
    tracker.handle(layer(version="4", joinDirectContent={"joiner": {"user": {"userId": "4"}}}))
    tracker.handle(layer(version="5", finishContent={}))
    assert tracker.ids == ["1"]


def test_legacy_connected_list_excludes_waiting_and_finished_users():
    tracker = PanelMembership("1", lambda *args: None)
    tracker.handle(events.LinkEvent.from_dict({"linkedListChangeContent": {"linkedUsers": [
        {"user": {"id": "2"}, "linkStatus": "STATUS_LINKED"},
        {"user": {"id": "3"}, "linkStatus": "STATUS_WAITING"},
        {"user": {"id": "4"}, "linkStatus": "STATUS_FINISHED"},
    ]}}))
    assert tracker.ids == ["1", "2"]


def test_cdn_retry_rebases_timeline_and_profile_completion_does_not_rejoin(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("core.guest_panel.time.monotonic", lambda: now[0])
    timeline = PanelTimeline({"id": "1", "name": "Host"})
    tracker = PanelMembership("1", lambda ids, source: timeline.update(ids, "", source))
    seed(tracker, [1, 2])
    timeline.mark_start()
    now[0] = 104
    tracker.handle(layer(leaveContent={"leaver": {"userId": "2"}}))
    timeline.profiles["2"] = {"id": "2", "name": "Late profile result"}
    assert timeline.export()["snapshots"][-1]["ids"] == ["1"]
    # CDN retry truncates the first output file. Keep only the current baseline.
    timeline.reset_start()
    now[0] = 110
    timeline.mark_start()
    now[0] = 112
    tracker.handle(layer(joinDirectContent={"joiner": {"user": {"userId": "3"}}}))
    assert [(s["time"], s["ids"]) for s in timeline.export()["snapshots"]] == [
        (0, ["1"]), (2, ["1", "3"]),
    ]


def test_event_feed_reconnects_without_losing_departures(monkeypatch):
    import TikTokLive

    capture = GuestPanelCapture.__new__(GuestPanelCapture)
    capture.username, capture.room_id = "creator", "123"
    capture.timeline = PanelTimeline({"id": "1", "name": "Host"})
    capture.timeline.mark_start()
    capture.timeline.profiles.update({str(i): {"id": str(i), "name": str(i)} for i in (2, 3, 4)})
    capture.membership = PanelMembership("1", capture._accept_roster)
    from core.speaker_identity import SpeakerIdentity
    capture.speaker_identity = SpeakerIdentity("1")
    capture.stop_event, capture.ready = threading.Event(), threading.Event()
    capture.last_ids, capture.pending_profiles, capture.profile_attempts = [], set(), {}
    instances = []

    class Client:
        def __init__(self, **kwargs):
            self.listeners = {}
            self.web = self
            instances.append(self)

        def add_listener(self, kind, callback):
            self.listeners[kind] = callback

        async def start(self, **kwargs):
            # Initial connection and reconnection both replay the old scores.
            notice = events.LinkMicFanTicketMethodEvent.from_dict({"fanTicketRoomNotice": {
                "scoreType": "ALL_FAN_TICKET", "userFanTicket": [
                    {"userId": str(i)} for i in (1, 2, 3)],
            }})
            await self.listeners[events.LinkMicFanTicketMethodEvent](notice)
            if len(instances) == 1:
                await self.listeners[events.LinkLayerEvent](layer(version="1", leaveContent={"leaver": {"userId": "2"}}))
            else:
                await self.listeners[events.LinkLayerEvent](layer(version="2", joinDirectContent={"joiner": {"user": {"userId": "4"}}}))
                capture.stop_event.set()
            return asyncio.create_task(asyncio.sleep(0))

        async def disconnect(self):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(TikTokLive, "TikTokLiveClient", Client)
    async def listen_with_deadline():
        await asyncio.wait_for(capture._listen(), timeout=8)
    asyncio.run(listen_with_deadline())
    assert len(instances) == 2
    assert capture.last_ids == ["1", "3", "4"]
    assert any(s["source"] == "disconnected" for s in capture.timeline.snapshots)
