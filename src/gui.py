"""Windows desktop interface for the local TikTok LIVE recorder."""

import json
import math
import os
import queue
import shutil
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from glass_ui import Button, Entry, PillSwitch, ChoicePills, build_shell, palette
from glass_ui import label as native_label
from glass_ui import CARD as CTK_CARD
from glass_ui import configure_if_changed, set_if_changed
from core.output_options import OutputOptions

from gui_backend import parse_target
from multi_backend import MultiRecorder
from creator_monitor import CreatorHistory, CreatorMonitor
from utils.login_store import has_login, load_login_settings, read_cookie_export, save_login_settings
from app_paths import application_dir, data_dir

ROOT_DIR = data_dir()
BG, CARD, FIELD, TEXT, MUTED, LINE, PINK, TEAL = (
    palette()[key] for key in ("BG", "CARD", "FIELD", "TEXT", "MUTED", "LINE", "ACCENT", "GREEN"))


class RecorderApp:
    def __init__(self, root, root_dir=ROOT_DIR):
        self.root, self.root_dir = root, Path(root_dir)
        self.events = queue.Queue()
        self.settings_path = self.root_dir / "gui-settings.json"
        self.panel_data = None
        self.state = "ready"
        self.closing = False
        self.recent_paths = {}
        self.last_scan = 0
        self.last_storage_check = 0
        self._storage_pending = False
        self.library_records = []
        self.presence = {}
        self.panels = {}
        self.auto_record = {}
        self.auto_due = {}
        settings = self._load_settings()
        self.dark_mode = tk.BooleanVar(value=bool(settings.get("dark_mode", False)))
        self._set_palette()
        self.target = tk.StringVar(value=settings.get("target", ""))
        self.output = tk.StringVar(value=settings.get("output", str(self.root_dir / "recordings")))
        self.mode = tk.StringVar(value=settings.get("mode", "Record now"))
        self.panel_enabled = tk.BooleanVar(value=settings.get("guests", settings.get("panel", True)))
        self.comments_enabled = tk.BooleanVar(value=settings.get("comments", settings.get("panel", True)))
        self.output_layout = tk.StringVar(value=settings.get("layout", "separate") if settings.get("layout", "separate") in ("combined", "separate") else "separate")
        self.output_summary = tk.StringVar()
        self.creator_query = tk.StringVar()
        self.creator_filter = tk.StringVar(value="All")
        self.online_only = tk.BooleanVar(value=False)
        self.creator_sort_column = "creator"
        self.creator_sort_reverse = False
        self.creator_count = tk.StringVar()
        self.recording_query = tk.StringVar()
        self.recording_count = tk.StringVar()
        self.storage_text = tk.StringVar(value="Checking available space…")
        self.storage_free = None
        self.duration = tk.StringVar(value=str(settings.get("duration", 0)))
        self.interval = tk.StringVar(value=str(settings.get("interval", 5)))
        try:
            limit = max(1, min(12, int(settings.get("max_concurrent", 2))))
        except (TypeError, ValueError):
            limit = 2
        self.monitoring = tk.BooleanVar(value=bool(settings.get("monitoring", False)))
        self.manager = MultiRecorder(self.root_dir, self.events.put, max_concurrent=limit)
        self.history = CreatorHistory(self.root_dir)
        self.history.seed(self.output.get(), targets=(self.target.get(),))
        self.monitor = CreatorMonitor(self.root_dir, self.events.put, interval=60)
        self.selected_creator = tk.StringVar(value=self.history.creators[0] if self.history.creators else "")
        try:
            selected, _ = parse_target(self.target.get())
            if selected.lower() in self.history.creators:
                self.selected_creator.set(selected.lower())
        except ValueError:
            pass
        self.status_text = tk.StringVar(value="Ready to record")
        self.elapsed_text = tk.StringVar(value="00:00:00")
        self.guest_text = tk.StringVar(value="—")
        self.session_text = tk.StringVar(value=f"0 / {limit} active")
        self.notice = tk.StringVar(value="Enter a creator, then start recording.")
        self.login_text = tk.StringVar(value="TikTok login: not set")
        self._refresh_login_status()
        root.title("TikTok Live Recorder")
        # CTk sizes are logical pixels; Windows DPI is applied by the native root.
        scale = root._get_window_scaling() if hasattr(root, "_get_window_scaling") else 1
        # Tk caches logical screen dimensions before CTk enables DPI awareness.
        # Avoid applying the DPI divisor twice on a 4K Windows desktop.
        width = min(1360, root.winfo_screenwidth() - 60)
        height = min(1000, root.winfo_screenheight() - 90)
        root.geometry(f"{width}x{height}")
        root.minsize(min(1100, width), min(680, height))
        root.configure(**({"fg_color": BG} if isinstance(root, (ctk.CTk, ctk.CTkToplevel)) else {"bg": BG}))
        self._style()
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.bind("<Control-Return>", lambda _: self.start())
        self._scan_recordings()
        self._refresh_creators()
        self._request_storage_check()
        if self.monitoring.get():
            self.monitor.start(self.history.creators)
        if self.mode.get() == "Monitor automatically":
            self._watch_all_creators()
        root.after(150, self._tick)

    def _load_settings(self):
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        value = dict(target=self.target.get().strip(), output=self.output.get(),
                     mode=self.mode.get(), panel=self.panel_enabled.get(), guests=self.panel_enabled.get(),
                     comments=self.comments_enabled.get(), layout=self.output_layout.get(),
                     duration=int(self.duration.get() or 0), interval=int(self.interval.get() or 5))
        value.update(max_concurrent=self.manager.max_concurrent, monitoring=self.monitoring.get(),
                     dark_mode=self.dark_mode.get())
        try:
            temporary = self.settings_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
            temporary.replace(self.settings_path)
        except OSError:
            pass

    def _set_palette(self):
        global BG, CARD, FIELD, TEXT, MUTED, LINE, PINK, TEAL
        self.colors = palette(self.dark_mode.get())
        BG, CARD, FIELD, TEXT, MUTED, LINE, PINK, TEAL = (
            self.colors[key] for key in ("BG", "CARD", "FIELD", "TEXT", "MUTED", "LINE", "ACCENT", "GREEN"))
        ctk.set_appearance_mode("dark" if self.dark_mode.get() else "light")

    def _toggle_dark_mode(self):
        self._set_palette()
        self.root.configure(**({"fg_color": BG} if isinstance(self.root, (ctk.CTk, ctk.CTkToplevel)) else {"bg": BG}))
        self._style()
        self.log.configure(bg=FIELD, fg=MUTED, insertbackground=TEXT)
        self._apply_creator_colors()
        self._set_controls()
        self._apply_storage_color()
        self._save_settings()

    def _apply_creator_colors(self):
        self.creators.tag_configure("live", foreground=TEAL)
        self.creators.tag_configure("recording", foreground=PINK)
        self.creators.tag_configure("error", foreground=self.colors["ERROR"])

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        scale = self.root._get_window_scaling() if hasattr(self.root, "_get_window_scaling") else 1
        self.root.tk.call("tk", "scaling", scale * 96 / 72)
        self.root.option_add("*Font", "{Segoe UI} 11")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=CARD, foreground=TEXT)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, padding=9, bordercolor=LINE,
                        insertcolor=TEXT)
        style.configure("TSpinbox", fieldbackground=FIELD, background=LINE, foreground=TEXT, arrowcolor=MUTED, bordercolor=LINE, insertcolor=TEXT)
        style.configure("TCombobox", fieldbackground=FIELD, background=LINE, foreground=TEXT, padding=8,
                        arrowcolor=MUTED)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        style.configure("TCheckbutton", background=CARD, foreground=TEXT, padding=6)
        style.map("TCheckbutton", background=[("active", CARD)])
        style.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=TEXT,
                        rowheight=round(42 * scale), borderwidth=0, bordercolor=LINE,
                        lightcolor=LINE, darkcolor=LINE,
                        font=("Segoe UI", -round(13 * scale)))
        style.configure("Vertical.TScrollbar", background=LINE, troughcolor=FIELD,
                        bordercolor=CARD, arrowcolor=MUTED, lightcolor=LINE, darkcolor=LINE)
        style.map("Vertical.TScrollbar", background=[("active", self.colors["HOVER"])])
        style.configure("Treeview.Heading", background=CARD, foreground=MUTED, padding=10,
                        font=("Segoe UI", -round(11 * scale), "bold"), relief="flat")
        style.map("Treeview.Heading", background=[("active", self.colors["HOVER"]), ("pressed", self.colors["HOVER"])],
                  foreground=[("active", TEXT)])
        style.map("Treeview", background=[("selected", self.colors["SELECTED"])],
                  foreground=[("selected", self.colors["SELECTED_TEXT"])])

    def _update_mode(self):
        automatic = self.mode.get() == "Monitor automatically"
        set_if_changed(self.mode_display, "Automatic" if automatic else "Manual")
        if automatic:
            self.auto_warning.grid()
        else:
            self.auto_warning.grid_remove()
        configure_if_changed(self.start_button, text="Watch all creators" if automatic else "Start recording")
        if hasattr(self, "creators"):
            if automatic:
                self._watch_all_creators()
            else:
                self.auto_record.clear()
                self.auto_due.clear()
                self._save_settings()
                self._refresh_creators()
                self._set_controls()

    def _watch_all_creators(self):
        """Automatic mode watches the entire saved list, including offline creators."""
        if self.closing:
            return
        for creator in list(self.history.creators):
            if creator not in self.auto_record:
                try:
                    self._start_creator(creator)
                except (ValueError, OSError, RuntimeError) as exc:
                    self.notice.set(str(exc))
                    return
        if not self.monitoring.get():
            self.monitoring.set(True)
            self._toggle_monitoring()
        self.monitor.refresh()
        self._start_due_automatic()
        self.notice.set("Automatic recording is watching all saved creators. LIVE creators record as slots become available.")
        self._save_settings()
        self._set_controls()


    def _update_output_summary(self):
        selected = OutputOptions(self.panel_enabled.get(), self.comments_enabled.get(), self.output_layout.get())
        set_if_changed(self.output_summary, selected.summary)
        if hasattr(self, "layout_display"):
            set_if_changed(self.layout_display, "Separate clips" if selected.layout == "separate" else "Combined video")

    def _build(self):
        self.tabs = build_shell(self)
        creators = ttk.Frame(self.tabs.content, style="Card.TFrame", padding=8)
        recordings = ttk.Frame(self.tabs.content, style="Card.TFrame", padding=8)
        activity = ttk.Frame(self.tabs.content, style="Card.TFrame", padding=8)
        self.tabs.add(creators, text="Creators")
        self.tabs.add(recordings, text="Recordings")
        self.tabs.add(activity, text="Activity Log")
        monitor_bar = ttk.Frame(creators, style="Card.TFrame")
        monitor_bar.pack(fill="x", pady=(0, 9))
        self.monitor_switch = PillSwitch(monitor_bar, text="Monitor LIVE status",
            variable=self.monitoring, command=self._toggle_monitoring)
        self.monitor_switch.pack(side="left")
        Button(monitor_bar, text="Check now", command=self._check_now).pack(side="right")
        ttk.Label(creators, text="Ctrl-click to select several creators. LIVE status updates every minute.",
                  style="Muted.TLabel", wraplength=680).pack(anchor="w", pady=(0, 12))
        creator_filters = ctk.CTkFrame(creators, fg_color=CTK_CARD, corner_radius=0)
        creator_filters.pack(fill="x", pady=(0, 6))
        creator_filters.columnconfigure(0, weight=1)
        native_label(creator_filters, "Search creators", size=11, muted=True).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.creator_search_entry = Entry(creator_filters, textvariable=self.creator_query,
            placeholder_text="Search creators")
        self.creator_search_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.creator_filter_control = ChoicePills(creator_filters,
            values=["All", "LIVE", "Active"], variable=self.creator_filter)
        self.creator_filter_control.grid(row=1, column=1, sticky="e")
        self.online_switch = PillSwitch(creator_filters, text="Show online only", variable=self.online_only)
        self.online_switch.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(creators, textvariable=self.creator_count, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        creator_table = ttk.Frame(creators, style="Card.TFrame")
        creator_table.pack(fill="both", expand=True)
        self.creators = ttk.Treeview(creator_table, columns=("creator", "live", "recording", "elapsed", "checked"),
                                     show="headings", selectmode="extended", height=8)
        for name, label, width in (("creator", "CREATOR", 190), ("live", "LIVE STATUS", 110),
                                   ("recording", "RECORDING", 130), ("elapsed", "TIME", 85),
                                   ("checked", "CHECKED", 95)):
            self.creators.heading(name, text=label + (" ▲" if name == "creator" else ""), command=lambda column=name: self._sort_creators(column))
            scale = self.root._get_window_scaling() if hasattr(self.root, "_get_window_scaling") else 1
            self.creators.column(name, width=round(width * scale), minwidth=round(70 * scale), stretch=name == "creator")
        self._apply_creator_colors()
        scrollbar = ttk.Scrollbar(creator_table, orient="vertical", command=self.creators.yview)
        scrollbar.pack(side="right", fill="y")
        self.creators.configure(yscrollcommand=scrollbar.set)
        self.creators.pack(side="left", fill="both", expand=True)
        self.creators.bind("<<TreeviewSelect>>", self._select_creator)
        creator_actions = ttk.Frame(creators, style="Card.TFrame")
        creator_actions.pack(side="bottom", before=creator_table, fill="x", pady=(12, 0))
        Button(creator_actions, text="Record selected", style="Start.TButton",
                   command=self._record_selected).pack(side="left")
        Button(creator_actions, text="Stop selected", command=self._stop_selected).pack(side="left", padx=(8, 0))
        Button(creator_actions, text="Remove", command=self._remove_selected).pack(side="right")
        toolbar = ttk.Frame(recordings, style="Card.TFrame")
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Label(toolbar, text="Saved videos", font=("Segoe UI", 13, "bold")).pack(side="left")
        Button(toolbar, text="Refresh", command=self._scan_recordings).pack(side="right")
        Button(toolbar, text="Open folder", command=self._open_folder).pack(side="right", padx=(0, 8))
        library_search = ctk.CTkFrame(recordings, fg_color=CTK_CARD, corner_radius=0)
        library_search.pack(fill="x", pady=(0, 8))
        native_label(library_search, "Search saved recordings", size=11, muted=True).pack(anchor="w", pady=(0, 4))
        self.recording_search_entry = Entry(library_search, textvariable=self.recording_query,
            placeholder_text="Search saved recordings")
        self.recording_search_entry.pack(fill="x")
        ttk.Label(recordings, textvariable=self.recording_count, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        library_table = ttk.Frame(recordings, style="Card.TFrame")
        library_table.pack(fill="both", expand=True)
        self.files = ttk.Treeview(library_table, columns=("name", "saved", "size"), show="headings", selectmode="browse")
        self.files.heading("name", text="RECORDING")
        self.files.heading("saved", text="SAVED")
        self.files.column("saved", width=120, minwidth=100, stretch=False)
        self.files.heading("size", text="SIZE")
        self.files.column("name", width=390, minwidth=200)
        self.files.column("size", width=85, minwidth=65, stretch=False, anchor="e")
        file_scroll = ttk.Scrollbar(library_table, orient="vertical", command=self.files.yview)
        file_scroll.pack(side="right", fill="y")
        self.files.configure(yscrollcommand=file_scroll.set)
        self.files.pack(side="left", fill="both", expand=True)
        self.files.bind("<Double-1>", lambda _: self._play_selected())
        self.files.bind("<Return>", lambda _: self._play_selected())
        Button(recordings, text="Play selected video", command=self._play_selected).pack(anchor="e", pady=(12, 0))
        self.log = tk.Text(activity, bg=FIELD, fg=MUTED, insertbackground=TEXT, relief="flat",
                           wrap="word", padx=12, pady=12, font=("Consolas", 10), state="disabled")
        self.log.pack(fill="both", expand=True)
        self.creator_query.trace_add("write", lambda *_: self._refresh_creators())
        self.creator_filter.trace_add("write", lambda *_: self._refresh_creators())
        self.online_only.trace_add("write", lambda *_: self._refresh_creators())
        self.recording_query.trace_add("write", lambda *_: self._render_recordings())
        self.output.trace_add("write", lambda *_: self._request_storage_check(force=True))
        self.creator_search_entry.bind("<Escape>", lambda _: self.creator_query.set(""))
        self.recording_search_entry.bind("<Escape>", lambda _: self.recording_query.set(""))

    def start(self):
        try:
            if self.mode.get() == "Monitor automatically":
                target = self.target.get().strip()
                if target:
                    self.history.add(target)
                self._watch_all_creators()
            else:
                self._start_creator(self.target.get().strip())
        except (ValueError, OSError, RuntimeError) as exc:
            self.notice.set(str(exc))
            messagebox.showerror("Cannot start recording", str(exc), parent=self.root)

    def _start_creator(self, target):
        if self.closing:
            return
        duration, interval = int(self.duration.get() or 0), int(self.interval.get() or 5)
        if duration < 0 or interval < 1:
            raise ValueError("Use a recording limit of 0 or more and a check interval of at least 1 minute.")
        creator = self.history.add(target)
        options = dict(target=creator, output=self.output.get().strip(), mode="manual",
                       panel=self.panel_enabled.get() or self.comments_enabled.get(), duration=duration, interval=interval,
                       guests=self.panel_enabled.get(), comments=self.comments_enabled.get(), layout=self.output_layout.get())
        if not options["output"]:
            raise ValueError("Choose a folder for saved recordings.")
        if self.mode.get() == "Monitor automatically":
            if creator in self.auto_record:
                raise RuntimeError(f"Already watching @{creator} for automatic recording.")
            self.auto_record[creator] = options
            self.auto_due[creator] = 0
            if not self.monitoring.get():
                self.monitoring.set(True)
                self._toggle_monitoring()
            self.monitor.refresh()
            self.notice.set(f"Watching @{creator}. Recording starts when LIVE and a slot is available.")
        else:
            self.manager.start(**options)
            self.panels.pop(creator, None)
            self.notice.set(f"@{creator} added to recordings. Extra selections wait for a free slot.")
        self.selected_creator.set(creator)
        self.monitor.set_creators(self.history.creators)
        self._save_settings()
        self._refresh_creators()
        self._choose_creator()
        self._set_controls()

    def stop(self):
        self.auto_record.clear()
        self.auto_due.clear()
        self.manager.stop_all()
        self.notice.set("Stopping recordings. Keep this window open while videos finish saving.")
        self._set_controls()

    def _add_creator(self):
        try:
            creator = self.history.add(self.target.get())
        except (ValueError, OSError) as exc:
            messagebox.showerror("Cannot add creator", str(exc), parent=self.root)
            return
        self.monitor.set_creators(self.history.creators)
        self.selected_creator.set(creator)
        self._refresh_creators()
        self.creators.selection_set(creator)
        self.notice.set(f"@{creator} saved to your creator list.")
        if self.mode.get() == "Monitor automatically":
            self._watch_all_creators()

    def _record_selected(self):
        selected = self.creators.selection()
        if not selected:
            self.notice.set("Select one or more creators in the list first.")
            return
        errors = []
        for creator in selected:
            try:
                self._start_creator(creator)
            except (ValueError, OSError, RuntimeError) as exc:
                errors.append(str(exc))
        if errors:
            self.notice.set(errors[0])

    def _stop_selected(self):
        for creator in self.creators.selection():
            self.auto_record.pop(creator, None)
            self.auto_due.pop(creator, None)
            self.manager.stop(creator)
        self._refresh_creators()
        self._set_controls()

    def _remove_selected(self):
        busy = {s["creator"] for s in self.manager.snapshot() if s["state"] not in ("finished", "error")}
        for creator in self.creators.selection():
            if creator in busy or creator in self.auto_record:
                self.notice.set(f"Stop @{creator} before removing it from the list.")
                continue
            try:
                self.history.remove(creator)
            except (OSError, ValueError):
                self.notice.set("The creator list could not be saved. Try removing the creator again.")
                continue
            self.presence.pop(creator, None)
            self.panels.pop(creator, None)
        self.monitor.set_creators(self.history.creators)
        if self.selected_creator.get() not in self.history.creators:
            self.selected_creator.set(self.history.creators[0] if self.history.creators else "")
        self._refresh_creators()
        self._choose_creator()

    def _toggle_monitoring(self):
        if self.monitoring.get():
            self.monitor.start(self.history.creators)
        else:
            self.monitor.stop()
            self.auto_record.clear()
            self.auto_due.clear()
        self._save_settings()
        self._refresh_creators()

    def _check_now(self):
        if not self.monitoring.get():
            self.monitoring.set(True)
            self._toggle_monitoring()
        self.monitor.refresh()

    def _select_creator(self, _event=None):
        selected = self.creators.selection()
        if selected:
            self.selected_creator.set(selected[0])
            self.target.set(selected[0])
            self._choose_creator()

    def _choose_creator(self):
        self.panel_data = self.panels.get(self.selected_creator.get())
        snapshots = (self.panel_data or {}).get("timeline", {}).get("snapshots", [])
        self.guest_text.set(str(max(0, len(snapshots[-1]["ids"]) - 1)) if snapshots else "—")

    @staticmethod
    def _elapsed(started_at, ended_at=None):
        if started_at is None:
            return "—"
        seconds = max(0, int((ended_at if ended_at is not None else time.monotonic()) - started_at))
        return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"

    def _refresh_creators(self):
        sessions = {s["creator"]: s for s in self.manager.snapshot()}
        all_names = self.history.creators
        query = self.creator_query.get().strip().lstrip("@").casefold()
        view = self.creator_filter.get()
        now = time.time()
        live = {name for name in all_names if self.monitoring.get()
                and self.presence.get(name, {}).get("live") is True
                and now - (self.presence[name].get("checked_at") or 0) <= 120}
        active = {name for name, session in sessions.items() if session.get("state")
                  not in (None, "finished", "error")}
        names = [name for name in all_names if query in name.casefold()
                 and (view != "LIVE" or name in live) and (not self.online_only.get() or name in live)
                 and (view != "Active" or name in active)]
        live_rank = {"live": 0, "checking": 1, "offline": 2, "error": 3}
        def sort_key(name):
            row, presence = sessions.get(name, {}), self.presence.get(name, {})
            column = self.creator_sort_column
            if column == "live":
                return (live_rank.get(presence.get("state"), 4), name.casefold())
            if column == "recording":
                return (row.get("state", ""), name.casefold())
            if column == "elapsed":
                started = row.get("started_at")
                elapsed = max(0, (row.get("ended_at") or time.monotonic()) - started) if started is not None else -1
                return (elapsed, name.casefold())
            if column == "checked":
                return (presence.get("checked_at") or 0, name.casefold())
            return name.casefold()
        names.sort(key=sort_key, reverse=self.creator_sort_reverse)
        set_if_changed(self.creator_count, f"{len(names)} of {len(all_names)} creators · {len(live)} LIVE · {len(active)} active")
        for creator in set(self.creators.get_children()) - set(names):
            self.creators.delete(creator)
        live_labels = dict(live="LIVE", offline="Offline", error="Check failed", checking="Checking…")
        recording_labels = dict(connecting="Connecting", recording="Recording", waiting="Waiting",
                                finalizing="Saving video", queued="Queued", stopping="Stopping",
                                finished="Saved", error="Needs attention")
        for position, creator in enumerate(names):
            presence = self.presence.get(creator, {})
            session = sessions.get(creator, {})
            state = session.get("state")
            recording = recording_labels.get(state, "—")
            if state == "finalizing" and session.get("progress") is not None:
                recording = f"Saving {session['progress']:.0%}"
            if session.get("cancelled"):
                recording = "Cancelled"
            if creator in self.auto_record and state in (None, "finished", "error"):
                recording = "Watching LIVE"
            checked = presence.get("checked_at")
            checked_text = time.strftime("%H:%M:%S", time.localtime(checked)) if checked else "—"
            live = live_labels.get(presence.get("state"), "Not checked")
            if not self.monitoring.get():
                live = "Paused"
            values = ("@" + creator, live, recording,
                      self._elapsed(session.get("started_at"), session.get("ended_at")), checked_text)
            tag = ""
            if state == "recording":
                tag = "recording"
            elif live == "LIVE":
                tag = "live"
            elif presence.get("state") == "error":
                tag = "error"
            if self.creators.exists(creator):
                if tuple(self.creators.item(creator, "values")) != values:
                    self.creators.item(creator, values=values, tags=(tag,))
            else:
                self.creators.insert("", "end", iid=creator, values=values, tags=(tag,))
            self.creators.move(creator, "", position)
        selected = sessions.get(self.selected_creator.get(), {})
        set_if_changed(self.elapsed_text, self._elapsed(selected.get("started_at"), selected.get("ended_at")))

    def _sort_creators(self, column):
        if self.creator_sort_column == column:
            self.creator_sort_reverse = not self.creator_sort_reverse
        else:
            self.creator_sort_column = column
            self.creator_sort_reverse = False
        headings = dict(creator="CREATOR", live="LIVE STATUS", recording="RECORDING", elapsed="TIME", checked="CHECKED")
        for key, title in headings.items():
            self.creators.heading(key, text=title + ((" ▼" if self.creator_sort_reverse else " ▲") if key == column else ""))
        self._refresh_creators()

    def _start_due_automatic(self):
        if self.closing:
            return
        sessions = {s["creator"]: s for s in self.manager.snapshot()}
        now = time.monotonic()
        for creator, options in list(self.auto_record.items()):
            if sessions.get(creator, {}).get("state") not in (None, "finished", "error"):
                continue
            presence = self.presence.get(creator, {})
            if presence.get("live") is not True or time.time() - (presence.get("checked_at") or 0) > 120:
                continue
            if now < self.auto_due.get(creator, 0):
                continue
            self.auto_due[creator] = now + options["interval"] * 60
            try:
                self.manager.start(**options)
                self.panels.pop(creator, None)
            except (ValueError, OSError, RuntimeError) as exc:
                self._append_log(f"@{creator} · {exc}")

    def _event(self, event):
        kind = event.get("type")
        creator = event.get("creator")
        if kind == "storage":
            self._storage_pending = False
            if event.get("folder") != self.output.get():
                self.last_storage_check = 0
                return
            self.storage_free = event.get("free")
            if self.storage_free is None:
                self.storage_text.set("Available space could not be checked")
            else:
                amount = self.storage_free / (1024 ** 3)
                self.storage_text.set(("Low disk space · " if amount < 2 else "") + f"{amount:.1f} GB free")
            self._apply_storage_color()
            return
        if kind == "presence":
            if creator in self.history.creators and self.monitoring.get():
                if event.get("state") == "checking":
                    previous = self.presence.get(creator, {})
                    event = dict(event, live=previous.get("live"), checked_at=previous.get("checked_at", event.get("checked_at")))
                self.presence[creator] = event
            return
        if kind == "log":
            self._append_log((f"@{creator} · " if creator else "") + event.get("message", ""))
        elif kind == "panel":
            if creator and event.get("timeline", {}).get("snapshots"):
                self.panels[creator] = event
                if creator == self.selected_creator.get():
                    self._choose_creator()
        elif kind == "state":
            self.state = event.get("state", "ready")
            message = event.get("message", "")
            if message:
                labelled = (f"@{creator} · " if creator else "") + message
                self.notice.set(labelled[:140])
                if "progress" not in event:
                    self._append_log(labelled)
            if self.state == "error":
                self.status_text.set("Needs attention")
                if any(text in message.lower() for text in ("login", "log in", "authenticate", "cookies")):
                    self.status_text.set("Login needed")
                    self.notice.set("Open TikTok login… and import your Chrome cookies, then try recording again.")
            if self.state in ("finished", "error"):
                self._scan_recordings()
            self._set_controls()

    def _set_controls(self):
        busy = self.manager.is_running or bool(self.auto_record)
        for widget in self.inputs:
            configure_if_changed(widget, state="disabled" if self.closing else "normal")
        configure_if_changed(self.login_button, state="disabled" if self.manager.is_running or self.closing else "normal")
        configure_if_changed(self.mode_combo, state="disabled" if self.closing else "normal")
        configure_if_changed(self.start_button, state="disabled" if self.closing else "normal")
        configure_if_changed(self.stop_button, state="normal" if busy and not self.closing else "disabled")
        sessions = self.manager.snapshot()
        self._update_saving_progress(sessions)
        active = sum(s["state"] == "recording" for s in sessions)
        if active:
            # One gentle pulse per second; use the existing UI tick, with no extra timer.
            strength = (1 + math.sin(time.monotonic() * 2 * math.pi)) / 2
            red = tuple(round(low + (high - low) * strength)
                        for low, high in zip((150, 35, 50), (255, 75, 90)))
            dot_color = "#%02x%02x%02x" % red
        else:
            dot_color = MUTED
        configure_if_changed(self.recording_dot, text_color=dot_color)

        queued = sum(s["state"] == "queued" for s in sessions)
        set_if_changed(self.session_text, f"{self.manager.active_count} / {self.manager.max_concurrent} active" +
                       (f" · {queued} queued" if queued else ""))
        if active:
            set_if_changed(self.status_text, f"{active} recording")
        elif self.manager.is_running:
            set_if_changed(self.status_text, "Saving videos" if any(s["state"] == "finalizing" for s in sessions) else "Connecting")
        elif self.auto_record:
            set_if_changed(self.status_text, f"Watching {len(self.auto_record)} creator{'s' if len(self.auto_record) != 1 else ''}")
        elif self.state != "error":
            set_if_changed(self.status_text, "Ready to record")
        configure_if_changed(self.badge, fg_color=("#e6eaff", "#27375a") if active else ("#def2eb", "#18382f"),
                             text_color=PINK if active else TEAL)

    def _update_saving_progress(self, sessions):
        saving = [s for s in sessions if s["state"] in ("stopping", "finalizing")]
        if not saving and not (self.closing and self.manager.is_running):
            self.saving_bar.stop()
            self.saving_bar_mode = None
            self.saving_panel.pack_forget()
            return
        if not self.saving_panel.winfo_manager():
            self.saving_panel.pack(fill="x", pady=(0, 12), before=self.saving_body)
        session = saving[0] if saving else {}
        fraction = session.get("progress") if session.get("state") == "finalizing" else None
        stage = session.get("message") or "Stopping capture…"
        creator = f"@{session['creator']} · " if session.get("creator") else ""
        heading = "Saving before closing" if self.closing else "Saving video"
        if len(saving) > 1:
            heading = f"Saving {len(saving)} videos" + (" before closing" if self.closing else "")
        text = f"{heading} · {creator}{stage}"
        if fraction is not None:
            text += f" · {fraction:.0%} of this stage"
        configure_if_changed(self.saving_label, text=text)
        mode = "indeterminate" if fraction is None else "determinate"
        if self.saving_bar_mode != mode:
            self.saving_bar.stop()
            self.saving_bar.configure(mode=mode)
            self.saving_bar_mode = mode
            if mode == "indeterminate":
                self.saving_bar.start()
        if fraction is not None:
            self.saving_bar.set(fraction)

    def _append_log(self, text):
        if not text:
            return
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {text}\n")
        if int(self.log.index("end-1c").split(".")[0]) > 700:
            self.log.delete("1.0", "100.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _tick(self):
        self.manager.poll()
        try:
            for _ in range(200):
                self._event(self.events.get_nowait())
        except queue.Empty:
            pass
        self._start_due_automatic()
        self._refresh_creators()
        self._request_storage_check()
        scale = self.root._get_window_scaling() if hasattr(self.root, "_get_window_scaling") else 1
        if scale != getattr(self, "_table_scale", None):
            self._table_scale = scale
            self._style()
        self._set_controls()
        if not self.manager.is_running and self.closing:
            errors = [s for s in self.manager.snapshot() if s["state"] == "error"]
            if errors:
                self.closing = False
                self.notice.set("Some recordings need attention. Check Activity Log before closing.")
                self._set_controls()
                messagebox.showerror("Recording needs attention", errors[0].get("message") or
                    "Saving failed. Check Activity Log for the recovery location.", parent=self.root)
            else:
                self.root.destroy()
                return
        if time.monotonic() - self.last_scan > 5:
            self._scan_recordings()
        self.root.after(200, self._tick)

    def _browse(self):
        path = filedialog.askdirectory(initialdir=self.output.get(), parent=self.root)
        if path:
            self.output.set(path)
            self._scan_recordings()

    def _refresh_login_status(self):
        try:
            configured = has_login(load_login_settings(self.root_dir))
            self.login_text.set("TikTok login: saved (not yet verified)" if configured else "TikTok login: not set")
        except (OSError, ValueError):
            self.login_text.set("TikTok login: import needed")

    def _import_login(self, path):
        if self.manager.is_running:
            raise RuntimeError("Finish the current recording before changing the TikTok login.")
        cookies = read_cookie_export(path)
        save_login_settings(self.root_dir, cookies)
        self._refresh_login_status()
        self.monitor.refresh()
        self.notice.set("TikTok login saved. Start recording to check access to this LIVE.")

    def _login(self):
        if self.manager.is_running:
            return
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("TikTok login")
        dialog.configure(fg_color=CARD)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        box = ttk.Frame(dialog, style="Card.TFrame", padding=24)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="Use your Chrome TikTok login", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(box, textvariable=self.login_text, style="Muted.TLabel").pack(anchor="w", pady=(7, 17))
        instructions = (
            "1. In Chrome, open chrome://extensions and turn on Developer mode.\n"
            "2. Choose Load unpacked and select the exporter folder below.\n"
            "3. Sign into TikTok in Chrome and open the creator’s LIVE.\n"
            "4. Click the TikTok Login Exporter extension, then Export TikTok login.\n"
            "5. Import the downloaded JSON file here, then start recording."
        )
        ttk.Label(box, text=instructions, justify="left", wraplength=650).pack(anchor="w", pady=(0, 16))
        actions = ttk.Frame(box, style="Card.TFrame")
        actions.pack(fill="x", pady=(0, 12))
        def open_folder():
            try:
                os.startfile(str(application_dir() / "browser-extension" / "tiktok-cookie-exporter"))
            except OSError:
                messagebox.showerror("Exporter folder unavailable", "Reinstall the TikTok Cookie Exporter folder.", parent=dialog)
        def open_live():
            try:
                username, _ = parse_target(self.target.get())
                url = f"https://www.tiktok.com/@{username}/live"
            except ValueError:
                url = "https://www.tiktok.com/"
            webbrowser.open(url)
        Button(actions, text="Open exporter folder", command=open_folder).pack(side="left")
        Button(actions, text="Open LIVE in browser", command=open_live).pack(side="left", padx=(10, 0))
        def import_file():
            path = filedialog.askopenfilename(parent=dialog, title="Import TikTok cookies",
                                              filetypes=(("Cookie JSON file", "*.json"),))
            if not path:
                return
            try:
                self._import_login(path)
            except (OSError, ValueError, RuntimeError):
                messagebox.showerror("Cannot import login", "Choose a fresh TikTok Login Exporter JSON file from Chrome.\nSign into TikTok before exporting it.", parent=dialog)
                return
            dialog.destroy()
        Button(box, text="Import cookies…", style="Start.TButton", command=import_file).pack(anchor="w", pady=(4, 12))
        ttk.Label(box, text="The export file contains your TikTok login. Keep it private.\n"
                  "Cookies are saved on this PC and sent only to TikTok over HTTPS.\n"
                  "Importing a login does not confirm access; the next recording checks it.",
                  style="Muted.TLabel", wraplength=650).pack(anchor="w", pady=(0, 4))
        Button(box, text="Close", command=dialog.destroy).pack(anchor="e", pady=(12, 0))
        dialog.grab_set()

    def _options(self):
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Recording options")
        dialog.configure(fg_color=CARD)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        box = ttk.Frame(dialog, style="Card.TFrame", padding=24)
        box.pack(fill="both", expand=True)
        folder = tk.StringVar(value=self.output.get())
        duration = tk.StringVar(value=self.duration.get())
        interval = tk.StringVar(value=self.interval.get())
        concurrent = tk.StringVar(value=str(self.manager.max_concurrent))
        ttk.Label(box, text="Save recordings to", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Entry(box, textvariable=folder, width=56).pack(fill="x", pady=(8, 8))
        def browse():
            selected = filedialog.askdirectory(initialdir=folder.get(), parent=dialog)
            if selected:
                folder.set(selected)
        Button(box, text="Choose folder", command=browse).pack(anchor="e", pady=(0, 20))
        ttk.Label(box, text="Recording limit (seconds)").pack(anchor="w")
        ttk.Entry(box, textvariable=duration).pack(fill="x", pady=(7, 5))
        ttk.Label(box, text="0 means record until the LIVE ends or you press Stop.", style="Muted.TLabel").pack(anchor="w", pady=(0, 17))
        ttk.Label(box, text="Automatic recording: retry interval (minutes)").pack(anchor="w")
        ttk.Entry(box, textvariable=interval).pack(fill="x", pady=(7, 17))
        ttk.Label(box, text="Maximum simultaneous recordings").pack(anchor="w")
        ttk.Spinbox(box, textvariable=concurrent, from_=1, to=12, width=8).pack(anchor="w", pady=(7, 5))
        ttk.Label(box, text="Start with 1–2. Increase gradually. Extra creators queue until a slot is free.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 18))
        def save():
            try:
                limit, minutes, simultaneous = int(duration.get()), int(interval.get()), int(concurrent.get())
                if limit < 0 or minutes < 1 or not 1 <= simultaneous <= 12 or not folder.get().strip():
                    raise ValueError
            except ValueError:
                messagebox.showerror("Check options", "Use a limit of 0 or more, an interval of at least 1, between 1 and 12 simultaneous recordings, and a save folder.", parent=dialog)
                return
            self.output.set(folder.get().strip())
            self.duration.set(str(limit))
            self.interval.set(str(minutes))
            self.manager.max_concurrent = simultaneous
            self._save_settings()
            self._scan_recordings()
            dialog.destroy()
        Button(box, text="Save options", style="Start.TButton", command=save).pack(anchor="e")
        dialog.grab_set()

    def _open_folder(self):
        path = Path(self.output.get())
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror("Cannot open folder", str(exc), parent=self.root)

    def _scan_recordings(self):
        self.last_scan = time.monotonic()
        records = []
        try:
            folder = Path(self.output.get())
            paths = list(folder.glob("*.mp4")) + list(folder.glob("TK_*/*.mp4"))
            for path in paths:
                if path.stem.endswith("_flv"):
                    continue
                try:
                    info = path.stat()
                    if path.is_file():
                        records.append((path, info.st_size, info.st_mtime))
                except OSError:
                    continue  # One disappearing/inaccessible file must not hide the library.
        except OSError:
            pass
        self.library_records = sorted(records, key=lambda item: item[2], reverse=True)[:500]
        self._render_recordings()

    def _render_recordings(self):
        query = self.recording_query.get().strip().casefold()
        visible = [record for record in self.library_records if query in record[0].name.casefold()]
        set_if_changed(self.recording_count, f"{len(visible)} of {len(self.library_records)} recordings · newest first (up to 500)")
        signature = [(str(path), size, modified) for path, size, modified in visible]
        if signature == getattr(self, "file_signature", None):
            return
        self.file_signature = signature
        wanted = {str(path) for path, _, _ in visible}
        for key in set(self.files.get_children()) - wanted:
            self.files.delete(key)
        self.recent_paths = {}
        for index, (path, size, modified) in enumerate(visible):
            key = str(path)
            self.recent_paths[key] = path
            values = (path.name, datetime.fromtimestamp(modified).strftime("%d %b %H:%M"), f"{size / 1048576:.1f} MB")
            if self.files.exists(key):
                if tuple(self.files.item(key, "values")) != values:
                    self.files.item(key, values=values)
                self.files.move(key, "", index)
            else:
                self.files.insert("", "end", iid=key, values=values)

    def _request_storage_check(self, force=False):
        if force:
            self.storage_free = None
            self.storage_text.set("Checking available space…")
            self.last_storage_check = 0
            self._apply_storage_color()
        if self._storage_pending or (not force and time.monotonic() - self.last_storage_check < 30):
            return
        self._storage_pending = True
        self.last_storage_check = time.monotonic()
        folder = self.output.get()
        def check():
            free = None
            try:
                path = Path(folder).expanduser()
                while not path.exists() and path.parent != path:
                    path = path.parent
                free = shutil.disk_usage(path).free
            except (OSError, ValueError):
                pass
            # Background thread never touches Tk. Ignore stale folder results in _event.
            self.events.put(dict(type="storage", folder=folder, free=free))
        threading.Thread(target=check, name="Storage space check", daemon=True).start()

    def _apply_storage_color(self):
        low = self.storage_free is not None and self.storage_free < 2 * 1024 ** 3
        configure_if_changed(self.storage_label, text_color=self.colors["ERROR"] if low else self.colors["MUTED"])

    def _play_selected(self):
        selection = self.files.selection()
        if selection and selection[0] in self.recent_paths:
            self._play(self.recent_paths[selection[0]])

    def _play_latest(self):
        self._scan_recordings()
        if self.library_records:
            self._play(self.library_records[0][0])
        else:
            self.notice.set("Your saved recordings will appear here.")

    def _play(self, path):
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror("Cannot play video", str(exc), parent=self.root)

    def _close(self):
        if self.manager.is_running or self.auto_record:
            if not messagebox.askyesno("Finish recordings?", "Stop all sessions and close after your videos are saved?", parent=self.root):
                return
            self.closing = True
            self.monitor.stop()
            self.stop()
        else:
            self.monitor.stop()
            self.root.destroy()


def main():
    root = ctk.CTk()
    try:
        RecorderApp(root)
    except (OSError, ValueError) as exc:
        root.withdraw()
        messagebox.showerror("Cannot open recorder", str(exc), parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
