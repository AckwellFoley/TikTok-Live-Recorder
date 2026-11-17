"""Render exactly the selected outputs; source capture stays safe until success."""
import json
import os
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

from core.guest_panel import draw_panel, panel_frames
from core.comment_panel import draw_comments, comment_frames


def _notify(callback, stage, fraction=None):
    if callback:
        try:
            callback(stage, fraction)
        except Exception:
            pass  # Display failures must never interrupt saving.


def _run(command, duration=None, progress=None):
    if progress is None:
        try:
            subprocess.run(command, check=True, capture_output=True, stdin=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(exc.stderr.decode(errors="replace")[-1500:]) from exc
        return
    # Separate stderr avoids pipe deadlocks; only machine-readable progress is streamed.
    command = command[:1] + ["-nostats", "-stats_period", "0.5", "-progress", "pipe:1"] + command[1:]
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) as process:
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and duration and duration > 0:
                    try:
                        fraction = min(.99, max(0, int(value) / 1_000_000 / duration))
                    except ValueError:
                        continue
                    _notify(progress, "", fraction)
            code = process.wait()
        if code:
            errors.seek(0, 2)
            errors.seek(max(0, errors.tell() - 1500))
            raise RuntimeError(errors.read().decode(errors="replace"))
    _notify(progress, "", 1.0)


def _stage_feed(source, target):
    # The private workspace and exports share a volume. A hard link avoids
    # copying the entire feed again and survives workspace cleanup.
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _playlist(section, timeline, assets, duration, progress=None):
    snapshots = (panel_frames(timeline, duration) if section == "guests" else
                 comment_frames(timeline, duration, updates_per_second=5))
    entries, cache = [], {}
    last_percent = -1
    for index, snapshot in enumerate(snapshots):
        percent = int(index * 100 / len(snapshots))
        if percent != last_percent:
            _notify(progress, f"Preparing {section}", percent / 100)
            last_percent = percent
        signature = (tuple(snapshot["ids"]), snapshot.get("status", ""),
                     tuple(snapshot["active_speakers"])) if section == "guests" else index
        if signature not in cache:
            image = assets / f"{section}-{len(cache):06}.png"
            (draw_panel(timeline, snapshot, assets, (720, 1280)) if section == "guests" else
             draw_comments(timeline, snapshot, (720, 1280))).convert("RGB").save(image, compress_level=1)
            cache[signature] = image
        end = snapshots[index + 1]["time"] if index + 1 < len(snapshots) else duration
        entries += [f"file '{cache[signature].name}'", f"duration {max(.04, end - snapshot['time']):.3f}"]
    entries.append(f"file '{cache[signature].name}'")
    path = assets / f"{section}.txt"
    path.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return path


def export_recording(video, timeline, assets, options, destination, ffmpeg="ffmpeg", progress=None):
    """Combined: one file. Separate: feed with audio, silent synchronized panels.

    The caller owns its private capture workspace and removes it after success.
    All outputs finish in that workspace before any are published to the folder.
    """
    video, assets, destination = Path(video), Path(assets), Path(destination)
    stage = video.parent / "exports"
    stage.mkdir(exist_ok=True)
    assets.mkdir(exist_ok=True)
    sections = options.sections
    names = ([destination.name] if options.layout == "combined" else
             [destination.stem + f"_{s}.mp4" for s in sections])
    targets = [destination.with_name(n) for n in names]
    if any(p.exists() for p in targets):
        raise FileExistsError("A recording with this name already exists; the new capture is preserved.")
    _notify(progress, "Preparing saved video")
    if sections == ("feed",):
        _stage_feed(video, stage / names[0])
    else:
        probe = Path(ffmpeg).with_name("ffprobe.exe" if Path(ffmpeg).suffix == ".exe" else "ffprobe")
        metadata = json.loads(subprocess.check_output([str(probe), "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,r_frame_rate,avg_frame_rate", "-of", "json", str(video)],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        duration = float(metadata["format"]["duration"])
        stream = next(s for s in metadata["streams"] if s["codec_type"] == "video")
        rate = Fraction(25)
        for key in ("r_frame_rate", "avg_frame_rate"):
            try:
                candidate = Fraction(stream.get(key) or "0/0")
                if 1 <= candidate <= 120:
                    rate = candidate
                    break
            except (ValueError, ZeroDivisionError):
                pass
        playlists = {s: _playlist(s, timeline, assets, duration, progress) for s in sections if s != "feed"}
        def encode_video(command, description):
            _notify(progress, description, 0.0)
            if progress:
                _run(command, duration, lambda _stage, fraction: _notify(progress, description, fraction))
            else:
                _run(command)
        encode = ["-c:v", "libx264", "-preset", "veryfast", "-threads", "4", "-crf", "20",
                  "-pix_fmt", "yuv420p", "-t", str(duration), "-movflags", "+faststart"]
        if options.layout == "separate":
            for section, name in zip(sections, names):
                if section == "feed":
                    _stage_feed(video, stage / name)
                else:
                    encode_video([str(ffmpeg), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i",
                          str(playlists[section]), "-vf", f"fps={rate},setsar=1", "-an", *encode, str(stage / name)], f"Encoding {section}")
        else:
            command = [str(ffmpeg), "-v", "error", "-y", "-i", str(video)]
            filters = ["[0:v]scale=720:1280:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1,"
                       "pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p[feed]"]
            for index, section in enumerate(playlists, 1):
                command += ["-f", "concat", "-safe", "0", "-i", str(playlists[section])]
                filters.append(f"[{index}:v]setsar=1,format=yuv420p[{section}]")
            filters.append("".join(f"[{s}]" for s in sections) +
                           f"hstack=inputs={len(sections)}:shortest=0,fps={rate},format=yuv420p[v]")
            encode_video(command + ["-filter_complex_threads", "4", "-filter_complex", ";".join(filters),
                            "-map", "[v]", "-map", "0:a?", "-c:a", "copy", *encode, str(stage / names[0])], "Encoding combined video")
    _notify(progress, "Finishing saved files")
    for source, target in zip((stage / n for n in names), targets):
        # No replacement of an existing recording, including a last-minute collision.
        if target.exists():
            raise FileExistsError(str(target))
        source.rename(target)
    _notify(progress, "Saved", 1.0)
    return targets
