"""Source launcher; portable release users open TikTok Live Recorder.exe."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from app_entry import main
raise SystemExit(main())
