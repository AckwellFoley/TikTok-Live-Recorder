"""Download a version-pinned, hash-verified FFmpeg archive on first use.

No system installation, PATH changes, elevated privileges or login cookies.
Only the two named binaries and license text are extracted. FFmpeg binaries
are downloaded by the end user from the upstream distributor, not redistributed
inside this application's ZIP.
"""
import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

VERSION = "9.0.2"
ARCHIVE_URL = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip"
ARCHIVE_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024


def cached_engine(root):
    return Path(root) / "tools" / f"ffmpeg-{VERSION}"


def find_ffmpeg(root):
    custom = os.environ.get("TIKTOK_RECORDER_FFMPEG")
    if custom:
        path = Path(custom).expanduser()
        if not path.is_file():
            raise ValueError("TIKTOK_RECORDER_FFMPEG does not point to a file.")
        return str(path.resolve())
    cache = cached_engine(root)
    if all((cache / name).is_file() for name in ("ffmpeg.exe", "ffprobe.exe", "installed.json")):
        return str(cache / "ffmpeg.exe")
    return shutil.which("ffmpeg")


def extract_engine(archive, destination):
    """Whitelist destination names; never extract archive-controlled paths."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            matches = [i for i in bundle.infolist() if i.filename.endswith("/bin/" + name)]
            if len(matches) != 1 or not 0 < matches[0].file_size <= MAX_ARCHIVE_BYTES:
                raise ValueError("The video engine archive has unexpected contents.")
            with bundle.open(matches[0]) as source, (destination / (name + ".tmp")).open("wb") as target:
                shutil.copyfileobj(source, target)
            (destination / (name + ".tmp")).replace(destination / name)
        for entry in bundle.infolist():
            if Path(entry.filename).name.lower() in ("license", "license.txt") and entry.file_size < 1024 * 1024:
                (destination / "FFmpeg-LICENSE.txt").write_bytes(bundle.read(entry))
                break


def ensure_ffmpeg(root, progress=lambda message: None, stop_event=None):
    existing = find_ffmpeg(root)
    if existing:
        return existing
    if os.name != "nt":
        raise RuntimeError("Install FFmpeg and ffprobe with your system package manager, then try again.")
    destination = cached_engine(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.with_suffix(".lock")
    deadline = time.monotonic() + 600
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            break
        except FileExistsError:
            existing = find_ffmpeg(root)
            if existing:
                return existing
            progress("Waiting for video engine preparation…")
            if time.monotonic() > deadline:
                raise RuntimeError("Video engine preparation timed out. Close other sessions and try again.")
            if stop_event and stop_event.wait(.5):
                raise RuntimeError("Recording stopped before video engine preparation completed.")
            if not stop_event:
                time.sleep(.5)
    archive_path = None
    try:
        existing = find_ffmpeg(root)
        if existing:
            return existing
        progress("Preparing video engine — downloading once (about 110 MB)…")
        request = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "TikTok-Live-Recorder/1.0"})
        digest = hashlib.sha256()
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".download", delete=False) as target:
            archive_path = Path(target.name)
            with urllib.request.urlopen(request, timeout=30) as response:
                if not response.url.startswith("https://"):
                    raise ValueError("Refusing an insecure video engine download.")
                total, received, last = int(response.headers.get("Content-Length", 0)), 0, -1
                while chunk := response.read(1024 * 1024):
                    if stop_event and stop_event.is_set():
                        raise RuntimeError("Recording stopped during video engine preparation.")
                    received += len(chunk)
                    if received > MAX_ARCHIVE_BYTES:
                        raise ValueError("The video engine download exceeds its size limit.")
                    target.write(chunk)
                    digest.update(chunk)
                    percent = int(received * 100 / total) if total else 0
                    if percent // 10 != last:
                        last = percent // 10
                        progress(f"Preparing video engine — {min(100, percent)}%")
        if digest.hexdigest() != ARCHIVE_SHA256:
            raise ValueError("Video engine checksum did not match. The download was rejected; try again later.")
        progress("Preparing video engine — verifying and unpacking…")
        extract_engine(archive_path, destination)
        (destination / "installed.json").write_text(json.dumps({
            "version": VERSION, "url": ARCHIVE_URL, "archive_sha256": ARCHIVE_SHA256,
        }, indent=2), encoding="utf-8")
        return str(destination / "ffmpeg.exe")
    finally:
        if archive_path:
            archive_path.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
