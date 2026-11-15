"""Bounded, incremental decoding of FLV script metadata and AVC SEI JSON.

This module only decodes stream-provided metadata. It never tries to infer a
speaker from mixed audio. ``feed`` returns event dictionaries and optionally
delivers each event to a callback. The original media bytes are not modified.
"""

import json
import struct


MAX_METADATA_BYTES = 256 * 1024
MAX_TAG_BYTES = 2 * 1024 * 1024
MAX_HEADER_BYTES = 4096
MAX_AMF_DEPTH = 16
MAX_AMF_VALUES = 2048
MAX_EVENTS_PER_FEED = 256


class _DecodeError(ValueError):
    pass


class _AMF0:
    """The AMF0 value types used by ordinary FLV script-data tags."""

    def __init__(self, data):
        if len(data) > MAX_METADATA_BYTES:
            raise _DecodeError("Script tag is too large")
        self.data = memoryview(data)
        self.pos = 0
        self.remaining_values = MAX_AMF_VALUES

    def take(self, count):
        if count < 0 or self.pos + count > len(self.data):
            raise _DecodeError("Truncated AMF0 value")
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result

    def unsigned(self, count):
        return int.from_bytes(self.take(count), "big")

    def text(self, long=False):
        size = self.unsigned(4 if long else 2)
        if size > MAX_METADATA_BYTES:
            raise _DecodeError("AMF0 string is too large")
        return bytes(self.take(size)).decode("utf-8", errors="replace")

    def properties(self, depth):
        result = {}
        while self.pos < len(self.data):
            if bytes(self.data[self.pos:self.pos + 3]) == b"\0\0\x09":
                self.pos += 3
                return result
            name = self.text()
            result[name] = self.value(depth + 1)
        raise _DecodeError("Unterminated AMF0 object")

    def value(self, depth=0):
        self.remaining_values -= 1
        if depth > MAX_AMF_DEPTH or self.remaining_values < 0:
            raise _DecodeError("AMF0 nesting/value limit")
        kind = self.unsigned(1)
        if kind == 0:
            return struct.unpack(">d", self.take(8))[0]
        if kind == 1:
            return bool(self.unsigned(1))
        if kind == 2:
            return self.text()
        if kind == 3:
            return self.properties(depth)
        if kind in (5, 6):
            return None
        if kind == 7:
            self.take(2)  # References are not needed to recover JSON metadata.
            return None
        if kind == 8:
            self.take(4)  # ECMA-array count is only advisory in real FLV files.
            return self.properties(depth)
        if kind == 10:
            count = self.unsigned(4)
            if count > self.remaining_values:
                raise _DecodeError("AMF0 array is too large")
            return [self.value(depth + 1) for _ in range(count)]
        if kind == 11:
            value = struct.unpack(">d", self.take(8))[0]
            self.take(2)
            return {"date_ms": value}
        if kind in (12, 15):
            return self.text(long=True)
        if kind == 16:
            name = self.text()
            return {"__class__": name, **self.properties(depth)}
        raise _DecodeError("Unsupported AMF0 value type")


def _extract_json(payload):
    """Accept prefixed JSON (for example ``JSON{...}\0``), with bounded work."""
    if not payload or len(payload) > MAX_METADATA_BYTES:
        return None
    text = bytes(payload).decode("utf-8", errors="replace")
    decoder = json.JSONDecoder()
    attempts = 0
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        attempts += 1
        if attempts > 32:
            break
        try:
            value, _ = decoder.raw_decode(text, index)
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return {"items": value}
    return None


def _script_data(payload):
    try:
        decoder = _AMF0(payload)
        name = decoder.value()
        if name == "@setDataFrame":
            name = decoder.value()
        if name not in ("onMetaData", "onTextData", "onCuePoint", "onSEIData"):
            return None
        value = decoder.value()
        if isinstance(value, str):
            value = _extract_json(value.encode("utf-8")) or {"text": value}
        if isinstance(value, dict):
            return name, value
    except (ValueError, IndexError, struct.error, RecursionError):
        pass
    return None


def strip_emulation_prevention(data):
    result = bytearray()
    zeros = 0
    for index, value in enumerate(data):
        if zeros >= 2 and value == 3 and index + 1 < len(data) and data[index + 1] <= 3:
            zeros = 0
            continue
        result.append(value)
        zeros = zeros + 1 if value == 0 else 0
    return bytes(result)


def parse_sei_nal(nal, timestamp_ms=0):
    """Decode JSON from one complete H.264 SEI NAL, including its NAL header.

    An optional Annex-B start code is accepted. Supports unregistered user data
    (type 5, after its UUID) and the JSON-carrying types 100/101 seen in TikTok
    samples. Nested JSON-encoded strings such as ``app_data`` remain strings.
    """
    if len(nal) > MAX_TAG_BYTES:
        return []
    if nal.startswith(b"\0\0\0\x01"):
        nal = nal[4:]
    elif nal.startswith(b"\0\0\x01"):
        nal = nal[3:]
    if not nal or nal[0] & 0x80 or nal[0] & 0x1f != 6:
        return []
    rbsp = strip_emulation_prevention(nal[1:])
    position = 0
    result = []
    for _ in range(64):
        if position >= len(rbsp):
            break
        if rbsp[position] == 0x80 and not any(rbsp[position + 1:]):
            break
        values = []
        for _ in range(2):
            total = 0
            for _ in range(1024):
                if position >= len(rbsp):
                    return result
                part = rbsp[position]
                position += 1
                total += part
                if total > MAX_METADATA_BYTES:
                    return result
                if part != 255:
                    break
            else:
                return result
            values.append(total)
        kind, size = values
        if position + size > len(rbsp):
            break
        payload = rbsp[position:position + size]
        position += size
        if kind == 5:
            if len(payload) < 16:
                continue
            payload = payload[16:]
        elif kind not in (100, 101):
            continue
        value = _extract_json(payload)
        if value is not None:
            result.append({"timestamp_ms": int(timestamp_ms), "source": "avc_sei",
                           "sei_type": kind, "data": value})
    return result


