"""Check release contents and hash-listed ZIPs without executing them."""
import hashlib
import re
import sys
import zipfile
from pathlib import Path

FORBIDDEN_PARTS = {"data", ".venv", ".build-venv", "recordings", ".git", "__pycache__"}
FORBIDDEN_NAMES = {"cookies.local.json", "cookies.json", "telegram.json", "gui-settings.json",
                   "creators.json", "creator-stats.json", "tiktok-recorder-cookies.json"}


def verify(path):
    errors = []
    with zipfile.ZipFile(path) as bundle:
        for entry in bundle.infolist():
            parts = Path(entry.filename).parts
            # Runtime package internals can legitimately contain a directory
            # named data. Only the application top level/source must be clean.
            public_source = "_internal" not in parts
            if public_source and (set(parts) & FORBIDDEN_PARTS or Path(entry.filename).name in FORBIDDEN_NAMES
                                  or entry.filename.endswith((".mp4", ".flv", ".log", ".session"))):
                errors.append(entry.filename)
            if public_source and entry.file_size < 5 * 1024 * 1024 and not entry.is_dir():
                content = bundle.read(entry)
                if re.search(rb"[CFG]:[/\\](?:Users[/\\]|code[/\\]|New folder)", content, re.I):
                    errors.append(entry.filename + ": developer-specific path")
    if errors:
        raise RuntimeError("Release contains excluded/private artifacts: " + ", ".join(errors))
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == "__main__":
    for filename in sys.argv[1:]:
        path = Path(filename)
        print(path.name, verify(path), "contents check passed")
