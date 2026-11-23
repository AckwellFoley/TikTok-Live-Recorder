import json
import sys
import time
from pathlib import Path

import pytest
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import gui


class FakeManager:
    def __init__(self, root_dir, on_event, max_concurrent=4):
        self.max_concurrent = max_concurrent
        self.jobs = {}
        self.options = []
        self.stopped = []

    @property
    def active_count(self):
        return sum(job["state"] not in ("finished", "error", "queued") for job in self.jobs.values())

    @property
    def is_running(self):
        return any(job["state"] not in ("finished", "error") for job in self.jobs.values())

    def start(self, **options):
        creator = options["target"].lstrip("@").lower()
        if creator in self.jobs and self.jobs[creator]["state"] not in ("finished", "error"):
            raise RuntimeError("Already recording this creator.")
        self.options.append(options)
        self.jobs[creator] = dict(creator=creator, state="connecting", started_at=None, session_id=creator)
        return creator

    def stop(self, creator):
        self.stopped.append(creator)
        if creator in self.jobs:
            self.jobs[creator]["state"] = "stopping"

    def stop_all(self):
        for creator in self.jobs:
            self.stop(creator)

    def snapshot(self):
        return list(self.jobs.values())

    def poll(self):
        pass


class FakeMonitor:
    def __init__(self, root_dir, on_event, interval=60):
        self.creators = []
        self.running = False

    def start(self, creators):
        self.creators = list(creators)
        self.running = True

    def set_creators(self, creators):
        self.creators = list(creators)

    def refresh(self):
        pass

    def stop(self):
        self.running = False


@pytest.fixture
def app(monkeypatch, tmp_path, gui_root):
    monkeypatch.setattr(gui, "MultiRecorder", FakeManager)
    monkeypatch.setattr(gui, "CreatorMonitor", FakeMonitor)
    root = tk.Toplevel(gui_root)
    root.withdraw()
    result = gui.RecorderApp(root, tmp_path)
    yield result
    result.monitor.stop()
    try:
        root.destroy()
    except tk.TclError:
        pass


def test_new_session_controls_remain_available_during_other_recordings(app):
    app.target.set("@alice")
    app.duration.set("30")
    app.start()
    app.manager.jobs["alice"].update(state="recording", started_at=time.monotonic())
    app.target.set("bob")
    app.start()
    assert [options["target"] for options in app.manager.options] == ["alice", "bob"]
    assert app.manager.options[0]["duration"] == 30
    assert app.manager.options[0]["panel"] is True
    assert not app.start_button.instate(["disabled"])
    assert not app.target_entry.instate(["disabled"])
    assert app.login_button.instate(["disabled"])
    app.stop()
    assert set(app.manager.stopped) == {"alice", "bob"}


def test_selected_outputs_are_captured_per_session(app):
    app.comments_enabled.set(False)
    app.output_layout.set("separate")
    app._start_creator("alice")
    app.panel_enabled.set(False)
    app.comments_enabled.set(True)
    app.output_layout.set("combined")
    app._start_creator("bob")
    first, second = app.manager.options
    assert (first["guests"], first["comments"], first["layout"]) == (True, False, "separate")
    assert (second["guests"], second["comments"], second["layout"]) == (False, True, "combined")


def test_idle_status_refresh_does_not_redraw_the_buttons(app, monkeypatch):
    from unittest.mock import Mock
    app._refresh_creators()
    app._set_controls()
    buttons = [*app.mode_combo._buttons_dict.values(), *app.layout_switch._buttons_dict.values(),
               app.start_button, app.stop_button, app.login_button]
    draws = []
    for button in buttons:
        draw = Mock(wraps=button._draw)
        monkeypatch.setattr(button, "_draw", draw)
        draws.append(draw)
    changes = []
    for variable in (app.session_text, app.status_text, app.elapsed_text):
        variable.trace_add("write", lambda *_: changes.append(True))
    for _ in range(25):
        app._refresh_creators()
        app._set_controls()
    assert not any(draw.called for draw in draws)
    assert not changes


def test_control_states_still_change_when_a_recording_starts_or_app_closes(app):
    app._set_controls()
    assert app.stop_button.instate(["disabled"])
    app._start_creator("alice")
    assert not app.stop_button.instate(["disabled"])
    assert app.login_button.instate(["disabled"])
    assert app.mode_combo.cget("state") == "normal"
    app.closing = True
    app._set_controls()
    assert app.mode_combo.cget("state") == "disabled"
    assert app.layout_switch.cget("state") == "disabled"