class StreamMetadataDecoder:
    """Incremental FLV parser safe to feed directly from download chunks.

    ``feed(chunk)`` returns a bounded list of metadata events. With a callback,
    every decoded event is also delivered immediately. Oversized tags are
    skipped incrementally rather than accumulated. Malformed metadata and
    callback failures never modify or interrupt the media download.
    """

    def __init__(self, callback=None, max_tag_bytes=MAX_TAG_BYTES):
        self.callback = callback
        self.max_tag_bytes = max(64, min(int(max_tag_bytes), MAX_TAG_BYTES))
        self._buffer = bytearray()
        self._header_done = False
        self._skip_remaining = 0
        self._nal_length_size = 4
        self._first_media_timestamp_ms = None
        self.callback_errors = 0

    @property
    def first_media_timestamp_ms(self):
        """Earliest observed coded-video PTS or AAC raw-audio timestamp.

        Configuration and script tags do not establish the media clock. A
        later-arriving AAC packet can lower this value when video reordering
        made the first observed coded-video PTS slightly later.
        """
        return self._first_media_timestamp_ms

    def _mark_media(self, timestamp):
        if self._first_media_timestamp_ms is None or timestamp < self._first_media_timestamp_ms:
            self._first_media_timestamp_ms = timestamp

    def _deliver(self, event, events):
        if len(events) < MAX_EVENTS_PER_FEED:
            events.append(event)
        if self.callback is not None:
            try:
                self.callback(event)
            except Exception:
                self.callback_errors += 1

    def feed(self, chunk):
        events = []
        data = memoryview(chunk)
        # Never append an arbitrarily large input chunk before parsing it.
        for start in range(0, len(data), 64 * 1024):
            self._buffer.extend(data[start:start + 64 * 1024])
            self._parse(events)
        return events

    def _parse(self, events):
        while True:
            if self._skip_remaining:
                count = min(self._skip_remaining, len(self._buffer))
                del self._buffer[:count]
                self._skip_remaining -= count
                if self._skip_remaining:
                    return
            if not self._header_done:
                if len(self._buffer) < 9:
                    return
                if self._buffer[:3] != b"FLV":
                    found = self._buffer.find(b"FLV", 1)
                    if found < 0:
                        del self._buffer[:-2]
                        return
                    del self._buffer[:found]
                    continue
                offset = int.from_bytes(self._buffer[5:9], "big")
                if self._buffer[3] != 1 or not 9 <= offset <= MAX_HEADER_BYTES:
                    del self._buffer[0]
                    continue
                if len(self._buffer) < offset + 4:
                    return
                del self._buffer[:offset + 4]
                self._header_done = True
                self._nal_length_size = 4
            if len(self._buffer) < 11:
                return
            if self._buffer[:3] == b"FLV":
                self._header_done = False
                continue
            kind = self._buffer[0] & 0x1f
            size = int.from_bytes(self._buffer[1:4], "big")
            timestamp = int.from_bytes(self._buffer[4:7], "big") | self._buffer[7] << 24
            # Filtered/encrypted tags and irrelevant media need no buffering.
            supported = kind in (8, 9, 18) and not self._buffer[0] & 0x20
            if not supported or size > self.max_tag_bytes or (kind == 18 and size > MAX_METADATA_BYTES):
                del self._buffer[:11]
                self._skip_remaining = size + 4
                continue
            end = 11 + size + 4
            if len(self._buffer) < end:
                return
            payload = bytes(self._buffer[11:11 + size])
            del self._buffer[:end]
            if kind == 18:
                parsed = _script_data(payload)
                if parsed:
                    name, value = parsed
                    self._deliver({"timestamp_ms": timestamp, "source": "flv_script",
                                   "name": name, "data": value}, events)
            elif kind == 9:
                self._video(payload, timestamp, events)
            elif len(payload) >= 2 and payload[0] >> 4 == 10 and payload[1] == 1:
                self._mark_media(timestamp)

    def _video(self, payload, timestamp, events):
        if len(payload) < 5 or payload[0] & 0x0f != 7:
            return
        packet_type = payload[1]
        if packet_type == 0:
            config = payload[5:]
            if len(config) >= 7 and config[0] == 1:
                length_size = (config[4] & 3) + 1
                if length_size in (1, 2, 4):
                    self._nal_length_size = length_size
            return
        if packet_type != 1:
            return
        composition_time = int.from_bytes(payload[2:5], "big", signed=True)
        timestamp = max(0, timestamp + composition_time)
        position = 5
        nals = []
        for _ in range(1024):
            if position + self._nal_length_size > len(payload):
                break
            size = int.from_bytes(payload[position:position + self._nal_length_size], "big")
            position += self._nal_length_size
            if size > len(payload) - position:
                break
            nal = payload[position:position + size]
            position += size
            nals.append(nal)
            if nal and not nal[0] & 0x80 and 1 <= nal[0] & 0x1f <= 5:
                self._mark_media(timestamp)
        # Establish the video clock before any metadata callback, even when the
        # SEI NAL precedes its coded picture in the same AVC packet.
        for nal in nals:
            if nal and nal[0] & 0x1f == 6:
                for event in parse_sei_nal(nal, timestamp):
                    self._deliver(event, events)
