"""Application files and portable user data, independent of the build machine."""
import os
import sys
from pathlib import Path


def application_dir():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]


def data_dir():
    override = os.environ.get("TIKTOK_RECORDER_DATA_DIR")
    return Path(override).expanduser().resolve() if override else application_dir() / "data"


def worker_command():
    if getattr(sys, "frozen", False):
        return [str(application_dir() / "recorder-worker.exe"), "--worker"]
    return [sys.executable, "-u", str(Path(__file__).with_name("recorder_worker.py"))]