def test_dark_mode_updates_tables_and_saves_preference_without_changing_output(app):
    from glass_ui import DARK, LIGHT
    from tkinter import ttk
    original_output = (app.panel_enabled.get(), app.comments_enabled.get(), app.output_layout.get())
    app.dark_mode.set(True)
    app._toggle_dark_mode()
    assert ttk.Style(app.root).lookup("Treeview", "background") == DARK["FIELD"]
    assert app.log.cget("bg") == DARK["FIELD"]
    def toolbar_buttons(parent):
        for child in parent.winfo_children():
            if isinstance(child, gui.Button) and isinstance(child.master, ttk.Frame):
                yield child
            yield from toolbar_buttons(child)
    buttons = list(toolbar_buttons(app.root))
    assert len(buttons) >= 4
    assert all(button._apply_appearance_mode(button.cget("bg_color")) == DARK["CARD"] for button in buttons)
    saved = json.loads(app.settings_path.read_text())
    assert saved["dark_mode"] is True
    assert original_output == (app.panel_enabled.get(), app.comments_enabled.get(), app.output_layout.get())
    reopened_root = tk.Toplevel(app.root)
    reopened_root.withdraw()
    try:
        reopened = gui.RecorderApp(reopened_root, app.root_dir)
        assert reopened.dark_mode.get() is True
        assert reopened.colors == DARK
        reopened.monitor.stop()
    finally:
        reopened_root.destroy()
    app.dark_mode.set(False)
    app._toggle_dark_mode()
    assert ttk.Style(app.root).lookup("Treeview", "background") == LIGHT["FIELD"]


def test_header_and_window_use_requested_app_name(app):
    assert app.title_label.cget("text") == "TikTok Live Recorder"
    assert app.root.title() == "TikTok Live Recorder"


def test_manual_default_and_automatic_disk_warning(app):
    assert app.mode.get() == "Record now"
    assert not app.auto_warning.winfo_manager()
    app.mode.set("Monitor automatically")
    assert app.auto_warning.winfo_manager() == "grid"
    assert app.start_button.cget("text") == "Watch all creators"
    app.mode.set("Record now")
    assert not app.auto_warning.winfo_manager()


def test_record_selected_starts_every_selected_creator_and_stop_is_individual(app):
    for creator in ("alice", "bob"):
        app.history.add(creator)
    app._refresh_creators()
    app.creators.selection_set(("alice", "bob"))
    app._record_selected()
    assert [item["target"] for item in app.manager.options] == ["alice", "bob"]
    app.creators.selection_set("alice")
    app._stop_selected()
    assert app.manager.stopped == ["alice"]
    assert app.manager.jobs["bob"]["state"] == "connecting"


def test_automatic_watch_waits_without_a_slot_and_starts_only_when_live(app):
    app.mode.set("Monitor automatically")
    app.target.set("alice")
    app.start()
    assert app.manager.active_count == 0
    app._event(dict(type="presence", creator="alice", state="offline", live=False, checked_at=time.time()))
    app._start_due_automatic()
    assert app.manager.options == []
    app._event(dict(type="presence", creator="alice", state="live", live=True, checked_at=time.time()))
    app._start_due_automatic()
    assert app.manager.options[0]["target"] == "alice"
    assert app.manager.options[0]["mode"] == "manual"
    app._start_due_automatic()
    assert len(app.manager.options) == 1
    app.stop()
    assert app.auto_record == {}


def test_creator_presence_and_panels_do_not_overwrite_each_other(app):
    app.monitoring.set(True)
    for creator in ("alice", "bob"):
        app.history.add(creator)
    app.selected_creator.set("alice")
    timeline = {"host_id": "1", "profiles": {"1": {"name": "Host"}},
                "snapshots": [{"ids": ["1", "2"], "status": ""}]}
    app._event(dict(type="panel", creator="alice", timeline=timeline, assets=str(app.root_dir)))
    assert app.guest_text.get() == "1"
    app._event(dict(type="presence", creator="bob", state="live", live=True, checked_at=time.time()))
    app._event(dict(type="presence", creator="alice", state="error", live=None, checked_at=time.time()))
    app._refresh_creators()
    assert app.creators.item("bob", "values")[1] == "LIVE"
    assert app.creators.item("alice", "values")[1] == "Check failed"
    assert app.guest_text.get() == "1"
    app.selected_creator.set("bob")
    app._choose_creator()
    assert app.guest_text.get() == "—"
    assert app.panel_data is None


