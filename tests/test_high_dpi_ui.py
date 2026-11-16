"""Exercise the production CTk root, rather than Tk-only controller fixtures."""
import customtkinter as ctk
import pytest
import gui
from tests.test_gui import FakeManager, FakeMonitor


@pytest.mark.parametrize("zoom", [1, 1.25])
def test_native_dpi_layout_and_equal_tabs(tmp_path, monkeypatch, zoom):
    monkeypatch.setattr(gui, "MultiRecorder", FakeManager)
    monkeypatch.setattr(gui, "CreatorMonitor", FakeMonitor)
    ctk.set_widget_scaling(zoom)
    ctk.set_window_scaling(zoom)
    root = ctk.CTk()
    try:
        app = gui.RecorderApp(root, tmp_path)
        root.update()
        assert len({b.winfo_height() for b in app.tabs.buttons}) == 1
        assert app.tabs.content.winfo_width() > 300
        assert app.target_entry.winfo_height() >= 38
        assert not app.auto_warning.winfo_ismapped()
        app.mode.set("Monitor automatically")
        root.update()
        assert app.auto_warning.winfo_ismapped()
        app.tabs.select(1)
        root.update()
        assert app.preview.winfo_ismapped()
    finally:
        root.destroy()
        ctk.set_widget_scaling(1)
        ctk.set_window_scaling(1)
