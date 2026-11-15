"""Public TikTok comment fields and a relative-video-time comment timeline.

Registration with TikTokLive 7 uses ``client.add_listener(CommentEvent, async_fn)``.
The event's canonical text field is ``content``; ``comment`` is its legacy alias.
No event/server timestamp is used as a recording timestamp here.
"""

import hashlib
import json
import math
import unicodedata
import uuid
from collections import deque


MAX_TEXT = 2000
MAX_TEXT_LINES = 20
MAX_NAME = 80
MAX_USERNAME = 64
MAX_ID = 128
RECENT_COMMENTS = 30
RECENT_IDS = 10_000
_BIDI_CONTROLS = frozenset("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff")


def _field(value, *names):
    for name in names:
        try:
            candidate = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        except (AttributeError, KeyError):
            continue
        if candidate is not None and candidate != "" and candidate != 0:
            return candidate
    return None


def _text(value, limit, multiline=False):
    if value is None:
        return ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return ""
    # Bound sanitizing work as well as the final stored length. ZWJ and variation
    # selectors are retained, so composed emoji and non-Latin text stay intact.
    value = str(value)[:limit * 4].replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    cleaned = []
    for char in value:
        if char in _BIDI_CONTROLS:
            continue
        if char == "\n":
            cleaned.append("\n" if multiline else " ")
        elif unicodedata.category(char) not in ("Cc", "Cs"):
            cleaned.append(char)
    result = "".join(cleaned)
    if multiline:
        result = "\n".join(result.split("\n")[:MAX_TEXT_LINES])
    else:
        result = " ".join(result.split())
    return result[:limit].strip()


def comment_from_event(event):
    """Return only bounded public display fields, or None for empty comments.

    Actual message IDs are preferred so repeated text with distinct IDs is
    preserved. Missing-ID events with timestamp hints receive a deterministic
    public-field hash; completely unidentifiable events get a unique ID rather
    than collapsing a genuine repeat. Neither path supplies recording time.
    """
    text = _text(_field(event, "content", "comment", "text"), MAX_TEXT, multiline=True)
    if not text:
        return None
    user = _field(event, "user")
    common = _field(event, "common")
    user_id = _text(_field(user, "id_str", "id", "user_id", "idStr"), MAX_ID)
    username = _text(_field(user, "unique_id", "display_id", "uniqueId", "displayId"), MAX_USERNAME).lstrip("@")
    name = _text(_field(user, "nickname", "name"), MAX_NAME) or username or "Viewer"
    message_id = _field(common, "msg_id", "msgId", "message_id")
    if not message_id:
        message_id = _field(event, "msg_id", "message_id", "event_id", "id")
    message_id = _text(message_id, MAX_ID)
    if not message_id or message_id == "0":
        hints = [user_id, username, name, text,
                 _text(_field(common, "create_time", "createTime"), MAX_ID),
                 _text(_field(event, "screen_time", "screenTime"), MAX_ID)]
        digest = (hashlib.blake2b(json.dumps(hints, ensure_ascii=True).encode("utf-8"), digest_size=16).hexdigest()
                  if any(hint and hint != "0" for hint in hints[-2:]) else uuid.uuid4().hex)
        message_id = "local-" + digest
    return {"id": message_id, "user_id": user_id, "username": username, "name": name, "text": text}


class CommentTimeline:
    """Append-only comment history with bounded recent-ID deduplication.

    The caller supplies elapsed seconds relative to this recording, under its
    existing timeline lock. Full history grows linearly because final rendering
    needs all comments; live exports contain only the most recent 30 comments.
    """

    def __init__(self):
        self._recent_ids = deque()
        self._seen_ids = set()
        self.reset()

    def reset(self):
        """Reset video-time history for a CDN retry, retaining replay IDs."""
        self._comments = []
        self._ordered = True

    @property
    def count(self):
        return len(self._comments)

    def add(self, comment, elapsed):
        """Append a comment after recording begins; return whether accepted."""
        if not isinstance(comment, dict):
            return False
        identity = _text(comment.get("id"), MAX_ID)
        text = _text(comment.get("text"), MAX_TEXT, multiline=True)
        if not identity or not text or identity in self._seen_ids:
            return False
        # TikTok replays initial events when (re)connecting. Remember their IDs
        # even before media starts so old comments aren't timestamped as new.
        self._seen_ids.add(identity)
        self._recent_ids.append(identity)
        if len(self._recent_ids) > RECENT_IDS:
            self._seen_ids.remove(self._recent_ids.popleft())
        if elapsed is None or isinstance(elapsed, bool):
            return False
        try:
            at = float(elapsed)
        except (TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(at) or at < 0:
            return False
        at = round(at, 3)
        if self._comments and at < self._comments[-1]["time"]:
            self._ordered = False
        self._comments.append({
            "time": at, "id": identity,
            "user_id": _text(comment.get("user_id"), MAX_ID),
            "username": _text(comment.get("username"), MAX_USERNAME),
            "name": _text(comment.get("name"), MAX_NAME) or "Viewer", "text": text,
        })
        return True

    def export(self, live=False):
        selected = self._comments[-RECENT_COMMENTS:] if live else self._comments
        if not self._ordered:
            selected = sorted(selected, key=lambda comment: comment["time"])
        return [dict(comment) for comment in selected]
