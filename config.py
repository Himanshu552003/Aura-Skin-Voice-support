"""
config.py - All settings in one place.

API keys come from the .env file (locally) or from "Secrets" (on Hugging Face Spaces).
Model names have sensible defaults but can be changed in .env without touching code.
"""

import os

from dotenv import load_dotenv

load_dotenv()  # reads the .env file, if there is one

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()

# Speech-to-text (Groq Whisper). "turbo" is the fastest Whisper on Groq.
STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3-turbo")

# LLM (Groq). gpt-oss-120b supports tool calling and answered in ~0.3 s in our tests.
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")

# Text-to-speech (Sarvam Bulbul v3, Indian English voice).
TTS_MODEL = "bulbul:v3"
TTS_SPEAKER = os.getenv("TTS_SPEAKER", "priya")
TTS_LANGUAGE = "en-IN"
TTS_SAMPLE_RATE = 24000  # Hz; FastRTC plays audio at 24 kHz by default


def check_keys() -> None:
    """Stop early with a clear message if an API key is missing."""
    missing = [name for name, value in
               [("GROQ_API_KEY", GROQ_API_KEY), ("SARVAM_API_KEY", SARVAM_API_KEY)]
               if not value or value.startswith("your_")]
    if missing:
        raise SystemExit(f"Missing API key(s): {', '.join(missing)}. "
                         "Copy .env.example to .env and add your keys.")
