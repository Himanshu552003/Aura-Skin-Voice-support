"""
app.py - The web app. Run it with:  python app.py

How one turn of the conversation works:
  1. FastRTC streams your microphone audio from the browser.
  2. ReplyOnPause waits until you stop talking, then calls respond() with your speech.
  3. respond(): speech -> text (Groq Whisper) -> Aria's answer (Groq LLM) -> voice (Sarvam).
  4. The voice is sent back to the browser in small pieces, so playback starts quickly.
"""

import logging
import os
import time

import gradio as gr
from fastrtc import (AdditionalOutputs, ReplyOnPause, WebRTC,
                     get_cloudflare_turn_credentials, get_current_context)

import config
import tts
from agent import DIDNT_CATCH, GREETING, TECH_TROUBLE, CallSession
from stt import transcribe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aria")

config.check_keys()

# Each browser call has its own id (webrtc_id) and its own CallSession.
sessions: dict[str, CallSession] = {}

# The greeting is always the same, so we record it once at start-up. Then Aria
# can say hello the instant a call starts, with no waiting for the TTS service.
log.info("Pre-recording greeting with Sarvam voice '%s'...", config.TTS_SPEAKER)
GREETING_AUDIO = tts.synthesize(GREETING)


def current_session() -> CallSession:
    """Find (or create) the CallSession for the call that is running right now."""
    call_id = get_current_context().webrtc_id
    if call_id not in sessions:
        sessions[call_id] = CallSession()
    return sessions[call_id]


def greet():
    """Runs once when a call starts: Aria says hello."""
    session = current_session()
    session.add_agent_line(GREETING)
    yield AdditionalOutputs(list(session.transcript))
    yield GREETING_AUDIO


def respond(audio):
    """Runs every time the customer finishes speaking."""
    session = current_session()
    sample_rate, samples = audio
    t_start = time.time()

    # 1. Speech -> text
    try:
        customer_text = transcribe(sample_rate, samples)
    except Exception:
        log.exception("Speech-to-text failed")
        customer_text = None
    t_stt = time.time()

    # 2. Text -> Aria's answer
    if customer_text is None:
        answer = TECH_TROUBLE
        session.add_agent_line(answer)
    elif customer_text == "":
        answer = DIDNT_CATCH  # silence, noise or mumbling
        session.add_agent_line(answer)
    else:
        log.info("Customer: %s", customer_text)
        session.add_customer_line(customer_text)
        yield AdditionalOutputs(list(session.transcript))  # show the customer's words right away
        answer = session.reply()
    t_llm = time.time()
    log.info("Aria: %s", answer)
    yield AdditionalOutputs(list(session.transcript))

    # 3. Answer -> voice, streamed piece by piece
    first_audio = None
    for piece in tts.stream_speech(answer):
        if first_audio is None:
            first_audio = time.time()
        yield piece

    if first_audio:
        log.info("Latency: STT %.0f ms | LLM %.0f ms | TTS first audio %.0f ms | total %.0f ms",
                 (t_stt - t_start) * 1000, (t_llm - t_stt) * 1000,
                 (first_audio - t_llm) * 1000, (first_audio - t_start) * 1000)


def turn_server_config():
    """On Hugging Face Spaces, browsers need a TURN server to connect. Locally they don't."""
    if os.getenv("SPACE_ID") and os.getenv("HF_TOKEN"):
        return get_cloudflare_turn_credentials()
    return None


with gr.Blocks(title="Aria - Aura Skincare Support") as demo:
    gr.Markdown("# 🌿 Aria: Aura Skincare Voice Support\n"
                "Click **Start Call**, allow the microphone, and talk to Aria.")
    with gr.Row():
        with gr.Column(scale=1, min_width=300):
            voice = WebRTC(
                label="Call",
                modality="audio",
                mode="send-receive",
                button_labels={"start": "Start Call", "stop": "End Call"},
                rtc_configuration=turn_server_config(),
                full_screen=False,  # keep the call button inside its own box
            )
        with gr.Column(scale=2):
            chat = gr.Chatbot(label="Conversation", type="messages", height=450,
                              allow_tags=False)

    voice.stream(
        ReplyOnPause(respond, startup_fn=greet),
        inputs=[voice],
        outputs=[voice],
        time_limit=600,         # a call can last up to 10 minutes
        concurrency_limit=10,   # up to 10 calls at the same time
    )
    # Sends each transcript update to the chat box. This is a streaming event, so it
    # must use Gradio's queue; concurrency_limit=None lets every call update at once.
    voice.on_additional_outputs(lambda transcript: transcript, outputs=[chat],
                                concurrency_limit=None, show_progress="hidden")


if __name__ == "__main__":
    demo.launch()
