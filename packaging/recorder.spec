# Build with: python -m PyInstaller --noconfirm packaging/recorder.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata

root = Path(SPECPATH).parent
hidden = ["PIL._tkinter_finder"]
datas = collect_data_files("customtkinter")
for package in ("TikTokLive", "TikTokLiveProto", "EulerApiSdk"):
    hidden += collect_submodules(package)
    datas += collect_data_files(package)
for line in (root / "requirements-lock.txt").read_text().splitlines():
    if line and not line.startswith("#"):
        datas += copy_metadata(line.split("==")[0])
a = Analysis([str(root / "src" / "app_entry.py")], pathex=[str(root / "src")],
    binaries=[], datas=datas, hiddenimports=hidden, hookspath=[],
    excludes=["pytest", "pip_audit", "piplicenses", "telethon"], noarchive=False)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TikTok Live Recorder",
    debug=False, strip=False, upx=False, console=False)
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name="recorder-worker",
    debug=False, strip=False, upx=False, console=True)
app = COLLECT(gui, worker, a.binaries, a.datas, strip=False, upx=False,
    name="TikTok-Live-Recorder")
