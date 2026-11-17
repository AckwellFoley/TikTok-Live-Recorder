"""Package only explicit, reviewed files. Never copy developer user data."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.0"
SOURCE_FOLDERS = ("src", "tests", "tools", "packaging", "browser-extension", "docs", "THIRD-PARTY-LICENSES", ".github")
ROOT_FILES = ("README.md", "LICENSE", "SECURITY.md", "PRIVACY.md", "CONTRIBUTING.md", "CHANGELOG.md",
              "THIRD-PARTY-NOTICES.md", "DEPENDENCIES.json", "pyproject.toml", "requirements-lock.txt",
              "requirements-build.txt", ".gitignore", "Start.pyw", "Run from source.cmd")


def source_files():
    for name in ROOT_FILES:
        path = ROOT / name
        if path.is_file():
            yield path
    for name in SOURCE_FOLDERS:
        folder = ROOT / name
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".log"):
                yield path


def main():
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(ROOT / "packaging" / "recorder.spec")], cwd=ROOT, check=True)
    app = ROOT / "dist" / "TikTok-Live-Recorder"
    for folder in ("browser-extension", "THIRD-PARTY-LICENSES"):
        shutil.copytree(ROOT / folder, app / folder, dirs_exist_ok=True)
    for name in ("README.md", "LICENSE", "PRIVACY.md", "SECURITY.md", "THIRD-PARTY-NOTICES.md", "DEPENDENCIES.json", "requirements-lock.txt"):
        shutil.copyfile(ROOT / name, app / name)
    (app / "START HERE.txt").write_text(
        "TikTok Live Recorder\n\nOpen TikTok Live Recorder.exe. No Python installation is needed.\n"
        "Enter a creator username, then Start recording. On first use the app\n"
        "downloads and verifies FFmpeg (about 110 MB). Wait for Saving before\n"
        "closing. Videos are in data/recordings unless you choose another folder.\n\n"
        "If TikTok requires login, use the TikTok login button and the included\n"
        "Chrome exporter. Keep exported cookies private. See README.md.\n\n"
        "Source: https://github.com/AckwellFoley/TikTok-Live-Recorder\n", encoding="utf-8")
    # Include the exact human-readable application source alongside the binary.
    for path in source_files():
        target = app / "source" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    output = ROOT / "release-artifacts"
    output.mkdir(exist_ok=True)
    filename = f"TikTok-Live-Recorder-{VERSION}-Windows-x64"
    archive = Path(shutil.make_archive(str(output / filename), "zip", app.parent, app.name))
    source_zip = output / f"TikTok-Live-Recorder-{VERSION}-Source.zip"
    with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in source_files():
            bundle.write(path, str(Path("TikTok-Live-Recorder") / path.relative_to(ROOT)))
    hashes = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in (archive, source_zip)]
    (output / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(json.dumps({"portable_zip": str(archive), "source_zip": str(source_zip), "sha256": hashes}, indent=2))


if __name__ == "__main__":
    main()
