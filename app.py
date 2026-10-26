"""
app.py - The web app. Run it with:  python app.py

How one turn of the conversation works:
  1. FastRTC streams your microphone audio from the browser.
  2. ReplyOnPause waits until you stop talking, then calls respond() with your speech.
  3. respond(): speech -> text (Groq Whisper) -> Aria's answer (Groq LLM + tools) -> voice (Sarvam).
  4. The voice is sent back to the browser in small pieces, so playback starts quickly.

While this happens, respond() also sends the live transcript and the current state
(listening / thinking / speaking) to the page. When the call ends, a separate LLM
call writes the JSON summary.
"""

import logging
import os
import time

import gradio as gr
from fastrtc import (AdditionalOutputs, ReplyOnPause, WebRTC,
                     get_cloudflare_turn_credentials,
                     get_cloudflare_turn_credentials_async, get_current_context)

import config
import tts
import ui
from agent import DIDNT_CATCH, GREETING, TECH_TROUBLE, CallSession
from stt import transcribe
from summary import summarize_call

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aria")

config.check_keys()

# Each browser call has its own id (webrtc_id) and its own CallSession.
sessions: dict[str, CallSession] = {}
OLD_SESSION_SECONDS = 3600  # forget calls older than 1 hour (e.g. browser closed without End Call)

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


def update_page(session: CallSession, state: str) -> AdditionalOutputs:
    """Send the live transcript + current state (listening/thinking/speaking) to the page."""
    return AdditionalOutputs(list(session.transcript), state)


PLAYBACK_MARGIN = 0.4  # seconds: the browser plays audio slightly after we send it


def play_and_wait(audio_pieces):
    """
    Send Aria's voice to the browser, then wait until it has finished PLAYING.

    FastRTC queues audio faster than it plays, so without waiting:
      - the status would switch to "Listening" while Aria is still talking, and
      - the microphone would be switched back on while Aria's voice is still
        coming out of the speakers (it would hear her as if she were the customer).
    We wait in small 0.1 s steps (yielding None = "nothing to send").
    """
    started, seconds = None, 0.0
    for sample_rate, audio in audio_pieces:
        if started is None:
            started = time.time()
        seconds += audio.shape[1] / sample_rate
        yield sample_rate, audio
    if started is None:
        return
    while time.time() < started + seconds + PLAYBACK_MARGIN:
        time.sleep(0.1)
        yield None


def greet():
    """Runs once when a call starts: Aria says hello."""
    session = current_session()
    session.add_agent_line(GREETING)
    yield update_page(session, "speaking")
    yield from play_and_wait([GREETING_AUDIO])
    yield update_page(session, "listening")


def respond(audio):
    """Runs every time the customer finishes speaking."""
    session = current_session()
    sample_rate, samples = audio
    t_start = time.time()
    yield update_page(session, "thinking")

    # 1. Speech -> text
    try:
        customer_text = transcribe(sample_rate, samples)
    except Exception:
        log.exception("Speech-to-text failed")
        customer_text = None
    t_stt = time.time()

    # 2. Text -> Aria's answer (the agent calls tools if it needs order data)
    if customer_text is None:
        answer = TECH_TROUBLE
        session.add_agent_line(answer)
    elif customer_text == "":
        answer = DIDNT_CATCH  # silence, noise or mumbling
        session.add_agent_line(answer)
    else:
        log.info("Customer: %s", customer_text)
        session.add_customer_line(customer_text)
        yield update_page(session, "thinking")  # show the customer's words right away
        answer = session.reply()
    t_llm = time.time()
    log.info("Aria: %s", answer)
    yield update_page(session, "speaking")

    # 3. Answer -> voice, streamed piece by piece
    first_audio = None
    for piece in play_and_wait(tts.stream_speech(answer)):
        if piece is not None and first_audio is None:
            first_audio = time.time()
            log.info("Latency: STT %.0f ms | LLM %.0f ms | TTS first audio %.0f ms | "
                     "total %.0f ms", (t_stt - t_start) * 1000, (t_llm - t_stt) * 1000,
                     (first_audio - t_llm) * 1000, (first_audio - t_start) * 1000)
        yield piece

    yield update_page(session, "listening")


def on_call_start():
    """Start Call clicked: reset the page and forget very old calls."""
    for call_id, session in list(sessions.items()):
        if time.time() - session.started_at > OLD_SESSION_SECONDS:
            sessions.pop(call_id, None)
    return (ui.status_html("connecting"), [],
            "_The transcript appears here after you end the call._", None)


def on_call_end(call_id):
    """End Call clicked: show the full transcript and the JSON summary."""
    yield ui.status_html("ended"), "_Preparing the transcript and summary..._", None
    session = sessions.pop(call_id, None) if call_id else None
    if session is None:
        yield ui.status_html("ended"), "_No conversation was recorded for this call._", None
        return
    yield (ui.status_html("ended"), ui.transcript_markdown(session.timed_transcript),
           summarize_call(session))


