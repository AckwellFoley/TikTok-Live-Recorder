import tkinter as tk
import pytest


@pytest.fixture(scope="session")
def gui_root():
    """One Tcl interpreter shared by all controller and portable-data tests."""
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()
