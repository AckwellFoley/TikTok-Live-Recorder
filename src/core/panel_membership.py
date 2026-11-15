"""Maintain panel membership from link-mic snapshots and lifecycle events.

Invites, applications, chat and score-only subsets never establish membership.
Fan-ticket notices are a provisional seed until explicit membership is known.
"""


def user_id(value):
    if value is None:
        return None
    nested = getattr(value, "user", None)
    if nested is not None:
        return user_id(nested)
    for key in ("user_id", "id", "id_str"):
        uid = str(getattr(value, key, "") or "")
        if uid.isdigit() and int(uid):
            return uid
    return None


def linked_ids(users):
    result = []
    for item in users:
        # A legacy linked-users list can also contain waiting/finished entries.
        if int(getattr(item, "link_status", 0)) not in (0, 2):
            continue
        uid = user_id(item)
        if uid and uid not in result:
            result.append(uid)
    return result


def stable_panel_ids(current, incoming, host_id):
    """Keep surviving guests in place; append newcomers in incoming order."""
    incoming = list(dict.fromkeys(str(uid) for uid in incoming
                                 if str(uid).isdigit() and int(uid)))
    present = set(incoming)
    ordered = [str(host_id)] if host_id is not None else []
    seen = set(ordered)
    for uid in list(current) + incoming:
        uid = str(uid)
        if uid in present and uid not in seen:
            ordered.append(uid)
            seen.add(uid)
    return ordered


class PanelMembership:
    def __init__(self, host_id, on_change):
        self.host_id = str(host_id)
        self.ids = [self.host_id]
        self.on_change = on_change
        self.authoritative = False
        self.removed = set()
        self.departures = {}
        self.versions = {}
        self.seed_time = 0
        self.linkmic_users = {}
        self.ambiguous_linkmic_ids = set()

    def remember_links(self, event):
        mapping = dict(self.linkmic_users)
        def remember(uid, link):
            if not uid or not link:
                return
            link = str(link)
            if link in mapping and mapping[link] != uid:
                self.ambiguous_linkmic_ids.add(link)
                mapping.pop(link)
            if link not in self.ambiguous_linkmic_ids:
                mapping[link] = uid
        def visit(value, depth=0):
            if value is None or depth > 6:
                return
            if isinstance(value, (list, tuple)):
                for item in value[:128]:
                    visit(item, depth + 1)
                return
            if isinstance(value, dict):
                for item in list(value.values())[:128]:
                    visit(item, depth + 1)
                return
            uid = user_id(value)
            link = getattr(value, "linkmic_id_str", None) or getattr(value, "linkmic_id", None)
            remember(uid, link)
            remember(user_id(getattr(value, "joiner", None)), getattr(value, "joiner_link_mic_id", None))
            remember(self.host_id, getattr(value, "anchor_linkmic_id_str", None))
            rival = getattr(value, "rival_anchor_id", 0)
            if rival:
                remember(str(rival), getattr(value, "rival_linkmic_id_str", None))
            for key in ("user_states", "linked_users", "linked_list", "list", "all_users", "joiner",
                        "list_change_content", "linked_list_change_content", "enter_content", "join_direct_content",
                        "join_room_direct_content", "join_group_content", "join_group_direct_content",
                        "group_user", "players", "user_infos", "guest_user_infos"):
                visit(getattr(value, key, None), depth + 1)
        visit(event)
        self.linkmic_users = mapping

    def _publish(self, ids, source, authoritative=False):
        ids = stable_panel_ids(self.ids, ids, self.host_id)
        if authoritative:
            self.authoritative = True
            self.removed.update(set(self.ids) - set(ids))
            self.removed.difference_update(ids)
        self.ids = ids
        self.on_change(ids, source)

    def tickets(self, notice):
        if self.authoritative or notice is None or int(notice.score_type) != 1:
            return
        stamp = int(getattr(notice, "event_time", 0) or 0)
        if stamp and stamp < self.seed_time:
            return
        ids = [str(u.user_id) for u in notice.user_fan_ticket if u.user_id]
        if self.host_id in ids:
            self.seed_time = max(self.seed_time, stamp)
            self._publish(ids, "linkmic_fan_ticket")

    def _ordered(self, event):
        source = type(event).__name__
        channel = str(getattr(event, "channel_id", None) or getattr(event, "linker_id", 0))
        version = int(getattr(event, "version", 0) or 0)
        key = (source, channel)
        if version and version < self.versions.get(key, 0):
            return False
        if version:
            self.versions[key] = max(version, self.versions.get(key, 0))
        return True

    def handle(self, event):
        if not self._ordered(event):
            return
        self.remember_links(event)
        source = type(event).__name__
        key = (source, str(getattr(event, "channel_id", None) or getattr(event, "linker_id", 0)))
        version = int(getattr(event, "version", 0) or 0)
        stamp = int(getattr(getattr(event, "common", None), "create_time", 0) or 0)
        if source == "LinkStateEvent":
            # Mute/camera/network/layout patches are not complete rosters. They
            # can confirm an unknown participant, but never revive a departure.
            ids = linked_ids(getattr(event, "user_states", []))
            additions = [uid for uid in ids if uid not in self.removed]
            if additions:
                self._publish(self.ids + additions, "linkmic_state")
            return

        def commit(ids):
            for uid in set(self.ids) - set(ids) - {self.host_id}:
                self.departures[uid] = (key, version, stamp)
            self._publish(ids, source, authoritative=True)

        def snapshot(users):
            ids = []
            for uid in linked_ids(users):
                prior = self.departures.get(uid)
                if uid in self.removed and prior:
                    old_key, old_version, old_stamp = prior
                    newer = (key == old_key and old_version and version > old_version)
                    newer = newer or (old_stamp and stamp > old_stamp)
                    if not newer:
                        continue
                ids.append(uid)
            commit(ids)

        def leave(uid):
            if uid:
                self.removed.add(uid)
                self.departures[uid] = (key, version, stamp)
                commit([i for i in self.ids if i != uid])

        if source == "LinkLayerEvent":
            # These fields are typed by the upstream enum. Mere invitations,
            # permits and replies are deliberately not interpreted as joins.
            content = getattr(event, "list_change_content", None)
            if content is not None and content.list is not None:
                snapshot(content.list.linked_list)
                return
            content = getattr(event, "join_direct_content", None)
            if content is not None:
                if content.all_users is not None:
                    snapshot(content.all_users.linked_list)
                else:
                    uid = user_id(content.joiner)
                    if uid:
                        commit(self.ids + [uid])
                return
            content = getattr(event, "leave_content", None)
            if content is not None:
                leave(user_id(content.leaver))
                return
            content = getattr(event, "kick_out_content", None)
            if content is not None:
                leave(user_id(content.offliner))
                return
            if getattr(event, "finish_content", None) is not None:
                commit([])
        elif source == "LinkEvent":
            for name in ("linked_list_change_content", "list_change_content", "enter_content"):
                content = getattr(event, name, None)
                if content is not None:
                    snapshot(content.linked_users)
                    return
            for name in ("leave_content", "sys_kick_out_content"):
                content = getattr(event, name, None)
                if content is not None:
                    leave(user_id(content))
                    return
            # The legacy kick_out.from_user_id is the actor, not a safe target.
            # A subsequent linked-list event supplies the new membership.
            if getattr(event, "close_content", None) is not None:
                commit([])
