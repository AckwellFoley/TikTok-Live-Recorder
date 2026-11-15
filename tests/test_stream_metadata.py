import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.stream_metadata import StreamMetadataDecoder, parse_sei_nal, strip_emulation_prevention


HEADER = b"FLV\x01\x05\0\0\0\x09\0\0\0\0"


def tag(kind, payload, timestamp=0):
    header = bytes([kind]) + len(payload).to_bytes(3, "big")
    header += (timestamp & 0xffffff).to_bytes(3, "big") + bytes([timestamp >> 24]) + b"\0\0\0"
    return header + payload + (11 + len(payload)).to_bytes(4, "big")


def text(value):
    data = value.encode("utf-8")
    return b"\x02" + len(data).to_bytes(2, "big") + data


def object_data(data, ecma=False):
    result = b"\x08" + len(data).to_bytes(4, "big") if ecma else b"\x03"
    for key, value in data.items():
        name = key.encode()
        result += len(name).to_bytes(2, "big") + name
        if isinstance(value, str):
            result += text(value)
        elif isinstance(value, bool):
            result += b"\x01" + bytes([value])
        elif isinstance(value, dict):
            result += object_data(value)
        else:
            result += b"\x00" + struct.pack(">d", value)
    return result + b"\0\0\x09"


def length_field(value):
    return b"\xff" * (value // 255) + bytes([value % 255])


def sei(kind, data):
    if isinstance(data, dict):
        data = json.dumps(data).encode()
    if kind == 5:
        data = b"0123456789abcdef" + data
    rbsp = length_field(kind) + length_field(len(data)) + data + b"\x80"
    # Real Annex-B/AVC NAL data inserts these bytes before RBSP delivery.
    encoded = bytearray()
    zeros = 0
    for value in rbsp:
        if zeros >= 2 and value <= 3:
            encoded.append(3)
            zeros = 0
        encoded.append(value)
        zeros = zeros + 1 if value == 0 else 0
    return b"\x06" + bytes(encoded)


def avc(nal, length_size=4, composition=0):
    return b"\x27\x01" + composition.to_bytes(3, "big", signed=True) + len(nal).to_bytes(length_size, "big") + nal


def test_script_metadata_every_possible_split_boundary():
    expected = {"width": 720, "stereo": True, "data": {"text": "hello"}}
    data = HEADER + tag(18, text("onMetaData") + object_data(expected, ecma=True), 0x12345678)
    for split in range(len(data) + 1):
        decoder = StreamMetadataDecoder()
        events = decoder.feed(data[:split]) + decoder.feed(data[split:])
        assert len(events) == 1
        assert events[0]["data"] == expected
        assert events[0]["timestamp_ms"] == 0x12345678


def test_set_data_frame_and_on_text_data_json():
    data = HEADER + tag(18, text("@setDataFrame") + text("onTextData") + text('JSON{"infos":[{"uid":"guest","talk":1}]}\0'), 70)
    event, = StreamMetadataDecoder().feed(data)
    assert event["data"]["infos"][0]["uid"] == "guest"
    assert event["name"] == "onTextData"


@pytest.mark.parametrize("kind", [5, 100, 101])
def test_sei_json_payload_types_and_extended_lengths(kind):
    data = {"infos": [{"uid": "guest", "talk": 1, "volume": 14}], "padding": "x" * 400}
    raw = json.dumps(data).encode()
    event, = parse_sei_nal(sei(kind, b"JSON" + raw + b"\0"), 999)
    assert event["data"] == data
    assert event["timestamp_ms"] == 999
    assert event["sei_type"] == kind


def test_actual_tiktok_app_data_remains_json_encoded_string():
    app_data = json.dumps({"grids": [{"uid_str": "room_linkmic_123", "talk": 1, "talk_volume": 14, "mute_a": False}]})
    outer = {"app_data": app_data, "canvas": {"w": 720, "h": 1280}, "regions": []}
    event, = parse_sei_nal(b"\0\0\0\x01" + sei(100, b"JSON" + json.dumps(outer).encode() + b"\0"))
    assert event["data"]["app_data"] == app_data
    assert json.loads(event["data"]["app_data"])["grids"][0]["talk_volume"] == 14


@pytest.mark.parametrize("length_size", [1, 2, 4])
def test_avc_configuration_and_split_video_tags(length_size):
    config = b"\x17\0\0\0\0" + b"\x01\x64\0\x1f" + bytes([0xfc | (length_size - 1)]) + b"\xe0\0"
    nal = sei(100, {"talk": 1})
    data = HEADER + tag(9, config) + tag(9, avc(nal, length_size, composition=-5), 100)
    decoder = StreamMetadataDecoder()
    events = []
    for value in data:
        events.extend(decoder.feed(bytes([value])))
    assert len(events) == 1
    assert events[0]["timestamp_ms"] == 95


def test_several_sei_messages_and_irrelevant_nals():
    first = sei(100, {"talk": 1})
    second = sei(101, {"loudness": {"rms": 3}})
    nal = first[:-1] + second[1:]
    assert len(parse_sei_nal(nal)) == 2
    assert parse_sei_nal(b"\x65" + first[1:]) == []
    assert parse_sei_nal(b"\x86" + first[1:]) == []


def test_emulation_prevention_only_strips_inserted_bytes():
    assert strip_emulation_prevention(b"\0\0\x03\0\0\0\x03\x01\0\0\x03\x04") == b"\0\0\0\0\0\x01\0\0\x03\x04"


def test_malformed_and_oversized_tags_do_not_grow_buffer_or_lose_later_metadata():
    decoder = StreamMetadataDecoder(max_tag_bytes=256)
    large = tag(9, b"x" * 2000)
    valid = tag(18, text("onMetaData") + object_data({"talk": 1}), 30)
    events = []
    for start in range(0, len(HEADER + large + valid), 13):
        events.extend(decoder.feed((HEADER + large + valid)[start:start + 13]))
        assert len(decoder._buffer) <= 256 + 15
    assert len(events) == 1
    assert events[0]["data"] == {"talk": 1}
    assert decoder.feed(tag(18, b"\x02\xff\xffbad") + valid)[0]["timestamp_ms"] == 30
    assert parse_sei_nal(b"\x06\x64\xff\xff\xff") == []


def test_junk_and_bad_headers_are_bounded_and_can_resynchronize():
    decoder = StreamMetadataDecoder()
    assert decoder.feed(b"garbage" * 100_000) == []
    assert len(decoder._buffer) <= 2
    valid = HEADER + tag(18, text("onMetaData") + object_data({"ok": True}))
    assert decoder.feed(valid)[0]["data"] == {"ok": True}


def test_callback_failure_never_interrupts_decoding():
    def fail(event):
        raise RuntimeError("bad UI callback")
    decoder = StreamMetadataDecoder(fail)
    events = decoder.feed(HEADER + tag(9, avc(sei(100, {"talk": 1}))))
    assert len(events) == 1
    assert decoder.callback_errors == 1


def test_first_media_clock_ignores_configs_and_is_ready_before_video_metadata():
    seen = []
    decoder = StreamMetadataDecoder(lambda event: seen.append(decoder.first_media_timestamp_ms))
    config = b"\x17\0\0\0\0\x01\x64\0\x1f\xff\xe0\0"
    headers = HEADER + tag(9, config) + tag(8, b"\xaf\0\x12\x10")
    headers += tag(18, text("onMetaData") + object_data({"width": 720}))
    decoder.feed(headers)
    assert decoder.first_media_timestamp_ms is None
    # A metadata-only AVC packet does not establish an actual media frame.
    decoder.feed(tag(9, avc(sei(100, {"metadata_only": True})), 0))
    assert decoder.first_media_timestamp_ms is None
    nal = sei(100, {"talk": 1})
    payload = avc(nal, composition=133) + (2).to_bytes(4, "big") + b"\x65\x01"
    decoder.feed(tag(9, payload, 3159625))
    assert decoder.first_media_timestamp_ms == 3159758
    assert seen[-1] == 3159758
    decoder.feed(tag(8, b"\xaf\x01raw", 3159658))
    assert decoder.first_media_timestamp_ms == 3159658
    assert StreamMetadataDecoder().first_media_timestamp_ms is None
