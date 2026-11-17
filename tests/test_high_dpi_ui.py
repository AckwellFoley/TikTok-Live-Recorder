"""Exercise each production CTk root in its own process, as the app does."""
import subprocess
import sys
from pathlib import Path
import customtkinter as ctk
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gui
from tests.test_gui import FakeManager, FakeMonitor


@pytest.mark.parametrize("zoom", [1, 1.25])
def test_native_dpi_layout_and_equal_tabs(tmp_path, zoom):
    # CTk's process-wide DPI/font initialization must not contaminate other Tk
    # interpreters in pytest. A real app creates one CTk root per process.
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(tmp_path), str(zoom)],
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr


def check_native_window(folder, zoom):
    gui.MultiRecorder, gui.CreatorMonitor = FakeManager, FakeMonitor
    ctk.set_widget_scaling(zoom)
    ctk.set_window_scaling(zoom)
    root = ctk.CTk()
    try:
        app = gui.RecorderApp(root, folder)
        root.update()
        assert len({b.winfo_height() for b in app.tabs.buttons}) == 1
        assert app.tabs.content.winfo_width() > 300
        assert app.target_entry.winfo_height() >= 38
        assert app.start_button.winfo_ismapped() and app.stop_button.winfo_ismapped()
        assert app.dark_mode_switch.winfo_ismapped()
        assert app.dark_mode_switch.winfo_rootx() > app.title_label.winfo_rootx() + app.title_label.winfo_width()
        assert app.dark_mode_switch.winfo_rooty() < app.tabs.winfo_rooty()
        assert app.stop_button.winfo_rooty() + app.stop_button.winfo_height() < root.winfo_rooty() + root.winfo_height()
        assert not app.auto_warning.winfo_ismapped()
        app.mode.set("Monitor automatically")
        root.update()
        assert app.auto_warning.winfo_ismapped()
        app.tabs.select(1)
        root.update()
        assert [b.cget("text") for b in app.tabs.buttons] == ["Creators", "Recordings", "Activity Log"]
        assert app.files.winfo_ismapped()
        app.dark_mode.set(True)
        app._toggle_dark_mode()
        root.update()
        assert ctk.get_appearance_mode() == "Dark"
        assert app.dark_mode_switch.winfo_ismapped()
    finally:
        root.destroy()
        ctk.set_widget_scaling(1)
        ctk.set_window_scaling(1)


if __name__ == "__main__":
    check_native_window(Path(sys.argv[1]), float(sys.argv[2]))
