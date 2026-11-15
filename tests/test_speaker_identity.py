from types import SimpleNamespace

import pytest

from core.speaker_identity import SpeakerIdentity


def notice(scores, kind=1):
    return SimpleNamespace(score_type=kind, user_fan_ticket=[
        SimpleNamespace(user_id=int(uid), fan_ticket=score) for uid, score in scores.items()])


def stream(scores, kind=1):
    return {"mg_t": {"score_type": kind, "id_to_info_m": {
        uid: {"tkt_c": score} for uid, score in scores.items()}}}


def test_mid_session_guests_map_without_a_join_event_and_ties_stay_unresolved(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("core.speaker_identity.time.monotonic", lambda: now[0])
    identity = SpeakerIdentity("1")
    identity.tickets(notice({"1": 3062, "2": 111, "3": 124, "4": 100, "5": 100, "6": 0}))
    scores = {"anchor": 3062, "keebz": 111, "robin": 124, "a": 100, "b": 100, "new": 0}
    for at in (0, 1):
        now[0] = at
        assert not identity.metadata(stream(scores), "anchor")
    now[0] = 2
    assert identity.metadata(stream(scores), "anchor")
    assert identity.links == {"keebz": "2", "robin": "3", "new": "6"}


@pytest.mark.parametrize("scores,anchor", [
    ({"anchor": 99, "guest": 12}, "anchor"),  # host totals do not agree
    ({"anchor": 100, "guest": 12, "other": 9}, "anchor"),  # incomplete roster
    ({"anchor": 100, "guest": 12}, "missing"),
])
def test_mismatched_snapshots_never_establish_a_person(monkeypatch, scores, anchor):
    now = [0]
    monkeypatch.setattr("core.speaker_identity.time.monotonic", lambda: now[0])
    identity = SpeakerIdentity("1")
    identity.tickets(notice({"1": 100, "2": 12}))
    for at in (0, 1, 3):
        now[0] = at
        identity.metadata(stream(scores), anchor)
    assert identity.links == {}


def test_stale_scores_and_game_score_subsets_cannot_establish_an_identity(monkeypatch):
    now = [0]
    monkeypatch.setattr("core.speaker_identity.time.monotonic", lambda: now[0])
    identity = SpeakerIdentity("1")
    identity.tickets(notice({"1": 100, "2": 12}))
    for at in (16, 17, 19):
        now[0] = at
        identity.metadata(stream({"anchor": 100, "guest": 12}), "anchor")
    assert not identity.links
    identity.tickets(notice({"1": 100, "2": 12}, 2))
    assert identity.users_at == 0


def test_reassigned_stream_is_removed_and_never_flips_to_another_person(monkeypatch):
    now = [0]
    monkeypatch.setattr("core.speaker_identity.time.monotonic", lambda: now[0])
    identity = SpeakerIdentity("1")
    identity.tickets(notice({"1": 100, "2": 12}))
    for at in (0, 1, 2):
        now[0] = at
        identity.metadata(stream({"anchor": 100, "guest": 12}), "anchor")
    assert identity.links == {"guest": "2"}
    identity.tickets(notice({"1": 100, "3": 12}))
    assert identity.links == {}
    for at in (3, 4, 5):
        now[0] = at
        identity.metadata(stream({"anchor": 100, "guest": 12}), "anchor")
    assert identity.links == {}


def test_protocol_identity_overrides_inference_and_ambiguous_protocol_ids_are_excluded():
    from core.guest_panel import GuestPanelCapture, PanelTimeline
    capture = GuestPanelCapture.__new__(GuestPanelCapture)
    capture.timeline = PanelTimeline({"id": "1"})
    capture.membership = SimpleNamespace(linkmic_users={"a": "2"}, ambiguous_linkmic_ids={"b"})
    capture.speaker_identity = SimpleNamespace(links={"a": "3", "b": "4", "c": "5"})
    capture._sync_speaker_links()
    assert capture.timeline.linkmic_users == {"a": "2", "c": "5"}