def test_invalid_time_limit_shows_error_before_start(app, monkeypatch):
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kwargs: errors.append(args))
    app.duration.set("not a number")
    app.start()
    assert errors
    assert not app.manager.is_running


def test_recordings_list_excludes_unfinished_raw_capture(app):
    (app.root_dir / "recordings").mkdir()
    for name in ("video_flv.mp4", "video.mp4", "video_panel.mp4"):
        (app.root_dir / "recordings").mkdir(exist_ok=True)
        (app.root_dir / "recordings" / name).write_bytes(b"video")
    app._scan_recordings()
    assert {p.name for p in app.recent_paths.values()} == {"video.mp4", "video_panel.mp4"}


def test_login_error_gives_user_a_clear_import_action(app):
    app._event(dict(type="state", state="error", message="TikTok requires login; refresh cookies."))
    assert app.status_text.get() == "Login needed"
    assert "TikTok login" in app.notice.get()


def test_gui_import_keeps_credentials_out_of_settings_and_log(app):
    from utils.login_store import load_login_settings
    path = app.root_dir / "export.json"
    path.write_text(json.dumps({"format": "tiktok-recorder-cookies", "version": 1,
        "site": "https://www.tiktok.com", "cookies": [{"name": "sessionid_ss",
        "value": "FAKE_GUI_TEST_SESSION", "domain": ".tiktok.com", "path": "/"}]}), encoding="utf-8")
    app._import_login(path)
    app._save_settings()
    assert load_login_settings(app.root_dir)["sessionid_ss"] == "FAKE_GUI_TEST_SESSION"
    assert "not yet verified" in app.login_text.get()
    assert "FAKE_GUI_TEST_SESSION" not in app.settings_path.read_text(encoding="utf-8")
    assert "FAKE_GUI_TEST_SESSION" not in app.log.get("1.0", "end")


def test_login_import_waits_for_all_recording_sessions(app):
    app.manager.start(target="alice")
    with pytest.raises(RuntimeError, match="Finish the current recording"):
        app._import_login(app.root_dir / "does-not-exist.json")


def test_removed_creator_results_are_ignored(app):
    app.history.add("alice")
    app.history.remove("alice")
    app._event(dict(type="presence", creator="alice", state="live", live=True, checked_at=time.time()))
    assert "alice" not in app.presence


def test_elapsed_time_freezes_after_session_finishes(app, monkeypatch):
    monkeypatch.setattr(gui.time, "monotonic", lambda: 999)
    assert app._elapsed(10, 75) == "00:01:05"


def test_generic_failure_updates_idle_badge(app):
    app._event(dict(type="state", state="error", message="This creator has ended their LIVE."))
    assert app.status_text.get() == "Needs attention"


def test_rearmed_automatic_watch_overrides_old_cancelled_label(app):
    app.history.add("alice")
    app.manager.jobs["alice"] = dict(creator="alice", state="finished", cancelled=True, started_at=None)
    app.auto_record["alice"] = {}
    app._refresh_creators()
    assert app.creators.item("alice", "values")[2] == "Watching LIVE"


def test_automatic_mode_watches_all_saved_live_creators(app):
    for creator in ("alice", "bob", "offline"):
        app.history.add(creator)
    app.presence = {creator: dict(live=creator != "offline", checked_at=time.time())
                    for creator in app.history.creators}
    app.mode.set("Monitor automatically")
    assert set(app.auto_record) == {"alice", "bob", "offline"}
    assert app.monitoring.get()
    assert {options["target"] for options in app.manager.options} == {"alice", "bob"}
    app._start_due_automatic()
    assert len(app.manager.options) == 2
    app.target.set("newcreator")
    app._add_creator()
    assert "newcreator" in app.auto_record
    app.mode.set("Record now")
    assert app.auto_record == {}
    assert app.manager.stopped == []


def test_stop_all_does_not_rearm_automatic_creators(app):
    app.history.add("alice")
    app.mode.set("Monitor automatically")
    app.stop()
    app._start_due_automatic()
    assert app.auto_record == {}
    assert app.manager.options == []


