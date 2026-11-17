"""Voice notes for on-call engineers, transcribed on the machine (no audio leaves it).

faster-whisper (CTranslate2) runs Whisper base.en on CPU with int8 weights. A note
becomes a timeline entry: the transcript (redacted), the services it mentions, and any
action it talks about, flagged so nobody mistakes "I'm asking for a rollback" for a
rollback that happened.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from fieldcopilot.guard.pii import redact

SERVICES = ["shipment-api", "tracking-worker", "rate-quote", "label-printer", "orders-consumer", "carrier-gateway"]
ACTIONS = {"rollback": r"\broll ?back\b|\brollback\b", "scale": r"\bscal(e|ing)\b", "restart": r"\brestart",
           "flag": r"\bcircuit breaker\b|\bflag\b"}


@dataclass
class VoiceNote:
    transcript: str
    services: list[str] = field(default_factory=list)
    actions_mentioned: list[str] = field(default_factory=list)
    redactions: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    note: str = "Actions mentioned in a voice note are not actions taken; check the approvals log."


@lru_cache(maxsize=1)
def _model(size: str = "base.en"):
    from faster_whisper import WhisperModel

    # Uses the Hugging Face cache (HF_HOME), where Systran/faster-whisper-base.en already sits.
    return WhisperModel(size, device="cpu", compute_type="int8")


def read_wav(path: str | Path):
    """16-bit PCM WAV -> float32 mono samples at 16 kHz. Decoding ourselves avoids PyAV,
    whose newer releases break faster-whisper's own decoder. Other formats are converted
    to WAV before upload (the browser recorder sends WAV)."""
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM WAV")
        rate, channels = w.getframerate(), w.getnchannels()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000:  # simple linear resample; fine for speech notes
        positions = np.linspace(0, len(audio) - 1, int(len(audio) * 16000 / rate))
        audio = np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)
    return audio


def normalise_services(text: str) -> str:
    """Speech says "shipment api"; the system says shipment-api."""
    for service in SERVICES:
        spoken = service.replace("-", r"[\s-]?")
        text = re.sub(spoken, service, text, flags=re.I)
    return text


def transcribe(path: str | Path, size: str = "base.en") -> VoiceNote:
    segments, info = _model(size).transcribe(read_wav(path), beam_size=1, vad_filter=True)
    raw = " ".join(s.text.strip() for s in segments)
    text = normalise_services(raw)
    clean, kinds = redact(text)
    return VoiceNote(
        transcript=clean,
        services=[s for s in SERVICES if s in text],
        actions_mentioned=[a for a, p in ACTIONS.items() if re.search(p, text, re.I)],
        redactions=kinds, duration_s=round(info.duration, 1),
    )


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref = re.findall(r"[a-z0-9']+", reference.lower())
    hyp = re.findall(r"[a-z0-9']+", hypothesis.lower())
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[len(hyp)] / max(1, len(ref))
