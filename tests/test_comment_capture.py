import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.comment_capture import CommentTimeline, comment_from_event


def actual_event(message_id="123", text="Hello 👩🏽‍💻\nNice stream!", **extra):
    events = pytest.importorskip("TikTokLive.events")
    return events.CommentEvent.from_dict({
        "common": {"msgId": message_id, "createTime": "1793586890"},
        "user": {"id": "123456789", "idStr": "123456789", "displayId": "viewer.name", "nickname": "Viewer 🌟"},
        "content": text, **extra,
    })


def test_installed_comment_event_uses_content_and_public_identity_only():
    parsed = comment_from_event(actual_event(signature="must-not-export", ecStreamerKey="private"))
    assert parsed == {"id": "123", "user_id": "123456789", "username": "viewer.name",
                      "name": "Viewer 🌟", "text": "Hello 👩🏽‍💻\nNice stream!"}
    assert "time" not in parsed


def test_reconnect_replay_dedup_does_not_collapse_repeated_text_with_new_id():
    timeline = CommentTimeline()
    first = comment_from_event(actual_event("101", "same text"))
    second = comment_from_event(actual_event("102", "same text"))
    assert timeline.add(first, 1)
    assert not timeline.add(first, 3)  # Replayed after reconnect.
    assert timeline.add(second, 4)
    assert [item["id"] for item in timeline.export()] == ["101", "102"]


@pytest.mark.parametrize("elapsed", [None, -1, float("inf"), float("nan"), "bad", False])
def test_discards_prestart_or_invalid_times_and_dedups_replayed_message(elapsed):
    timeline = CommentTimeline()
    item = comment_from_event(actual_event())
    assert not timeline.add(item, elapsed)
    assert not timeline.add(item, 0)
    assert timeline.add(comment_from_event(actual_event("124")), 0)
    assert timeline.export()[0]["time"] == 0


def test_reset_cdn_capture_clears_saved_comments_but_keeps_replay_ids():
    timeline = CommentTimeline()
    item = comment_from_event(actual_event())
    assert timeline.add(item, 50)
    timeline.reset()
    assert timeline.export() == []
    assert not timeline.add(item, 0.5)
    assert timeline.add(comment_from_event(actual_event("124")), 0.5)
    assert timeline.export()[0]["time"] == 0.5
    assert timeline.count == 1
    assert CommentTimeline().add(item, 0.5)  # A fresh recording has a fresh dedup window.


def test_missing_fields_and_deterministic_fallback_id():
    events = pytest.importorskip("TikTokLive.events")
    event = events.CommentEvent.from_dict({"content": "Anonymous comment", "common": {"createTime": "100"}})
    first, second = comment_from_event(event), comment_from_event(event)
    assert first == second
    assert first["id"].startswith("local-")
    assert first["name"] == "Viewer" and first["user_id"] == first["username"] == ""
    assert comment_from_event(events.CommentEvent()) is None
    assert comment_from_event(None) is None
    assert comment_from_event(SimpleNamespace(comment="Legacy text"))["text"] == "Legacy text"
    unknown = events.CommentEvent.from_dict({"content": "No identity hints"})
    assert comment_from_event(unknown)["id"] != comment_from_event(unknown)["id"]
    later = events.CommentEvent.from_dict({"content": "Anonymous comment", "common": {"createTime": "101"}})
    assert comment_from_event(later)["id"] != first["id"]


@pytest.mark.parametrize("user", [{"id": "22", "nickname": "Alice"}, None])
def test_actual_proto_minimal_or_missing_user_is_safe(user):
    events = pytest.importorskip("TikTokLive.events")
    event = events.CommentEvent.from_dict({"common": {"msgId": "12345"}, "user": user, "content": "hello"})
    parsed = comment_from_event(event)
    assert parsed["id"] == "12345" and parsed["text"] == "hello"
    assert parsed["username"] == ""
    assert parsed["name"] == ("Alice" if user else "Viewer")


def test_unavailable_legacy_alias_does_not_hide_canonical_handle():
    class User:
        id = 22
        display_id = "alice"
        nickname = "Alice"
        @property
        def unique_id(self):
            raise KeyError("Legacy alias unavailable")
    event = SimpleNamespace(common=SimpleNamespace(msg_id=12345), user=User(), content="hello")
    assert comment_from_event(event)["username"] == "alice"


def test_unicode_sanitization_preserves_emoji_and_text_newlines():
    event = SimpleNamespace(common=SimpleNamespace(msg_id=1),
        user=SimpleNamespace(id=7, unique_id="@viewer\x00", nickname="A\u202e\x1b\nB"),
        content="Hello\x00\x1b 👩🏽‍💻\r\nمرحباً\tworld\ud800\u202e")
    parsed = comment_from_event(event)
    assert parsed["username"] == "viewer"
    assert parsed["name"] == "A B"
    assert parsed["text"] == "Hello 👩🏽‍💻\nمرحباً world"
    assert comment_from_event(SimpleNamespace(content="\x00\x1b\ud800\u202e")) is None


def test_text_bounds_and_equal_arrival_timestamps_are_stable():
    timeline = CommentTimeline()
    for identity in ("first", "second", "third"):
        assert timeline.add({"id": identity, "text": "x" * 9000, "name": "n" * 500}, 2.5)
    exported = timeline.export()
    assert [item["id"] for item in exported] == ["first", "second", "third"]
    assert all(len(item["text"]) == 2000 and len(item["name"]) == 80 for item in exported)
    exported[0]["text"] = "changed copy"
    assert timeline.export()[0]["text"] != "changed copy"


def test_live_export_is_recent_only_and_duplicate_window_is_bounded(monkeypatch):
    monkeypatch.setattr("core.comment_capture.RECENT_IDS", 3)
    timeline = CommentTimeline()
    for index in range(40):
        assert timeline.add({"id": str(index), "text": str(index)}, index)
    assert len(timeline.export()) == 40
    assert [item["id"] for item in timeline.export(live=True)] == [str(i) for i in range(10, 40)]
    assert len(timeline._seen_ids) == len(timeline._recent_ids) == 3
    assert not timeline.add({"id": "39", "text": "replay"}, 41)
    assert timeline.add({"id": "0", "text": "older than bounded window"}, 42)


def test_sorts_out_of_order_times_stably_without_affecting_equal_arrivals():
    timeline = CommentTimeline()
    timeline.add({"id": "later", "text": "Later"}, 3)
    timeline.add({"id": "earlier-a", "text": "Earlier"}, 1)
    timeline.add({"id": "earlier-b", "text": "Earlier again"}, 1)
    assert [item["id"] for item in timeline.export()] == ["earlier-a", "earlier-b", "later"]
