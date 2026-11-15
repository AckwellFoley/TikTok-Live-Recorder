"""Collect link-mic membership and save the feed beside a guest panel.

No chat/viewer events are treated as panel membership. Fan-ticket notices seed
the panel until explicit membership events are available. Names
and avatars come from TikTok's public webcast user endpoint. Speaking activity
is deliberately not inferred from the mixed audio.
"""

import asyncio
import io
import json
import os
import re
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path

import requests

from core.tiktok_api import TikTokAPI
from core.comment_capture import CommentTimeline, comment_from_event
from core.comment_panel import comment_frames, draw_comments
from core.panel_membership import PanelMembership, stable_panel_ids
from core.speaker_activity import SpeakerActivity, panel_frames
from core.speaker_identity import SpeakerIdentity
from core.stream_metadata import StreamMetadataDecoder
from utils.logger_manager import logger


def profile_from_user(user, fallback_id=""):
    """Normalize only the public display fields needed for the panel."""
    uid = str(user.get("id_str") or user.get("id") or user.get("user_id") or fallback_id)
    if not uid.isdigit() or int(uid) == 0:
        return None
    avatar = user.get("avatar_thumb") or user.get("avatarThumb") or {}
    urls = avatar.get("url_list", []) if isinstance(avatar, dict) else [avatar]
    return {
        "id": uid,
        "username": user.get("display_id") or user.get("unique_id") or user.get("uniqueId") or "",
        "name": user.get("nickname") or "",
        "avatar_url": next((u for u in urls if isinstance(u, str) and u.startswith("https://")), ""),
    }


def roster_from_ticket_notice(notice, host_id):
    """Accept the full participant notice, never a game-score subset."""
    if not notice or int(notice.score_type) != 1:
        return None
    ids = list(dict.fromkeys(str(u.user_id) for u in notice.user_fan_ticket if u.user_id))
    return ids if host_id in ids else None


def fetch_public_profile(api, uid):
    """Fall back to profile-page data when the user API is empty/blocked."""
    try:
        response = api.http_client.get(
            f"{api.WEBCAST_URL}/webcast/user/",
            params={"aid": 1988, "user_id": uid, "target_uid": uid}, timeout=10,
        ).json()
        if response.get("status_code", 0) == 0:
            data = response.get("data") or {}
            profile = profile_from_user(data.get("user") or data)
            if profile and profile["id"] == uid:
                return profile
    except (ValueError, requests.RequestException):
        pass
    html = api.http_client.get(f"{api.BASE_URL}/@{uid}", timeout=10).text
    match = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S)
    if match:
        data = json.loads(match.group(1)).get("__DEFAULT_SCOPE__", {})
        user = data.get("webapp.user-detail", {}).get("userInfo", {}).get("user", {})
        profile = profile_from_user(user)
        if profile and profile["id"] == uid:
            return profile
    raise ValueError("TikTok did not return the participant profile")


