"""DPI-aware native controls and a consistent, equal-height tab strip."""
import customtkinter as ctk
from tkinter import ttk
from customtkinter.windows.widgets.core_rendering import DrawEngine

LIGHT = dict(BG="#edf0f7", CARD="#fafbff", FIELD="#ffffff", TEXT="#202b46",
             MUTED="#60708b", LINE="#dce2ef", ACCENT="#5267e8", GREEN="#147c66",
             HOVER="#e8ecf7", SELECTED="#e7ecff", SELECTED_TEXT="#3547a0", ERROR="#986319")
DARK = dict(BG="#101521", CARD="#192130", FIELD="#111a28", TEXT="#edf2ff",
            MUTED="#a5b3cc", LINE="#344159", ACCENT="#9aabff", GREEN="#67d6b0",
            HOVER="#2a3650", SELECTED="#303f67", SELECTED_TEXT="#edf2ff", ERROR="#ffc475")


def palette(dark=False):
    return DARK if dark else LIGHT


BG, CARD, FIELD, TEXT, MUTED, LINE, ACCENT, GREEN = (
    (LIGHT[key], DARK[key]) for key in ("BG", "CARD", "FIELD", "TEXT", "MUTED", "LINE", "ACCENT", "GREEN"))
SURFACE, HOVER = ("#e8ecf7", "#263149"), ("#dce3f5", "#33415d")
BORDER, BAR = ("#cbd3e2", "#344159"), ("#e9edf7", "#131c2b")
SELECTED, SELECTED_HOVER = ("#d2dcff", "#354777"), ("#c4d0fa", "#435789")
PRIMARY = "#5267e8"

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


def configure_if_changed(widget, **options):
    """Avoid CTk redraws when a periodic refresh repeats the current state."""
    changed = {key: value for key, value in options.items() if widget.cget(key) != value}
    if changed:
        widget.configure(**changed)


def set_if_changed(variable, value):
    """Tk variable writes notify widgets even when the text stays the same."""
    if variable.get() != value:
        variable.set(value)


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
        # ttk parents do not notify CTk when their style background changes.
        # Keep the rounded corners tied to the light/dark card palette.
        if isinstance(master, ttk.Frame):
            kw.setdefault("bg_color", CARD)
        super().__init__(master, height=36, corner_radius=10,
                         fg_color=PRIMARY if primary else SURFACE,
                         hover_color="#4255ce" if primary else HOVER,
                         text_color="white" if primary else TEXT,
                         font=("Segoe UI", 12, "bold"), **kw)


class _InsetPillEngine(DrawEngine):
    """Keep the thumb inside the bordered track in the pinned CTk renderer."""
    def draw_rounded_slider_with_border_and_button(self, width, height, corner_radius,
            border_width, button_length, button_corner_radius, slider_value, orientation):
        changed = self.draw_rounded_progress_bar_with_border(width, height, corner_radius,
            border_width, 0, slider_value, orientation)
        if slider_value == 0:
            self._canvas.delete("progress_parts")
        radius = max(1, round(min(corner_radius, height / 2) - max(border_width, height * .12)))
        center = corner_radius + (width - 2 * corner_radius) * slider_value
        for tag, angle in (("pill_thumb_a", 0), ("pill_thumb_b", 180)):
            if not self._canvas.find_withtag(tag):
                self._canvas.create_aa_circle(0, 0, 0, tags=(tag, "slider_parts"), anchor="center", angle=angle)
                changed = True
            self._canvas.coords(tag, center, height / 2, radius)
        self._canvas.tag_raise("slider_parts")
        return changed


class PillSwitch(StateMixin, ctk.CTkSwitch):
    """Shared binary control: thumb left is Off, thumb right is On."""
    def __init__(self, master, **kw):
        if isinstance(master, ttk.Frame):
            kw.setdefault("bg_color", CARD)
        defaults = dict(height=36, switch_width=52, switch_height=28,
                        corner_radius=14, border_width=2, button_length=0,
                        border_color=("#52627e", "#70819f"),
                        fg_color=("#7a89a3", "#44516a"), progress_color=PRIMARY,
                        button_color="#ffffff", button_hover_color=("#f1f4ff", "#e0e7ff"),
                        text_color=TEXT, text_color_disabled=MUTED,
                        font=("Segoe UI", 13))
        defaults.update(kw)
        super().__init__(master, **defaults)
        self._canvas.delete("slider_parts")
        self._draw_engine = _InsetPillEngine(self._canvas)
        self._draw()


def label(parent, text=None, variable=None, size=13, bold=False, muted=False, **kw):
    return ctk.CTkLabel(parent, text=text or "", textvariable=variable,
                        text_color=MUTED if muted else TEXT, anchor="w",
                        font=("Segoe UI", size, "bold" if bold else "normal"), **kw)


class Tabs(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=CARD, corner_radius=18, border_width=1, border_color=BORDER)
        self.bar = ctk.CTkFrame(self, fg_color=BAR, corner_radius=12)
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
                             hover_color=HOVER)
        self.pages[self.current].pack(fill="both", expand=True)