def test_recording_indicator_pulses_only_for_active_capture(app, monkeypatch):
    app._set_controls()
    assert app.recording_dot.cget("text_color") == app.colors["MUTED"]
    app.manager.jobs["alice"] = dict(creator="alice", state="recording", started_at=0)
    monkeypatch.setattr(gui.time, "monotonic", lambda: 0)
    app._set_controls()
    first = app.recording_dot.cget("text_color")
    monkeypatch.setattr(gui.time, "monotonic", lambda: 0.25)
    app._set_controls()
    assert app.recording_dot.cget("text_color") != first
    app.manager.jobs["alice"]["state"] = "finalizing"
    app._set_controls()
    assert app.recording_dot.cget("text_color") == app.colors["MUTED"]


def test_saving_banner_shows_stage_progress_and_safe_closing(app):
    app.manager.jobs["alice"] = dict(creator="alice", state="finalizing",
        message="Encoding combined video", progress=.42)
    app._set_controls()
    assert app.saving_panel.winfo_manager() == "pack"
    assert "42% of this stage" in app.saving_label.cget("text")
    assert app.saving_bar_mode == "determinate"
    assert app.saving_bar.get() == pytest.approx(.42)
    app.manager.jobs["alice"]["progress"] = None
    app.closing = True
    app._set_controls()
    assert "before closing" in app.saving_label.cget("text")
    assert app.saving_bar_mode == "indeterminate"
    app.manager.jobs["alice"]["state"] = "finished"
    app._set_controls()
    assert not app.saving_panel.winfo_manager()
    assert app.saving_bar_mode is None


def test_saving_progress_does_not_flood_activity_log(app):
    for value in (.1, .2, .3):
        app._event(dict(type="state", state="finalizing", creator="alice", message="Encoding", progress=value))
    assert "Encoding" not in app.log.get("1.0", "end")


def test_failed_save_prevents_automatic_close(app, monkeypatch):
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kwargs: errors.append(args))
    app.manager.jobs["alice"] = dict(creator="alice", state="error", message="Capture preserved for recovery")
    app.closing = True
    app._tick()
    assert app.closing is False
    assert errors and "Capture preserved" in errors[0][1]
    assert app.root.winfo_exists()
    assert "Activity Log" in app.notice.get()


def test_pill_has_visible_border_and_inset_thumb_in_both_states(app):
    switch = app.comments_check
    assert switch.cget("border_color")[0] == "#52627e"
    for enabled in (False, True):
        app.comments_enabled.set(enabled)
        x, y = switch._canvas.coords("pill_thumb_a")
        width = switch._apply_widget_scaling(switch.cget("switch_width"))
        height = switch._apply_widget_scaling(switch.cget("switch_height"))
        assert y == pytest.approx(height / 2)
        assert 0 < x < width
        font = switch._canvas.itemcget("pill_thumb_a", "font")
        radius = abs(int(switch.tk.splitlist(font)[1])) / 2
        assert radius < height / 2
        assert x - radius > 0 and x + radius < width


def test_creator_filters_are_view_only_and_live_results_must_be_fresh(app):
    for name in ("alice", "bob", "carol"):
        app.history.add(name)
    app.mode.set("Monitor automatically")
    app.presence = {"alice": dict(live=True, checked_at=time.time()),
                    "bob": dict(live=True, checked_at=time.time()-300)}
    app._refresh_creators()
    app.creator_filter.set("LIVE")
    assert app.creators.get_children() == ("alice",)
    assert set(app.auto_record) == {"alice", "bob", "carol"}
    assert app.history.creators == ["alice", "bob", "carol"]
    app.creator_query.set("@ALICE")
    assert app.creators.get_children() == ("alice",)
    app.creator_query.set("bob")
    assert app.creators.get_children() == ()
    app.creator_filter.set("All")
    assert app.creators.get_children() == ("bob",)
    app.creator_query.set("")
    app.manager.jobs["carol"] = dict(creator="carol", state="finalizing")
    app.creator_filter.set("Active")
    assert app.creators.get_children() == ("carol",)
    app.creator_filter.set("All")
    assert set(app.creators.get_children()) == {"alice", "bob", "carol"}


def test_recording_search_retains_selection_and_latest_ignores_filter(app, monkeypatch):
    import os
    folder = app.root_dir / "recordings"
    folder.mkdir()
    older, newer = folder/"alice.mp4", folder/"bob.mp4"
    older.write_bytes(b"old")
    newer.write_bytes(b"new")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    app._scan_recordings()
    assert list(app.recent_paths.values()) == [newer, older]
    app.files.selection_set(str(older))
    newer.write_bytes(b"new and larger")
    app._scan_recordings()
    assert app.files.selection() == (str(older),)
    app.recording_query.set("ALICE")
    assert list(app.recent_paths.values()) == [older]
    played = []
    monkeypatch.setattr(app, "_play", played.append)
    app._play_latest()
    assert played == [newer]
    app.recording_query.set("not-found")
    assert not app.recent_paths
    assert app.recording_count.get().startswith("0 of 2")


