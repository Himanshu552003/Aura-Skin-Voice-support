"""
stt.py - Speech-to-text with Groq Whisper.

FastRTC gives us the customer's speech as numbers (a numpy array, usually at
48,000 samples per second). We shrink it to 16,000 per second (what Whisper
uses anyway, so the upload is 3x smaller), pack it as a WAV file in memory,
and send it to Groq.
"""

import io
import logging
import wave
from math import gcd

import numpy as np
from groq import Groq
from scipy.signal import resample_poly

import config

log = logging.getLogger(__name__)
client = Groq(api_key=config.GROQ_API_KEY)

WHISPER_RATE = 16000

# A hint so Whisper spells our brand words correctly ("Aria", not "Arya").
SPELLING_HINT = "Aura Skincare, Aria, order ID ORD-101, ORD-102, ORD-103, COD, UPI."

# Whisper gives each piece of text two "how sure am I" numbers. We drop a piece if:
#  - no_speech_prob is high (it was probably silence or background noise), or
#  - avg_logprob is very low (Whisper was guessing: mumbled or unclear audio).
# Then Aria asks the customer to repeat instead of answering a wrong guess.
NO_SPEECH_LIMIT = 0.6
MIN_CONFIDENCE = -1.0


def to_wav_bytes(sample_rate: int, audio: np.ndarray) -> bytes:
    """Turn a numpy audio array into the bytes of a .wav file (16 kHz, mono, 16-bit)."""
    audio = np.squeeze(audio)
    if audio.dtype != np.int16:  # float audio (-1.0 to 1.0) -> 16-bit integers
        audio = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    if sample_rate != WHISPER_RATE:
        g = gcd(WHISPER_RATE, sample_rate)
        audio = resample_poly(audio, WHISPER_RATE // g, sample_rate // g).astype(np.int16)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # 2 bytes = 16-bit
        wav.setframerate(WHISPER_RATE)
        wav.writeframes(audio.tobytes())
    return buffer.getvalue()


def transcribe(sample_rate: int, audio: np.ndarray) -> str:
    """Return what the customer said, or "" if nothing clear was heard."""
    if audio.size == 0:
        return ""
    result = client.audio.transcriptions.create(
        file=("speech.wav", to_wav_bytes(sample_rate, audio)),
        model=config.STT_MODEL,
        language="en",           # Indian English; Whisper still copes with some Hinglish
        prompt=SPELLING_HINT,
        response_format="verbose_json",  # gives us per-segment confidence numbers
        temperature=0,
    )
    segments = result.segments or []
    clear = [s["text"] for s in segments
             if s.get("no_speech_prob", 0) < NO_SPEECH_LIMIT
             and s.get("avg_logprob", 0) > MIN_CONFIDENCE]
    text = " ".join(t.strip() for t in clear).strip()

    # Text with no letters or digits at all (e.g. "." or "...") is not real speech.
    if not any(ch.isalnum() for ch in text):
        if result.text and result.text.strip():
            log.info("Ignored unclear audio: %r", result.text)
        return ""
    return text
