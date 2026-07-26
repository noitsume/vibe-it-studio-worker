from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg tidak tersedia",
)
def test_local_sample_renders_video_and_audio(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "sample.mp4"
    subprocess.run(
        [
            sys.executable,
            str(root / "render.py"),
            "--timeline-file",
            str(root / "examples" / "timeline.sample.json"),
            "--resolution",
            "480",
            "--output",
            str(output),
        ],
        cwd=root,
        check=True,
    )
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    assert float(payload["format"]["duration"]) > 2.5
    assert {stream["codec_type"] for stream in payload["streams"]} == {"video", "audio"}
