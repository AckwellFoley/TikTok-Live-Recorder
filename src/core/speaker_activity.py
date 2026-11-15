"""Speaking states from per-participant stream metadata, never mixed loudness."""

import json
import math
import time

from core.panel_membership import stable_panel_ids


def speaking_payload(data):
    if not isinstance(data, dict):
        return None
    app = data.get("app_data", {})
    if isinstance(app, str):
        if len(app) > 262144:
            app = {}
        else:
            try:
                app = json.loads(app)
            except ValueError:
                app = {}
    if not isinstance(app, dict):
        app = {}
    infos = data.get("infos")
    grids = app.get("grids")
    source = infos if isinstance(infos, list) and infos else grids
    if not isinstance(source, list):
        return None
    states = {}
    for item in source[:128]:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("uid_str") or item.get("uid") or "")
        if not uid or len(uid) > 160:
            continue
        volume = item.get("volume", item.get("talk_volume"))
        if volume is not None:
            try:
                volume = float(volume)
            except (ValueError, TypeError):
                continue
            if not math.isfinite(volume) or not 0 <= volume <= 255:
                continue
        try:
            talking = float(item["talk"]) > 0 if "talk" in item else volume is not None and volume > 0
        except (TypeError, ValueError):
            continue
        if "talk" not in item and volume is None:
            continue
        if item.get("mute_a") in (True, 1, "true") or item.get("mute_audio") in (True, 1, "true"):
            talking = False
        states[uid] = bool(talking)
    if not states:
        return None
    anchor = (app.get("anchor_link_mic_id") or app.get("anchor_linkmic_id")
              or data.get("anchor_linkmic_id") or data.get("anchor_link_mic_id"))
    return {"states": states, "anchor": str(anchor) if anchor else None,
            "source": "infos" if source is infos else "grids"}


class SpeakerActivity:
    """Compact state changes in media time, with stale highlights expiring."""

    TTL = 1.25

    def __init__(self):
        self.samples = []
        self.last_media_time = None
        self.last_wall_time = None
        self.current = []
        self.last_ids = []
        self.anchor = None
        self.seen_ids = set()
        self.last_infos_time = None

    def add(self, data, timestamp_ms):
        payload = speaking_payload(data)
        if payload is None:
            return False
        at = float(timestamp_ms) / 1000
        if not math.isfinite(at) or at < 0 or (self.last_media_time is not None and at < self.last_media_time):
            return False
        if payload["anchor"]:
            self.anchor = payload["anchor"]
        # Layout snapshots can carry stale talk flags alongside current audio
        # reports. Prefer the audio reports while they are fresh.
        if payload["source"] == "infos":
            self.last_infos_time = at
        elif self.last_infos_time is not None and at - self.last_infos_time <= self.TTL:
            return False
        self.seen_ids.update(payload["states"])
        ids = sorted(uid for uid, speaking in payload["states"].items() if speaking)
        live_changed = ids != self.current
        changed = self.last_media_time is None or ids != self.last_ids
        if self.last_media_time is not None and at - self.last_media_time > self.TTL and self.last_ids:
            self.samples.append({"time": self.last_media_time + self.TTL, "linkmic_ids": []})
            changed = True
        if changed:
            self.samples.append({"time": at, "linkmic_ids": ids})
        self.current = ids
        self.last_ids = ids
        self.last_media_time, self.last_wall_time = at, time.monotonic()
        return changed or live_changed

    def expire(self):
        if self.current and self.last_wall_time is not None and time.monotonic() - self.last_wall_time > self.TTL:
            self.current = []
            # The final timeline must also clear this state at the media TTL.
            return True
        return False

    def mapping(self, links, host_id):
        mapping = dict(links)
        if self.anchor:
            mapping[self.anchor] = str(host_id)
        return mapping

    def active_users(self, links, host_id, current_ids):
        mapping = self.mapping(links, host_id)
        return sorted({mapping[uid] for uid in self.current if uid in mapping and mapping[uid] in current_ids})

    def export(self, links, host_id, origin_ms):
        if origin_ms is None:
            return []
        mapping = self.mapping(links, host_id)
        samples = list(self.samples)
        if self.last_media_time is not None:
            samples.append({"time": self.last_media_time + self.TTL, "linkmic_ids": []})
        result = []
        for sample in samples:
            ids = sorted({mapping[uid] for uid in sample["linkmic_ids"] if uid in mapping})
            if result and ids == result[-1]["ids"]:
                continue
            result.append({"time": round(max(0, sample["time"] - origin_ms / 1000), 3), "ids": ids})
        return result


def panel_frames(timeline, duration):
    """Merge roster and speaking changes at video-frame precision."""
    roster = timeline["snapshots"]
    if not roster:
        return []
    host_id = timeline.get("host_id")
    if host_id is None:
        host_id = next((s["ids"][0] for s in roster if s["ids"]), None)
    events = [(max(0, float(s["time"])), 0, s) for s in roster]
    events += [(max(0, float(s["time"])), 1, s) for s in timeline.get("speaker_updates", [])]
    current = dict(roster[0], ids=stable_panel_ids([], roster[0]["ids"], host_id))
    initial = dict(current)
    speaking = []
    frames = {}
    for at, kind, event in sorted(events, key=lambda e: (e[0], e[1])):
        if at >= duration:
            continue
        if kind == 0:
            current = dict(event, ids=stable_panel_ids(current["ids"], event["ids"], host_id))
        else:
            speaking = event["ids"]
        frame = round(at * 25) / 25
        if frame >= duration:
            continue
        frames[frame] = dict(current, time=frame,
                             active_speakers=[uid for uid in speaking if uid in current["ids"]])
    if 0.0 not in frames:
        frames[0.0] = dict(initial, time=0.0, active_speakers=[])
    result = []
    for at, frame in sorted(frames.items()):
        signature = (frame["ids"], frame.get("status", ""), frame["active_speakers"])
        if not result or signature != (result[-1]["ids"], result[-1].get("status", ""), result[-1]["active_speakers"]):
            result.append(frame)
    return result