class PanelTimeline:
    """Thread-safe roster snapshots, relative to the first video bytes."""

    def __init__(self, host):
        self.host = host
        self.profiles = {host["id"]: host}
        self.snapshots = []
        self.recording_start = None
        self.lock = threading.RLock()
        self.activity = SpeakerActivity()
        self.linkmic_users = {}
        self.speaker_identity_sources = {}
        self.media_origin_ms = None
        self.panel_slots = 10
        self.comments = CommentTimeline()
        self.comment_statuses = [{"time": 0.0, "status": "Connecting to comments"}]
        self.update([], "Waiting for guest list", "initial")

    def reset_start(self):
        with self.lock:
            self.activity = SpeakerActivity()
            self.media_origin_ms = None
            self.recording_start = None
            self.snapshots = [dict(self.snapshots[-1], time=0.0)]
            self.comments.reset()
            self.comment_statuses = [dict(self.comment_statuses[-1], time=0.0)]

    def mark_start(self):
        with self.lock:
            if self.recording_start is None:
                self.recording_start = time.monotonic()
                # The last roster observed before recording is the baseline.
                self.snapshots = [dict(self.snapshots[-1], time=0.0)]

    def update(self, guests, status, source):
        with self.lock:
            previous = self.snapshots[-1]["ids"] if self.snapshots else []
            ids = stable_panel_ids(previous, guests, self.host["id"])
            self.panel_slots = max(self.panel_slots, len(ids))
            state = {"ids": ids, "status": status, "source": source}
            if self.snapshots and all(self.snapshots[-1][k] == state[k] for k in state):
                return
            elapsed = max(0, time.monotonic() - self.recording_start) if self.recording_start else 0.0
            snapshot = dict(state, time=round(elapsed, 3))
            if self.snapshots and elapsed == 0:
                self.snapshots[-1] = snapshot
            else:
                self.snapshots.append(snapshot)

    def add_comment(self, comment):
        with self.lock:
            elapsed = max(0, time.monotonic() - self.recording_start) if self.recording_start is not None else None
            return self.comments.add(comment, elapsed)

    def comment_connection(self, status):
        with self.lock:
            if self.comment_statuses[-1]["status"] == status:
                return
            elapsed = max(0, time.monotonic() - self.recording_start) if self.recording_start is not None else 0.0
            event = {"time": round(elapsed, 3), "status": status}
            if self.recording_start is None:
                self.comment_statuses = [event]
            else:
                self.comment_statuses.append(event)

    def export(self, live=False):
        with self.lock:
            ids = self.snapshots[-1]["ids"]
            mapped = self.activity.mapping(self.linkmic_users, self.host["id"])
            data = {
                "host_id": self.host["id"],
                "panel_slots": self.panel_slots,
                "comment_count": self.comments.count,
                "speaking_indicators": bool(set(mapped) & self.activity.seen_ids),
                "speaker_mapped_count": len(set(mapped.values()) & set(ids)),
                "speaker_participant_count": len(ids),
                "active_speakers": self.activity.active_users(self.linkmic_users, self.host["id"], ids),
                "snapshots": list(self.snapshots[-1:] if live else self.snapshots),
                "profiles": {uid: {k: v for k, v in p.items() if k != "avatar_url"}
                             for uid, p in self.profiles.items()},
            }
            if not live:
                data["speaker_updates"] = self.activity.export(self.linkmic_users, self.host["id"], self.media_origin_ms)
                data["linkmic_users"] = dict(self.linkmic_users)
                data["speaker_identity_sources"] = dict(self.speaker_identity_sources)
                data["comments"] = self.comments.export()
                data["comment_statuses"] = list(self.comment_statuses)
            return data


