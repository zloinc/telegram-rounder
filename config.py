import os
import shutil


FFMPEG = (
    os.getenv("FFMPEG_PATH")
    or shutil.which("ffmpeg")
    or "/opt/homebrew/bin/ffmpeg"
)

FFPROBE = (
    os.getenv("FFPROBE_PATH")
    or shutil.which("ffprobe")
    or "/opt/homebrew/bin/ffprobe"
)