def test_storage_results_ignore_old_folder_and_show_low_space_without_stopping(app):
    app._event(dict(type="storage", folder="old-folder", free=1))
    assert app.storage_free is None
    app._event(dict(type="storage", folder=app.output.get(), free=1024**3))
    assert "Low disk space" in app.storage_text.get()
    assert app.storage_label.cget("text_color") == app.colors["ERROR"]
    assert app.manager.stopped == []
    app._event(dict(type="storage", folder=app.output.get(), free=None))
    assert "could not be checked" in app.storage_text.get()
    app._event(dict(type="storage", folder=app.output.get(), free=20*1024**3))
    assert app.storage_text.get() == "20.0 GB free"
    assert app.storage_label.cget("text_color") == app.colors["MUTED"]


def test_watch_all_action_can_rearm_after_stop_without_duplicate_error(app, monkeypatch):
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kwargs: errors.append(args))
    app.history.add("alice")
    app.history.add("bob")
    app.mode.set("Monitor automatically")
    app.target.set("alice")
    app.stop()
    app.start()
    assert set(app.auto_record) == {"alice", "bob"}
    assert not errors
    app.start()
    assert not errors


def test_creator_headings_sort_and_online_only_preserves_watches(app):
    for name in ("zoe", "alice", "bob"):
        app.history.add(name)
    app.monitoring.set(True)
    app.presence = {"zoe": dict(state="live", live=True, checked_at=time.time()),
                    "alice": dict(state="offline", live=False, checked_at=time.time())}
    app._refresh_creators()
    assert app.creators.get_children() == ("alice", "bob", "zoe")
    app._sort_creators("creator")
    assert app.creators.get_children() == ("zoe", "bob", "alice")
    app._sort_creators("live")
    assert app.creators.get_children()[0] == "zoe"
    app.online_only.set(True)
    assert app.creators.get_children() == ("zoe",)
    app._event(dict(type="presence", creator="zoe", state="checking", checked_at=time.time(), live=None))
    app._refresh_creators()
    assert app.creators.get_children() == ("zoe",)
    assert app.history.creators == ["zoe", "alice", "bob"]




def test_separate_is_default_and_session_subfolders_appear_in_library(app):
    assert app.output_layout.get() == "separate"
    folder = app.root_dir / "recordings" / "TK_alice_2026.11.21_03-00-00"
    folder.mkdir(parents=True)
    (folder / "alice_feed.mp4").write_bytes(b"feed")
    (folder / "alice_comments.mp4").write_bytes(b"comments")
    recovery = folder.parent / ".recording-private"
    recovery.mkdir()
    (recovery / "unfinished.mp4").write_bytes(b"private capture")
    app._scan_recordings()
    assert {path.name for path in app.recent_paths.values()} == {"alice_feed.mp4", "alice_comments.mp4"}




def test_live_release_does_not_load_or_change_private_creator_stats(tmp_path, monkeypatch, gui_root):
    from pathlib import Path
    stats_file = tmp_path / "creator-stats.json"
    stats_file.write_bytes(b"private preview history")
    original_read = Path.read_text
    def checked_read(path, *args, **kwargs):
        assert path != stats_file, "Live app must not read preview statistics"
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", checked_read)
    monkeypatch.setattr(gui, "MultiRecorder", FakeManager)
    monkeypatch.setattr(gui, "CreatorMonitor", FakeMonitor)
    root = tk.Toplevel(gui_root)
    root.withdraw()
    try:
        live_app = gui.RecorderApp(root, tmp_path)
        assert not hasattr(live_app, "stats")
        assert not hasattr(live_app, "_open_stats")
        assert not Path(gui.__file__).with_name("creator_stats.py").exists()
        assert not Path(gui.__file__).with_name("stats_guest_observer.py").exists()
        live_app.history.add("alice")
        live_app.monitoring.set(True)
        live_app._event(dict(type="presence", creator="alice", state="live", live=True,
            checked_at=time.time(), room_id="123", host_id="1"))
        live_app.monitor.stop()
    finally:
        root.destroy()
    assert stats_file.read_bytes() == b"private preview history"