class GuestPanelCapture:
    def __init__(self, username, room_id, output, cookies=None, proxy=None, event_callback=None):
        # Fail early if the optional renderer/event packages aren't installed.
        from PIL import Image  # noqa: F401
        from TikTokLive import TikTokLiveClient  # noqa: F401

        self.username = username
        self.event_callback = event_callback
        self.room_id = str(room_id)
        self.api = TikTokAPI(proxy=proxy, cookies=cookies)
        self.assets = Path(output).with_suffix(".panel-assets")
        self.assets.mkdir(parents=True, exist_ok=True)
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.thread = None
        self.last_ids = []
        self.pending_profiles = set()
        self.profile_attempts = {}
        self.last_comment_preview = 0.0
        data = self.api.http_client.get(
            f"{self.api.WEBCAST_URL}/webcast/room/info/",
            params={"aid": 1988, "room_id": self.room_id}, timeout=12,
        ).json()
        host = profile_from_user((data.get("data") or {}).get("owner") or {})
        if not host:
            raise ValueError("TikTok did not return the host profile")
        self.timeline = PanelTimeline(host)
        self.membership = PanelMembership(host["id"], self._accept_roster)
        self.speaker_identity = SpeakerIdentity(host["id"])
        self.stream_decoder = StreamMetadataDecoder(self._on_stream_metadata)
        self._store_avatar(host)

    def _emit_panel(self):
        callback = getattr(self, "event_callback", None)
        if callback:
            try:
                callback({"type": "panel", "timeline": self.timeline.export(live=True),
                          "assets": str(self.assets)})
            except Exception:
                logger.debug("Panel preview callback failed", exc_info=True)

    def _sync_speaker_links(self):
        inferred = getattr(self, "speaker_identity", None)
        links = dict(inferred.links) if inferred else {}
        for link in self.membership.ambiguous_linkmic_ids:
            links.pop(link, None)
        links.update(self.membership.linkmic_users)
        self.timeline.linkmic_users = links
        self.timeline.speaker_identity_sources = {
            link: "linkmic_event" if link in self.membership.linkmic_users else "score_correlation"
            for link in links}

    def _store_avatar(self, profile):
        from PIL import Image

        url = profile.get("avatar_url")
        if not url:
            return
        try:
            response = requests.get(url, timeout=8)
            response.raise_for_status()
            path = self.assets / f"avatar-{profile['id']}.png"
            with Image.open(io.BytesIO(response.content)) as image:
                image.convert("RGB").resize((160, 160)).save(path)
            with self.timeline.lock:
                profile["avatar"] = path.name
        except Exception as exc:
            logger.debug("Panel avatar unavailable: %s", type(exc).__name__)

    def _resolve_profile(self, uid):
        try:
            # Resolution runs in worker threads; curl sessions aren't shared.
            api = TikTokAPI(proxy=self.api.http_client.proxies.get("https"),
                            cookies=self.api.http_client.cookies.get_dict())
            profile = fetch_public_profile(api, uid)
            self._store_avatar(profile)
            with self.timeline.lock:
                self.timeline.profiles[uid] = profile
            self._emit_panel()
            logger.info("Panel participant: @%s (%s)", profile["username"] or uid, profile["name"])
            return True
        except Exception as exc:
            logger.warning("Could not resolve panel participant %s: %s", uid, type(exc).__name__)
            return False

    def _accept_roster(self, ids, source):
        ids = list(dict.fromkeys(str(uid) for uid in ids if str(uid).isdigit() and int(uid)))
        changed = self.last_ids != ids
        self.last_ids = ids
        self.timeline.update(ids, "", source)
        with self.timeline.lock:
            self._sync_speaker_links()
            for uid in ids:
                if uid not in self.timeline.profiles:
                    self.pending_profiles.add(uid)
        if changed:
            names = ["@" + self.timeline.profiles[uid]["username"]
                     if uid in self.timeline.profiles and self.timeline.profiles[uid].get("username")
                     else uid for uid in ids]
            logger.info("Current panel: %s", ", ".join(names))
        self.ready.set()
        self._emit_panel()

    async def _listen(self):
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import CommentEvent, ConnectEvent, LinkEvent, LinkLayerEvent, LinkMicFanTicketMethodEvent, LinkStateEvent, LinkMicMethodEvent

        # Anonymous event connection. Never forward the user's session cookie to
        # the library's signing service.
        async def tickets(event):
            self.membership.tickets(event.fan_ticket_room_notice)
            with self.timeline.lock:
                changed = self.speaker_identity.tickets(event.fan_ticket_room_notice)
                self._sync_speaker_links()
            if changed:
                self._emit_panel()

        async def membership_event(event):
            self.membership.handle(event)

        async def identity_event(event):
            self.membership.remember_links(event)
            with self.timeline.lock:
                self._sync_speaker_links()
            self._emit_panel()

        async def connected(event):
            if not self.stop_event.is_set():
                self.timeline.comment_connection("Connected")
                logger.info("Connected to guest and comment events for @%s", self.username)

        async def comment_event(event):
            if self.stop_event.is_set():
                return
            comment = comment_from_event(event)
            if comment is not None and self.timeline.add_comment(comment):
                now = time.monotonic()
                if now - self.last_comment_preview >= 1:
                    self.last_comment_preview = now
                    self._emit_panel()

        jobs = {}
        retry = 2
        try:
            while not self.stop_event.is_set():
                client = TikTokLiveClient(unique_id=f"@{self.username}", ws_kwargs={"close_timeout": 2})
                client.add_listener(LinkMicFanTicketMethodEvent, tickets)
                client.add_listener(CommentEvent, comment_event)
                client.add_listener(ConnectEvent, connected)
                client.add_listener(LinkMicMethodEvent, identity_event)
                for event_type in (LinkStateEvent, LinkEvent, LinkLayerEvent):
                    client.add_listener(event_type, membership_event)
                try:
                    task = await asyncio.wait_for(client.start(
                        room_id=int(self.room_id), fetch_live_check=False,
                        fetch_room_info=False, fetch_gift_info=False,
                    ), timeout=20)
                    # Retained identities are marked uncertain across a gap
                    # until a fresh membership message establishes the panel.
                    connected_at = time.monotonic()
                    while not self.stop_event.is_set() and not task.done():
                        now = time.monotonic()
                        for uid, job in list(jobs.items()):
                            if job.done():
                                job.result()
                                del jobs[uid]
                        with self.timeline.lock:
                            pending = list(self.pending_profiles)
                        for uid in pending:
                            attempts, due = self.profile_attempts.get(uid, (0, 0))
                            if uid not in jobs and attempts < 3 and now >= due:
                                self.profile_attempts[uid] = (attempts + 1, now + 30 * (attempts + 1))
                                jobs[uid] = asyncio.create_task(asyncio.to_thread(self._resolve_profile, uid))
                            with self.timeline.lock:
                                if uid in self.timeline.profiles or attempts >= 3:
                                    self.pending_profiles.discard(uid)
                        await asyncio.sleep(0.2)
                    if task.done():
                        task.result()
                    if time.monotonic() - connected_at >= 30:
                        retry = 2
                except Exception as exc:
                    logger.warning("Guest updates interrupted (%s); reconnecting", type(exc).__name__)
                finally:
                    try:
                        await asyncio.wait_for(client.disconnect(), timeout=5)
                    except (Exception, asyncio.CancelledError):
                        pass
                    # Work around TikTokLive 7.0.1's close() running a nested loop.
                    try:
                        await client.web.close()
                    except Exception:
                        logger.debug("Panel HTTP cleanup failed", exc_info=True)
                if not self.stop_event.is_set():
                    self.timeline.update(self.last_ids, "Guest updates reconnecting", "disconnected")
                    self.timeline.comment_connection("Comment updates reconnecting")
                    self._emit_panel()
                    until = time.monotonic() + retry
                    while not self.stop_event.is_set() and time.monotonic() < until:
                        await asyncio.sleep(0.2)
                    retry = min(60, retry * 2)
        finally:
            self.ready.set()
            if jobs:
                await asyncio.gather(*jobs.values(), return_exceptions=True)

    def start(self):
        def expire_highlights():
            while not self.stop_event.wait(0.15):
                with self.timeline.lock:
                    expired = self.timeline.activity.expire()
                if expired:
                    self._emit_panel()
        self.speaker_timer = threading.Thread(target=expire_highlights, daemon=True)
        self.speaker_timer.start()
        self.thread = threading.Thread(target=lambda: asyncio.run(self._listen()), daemon=True)
        self.thread.start()
        self.ready.wait(timeout=8)
        self._emit_panel()

    def mark_start(self):
        self.timeline.mark_start()

    def reset_start(self):
        self.timeline.reset_start()
        self.stream_decoder = StreamMetadataDecoder(self._on_stream_metadata)

    def _on_stream_metadata(self, event):
        if event.get("source") != "avc_sei":
            return
        with self.timeline.lock:
            changed = self.timeline.activity.add(event["data"], event["timestamp_ms"])
            mapped = self.speaker_identity.metadata(event["data"], self.timeline.activity.anchor)
            self._sync_speaker_links()
        if changed or mapped:
            self._emit_panel()

    def feed_stream(self, chunk):
        try:
            self.stream_decoder.feed(chunk)
            with self.timeline.lock:
                self.timeline.media_origin_ms = self.stream_decoder.first_media_timestamp_ms
                expired = self.timeline.activity.expire()
            if expired:
                self._emit_panel()
        except Exception:
            logger.debug("Unsupported stream metadata; continuing video recording", exc_info=True)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=25)
        timer = getattr(self, "speaker_timer", None)
        if timer:
            timer.join(timeout=1)

    def render(self, video, ffmpeg="ffmpeg"):
        self.stop()
        video = Path(video)
        manifest = video.with_name(video.stem + "_panel.json")
        timeline = self.timeline.export()
        manifest.write_text(json.dumps(timeline, indent=2, ensure_ascii=False), encoding="utf-8")
        return render_panel(video, timeline, self.assets, ffmpeg)


