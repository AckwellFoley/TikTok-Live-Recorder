"""DPI-aware native controls and a consistent, equal-height tab strip."""
import customtkinter as ctk

BG, CARD, FIELD = "#edf0f7", "#fafbff", "#ffffff"
TEXT, MUTED, LINE = "#202b46", "#60708b", "#dce2ef"
ACCENT, GREEN = "#5267e8", "#147c66"

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class StateMixin:
    def instate(self, states):
        disabled = self.cget("state") == "disabled"
        return all(disabled if s == "disabled" else not disabled for s in states)


class Entry(StateMixin, ctk.CTkEntry):
    def __init__(self, master, **kw):
        super().__init__(master, height=38, corner_radius=10, border_color=LINE,
                         fg_color=FIELD, text_color=TEXT, font=("Segoe UI", 13), **kw)


class Button(StateMixin, ctk.CTkButton):
    def __init__(self, master, style=None, **kw):
        primary = style == "Start.TButton"
        super().__init__(master, height=36, corner_radius=10,
                         fg_color=ACCENT if primary else "#e8ecf7",
                         hover_color="#4255ce" if primary else "#dce3f5",
                         text_color="white" if primary else TEXT,
                         font=("Segoe UI", 12, "bold"), **kw)


def label(parent, text=None, variable=None, size=13, bold=False, muted=False, **kw):
    return ctk.CTkLabel(parent, text=text or "", textvariable=variable,
                        text_color=MUTED if muted else TEXT, anchor="w",
                        font=("Segoe UI", size, "bold" if bold else "normal"), **kw)


class Tabs(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=CARD, corner_radius=18, border_width=1, border_color="white")
        self.bar = ctk.CTkFrame(self, fg_color="#e9edf7", corner_radius=12)
        self.bar.pack(fill="x", padx=16, pady=(16, 8))
        self.content = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0)
        self.content.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.pages, self.buttons = [], []
        self.current = 0

    def add(self, page, text):
        index = len(self.pages)
        self.pages.append(page)
        self.bar.columnconfigure(index, weight=1, uniform="tab")
        button = ctk.CTkButton(self.bar, text=text, height=36, corner_radius=9,
                               font=("Segoe UI", 12, "bold"), command=lambda: self.select(index))
        button.grid(row=0, column=index, sticky="ew", padx=4, pady=4)
        self.buttons.append(button)
        self.select(self.current)

    def select(self, index):
        self.current = int(index)
        for i, (page, button) in enumerate(zip(self.pages, self.buttons)):
            page.pack_forget()
            button.configure(fg_color=FIELD if i == self.current else "transparent",
                             text_color=ACCENT if i == self.current else MUTED,
                             hover_color="#dce3f5")
        self.pages[self.current].pack(fill="both", expand=True)


