"""ffmpeg helpers for audio upload mode.

Video uploads are reduced to their audio track at upload time so only the
small audio file is stored and shipped to the transcription service. Runs
in the API container (ffmpeg installed in Dockerfile.api/.worker).
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm"}
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

FFMPEG = os.getenv("FFMPEG_PATH", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_PATH", "ffprobe")

# 16 kHz mono is what Whisper resamples to anyway; 96k AAC keeps artifacts low.
TRANSCODE_ARGS = ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "96k"]


class AudioExtractionError(Exception):
    """ffmpeg could not produce an audio file from the upload."""


async def _run(cmd: list) -> Tuple[int, str]:
    """Run a subprocess, returning (returncode, stderr tail)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError) as e:
        return 127, f"{cmd[0]} not available: {e}"
    _, stderr = await proc.communicate()
    tail = stderr.decode(errors="replace")[-2000:] if stderr else ""
    return proc.returncode or 0, tail


async def extract_audio(video_path: Path, output_path: Path) -> Path:
    """Extract the audio track from a video file into ``output_path`` (.m4a).

    Tries a stream copy first (instant for AAC-in-MP4/MOV); if the source
    codec doesn't fit the m4a container, falls back to a 16 kHz mono AAC
    transcode. Raises AudioExtractionError with the ffmpeg stderr tail on
    failure.
    """
    copy_cmd = [FFMPEG, "-y", "-i", str(video_path), "-vn", "-acodec", "copy", str(output_path)]
    code, stderr_tail = await _run(copy_cmd)
    if code == 0 and output_path.exists() and output_path.stat().st_size > 0:
        logger.info(f"Audio stream-copied: {video_path.name} -> {output_path.name}")
        return output_path

    logger.info(f"Stream copy failed for {video_path.name} (rc={code}); transcoding")
    output_path.unlink(missing_ok=True)
    transcode_cmd = [FFMPEG, "-y", "-i", str(video_path), *TRANSCODE_ARGS, str(output_path)]
    code, stderr_tail = await _run(transcode_cmd)
    if code != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        output_path.unlink(missing_ok=True)
        raise AudioExtractionError(f"ffmpeg failed (rc={code}): {stderr_tail[-500:]}")

    logger.info(f"Audio transcoded: {video_path.name} -> {output_path.name}")
    return output_path


async def get_duration_seconds(media_path: Path) -> Optional[float]:
    """Media duration via ffprobe, or None if it can't be determined."""
    try:
        proc = await asyncio.create_subprocess_exec(
            FFPROBE,
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        logger.warning("ffprobe not available; skipping duration probe")
        return None
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except (ValueError, AttributeError):
        return None
