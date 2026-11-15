"""Windows desktop interface for the local TikTok LIVE recorder."""

import json
import os
import queue
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from core.guest_panel import draw_panel
from gui_backend import parse_target
from multi_backend import MultiRecorder
from creator_monitor import CreatorHistory, CreatorMonitor
from utils.login_store import has_login, load_login_settings, read_cookie_export, save_login_settings
from app_paths import application_dir, data_dir

ROOT_DIR = data_dir()
BG, CARD, FIELD = "#10131c", "#191e2b", "#111620"
TEXT, MUTED, LINE = "#f0f3fa", "#a7b2c7", "#30394b"
PINK, TEAL = "#ff4169", "#39d7cf"


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
        self.last_preview = 0
        self.preview_dirty = False
        self.presence = {}
        self.panels = {}
        self.auto_record = {}
        self.auto_due = {}
        settings = self._load_settings()
        self.target = tk.StringVar(value=settings.get("target", ""))
        self.output = tk.StringVar(value=settings.get("output", str(self.root_dir / "recordings")))
        self.mode = tk.StringVar(value=settings.get("mode", "Record now"))
        self.panel_enabled = tk.BooleanVar(value=settings.get("panel", True))
        self.duration = tk.StringVar(value=str(settings.get("duration", 0)))
        self.interval = tk.StringVar(value=str(settings.get("interval", 5)))
        try:
            limit = max(1, min(12, int(settings.get("max_concurrent", 1))))
        except (TypeError, ValueError):
            limit = 1
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
        self.panel_caption = tk.StringVar(value="Guest profiles update as people join and leave.")
        self.notice = tk.StringVar(value="Enter a creator, then start recording.")
        self.login_text = tk.StringVar(value="TikTok login: not set")
        self._refresh_login_status()
        root.title("TikTok LIVE Recorder")
        root.geometry("1240x880")
        root.minsize(1120, 820)
        root.configure(bg=BG)
        self._style()
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.bind("<Control-Return>", lambda _: self.start())
        self._scan_recordings()
        self._refresh_creators()
        if self.monitoring.get():
            self.monitor.start(self.history.creators)
        root.after(150, self._tick)

    def _load_settings(self):
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        value = dict(target=self.target.get().strip(), output=self.output.get(),
                     mode=self.mode.get(), panel=self.panel_enabled.get(),
                     duration=int(self.duration.get() or 0), interval=int(self.interval.get() or 5))
        value.update(max_concurrent=self.manager.max_concurrent, monitoring=self.monitoring.get())
        try:
            temporary = self.settings_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
            temporary.replace(self.settings_path)
        except OSError:
            pass

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        self.root.option_add("*Font", "{Segoe UI} 11")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=CARD, foreground=TEXT)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, bordercolor=LINE,
                        insertcolor=TEXT, padding=9)
        style.map("TEntry", fieldbackground=[("disabled", CARD)])
        style.configure("TCombobox", fieldbackground=FIELD, background=LINE, foreground=TEXT,
                        arrowcolor=MUTED, padding=8, selectbackground=FIELD, selectforeground=TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        style.configure("TButton", background=LINE, foreground=TEXT, borderwidth=0,
                        padding=(13, 10), font=("Segoe UI", 10, "bold"))
        style.map("TButton", background=[("active", "#424f68")],
                  foreground=[("disabled", "#6c7890")])
        style.configure("Start.TButton", background=PINK, foreground="white")
        style.map("Start.TButton", background=[("disabled", "#563041"), ("active", "#e7355d")])
        style.configure("TCheckbutton", background=CARD, foreground=TEXT, padding=(0, 7))
        style.map("TCheckbutton", background=[("active", CARD)])
        style.configure("TNotebook", background=CARD, borderwidth=0)
        style.configure("TNotebook.Tab", background=CARD, foreground=MUTED, padding=(18, 12))
        style.map("TNotebook.Tab", background=[("selected", FIELD)], foreground=[("selected", TEXT)])
        style.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=TEXT,
                        rowheight=35, borderwidth=0)
        style.configure("Treeview.Heading", background=CARD, foreground=MUTED, padding=8,
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#343651")])

    def _build(self):
        outer = ttk.Frame(self.root, padding=26)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 20))
        tk.Label(header, text="●", bg=BG, fg=PINK, font=("Segoe UI", 28)).pack(side="left", padx=(0, 12))
        title = ttk.Frame(header)
        title.pack(side="left")
        tk.Label(title, text="LIVE Recorder", bg=BG, fg=TEXT,
                 font=("Segoe UI", 23, "bold")).pack(anchor="w")
        tk.Label(title, text="Capture the conversation. Keep the whole panel.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))
        self.badge = tk.Label(header, textvariable=self.status_text, bg="#193630", fg=TEAL,
                              padx=16, pady=9, font=("Segoe UI", 10, "bold"))
        self.badge.pack(side="right")

        stats = ttk.Frame(outer)
        stats.pack(fill="x", pady=(0, 18))
        for column, (label, value) in enumerate((("RECORDING SLOTS", self.session_text),
                                               ("SELECTED SESSION", self.elapsed_text),
                                               ("CONNECTED GUESTS", self.guest_text))):
            stats.columnconfigure(column, weight=1, uniform="stats")
            box = ttk.Frame(stats, style="Card.TFrame", padding=(18, 13))
            box.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 10, 0))
            ttk.Label(box, text=label, style="Muted.TLabel").pack(anchor="w")
            ttk.Label(box, textvariable=value, font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(5, 0))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=0, minsize=340)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        setup = ttk.Frame(body, style="Card.TFrame", padding=22)
        setup.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        setup.columnconfigure(0, weight=1)
        ttk.Label(setup, text="New recording", font=("Segoe UI", 15, "bold")).grid(sticky="w", pady=(0, 18))
        ttk.Label(setup, text="TikTok creator or LIVE link").grid(sticky="w")
        self.target_entry = ttk.Entry(setup, textvariable=self.target, width=30)
        self.target_entry.grid(sticky="ew", pady=(7, 5))
        target_actions = ttk.Frame(setup, style="Card.TFrame")
        target_actions.grid(sticky="ew", pady=(3, 12))
        ttk.Button(target_actions, text="Add to creator list", command=self._add_creator).pack(side="left")
        ttk.Label(setup, text="Recording mode").grid(sticky="w")
        self.mode_combo = ttk.Combobox(setup, textvariable=self.mode, state="readonly",
                                      values=("Record now", "Monitor automatically"))
        self.mode_combo.grid(sticky="ew", pady=(7, 15))
        self.panel_check = ttk.Checkbutton(setup, text="Include comments & guest panels", variable=self.panel_enabled)
        self.panel_check.grid(sticky="w", pady=(0, 6))
        ttk.Label(setup, text="Saved video: comments, feed, then guests.\nGuests keep their order; new guests join below.\nSpeakers glow green when data is available.",
                  style="Muted.TLabel", wraplength=300).grid(sticky="w", pady=(0, 17))
        settings_actions = ttk.Frame(setup, style="Card.TFrame")
        settings_actions.grid(sticky="ew", pady=(4, 5))
        settings_actions.columnconfigure((0, 1), weight=1)
        self.options_button = ttk.Button(settings_actions, text="Recording options…", command=self._options)
        self.options_button.grid(row=0, column=0, sticky="ew", padx=(0, 7))
        self.login_button = ttk.Button(settings_actions, text="TikTok login…", command=self._login)
        self.login_button.grid(row=0, column=1, sticky="ew")
        ttk.Label(setup, textvariable=self.login_text, style="Muted.TLabel").grid(sticky="w", pady=(0, 15))
        actions = ttk.Frame(setup, style="Card.TFrame")
        actions.grid(sticky="ew")
        self.start_button = ttk.Button(actions, text="●  Start recording", style="Start.TButton", command=self.start)
        self.start_button.pack(side="left", fill="x", expand=True)
        self.stop_button = ttk.Button(actions, text="Stop all", command=self.stop, state="disabled")
        self.stop_button.pack(side="right", padx=(10, 0))
        self.inputs = (self.target_entry, self.options_button, self.panel_check)

        right = ttk.Frame(body, style="Card.TFrame", padding=12)
        right.grid(row=0, column=1, sticky="nsew")
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)
        creators = ttk.Frame(self.tabs, style="Card.TFrame", padding=12)
        panel = ttk.Frame(self.tabs, style="Card.TFrame", padding=16)
        recordings = ttk.Frame(self.tabs, style="Card.TFrame", padding=12)
        activity = ttk.Frame(self.tabs, style="Card.TFrame", padding=12)
        self.tabs.add(creators, text="Creators")
        self.tabs.add(panel, text="Guest panel")
        self.tabs.add(recordings, text="Recordings")
        self.tabs.add(activity, text="Activity")
        monitor_bar = ttk.Frame(creators, style="Card.TFrame")
        monitor_bar.pack(fill="x", pady=(0, 9))
        ttk.Checkbutton(monitor_bar, text="Monitor LIVE status", variable=self.monitoring,
                        command=self._toggle_monitoring).pack(side="left")
        ttk.Button(monitor_bar, text="Check now", command=self._check_now).pack(side="right")
        ttk.Label(creators, text="Ctrl-click to select several creators. LIVE status updates every minute.",
                  style="Muted.TLabel", wraplength=680).pack(anchor="w", pady=(0, 12))
        creator_table = ttk.Frame(creators, style="Card.TFrame")
        creator_table.pack(fill="both", expand=True)
        self.creators = ttk.Treeview(creator_table, columns=("creator", "live", "recording", "elapsed", "checked"),
                                     show="headings", selectmode="extended", height=8)
        for name, label, width in (("creator", "CREATOR", 190), ("live", "LIVE STATUS", 110),
                                   ("recording", "RECORDING", 130), ("elapsed", "TIME", 85),
                                   ("checked", "CHECKED", 95)):
            self.creators.heading(name, text=label)
            self.creators.column(name, width=width, minwidth=70, stretch=name == "creator")
        self.creators.tag_configure("live", foreground="#39efad")
        self.creators.tag_configure("recording", foreground="#ff7595")
        self.creators.tag_configure("error", foreground="#ffbf70")
        scrollbar = ttk.Scrollbar(creator_table, orient="vertical", command=self.creators.yview)
        scrollbar.pack(side="right", fill="y")
        self.creators.configure(yscrollcommand=scrollbar.set)
        self.creators.pack(side="left", fill="both", expand=True)
        self.creators.bind("<<TreeviewSelect>>", self._select_creator)
        self.creators.bind("<Double-1>", lambda _: self._show_selected_panel())
        creator_actions = ttk.Frame(creators, style="Card.TFrame")
        creator_actions.pack(side="bottom", before=creator_table, fill="x", pady=(12, 0))
        ttk.Button(creator_actions, text="Record selected", style="Start.TButton",
                   command=self._record_selected).pack(side="left")
        ttk.Button(creator_actions, text="Stop selected", command=self._stop_selected).pack(side="left", padx=(8, 0))
        ttk.Button(creator_actions, text="Remove", command=self._remove_selected).pack(side="right")
        ttk.Label(panel, text="Who's on the LIVE", font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(6, 7))
        self.session_selector = ttk.Combobox(panel, textvariable=self.selected_creator, state="readonly",
                                             values=self.history.creators)
        self.session_selector.pack(fill="x", pady=(0, 9))
        self.session_selector.bind("<<ComboboxSelected>>", lambda _: self._choose_creator())
        ttk.Label(panel, textvariable=self.panel_caption, style="Muted.TLabel", wraplength=540).pack(anchor="w", pady=(0, 18))
        self.preview = tk.Canvas(panel, bg=FIELD, highlightthickness=0)
        self.preview.pack(fill="both", expand=True)
        self.preview.bind("<Configure>", lambda _: self._draw_preview())
        ttk.Label(panel, text="The video continues while the panel updates.", style="Muted.TLabel").pack(anchor="w", pady=(14, 4))
        toolbar = ttk.Frame(recordings, style="Card.TFrame")
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Label(toolbar, text="Saved videos", font=("Segoe UI", 13, "bold")).pack(side="left")
        ttk.Button(toolbar, text="Refresh", command=self._scan_recordings).pack(side="right")
        ttk.Button(toolbar, text="Open folder", command=self._open_folder).pack(side="right", padx=(0, 8))
        self.files = ttk.Treeview(recordings, columns=("name", "size"), show="headings", selectmode="browse")
        self.files.heading("name", text="RECORDING")
        self.files.heading("size", text="SIZE")
        self.files.column("name", width=390, minwidth=200)
        self.files.column("size", width=85, minwidth=65, stretch=False, anchor="e")
        self.files.pack(fill="both", expand=True)
        self.files.bind("<Double-1>", lambda _: self._play_selected())
        ttk.Button(recordings, text="Play selected video", command=self._play_selected).pack(anchor="e", pady=(12, 0))
        self.log = tk.Text(activity, bg=FIELD, fg=MUTED, insertbackground=TEXT, relief="flat",
                           wrap="word", padx=12, pady=12, font=("Consolas", 10), state="disabled")
        self.log.pack(fill="both", expand=True)
        footer = ttk.Frame(outer)
        footer.pack(side="bottom", before=body, fill="x", pady=(17, 0))
        tk.Label(footer, textvariable=self.notice, bg=BG, fg=MUTED, anchor="w", wraplength=850).pack(side="left")
        ttk.Button(footer, text="Play latest", command=self._play_latest).pack(side="right")

    def start(self):
        try:
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
                       panel=self.panel_enabled.get(), duration=duration, interval=interval)
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

    def _show_selected_panel(self):
        self._select_creator()
        self.tabs.select(1)

    def _choose_creator(self):
        creator = self.selected_creator.get()
        self.panel_data = self.panels.get(creator)
        self.guest_text.set("—")
        self.panel_caption.set(f"Panel for @{creator}" if creator else "Select a creator to view its guest panel.")
        if self.panel_data:
            data = self.panel_data["timeline"]
            current = data["snapshots"][-1]
            count = max(0, len(current["ids"]) - 1)
            self.guest_text.set(str(count))
            caption = current.get("status") or f"@{creator} · {count} guest{'s' if count != 1 else ''} on the panel"
            if "speaker_participant_count" in data:
                caption += f" · Speaker identities {data.get('speaker_mapped_count', 0)}/{data['speaker_participant_count']}"
            elif data.get("speaking_indicators"):
                caption += " · Speaker highlighting active"
            if "comment_count" in data:
                caption += f" · {data['comment_count']} comments captured"
            self.panel_caption.set(caption)
        self.preview_dirty = True

    @staticmethod
    def _elapsed(started_at, ended_at=None):
        if started_at is None:
            return "—"
        seconds = max(0, int((ended_at if ended_at is not None else time.monotonic()) - started_at))
        return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"

    def _refresh_creators(self):
        sessions = {s["creator"]: s for s in self.manager.snapshot()}
        names = self.history.creators
        for creator in set(self.creators.get_children()) - set(names):
            self.creators.delete(creator)
        live_labels = dict(live="LIVE", offline="Offline", error="Check failed", checking="Checking…")
        recording_labels = dict(connecting="Connecting", recording="Recording", waiting="Waiting",
                                finalizing="Saving video", queued="Queued", stopping="Stopping",
                                finished="Saved", error="Needs attention")
        for creator in names:
            presence = self.presence.get(creator, {})
            session = sessions.get(creator, {})
            state = session.get("state")
            recording = recording_labels.get(state, "—")
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
        self.session_selector.configure(values=names)
        selected = sessions.get(self.selected_creator.get(), {})
        self.elapsed_text.set(self._elapsed(selected.get("started_at"), selected.get("ended_at")))

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
        if kind == "presence":
            if creator in self.history.creators and self.monitoring.get():
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
            widget.configure(state="disabled" if self.closing else "normal")
        self.login_button.configure(state="disabled" if self.manager.is_running or self.closing else "normal")
        self.mode_combo.configure(state="disabled" if self.closing else "readonly")
        self.start_button.configure(state="disabled" if self.closing else "normal")
        self.stop_button.configure(state="normal" if busy and not self.closing else "disabled")
        sessions = self.manager.snapshot()
        active = sum(s["state"] == "recording" for s in sessions)
        queued = sum(s["state"] == "queued" for s in sessions)
        self.session_text.set(f"{self.manager.active_count} / {self.manager.max_concurrent} active" +
                              (f" · {queued} queued" if queued else ""))
        if active:
            self.status_text.set(f"{active} recording")
        elif self.manager.is_running:
            self.status_text.set("Saving videos" if any(s["state"] == "finalizing" for s in sessions) else "Connecting")
        elif self.auto_record:
            self.status_text.set(f"Watching {len(self.auto_record)} creator{'s' if len(self.auto_record) != 1 else ''}")
        elif self.state != "error":
            self.status_text.set("Ready to record")
        self.badge.configure(bg="#442239" if active else "#193630", fg=PINK if active else TEAL)

    def _append_log(self, text):
        if not text:
            return
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {text}\n")
        if int(self.log.index("end-1c").split(".")[0]) > 700:
            self.log.delete("1.0", "100.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _draw_preview(self):
        self.preview.delete("all")
        w, h = self.preview.winfo_width(), self.preview.winfo_height()
        if w < 20 or h < 20:
            return
        if not self.panel_data or not self.panel_data.get("timeline", {}).get("snapshots"):
            self.preview.create_text(w / 2, h / 2 - 18, text="Your panel will appear here", fill=TEXT,
                                     font=("Segoe UI", 14, "bold"))
            self.preview.create_text(w / 2, h / 2 + 18, text="Start a recording to see the host and guests.",
                                     fill=MUTED, font=("Segoe UI", 10), width=w - 30)
            return
        try:
            data = self.panel_data["timeline"]
            image = draw_panel(data, data["snapshots"][-1], self.panel_data["assets"], (720, 1280))
            image = image.crop(image.getbbox())
            background = Image.new("RGBA", image.size, FIELD)
            background.alpha_composite(image)
            background.thumbnail((w - 24, h - 24), Image.Resampling.LANCZOS)
            self.preview_image = ImageTk.PhotoImage(background.convert("RGB"))
            self.preview.create_image(w / 2, h / 2, image=self.preview_image)
        except (OSError, ValueError, KeyError):
            self.preview.create_text(w / 2, h / 2, text="Updating panel…", fill=MUTED)

    def _tick(self):
        self.manager.poll()
        try:
            for _ in range(200):
                self._event(self.events.get_nowait())
        except queue.Empty:
            pass
        self._start_due_automatic()
        self._refresh_creators()
        now = time.monotonic()
        if self.preview_dirty and now - self.last_preview > 0.5:
            self._draw_preview()
            self.last_preview = now
            self.preview_dirty = False
        self._set_controls()
        if not self.manager.is_running and self.closing:
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
        dialog = tk.Toplevel(self.root)
        dialog.title("TikTok login")
        dialog.configure(bg=CARD)
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
        ttk.Button(actions, text="Open exporter folder", command=open_folder).pack(side="left")
        ttk.Button(actions, text="Open LIVE in browser", command=open_live).pack(side="left", padx=(10, 0))
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
        ttk.Button(box, text="Import cookies…", style="Start.TButton", command=import_file).pack(anchor="w", pady=(4, 12))
        ttk.Label(box, text="The export file contains your TikTok login. Keep it private.\n"
                  "Cookies are saved on this PC and sent only to TikTok over HTTPS.\n"
                  "Importing a login does not confirm access; the next recording checks it.",
                  style="Muted.TLabel", wraplength=650).pack(anchor="w", pady=(0, 4))
        ttk.Button(box, text="Close", command=dialog.destroy).pack(anchor="e", pady=(12, 0))
        dialog.grab_set()

    def _options(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Recording options")
        dialog.configure(bg=CARD)
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
        ttk.Button(box, text="Choose folder", command=browse).pack(anchor="e", pady=(0, 20))
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
        ttk.Button(box, text="Save options", style="Start.TButton", command=save).pack(anchor="e")
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
        try:
            paths = sorted(Path(self.output.get()).glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]
            paths = [p for p in paths if not p.stem.endswith("_flv")]
            signature = [(str(p), p.stat().st_size) for p in paths]
        except OSError:
            paths, signature = [], []
        if signature == getattr(self, "file_signature", None):
            return
        self.file_signature = signature
        self.files.delete(*self.files.get_children())
        self.recent_paths = {}
        for index, path in enumerate(paths):
            key = str(index)
            self.recent_paths[key] = path
            self.files.insert("", "end", iid=key, values=(path.name, f"{path.stat().st_size / 1048576:.1f} MB"))

    def _play_selected(self):
        selection = self.files.selection()
        if selection and selection[0] in self.recent_paths:
            self._play(self.recent_paths[selection[0]])

    def _play_latest(self):
        self._scan_recordings()
        paths = list(self.recent_paths.values())
        panel_paths = [p for p in paths if p.stem.endswith("_panel")]
        if paths:
            self._play((panel_paths or paths)[0])
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
    root = tk.Tk()
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
