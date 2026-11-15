import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.comment_panel import BG, WAITING, _width, _wrap_text, comment_frames, draw_comments


def comment(at, number, text="A real comment", name="Viewer"):
    return dict(time=at, id=str(number), user_id=str(number), username="viewer" + str(number), name=name, text=text)


def test_legacy_and_empty_timelines_show_waiting_without_invented_comments():
    assert comment_frames({}, 10) == [{"time": 0, "comments": [], "status": WAITING}]
    assert comment_frames({"comments": []}, 10) == [{"time": 0, "comments": [], "status": WAITING}]
    assert comment_frames({}, 0) == []


def test_replay_is_chronological_bounded_and_never_shows_future_comments():
    data = {"comments": [comment(1.019, 3), comment(0.019, 1), comment(0.071, 2), comment(2, 4)]}
    frames = comment_frames(data, 2, max_visible=2, updates_per_second=25)
    assert [(f["time"], [c["id"] for c in f["comments"]]) for f in frames] == [
        (0, []), (0.04, ["1"]), (0.08, ["1", "2"]), (1.04, ["2", "3"]),
    ]
    assert all(c["time"] <= f["time"] for f in frames for c in f["comments"])
    assert data["comments"][0]["time"] == 1.019


def test_same_frame_comments_merge_deduplicate_ids_and_allow_repeated_text():
    frames = comment_frames({"comments": [comment(0.001, 1), comment(0.002, 1), comment(0.003, 2)]}, 1)
    assert len(frames) == 2
    assert [c["id"] for c in frames[-1]["comments"]] == ["1", "2"]
    assert frames[-1]["time"] == 0.2


def test_comments_remain_on_screen_during_silence_and_connection_status_changes():
    frames = comment_frames({"comments": [comment(1, 1)], "comment_statuses": [
        {"time": 2, "status": "Comment updates reconnecting"},
        {"time": 3, "status": "Comment updates reconnecting"},
        {"time": 5, "status": ""},
    ]}, 100)
    assert [(f["time"], f["status"]) for f in frames] == [
        (0, WAITING), (1, ""), (2, "Comment updates reconnecting"), (5, ""),
    ]
    assert all(f["comments"][0]["id"] == "1" for f in frames[1:])


def test_invalid_and_outside_duration_entries_are_ignored():
    frames = comment_frames({"comments": [comment(float("nan"), 1), comment(1, 2, " \n "),
                                          comment(1.99, 3), comment(3, 4)],
                             "comment_statuses": [{"time": float("inf"), "status": "wrong"}]}, 2)
    assert frames == [{"time": 0, "comments": [], "status": WAITING}]


def test_many_comments_keep_only_thirty_and_do_not_retain_full_histories():
    frames = comment_frames({"comments": [comment(i / 10, i) for i in range(1000)]}, 101)
    assert len(frames[-1]["comments"]) == 30
    assert [c["id"] for c in frames[-1]["comments"]] == [str(i) for i in range(970, 1000)]
    assert max(len(f["comments"]) for f in frames) == 30


@pytest.mark.parametrize("text", [
    "https://example.com/" + "a" * 2000,
    "First line\nSecond line\n\nThird line",
    "مرحبا بالعالم 😊 Ελληνικά 中文 Привет 👨‍👩‍👧‍👦" * 10,
])
def test_unicode_newlines_and_long_tokens_wrap_within_the_available_width(text):
    lines = _wrap_text(text, 638, 25)
    assert 1 <= len(lines) <= 6
    assert all(_width(line, 25) <= 638 for line in lines)
    assert all("\n" not in line for line in lines)


def test_drawn_long_comments_are_opaque_and_stay_inside_panel_margins():
    snapshot = {"comments": [comment(i, i, ("https://example.com/" + "x" * 1000 + "\nمرحبا 😊") * 3)
                              for i in range(30)], "status": ""}
    image = draw_comments({}, snapshot)
    assert image.mode == "RGBA" and image.size == (720, 1280)
    assert image.getchannel("A").getextrema() == (255, 255)
    background = Image.new("RGBA", image.size, BG)
    for box in ((0, 128, 24, 1280), (697, 128, 720, 1280), (0, 1253, 720, 1280)):
        assert ImageChops.difference(image.crop(box), background.crop(box)).convert("RGB").getbbox() is None
    assert ImageChops.difference(image.crop((24, 128, 697, 1253)),
                                background.crop((24, 128, 697, 1253))).convert("RGB").getbbox() is not None


def test_newest_message_stays_at_bottom_and_old_messages_scroll_out_whole():
    newest = comment(99, 99, "Newest stays here")
    single = draw_comments({}, {"comments": [newest], "status": ""})
    many = draw_comments({}, {"comments": [comment(i, i, "Older text") for i in range(29)] + [newest], "status": ""})
    background = Image.new("RGBA", single.size, BG)
    occupied = ImageChops.difference(single.crop((0, 128, 720, 1280)),
                                    background.crop((0, 128, 720, 1280))).convert("RGB").getbbox()
    top = 128 + occupied[1]
    assert single.crop((0, top, 720, 1280)).tobytes() == many.crop((0, top, 720, 1280)).tobytes()


def test_rtl_emoji_and_newline_comment_renders_visible_text_inside_the_card():
    image = draw_comments({}, {"comments": [comment(0, 1, "مرحبا بالعالم 😊\nΕλληνικά 中文 Привет 👨‍👩‍👧‍👦", "رُهام")], "status": ""})
    text_pixels = [(x, y) for y in range(1150, 1252) for x in range(41, 679)
                   if all(value > 195 for value in image.getpixel((x, y))[:3])]
    assert len(text_pixels) > 100
    assert all(41 <= x < 679 and 1150 <= y < 1252 for x, y in text_pixels)


def test_default_update_rate_groups_bursts_without_revealing_future_messages():
    frames = comment_frames({"comments": [comment(i / 100, i) for i in range(1, 101)]}, 2)
    assert [f["time"] for f in frames] == [0, 0.2, 0.4, 0.6, 0.8, 1.0]
    assert all(c["time"] <= f["time"] for f in frames for c in f["comments"])


@pytest.mark.parametrize("rate", [0, -1, float("inf"), "invalid"])
def test_invalid_update_rate_is_rejected(rate):
    with pytest.raises(ValueError):
        comment_frames({}, 1, updates_per_second=rate)


def test_tiny_panel_is_rejected_instead_of_drawing_outside_bounds():
    with pytest.raises(ValueError):
        draw_comments({}, {"comments": []}, (50, 100))
