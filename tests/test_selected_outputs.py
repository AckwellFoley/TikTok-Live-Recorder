import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.guest_panel import PanelTimeline
from core.output_options import OutputOptions
from core.recording_export import export_recording


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg required")
    path = tmp_path_factory.mktemp("feed") / "source.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x568:r=30:d=1.2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


@pytest.mark.parametrize("guests,comments,layout", list(itertools.product([False, True], [False, True], ["combined", "separate"])))
def test_exact_selected_outputs_and_audio(source, tmp_path, guests, comments, layout):
    work = tmp_path / "work"
    work.mkdir()
    captured = work / "capture.mp4"
    shutil.copy2(source, captured)
    timeline = PanelTimeline({"id": "1", "name": "Host"}).export()
    options = OutputOptions(guests, comments, layout)
    outputs = export_recording(captured, timeline, work / "assets", options, tmp_path / "recording.mp4", shutil.which("ffmpeg"))
    assert set(tmp_path.glob("*.mp4")) == set(outputs)
    assert len(outputs) == (1 if layout == "combined" else len(options.sections))
    ffmpeg = shutil.which("ffmpeg")
    def audio(path):
        return subprocess.check_output([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", "copy", "-f", "adts", "-"])
    for output in outputs:
        meta = json.loads(subprocess.check_output([shutil.which("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)]))
        video = next(s for s in meta["streams"] if s["codec_type"] == "video")
        is_feed = layout == "combined" or output.stem.endswith("_feed")
        dimensions = (320, 568) if options.sections == ("feed",) or (layout == "separate" and is_feed) else (720 * (len(options.sections) if layout == "combined" else 1), 1280)
        assert (video["width"], video["height"]) == dimensions
        assert float(meta["format"]["duration"]) == pytest.approx(1.2, abs=.1)
        assert any(s["codec_type"] == "audio" for s in meta["streams"]) == is_feed
        if is_feed:
            assert audio(output) == audio(source)
    assert captured.read_bytes() == source.read_bytes()


def test_export_failure_preserves_capture_and_publishes_nothing(source, tmp_path, monkeypatch):
    import core.recording_export as module
    def fail(*args):
        raise RuntimeError("disk full")
    monkeypatch.setattr(module, "_run", fail)
    with pytest.raises(RuntimeError, match="disk full"):
        export_recording(source, PanelTimeline({"id": "1", "name": "Host"}).export(), tmp_path / "assets",
                         OutputOptions(True, False), tmp_path / "result.mp4", shutil.which("ffmpeg"))
    assert source.exists()
    assert not (tmp_path / "result.mp4").exists()


def test_existing_recording_is_not_overwritten(source, tmp_path):
    target = tmp_path / "existing.mp4"
    target.write_bytes(b"existing recording")
    with pytest.raises(FileExistsError):
        export_recording(source, {}, tmp_path / "assets", OutputOptions(False, False), target)
    assert target.read_bytes() == b"existing recording"


def test_output_options_reject_invalid_controls():
    with pytest.raises(ValueError):
        OutputOptions(True, False, "both")
    with pytest.raises(ValueError):
        OutputOptions("yes", False)
