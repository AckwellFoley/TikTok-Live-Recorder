"""Conservative association of the two public multi-guest score feeds.

This is a fallback when the initial link-mic identity snapshot is absent. It
requires matching *complete* score multisets, a known host stream, and repeated
observations. Tied scores never establish identities. Protocol identities take
precedence over these inferred associations.
"""

from collections import Counter
import time


class SpeakerIdentity:
    MAX_AGE = 15.0
    CONFIRM_SECONDS = 2.0

    def __init__(self, host_id):
        self.host_id = str(host_id)
        self.users = {}
        self.streams = {}
        self.users_at = self.streams_at = None
        self.anchor = None
        self.pending = {}
        self.links = {}
        self.conflicts = set()

    @staticmethod
    def _score(value):
        if isinstance(value, bool):
            return None
        try:
            n = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return n if n >= 0 and str(value) == str(n) else None

    def tickets(self, notice):
        if notice is None or int(notice.score_type) != 1:
            return False
        users = {}
        for item in notice.user_fan_ticket[:128]:
            uid, score = str(item.user_id), self._score(item.fan_ticket)
            if not uid.isdigit() or not int(uid) or score is None or uid in users:
                return False
            users[uid] = score
        if self.host_id not in users:
            return False
        self.users, self.users_at = users, time.monotonic()
        return self._match()

    def metadata(self, data, anchor=None):
        if anchor:
            self.anchor = str(anchor)
        scores = data.get("mg_t") if isinstance(data, dict) else None
        if not isinstance(scores, dict) or scores.get("score_type") != 1:
            return False
        values = scores.get("id_to_info_m")
        if not isinstance(values, dict) or not 1 <= len(values) <= 128:
            return False
        streams = {}
        for link, info in values.items():
            score = self._score(info.get("tkt_c")) if isinstance(info, dict) else None
            if not isinstance(link, str) or not link or len(link) > 160 or score is None:
                return False
            streams[link] = score
        self.streams, self.streams_at = streams, time.monotonic()
        return self._match()

    def _match(self):
        now = time.monotonic()
        if (self.users_at is None or self.streams_at is None
                or now - min(self.users_at, self.streams_at) > self.MAX_AGE
                or self.anchor not in self.streams
                or self.users.get(self.host_id) != self.streams[self.anchor]
                or Counter(self.users.values()) != Counter(self.streams.values())):
            self.pending.clear()
            return False
        counts = Counter(self.users.values())
        unique = {score: uid for uid, score in self.users.items() if counts[score] == 1}
        candidates = {link: unique[score] for link, score in self.streams.items()
                      if score in unique and link != self.anchor and unique[score] != self.host_id}
        self.pending = {link: value for link, value in self.pending.items() if candidates.get(link) == value[0]}
        changed = False
        for link, uid in candidates.items():
            if link in self.conflicts:
                continue
            if link in self.links and self.links[link] != uid:
                del self.links[link]
                self.conflicts.add(link)
                changed = True
                continue
            previous_uid, since, observations = self.pending.get(link, (uid, now, 0))
            self.pending[link] = (uid, since, observations + 1)
            if observations >= 2 and now - since >= self.CONFIRM_SECONDS and self.links.get(link) != uid:
                self.links[link] = uid
                changed = True
        return changed