def build_shell(app):
    """Build the scalable shell; the existing recorder controller owns behavior."""
    outer = ctk.CTkFrame(app.root, fg_color=BG, corner_radius=0)
    outer.pack(fill="both", expand=True, padx=24, pady=20)
    header = ctk.CTkFrame(outer, fg_color="transparent")
    header.pack(fill="x", pady=(0, 18))
    title = ctk.CTkFrame(header, fg_color="transparent")
    title.pack(side="left")
    app.title_label = label(title, "TikTok Live Recorder", size=28, bold=True)
    app.title_label.pack(anchor="w")
    app.dark_mode_switch = PillSwitch(header, text="Dark mode", variable=app.dark_mode,
        command=app._toggle_dark_mode)
    app.dark_mode_switch.pack(side="right", padx=(18, 0))
    app.recording_dot = ctk.CTkLabel(header, text="●", width=28, height=34,
        text_color=MUTED, font=("Segoe UI", 22))
    app.recording_dot.pack(side="right", padx=(10, 0))

    app.badge = ctk.CTkLabel(header, textvariable=app.status_text, fg_color=("#def2eb", "#18382f"),
                            text_color=GREEN, corner_radius=16, height=34, width=160,
                            font=("Segoe UI", 12, "bold"))
    app.badge.pack(side="right")
    stats = ctk.CTkFrame(outer, fg_color="transparent")
    stats.pack(fill="x", pady=(0, 18))
    for column, (name, value) in enumerate((("RECORDING SLOTS", app.session_text),
            ("SELECTED SESSION", app.elapsed_text), ("CONNECTED GUESTS", app.guest_text))):
        stats.columnconfigure(column, weight=1, uniform="stats")
        box = ctk.CTkFrame(stats, fg_color=CARD, corner_radius=16, border_width=1, border_color=BORDER)
        box.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 12, 0))
        label(box, name, size=10, bold=True, muted=True).pack(anchor="w", padx=18, pady=(12, 0))
        label(box, variable=value, size=21, bold=True).pack(anchor="w", padx=18, pady=(0, 12))
    footer = ctk.CTkFrame(outer, fg_color="transparent")
    footer.pack(side="bottom", fill="x", pady=(12, 0))
    label(footer, variable=app.notice, size=11, muted=True, wraplength=800).pack(side="left")
    Button(footer, text="Play latest", width=110, command=app._play_latest).pack(side="right")
    body = ctk.CTkFrame(outer, fg_color="transparent")
    body.pack(fill="both", expand=True)
    app.saving_panel = ctk.CTkFrame(outer, fg_color=CARD, corner_radius=12)
    app.saving_label = label(app.saving_panel, "Saving video", size=13, bold=True, wraplength=950, justify="left")
    app.saving_label.pack(fill="x", padx=16, pady=(10, 5))
    app.saving_bar = ctk.CTkProgressBar(app.saving_panel, height=8, corner_radius=4,
        fg_color=LINE, progress_color=PRIMARY)
    app.saving_bar.pack(fill="x", padx=16, pady=(0, 12))
    app.saving_body = body
    app.saving_bar_mode = None
    body.columnconfigure(1, weight=1)
    body.rowconfigure(0, weight=1)
    sidebar = ctk.CTkFrame(body, fg_color=CARD, corner_radius=18,
                          border_width=1, border_color=BORDER)
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
    put(label(setup, "Add creators", size=20, bold=True), (12, 12))
    put(label(setup, "Creator or LIVE link", muted=True), (0, 4))
    app.target_entry = put(Entry(setup, textvariable=app.target, placeholder_text="@creator"))
    put(Button(setup, text="+  Add to creators", command=app._add_creator), (0, 16))
    put(label(setup, "Recording mode", bold=True), (0, 5))
    app.mode_display = ctk.StringVar(value="Automatic" if app.mode.get() == "Monitor automatically" else "Manual")
    app.mode_combo = put(ctk.CTkSegmentedButton(setup, values=["Manual", "Automatic"],
        variable=app.mode_display, height=36, selected_color=SELECTED, selected_hover_color=SELECTED_HOVER,
        unselected_color=SURFACE, unselected_hover_color=HOVER, text_color=TEXT,
        text_color_disabled=MUTED, font=("Segoe UI", 13),
        command=lambda value: app.mode.set("Monitor automatically" if value == "Automatic" else "Record now")))
    app.auto_warning = put(label(setup, "Unattended recordings can use a lot of disk space.\nCheck your save folder regularly.",
        size=11, wraplength=275, justify="left", fg_color=("#fff1d9", "#3c301c"), corner_radius=8), (0, 12))
    put(label(setup, "Include in recording", bold=True), (2, 8))
    app.feed_switch = put(PillSwitch(setup, text="Feed · always on",
        variable=ctk.BooleanVar(value=True), state="disabled"))
    app.panel_check = put(PillSwitch(setup, text="Guests + speaker highlights",
        variable=app.panel_enabled, command=app._update_output_summary))
    app.comments_check = put(PillSwitch(setup, text="Comments",
        variable=app.comments_enabled, command=app._update_output_summary), (0, 16))
    put(label(setup, "Save as", bold=True), (0, 5))
    app.layout_display = ctk.StringVar(value="Separate clips" if app.output_layout.get() == "separate" else "Combined video")
    app.layout_switch = put(ctk.CTkSegmentedButton(setup, values=["Combined video", "Separate clips"],
        variable=app.layout_display, height=36, selected_color=SELECTED, selected_hover_color=SELECTED_HOVER,
        unselected_color=SURFACE, unselected_hover_color=HOVER, text_color=TEXT,
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
