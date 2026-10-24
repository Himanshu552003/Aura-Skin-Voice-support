"""
tts.py - Text-to-speech with Sarvam Bulbul v3 (Indian voice).

We use Sarvam's STREAMING endpoint. It sends audio back in small pieces while
it is still generating, so Aria starts speaking after ~0.6 s instead of waiting
~1.5 s for the whole sentence. The audio is raw 16-bit PCM at 24 kHz, which we
turn into numpy arrays that FastRTC can play.
"""

import logging
import re
from collections.abc import Iterator

import numpy as np
import requests

import config

log = logging.getLogger(__name__)

STREAM_URL = "https://api.sarvam.ai/text-to-speech/stream"
CHUNK_BYTES = 9600  # 0.2 seconds of audio per piece (24,000 samples/s x 2 bytes x 0.2 s)

# One shared connection to Sarvam, so we don't reconnect for every sentence.
session = requests.Session()
session.headers.update({"api-subscription-key": config.SARVAM_API_KEY})


def clean_for_speech(text: str) -> str:
    """Remove things that sound bad when read aloud, like markdown stars or emojis."""
    text = re.sub(r"[*_#`>]", "", text)
    text = re.sub(r"[^\w\s.,!?;:'\"()\-₹%/&+@]", "", text)  # drops emojis and odd symbols
    return re.sub(r"\s+", " ", text).strip()


def stream_speech(text: str) -> Iterator[tuple[int, np.ndarray]]:
    """Yield (sample_rate, audio) pieces as Sarvam generates them."""
    text = clean_for_speech(text)
    if not text:
        return
    response = session.post(STREAM_URL, stream=True, timeout=30, json={
        "text": text,
        "language_code": config.TTS_LANGUAGE,
        "speaker": config.TTS_SPEAKER,
        "model": config.TTS_MODEL,
        "speech_sample_rate": config.TTS_SAMPLE_RATE,
        "output_audio_codec": "linear16",  # raw PCM, no file header: easy to stream
    })
    if not response.ok:
        log.error("Sarvam TTS error %s: %s", response.status_code, response.text[:300])
        return

    leftover = b""  # a 16-bit sample is 2 bytes; keep any odd byte for the next piece
    for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
        data = leftover + chunk
        usable = len(data) - (len(data) % 2)
        leftover = data[usable:]
        if usable:
            samples = np.frombuffer(data[:usable], dtype=np.int16)
            yield config.TTS_SAMPLE_RATE, samples.reshape(1, -1)


def synthesize(text: str) -> tuple[int, np.ndarray]:
    """Generate the whole audio at once (used to pre-record the greeting)."""
    pieces = [audio for _, audio in stream_speech(text)]
    audio = np.concatenate(pieces, axis=1) if pieces else np.zeros((1, 0), dtype=np.int16)
    return config.TTS_SAMPLE_RATE, audio
