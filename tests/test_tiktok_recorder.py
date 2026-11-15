import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.tiktok_recorder import TikTokRecorder  # noqa: E402
from utils.custom_exceptions import TikTokRecorderError, UserLiveError  # noqa: E402
from utils.enums import Mode, TikTokError  # noqa: E402
from utils.recorder_config import RecorderConfig  # noqa: E402


class FakeTikTokAPI:
    def __init__(self, blacklisted=True):
        self.blacklisted = blacklisted
        self.calls = []

    def is_country_blacklisted(self):
        self.calls.append("is_country_blacklisted")
        return self.blacklisted

    def get_room_id_from_user(self, user):
        self.calls.append(f"get_room_id_from_user:{user}")
        return "1234567890"

    def get_user_from_room_id(self, room_id):
        self.calls.append(f"get_user_from_room_id:{room_id}")
        return "creator"

    def get_sec_uid(self):
        self.calls.append("get_sec_uid")
        return "sec_uid"

    def is_room_alive(self, room_id):
        self.calls.append(f"is_room_alive:{room_id}")
        return True


def test_setup_resolves_room_id_before_country_check_for_manual_user():
    recorder = TikTokRecorder(
        RecorderConfig(mode=Mode.MANUAL, user="creator", cookies={})
    )
    fake_api = FakeTikTokAPI(blacklisted=True)
    recorder.tiktok = fake_api

    recorder._setup()

    assert recorder.room_id == "1234567890"
    assert fake_api.calls == [
        "get_room_id_from_user:creator",
        "is_country_blacklisted",
        "is_room_alive:1234567890",
    ]


def test_setup_keeps_followers_country_check_before_sec_uid():
    recorder = TikTokRecorder(RecorderConfig(mode=Mode.FOLLOWERS, cookies={}))
    fake_api = FakeTikTokAPI(blacklisted=True)
    recorder.tiktok = fake_api

    with pytest.raises(TikTokRecorderError, match="Captcha required"):
        recorder._setup()

    assert fake_api.calls == ["is_country_blacklisted"]


def test_setup_keeps_automatic_mode_blocked_after_room_resolution():
    recorder = TikTokRecorder(
        RecorderConfig(mode=Mode.AUTOMATIC, user="creator", cookies={})
    )
    fake_api = FakeTikTokAPI(blacklisted=True)
    recorder.tiktok = fake_api

    with pytest.raises(TikTokRecorderError, match="Automatic mode is available"):
        recorder._setup()

    assert recorder.room_id == "1234567890"
    assert fake_api.calls == [
        "get_room_id_from_user:creator",
        "is_country_blacklisted",
    ]


def test_setup_keeps_manual_room_id_allowed_when_country_check_is_blocked():
    recorder = TikTokRecorder(
        RecorderConfig(mode=Mode.MANUAL, room_id="1234567890", cookies={})
    )
    fake_api = FakeTikTokAPI(blacklisted=True)
    recorder.tiktok = fake_api

    recorder._setup()

    assert recorder.room_id == "1234567890"
    assert fake_api.calls == [
        "get_user_from_room_id:1234567890",
        "is_country_blacklisted",
        "is_room_alive:1234567890",
    ]


@pytest.mark.parametrize("cookie_name", ["sessionid_ss", "sessionid", "sid_tt"])
def test_saved_session_allows_automatic_target_access_despite_generic_redirect(cookie_name):
    recorder = TikTokRecorder(RecorderConfig(
        mode=Mode.AUTOMATIC, user="creator", cookies={cookie_name: "test-session-sentinel"},
    ))
    fake_api = FakeTikTokAPI(blacklisted=True)
    recorder.tiktok = fake_api
    started = []
    recorder.start_recording = lambda user, room_id: started.append((user, room_id))

    recorder._setup()
    recorder.manual_mode()

    assert "is_country_blacklisted" not in fake_api.calls
    assert fake_api.calls == [
        "get_room_id_from_user:creator",
        "is_room_alive:1234567890",
        "is_room_alive:1234567890",
    ]
    assert started == [("creator", "1234567890")]


def test_saved_session_does_not_suppress_target_room_access_failure():
    recorder = TikTokRecorder(RecorderConfig(
        mode=Mode.AUTOMATIC, user="creator", cookies={"sessionid_ss": "expired-session-sentinel"},
    ))
    fake_api = FakeTikTokAPI(blacklisted=True)
    recorder.tiktok = fake_api
    recorder._setup()
    failure = UserLiveError(TikTokError.LIVE_RESTRICTION)
    def denied(room_id):
        fake_api.calls.append(f"is_room_alive:{room_id}")
        raise failure
    fake_api.is_room_alive = denied

    with pytest.raises(UserLiveError) as caught:
        recorder.manual_mode()
    assert caught.value is failure
    assert "is_country_blacklisted" not in fake_api.calls


@pytest.mark.parametrize("cookies", [None, {}, {"sessionid_ss": ""}, {"sessionid": "  "},
                                    {"sid_tt": "", "tt-target-idc": "useast2a"}])
def test_empty_saved_login_keeps_the_automatic_country_guard(cookies):
    recorder = TikTokRecorder(RecorderConfig(mode=Mode.AUTOMATIC, user="creator", cookies=cookies))
    fake_api = FakeTikTokAPI(blacklisted=True)
    recorder.tiktok = fake_api

    with pytest.raises(TikTokRecorderError, match="Automatic mode is available"):
        recorder._setup()
    assert fake_api.calls == ["get_room_id_from_user:creator", "is_country_blacklisted"]


def test_generic_access_restriction_does_not_claim_a_private_live():
    message = str(TikTokError.LIVE_RESTRICTION)
    assert "restricted" in message
    assert "private" not in message.lower()
    assert "TikTok login setup" in message
    assert "refresh" in message
    assert "private" in str(TikTokError.ACCOUNT_PRIVATE).lower()
