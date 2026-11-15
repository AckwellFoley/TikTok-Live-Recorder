"""Shared source/packaged entry point. Workers use the console companion EXE."""
import os
import sys
from pathlib import Path


def main():
    from app_paths import data_dir
    root_dir = data_dir()
    root_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(root_dir)
    if "--worker" in sys.argv:
        from recorder_worker import main as worker_main
        return worker_main()
    if "--self-test" in sys.argv:
        return self_test(Path(sys.argv[sys.argv.index("--self-test") + 1]))
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    try:
        from gui import RecorderApp
        RecorderApp(root, root_dir)
    except Exception as exc:
        root.withdraw()
        messagebox.showerror("Cannot open TikTok Live Recorder", str(exc), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


def self_test(report_path):
    """Offline packaged smoke check: Tk, schemas, TLS packages, both renderers."""
    import json
    import tempfile
    import time
    import tkinter as tk
    import curl_cffi
    import TikTokLive
    from TikTokLive.events import LinkLayerEvent
    from core.guest_panel import PanelTimeline, draw_panel
    from core.comment_panel import draw_comments
    from core.comment_capture import CommentTimeline
    from gui import RecorderApp
    from multi_backend import ProcessRecorderJob
    ui = tk.Tk()
    ui.withdraw()
    with tempfile.TemporaryDirectory() as folder:
        app = RecorderApp(ui, folder)
        assert app.target.get() == "" and not app.monitoring.get()
        ui.update()
    ui.destroy()
    LinkLayerEvent.from_dict({"version": "1"})
    timeline = PanelTimeline({"id": "1", "name": "Example host"}).export()
    with tempfile.TemporaryDirectory() as folder:
        draw_panel(timeline, {"ids": ["1"], "active_speakers": ["1"]}, folder, (720, 1280)).save(Path(folder) / "panel.png")
    # Importing this schema and renderer catches missing frozen package data.
    assert CommentTimeline().count == 0
    assert callable(draw_comments) and callable(curl_cffi.Session) and TikTokLive.TikTokLiveClient
    events = []
    with tempfile.TemporaryDirectory() as folder:
        job = ProcessRecorderJob(folder, events.append)
        job.start("invalid target", folder)
        deadline = time.monotonic() + 15
        while job.is_running and time.monotonic() < deadline:
            time.sleep(.05)
        assert not job.is_running and any(e.get("state") == "error" for e in events), "Packaged worker IPC failed"
    report_path.write_text(json.dumps({"status": "passed", "checks": [
        "Tk GUI startup", "clean first launch", "TikTok schemas", "TLS client",
        "Pillow panel render", "comment renderer", "companion worker IPC",
    ]}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
