"""Transcribe each call's dual-channel recording, one side at a time.

Twilio records the trunk in dual channel: channel 1 is our patient bot (A),
channel 2 is the clinic agent (B). Splitting the channels and transcribing
each one separately gives a clean, correctly attributed transcript even when
both sides talk over each other.

Usage: python transcribe_calls.py            (every calls/*/recording.mp3 without a transcript)
Needs ffmpeg on PATH and OPENAI_API_KEY in .env.
"""

import os
import subprocess
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
CALLS_DIR = Path(os.getenv("CALLS_DIR", "calls"))
client = OpenAI()


def split_channels(recording: Path, out_dir: Path) -> tuple[Path, Path]:
    a, b = out_dir / "a.mp3", out_dir / "b.mp3"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(recording),
         "-filter_complex", "[0:a]channelsplit=channel_layout=stereo[l][r]",
         "-map", "[l]", "-ac", "1", "-ar", "16000", str(a),
         "-map", "[r]", "-ac", "1", "-ar", "16000", str(b)],
        check=True,
    )
    return a, b


def segments(path: Path) -> list:
    with path.open("rb") as f:
        resp = client.audio.transcriptions.create(
            model="whisper-1", file=f, language="en", response_format="verbose_json"
        )
    return resp.segments or []


def transcribe(call_dir: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        a, b = split_channels(call_dir / "recording.mp3", Path(tmp))
        segs = [(s.start, "A", s.text.strip()) for s in segments(a)]
        segs += [(s.start, "B", s.text.strip()) for s in segments(b)]
    segs.sort()
    lines: list[list] = []
    for start, speaker, text in segs:
        if lines and lines[-1][1] == speaker:
            lines[-1][2] += " " + text
        else:
            lines.append([start, speaker, text])
    out = "\n".join(f"[{int(t) // 60}:{int(t) % 60:02d}] {s}: {x}" for t, s, x in lines)
    (call_dir / "transcript.txt").write_text(out + "\n")
    print(f"transcribed {call_dir.name}")


def main() -> None:
    for rec in sorted(CALLS_DIR.glob("*/recording.mp3")):
        if not (rec.parent / "transcript.txt").exists():
            transcribe(rec.parent)


if __name__ == "__main__":
    main()
