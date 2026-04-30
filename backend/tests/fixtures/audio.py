"""Synthetic PCM audio generators for streaming-pipeline tests.

We do not ship real microphone recordings — tests synthesize tones / silence
that exercise VAD without leaking PII or model weights.
"""

from __future__ import annotations

import math
import struct
import wave
from io import BytesIO
from pathlib import Path

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit


def _samples_for_seconds(seconds: float) -> int:
    return int(SAMPLE_RATE * seconds)


def silence_pcm(seconds: float) -> bytes:
    """Return raw 16-bit mono PCM @ 16 kHz of pure silence."""
    return b"\x00\x00" * _samples_for_seconds(seconds)


def speech_like_pcm(seconds: float, freq: float = 220.0, amplitude: float = 0.4) -> bytes:
    """Sine wave loud enough to clear the SimpleVAD threshold (0.08).

    This is *not* real speech, but it triggers VAD and produces deterministic
    bytes for transcription mocks to consume.
    """
    n = _samples_for_seconds(seconds)
    peak = int(32767 * amplitude)
    out = bytearray()
    for i in range(n):
        sample = int(peak * math.sin(2 * math.pi * freq * (i / SAMPLE_RATE)))
        out += struct.pack("<h", sample)
    return bytes(out)


def speech_then_silence(speech_seconds: float, silence_seconds: float) -> bytes:
    return speech_like_pcm(speech_seconds) + silence_pcm(silence_seconds)


def write_wav(path: Path | str, pcm: bytes) -> Path:
    """Write raw PCM to a WAV file (used by Playwright fake-audio-capture)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return path


def pcm_to_wav_bytes(pcm: bytes) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()
