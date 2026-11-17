"""Local speech-to-text. Runs where faster-whisper and the cached base.en model exist (this
machine); skipped in CI, which doesn't download the model."""
import importlib.util
import os
from pathlib import Path

import pytest

from fieldcopilot.voice import normalise_services, word_error_rate

FIXTURE = Path(__file__).parent / "fixtures" / "voice-note.wav"
# The note was synthesised with Windows text-to-speech from this script, written the way
# Whisper writes numbers.
REFERENCE = ("Update on shipment-api. Error rate is 11% since release 2.14. New pods are crash looping. "
             "I am asking the on-call lead to approve a rollback to revision 2.13.")


def test_word_error_rate():
    assert word_error_rate("roll back release 214", "roll back release 214") == 0
    assert word_error_rate("a b c d", "a x c") == 0.5


def test_spoken_service_names_are_normalised():
    assert normalise_services("Shipment API and orders consumer") == "shipment-api and orders-consumer"


model_ready = importlib.util.find_spec("faster_whisper") is not None and os.environ.get("FIELD_TEST_VOICE", "1") == "1" \
    and Path(os.environ.get("HF_HOME", "D:/cache/hf"), "hub", "models--Systran--faster-whisper-base.en").exists()


@pytest.mark.skipif(not model_ready, reason="needs faster-whisper and the cached base.en model")
def test_voice_note_becomes_a_timeline_entry(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    from fieldcopilot.voice import transcribe

    note = transcribe(FIXTURE)
    assert note.services == ["shipment-api"] and note.actions_mentioned == ["rollback"]
    assert word_error_rate(REFERENCE, note.transcript) < 0.15
    assert "not actions taken" in note.note
