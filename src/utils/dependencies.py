"""Report missing prerequisites without silently installing software."""
import subprocess
from utils.logger_manager import logger

def check_ffmpeg_binary(ffmpeg_path="ffmpeg"):
    try:
        subprocess.run([ffmpeg_path,"-version"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
        return True
    except (OSError,subprocess.SubprocessError):
        return False

def check_ffmpeg(ffmpeg_path="ffmpeg"):
    if not check_ffmpeg_binary(ffmpeg_path):
        raise RuntimeError("FFmpeg is missing. Use the desktop app for automatic first-use setup, or install FFmpeg and ffprobe.")