def draw_panel(timeline, snapshot, assets, size):
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    import math

    width, height = size
    scale = width / 720
    canvas = Image.new("RGBA", size, "#10131c")
    draw = ImageDraw.Draw(canvas)
    fonts = {}
    def font(points, bold=False):
        key = (points, bold)
        if key not in fonts:
            for path in (("C:/Windows/Fonts/segoeuib.ttf", "DejaVuSans-Bold.ttf") if bold else
                         ("C:/Windows/Fonts/segoeui.ttf", "DejaVuSans.ttf")):
                try:
                    fonts[key] = ImageFont.truetype(path, max(10, round(points * scale)))
                    break
                except OSError:
                    continue
            else:
                fonts[key] = ImageFont.load_default()
        return fonts[key]
    def shorten(text, limit, face):
        text = str(text)
        if draw.textlength(text, font=face) <= limit:
            return text
        while text and draw.textlength(text + "…", font=face) > limit:
            text = text[:-1]
        return text + "…"

    ids = snapshot["ids"]
    active = set(snapshot.get("active_speakers", timeline.get("active_speakers", [])))
    draw.text((28 * scale, 25 * scale), "LIVE PANEL", font=font(31, True), fill="white")
    count = max(0, len(ids) - 1)
    draw.text((width - 28 * scale, 34 * scale), f"{count} guest" + ("s" if count != 1 else ""),
              font=font(20), anchor="ra", fill="#b9c2cf")
    mapped, total = timeline.get("speaker_mapped_count"), timeline.get("speaker_participant_count")
    status = f"Speaker identities: {mapped}/{total} · green = speaking" if total else "Green ring = speaking"
    draw.text((28 * scale, 77 * scale), shorten(snapshot.get("status") or status,
              width - 56 * scale, font(18)), font=font(18), fill="#a7b2c7")
    draw.line((24 * scale, 111 * scale, width - 24 * scale, 111 * scale), fill="#30394b")
    columns, gap, top = 3, 14 * scale, 128 * scale
    slots = max(15, int(timeline.get("panel_slots", 10)), len(ids),
                max((len(row["ids"]) for row in timeline.get("snapshots", [])), default=0))
    rows = math.ceil(slots / columns)
    cell = min((width - 48 * scale - gap * 2) / columns,
               (height - top - 28 * scale - gap * (rows - 1)) / rows)
    offset = (width - (columns * cell + gap * 2)) / 2
    tile_scale = cell / 214
    for index, uid in enumerate(ids):
        x = offset + (index % columns) * (cell + gap)
        y = top + (index // columns) * (cell + gap)
        speaking = uid in active
        draw.rounded_rectangle((x, y, x + cell, y + cell), radius=14 * scale,
            fill="#16382f" if speaking else "#1a2130",
            outline="#39efad" if speaking else "#2a3345", width=max(1, round((4 if speaking else 1) * scale)))
        draw.text((x + 12 * scale, y + 9 * scale), f"{index + 1:02}", font=font(12), fill="#8c9ab1")
        profile = timeline["profiles"].get(uid, {})
        cx, cy = x + cell / 2, y + 72 * tile_scale
        radius = max(1, round(40 * tile_scale))
        box = (round(cx - radius), round(cy - radius), round(cx + radius), round(cy + radius))
        pad = max(3, round(6 * tile_scale))
        draw.ellipse((box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad),
            fill="#164f42" if speaking else "#576579", outline="#39efad" if speaking else "#576579",
            width=max(1, round(3 * scale)))
        try:
            with Image.open(Path(assets) / profile.get("avatar", "missing.png")) as original:
                avatar = ImageOps.fit(original.convert("RGBA"), (radius * 2, radius * 2))
            mask = Image.new("L", avatar.size)
            ImageDraw.Draw(mask).ellipse((0, 0, avatar.width - 1, avatar.height - 1), fill=255)
            canvas.paste(avatar, box[:2], mask)
        except (OSError, ValueError):
            draw.ellipse(box, fill="#364253")
            draw.text((cx, cy), (profile.get("name") or "?")[:1].upper(), font=font(26), anchor="mm", fill="white")
        name = profile.get("name") or profile.get("username") or f"Guest {uid[-4:]}"
        draw.text((cx, y + 127 * tile_scale), shorten(name, cell - 20 * scale, font(17, True)),
                  font=font(17, True), anchor="mt", fill="white")
        username = "@" + profile["username"] if profile.get("username") else ""
        draw.text((cx, y + 153 * tile_scale), shorten(username, cell - 16 * scale, font(13)),
                  font=font(13), anchor="mt", fill="#b9c2cf")
        label = "SPEAKING" if speaking else "HOST" if uid == timeline["host_id"] else "GUEST"
        draw.text((cx, y + 181 * tile_scale), label, font=font(13, True), anchor="mt",
                  fill="#39efad" if speaking else "#b9c2cf")
    return canvas


def render_panel(video, timeline, assets, ffmpeg="ffmpeg"):
    """Save comments, source feed and guests as separate 720×1280 columns."""
    video, assets = Path(video), Path(assets)
    probe = Path(ffmpeg).with_name("ffprobe.exe" if Path(ffmpeg).suffix == ".exe" else "ffprobe")
    metadata = json.loads(subprocess.check_output([
        str(probe), "-v", "error", "-show_entries", "format=duration:stream=codec_type,r_frame_rate,avg_frame_rate",
        "-of", "json", str(video),
    ], creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    stream = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    duration = float(metadata["format"]["duration"])
    frame_rate = Fraction(25)
    for key in ("r_frame_rate", "avg_frame_rate"):
        try:
            candidate = Fraction(stream.get(key) or "0/0")
            if 1 <= candidate <= 120:
                frame_rate = candidate
                break
        except (ValueError, ZeroDivisionError):
            continue
    snapshots = panel_frames(timeline, duration)
    entries, rendered = [], {}
    for index, snapshot in enumerate(snapshots):
        signature = (tuple(snapshot["ids"]), snapshot.get("status", ""), tuple(snapshot["active_speakers"]))
        if signature not in rendered:
            path = assets / f"panel-{len(rendered):04}.png"
            draw_panel(timeline, snapshot, assets, (720, 1280)).save(path)
            rendered[signature] = path
        path = rendered[signature]
        end = snapshots[index + 1]["time"] if index + 1 < len(snapshots) else duration
        entries += [f"file '{path.name}'", f"duration {max(0.04, end - snapshot['time']):.3f}"]
    entries.append(f"file '{path.name}'")
    playlist = assets / "frames.txt"
    playlist.write_text("\n".join(entries) + "\n", encoding="utf-8")
    comment_images = []
    comment_playlist = None
    if "comments" in timeline:
        comments = comment_frames(timeline, duration, updates_per_second=5)
        comment_entries = []
        for index, snapshot in enumerate(comments):
            path = assets / f"comments-{index:06}.png"
            draw_comments(timeline, snapshot, (720, 1280)).save(path, compress_level=1)
            comment_images.append(path)
            end = comments[index + 1]["time"] if index + 1 < len(comments) else duration
            comment_entries += [f"file '{path.name}'", f"duration {max(0.04, end - snapshot['time']):.3f}"]
        comment_entries.append(f"file '{comment_images[-1].name}'")
        comment_playlist = assets / "comments-frames.txt"
        comment_playlist.write_text("\n".join(comment_entries) + "\n", encoding="utf-8")
    output = video.with_name(video.stem + "_panel.mp4")
    # Retain source timestamps for audio sync. A fixed output rate prevents
    # independent panel-image timestamps from increasing the video frame rate.
    command = [str(ffmpeg), "-v", "error", "-y", "-i", str(video),
               "-f", "concat", "-safe", "0", "-i", str(playlist)]
    filters = ("[0:v]scale=720:1280:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1,"
               "pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p[feed];"
               "[1:v]setsar=1,format=yuv420p[guests];")
    if comment_playlist is not None:
        command += ["-f", "concat", "-safe", "0", "-i", str(comment_playlist)]
        filters += ("[2:v]setsar=1,format=yuv420p[comments];"
                    f"[comments][feed][guests]hstack=inputs=3:shortest=0,fps={frame_rate},format=yuv420p[v]")
    else:
        filters += f"[feed][guests]hstack=inputs=2:shortest=0,fps={frame_rate},format=yuv420p[v]"
    command += ["-filter_complex_threads", "4", "-filter_complex", filters,
               "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-threads", "4",
               "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy", "-t", str(duration),
               "-movflags", "+faststart", str(output)]
    logger.info("Saving the recording panels to %s", output.name)
    try:
        subprocess.run(command, check=True, capture_output=True, stdin=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.CalledProcessError as exc:
        output.unlink(missing_ok=True)
        raise RuntimeError(exc.stderr.decode(errors="replace")[-1500:]) from exc
    for path in comment_images:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.debug("Temporary comment image cleanup deferred", exc_info=True)
    if comment_playlist is not None:
        try:
            comment_playlist.unlink(missing_ok=True)
        except OSError:
            logger.debug("Temporary comment playlist cleanup deferred", exc_info=True)
    logger.info("Panel recording saved: %s", output.resolve())
    return output