def build_shell(app):
    """Build the scalable shell; the existing recorder controller owns behavior."""
    outer = ctk.CTkFrame(app.root, fg_color=BG, corner_radius=0)
    outer.pack(fill="both", expand=True, padx=24, pady=20)
    header = ctk.CTkFrame(outer, fg_color="transparent")
    header.pack(fill="x", pady=(0, 18))
    title = ctk.CTkFrame(header, fg_color="transparent")
    title.pack(side="left")
    label(title, "CAPTURE STUDIO", size=10, bold=True, muted=True).pack(anchor="w")
    label(title, "Live Recorder", size=28, bold=True).pack(anchor="w")
    label(title, "Your feed. Your conversation. Your recording.", muted=True).pack(anchor="w")
    app.badge = ctk.CTkLabel(header, textvariable=app.status_text, fg_color="#def2eb",
                            text_color=GREEN, corner_radius=16, height=34, width=160,
                            font=("Segoe UI", 12, "bold"))
    app.badge.pack(side="right")
    stats = ctk.CTkFrame(outer, fg_color="transparent")
    stats.pack(fill="x", pady=(0, 18))
    for column, (name, value) in enumerate((("RECORDING SLOTS", app.session_text),
            ("SELECTED SESSION", app.elapsed_text), ("CONNECTED GUESTS", app.guest_text))):
        stats.columnconfigure(column, weight=1, uniform="stats")
        box = ctk.CTkFrame(stats, fg_color=CARD, corner_radius=16, border_width=1, border_color="white")
        box.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 12, 0))
        label(box, name, size=10, bold=True, muted=True).pack(anchor="w", padx=18, pady=(12, 0))
        label(box, variable=value, size=21, bold=True).pack(anchor="w", padx=18, pady=(0, 12))
    footer = ctk.CTkFrame(outer, fg_color="transparent")
    footer.pack(side="bottom", fill="x", pady=(12, 0))
    label(footer, variable=app.notice, size=11, muted=True, wraplength=800).pack(side="left")
    Button(footer, text="Play latest", width=110, command=app._play_latest).pack(side="right")
    body = ctk.CTkFrame(outer, fg_color="transparent")
    body.pack(fill="both", expand=True)
    body.columnconfigure(1, weight=1)
    body.rowconfigure(0, weight=1)
    sidebar = ctk.CTkFrame(body, fg_color=CARD, corner_radius=18,
                          border_width=1, border_color="white")
    sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
    fixed_actions = ctk.CTkFrame(sidebar, fg_color="transparent")
    fixed_actions.pack(side="bottom", fill="x", padx=24, pady=(8, 18))
    app.start_button = Button(fixed_actions, text="Start recording", style="Start.TButton", command=app.start)
    app.start_button.pack(fill="x", pady=(0, 8))
    app.stop_button = Button(fixed_actions, text="Stop all", command=app.stop, state="disabled")
    app.stop_button.pack(fill="x")
    setup = ctk.CTkScrollableFrame(sidebar, width=308, fg_color=CARD, corner_radius=18)
    setup.pack(fill="both", expand=True, padx=2, pady=(6, 0))
    setup.columnconfigure(0, weight=1)
    row = 0
    def put(widget, pady=(0, 8)):
        nonlocal row
        widget.grid(row=row, column=0, sticky="ew", padx=14, pady=pady)
        row += 1
        return widget
    put(label(setup, "New recording", size=20, bold=True), (12, 12))
    put(label(setup, "Creator or LIVE link", muted=True), (0, 4))
    app.target_entry = put(Entry(setup, textvariable=app.target, placeholder_text="@creator"))
    put(Button(setup, text="+  Add to creators", command=app._add_creator), (0, 16))
    put(label(setup, "Recording mode", bold=True), (0, 5))
    app.mode_display = ctk.StringVar(value="Automatic" if app.mode.get() == "Monitor automatically" else "Manual")
    app.mode_combo = put(ctk.CTkSegmentedButton(setup, values=["Manual", "Automatic"],
        variable=app.mode_display, height=36, selected_color="#d2dcff", selected_hover_color="#c4d0fa",
        unselected_color="#e8ecf7", unselected_hover_color="#dce3f5", text_color=TEXT,
        text_color_disabled=MUTED, font=("Segoe UI", 13),
        command=lambda value: app.mode.set("Monitor automatically" if value == "Automatic" else "Record now")))
    app.auto_warning = put(label(setup, "Unattended recordings can use a lot of disk space.\nCheck your save folder regularly.",
        size=11, wraplength=275, justify="left", fg_color="#fff1d9", corner_radius=8), (0, 12))
    put(label(setup, "Include in recording", bold=True), (2, 8))
    feed = put(ctk.CTkCheckBox(setup, text="Feed · always included", variable=ctk.BooleanVar(value=True),
        state="disabled", height=28, text_color_disabled=MUTED, fg_color=ACCENT))
    app.panel_check = put(ctk.CTkCheckBox(setup, text="Guests + speaker highlights", variable=app.panel_enabled,
        height=28, fg_color=ACCENT, text_color=TEXT, command=app._update_output_summary))
    app.comments_check = put(ctk.CTkCheckBox(setup, text="Comments", variable=app.comments_enabled,
        height=28, fg_color=ACCENT, text_color=TEXT, command=app._update_output_summary), (0, 16))
    put(label(setup, "Save as", bold=True), (0, 5))
    app.layout_display = ctk.StringVar(value="Separate clips" if app.output_layout.get() == "separate" else "Combined video")
    app.layout_switch = put(ctk.CTkSegmentedButton(setup, values=["Combined video", "Separate clips"],
        variable=app.layout_display, height=36, selected_color="#d2dcff", selected_hover_color="#c4d0fa",
        unselected_color="#e8ecf7", unselected_hover_color="#dce3f5", text_color=TEXT,
        font=("Segoe UI", 12), command=lambda value: app.output_layout.set("separate" if value == "Separate clips" else "combined")))
    put(label(setup, variable=app.output_summary, muted=True, size=11, wraplength=275, justify="left"), (0, 4))
    put(label(setup, "Only these outputs are saved. Speaker highlights depend on available LIVE data.",
        muted=True, size=11, wraplength=275, justify="left"), (0, 16))
    actions = ctk.CTkFrame(setup, fg_color="transparent")
    put(actions)
    actions.columnconfigure((0, 1), weight=1, uniform="actions")
    app.options_button = Button(actions, text="Options", width=90, command=app._options)
    app.options_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    app.login_button = Button(actions, text="TikTok login", width=90, command=app._login)
    app.login_button.grid(row=0, column=1, sticky="ew")
    put(label(setup, variable=app.login_text, size=11, muted=True, wraplength=275), (0, 12))
    app.inputs = (app.target_entry, app.options_button, app.panel_check, app.comments_check, app.layout_switch)
    app.mode.trace_add("write", lambda *_: app._update_mode())
    for var in (app.panel_enabled, app.comments_enabled, app.output_layout):
        var.trace_add("write", lambda *_: app._update_output_summary())
    app._update_mode()
    app._update_output_summary()
    right = Tabs(body)
    right.grid(row=0, column=1, sticky="nsew")
    return right