# A free public STUN server: helps browser and server find each other, but can't
# relay audio through strict firewalls. Used only if TURN is not available.
STUN_ONLY = {"iceServers": [{"urls": "stun:stun.l.google.com:19302"}]}


def has_cloudflare_turn_keys() -> bool:
    return bool(os.getenv("CLOUDFLARE_TURN_KEY_ID") and os.getenv("CLOUDFLARE_TURN_KEY_API_TOKEN"))


async def browser_turn_credentials():
    """
    Fresh Cloudflare TURN credentials for each call (they are short-lived).
    hf_token="" makes FastRTC use our Cloudflare keys directly, instead of its
    HF_TOKEN relay service (turn.fastrtc.org), whose SSL certificate has expired.
    """
    try:
        return await get_cloudflare_turn_credentials_async(hf_token="")
    except Exception:
        log.exception("Could not get TURN credentials, falling back to STUN only")
        return STUN_ONLY


def turn_server_config():
    """
    On Hugging Face Spaces, the browser and the server sit on different networks, so
    WebRTC needs a TURN relay server to connect them. Locally this isn't needed.
    Returns (browser config, server config). Never crashes the app.
    """
    if not os.getenv("SPACE_ID"):
        return None, None  # running locally
    if not has_cloudflare_turn_keys():
        log.warning("Cloudflare TURN keys are missing: using STUN only, so some "
                    "networks may fail to connect. See README > Deployment.")
        return STUN_ONLY, None
    try:
        server = get_cloudflare_turn_credentials(hf_token="", ttl=86_400)
    except Exception:
        log.exception("Could not get server TURN credentials")
        server = None
    return browser_turn_credentials, server


client_rtc, server_rtc = turn_server_config()

with gr.Blocks(title="Aria - Aura Skincare Support", css=ui.CSS,
               theme=gr.themes.Soft(primary_hue="green")) as demo:
    gr.Markdown("# 🌿 Aria: Aura Skincare Voice Support\n"
                "Click **Start Call**, allow the microphone, and talk to Aria. "
                "Headphones give the best experience.")

    with gr.Row():
        with gr.Column(scale=1, min_width=320):
            status = gr.HTML(ui.status_html("idle"))
            voice = WebRTC(
                label="Call",
                modality="audio",
                mode="send-receive",
                button_labels={"start": "Start Call", "stop": "End Call"},
                rtc_configuration=client_rtc,
                server_rtc_configuration=server_rtc,
                full_screen=False,  # keep the call button inside its own box
            )
        with gr.Column(scale=2):
            with gr.Accordion("🧪 Test Orders (use these IDs)", open=True):
                orders_md = gr.Markdown(ui.orders_panel())
            chat = gr.Chatbot(label="Live conversation", type="messages", height=320,
                              allow_tags=False)

    gr.Markdown("## 📋 Post-Call Summary")
    with gr.Row():
        with gr.Column():
            gr.Markdown("**Call transcript**")
            transcript_md = gr.Markdown("_The transcript appears here after you end the call._")
        with gr.Column():
            gr.Markdown("**Structured call outcome (JSON)**")
            summary_json = gr.JSON(label="Call summary")

    # The live call: ReplyOnPause calls respond() each time the customer pauses.
    voice.stream(
        # can_interrupt=False: ignore the microphone while Aria is speaking. Without
        # headphones the mic hears Aria's own voice from the speakers, and with
        # interruptions on, that echo kept cutting her off (broken, garbled speech).
        ReplyOnPause(respond, startup_fn=greet, can_interrupt=False),
        inputs=[voice],
        outputs=[voice],
        time_limit=600,         # a call can last up to 10 minutes
        concurrency_limit=10,   # up to 10 calls at the same time
    )
    # Live transcript + status updates sent by respond()/greet() via AdditionalOutputs.
    # This is a streaming event, so it must use Gradio's queue (no queue=False).
    voice.on_additional_outputs(
        lambda transcript, state: (transcript, ui.status_html(state)),
        outputs=[chat, status], concurrency_limit=None, show_progress="hidden")

    voice.start_recording(on_call_start, outputs=[status, chat, transcript_md, summary_json])
    voice.stop_recording(on_call_end, inputs=[voice],
                         outputs=[status, transcript_md, summary_json], concurrency_limit=10)

    # Rebuild the Test Orders panel on every page load, so "3 hours ago" etc. stay true.
    demo.load(ui.orders_panel, outputs=[orders_md])


if __name__ == "__main__":
    # ssr_mode=False: server-side rendering is not needed here and can clash with
    # custom components like FastRTC's WebRTC on Hugging Face Spaces.
    demo.launch(ssr_mode=False)
