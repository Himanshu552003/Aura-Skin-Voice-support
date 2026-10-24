# Aria: AI Voice Customer Support Agent for Aura Skincare

**Live Application:** _coming soon (Hugging Face Spaces link)_

> **Build status:** Step 2 of 7 done (mock data, policy engine, tools, real-time voice loop). Sections marked _(in progress)_ describe parts that are not built yet.

---

## Approach

Aria is a browser-based voice agent built as a modular pipeline: FastRTC streams microphone audio, Groq Whisper transcribes it, a Groq LLM reasons and calls tools, and Sarvam Bulbul speaks the reply in an Indian voice. Brand policies are enforced in Python inside the tools, not only in the prompt. The code makes every policy decision and the LLM only explains it. This keeps the agent from promising refunds or cancellations the policy does not allow.

## Demo Video

_coming soon_

---

## Features

The table maps each part of the build to the assignment's Core Requirements (Section 2).

| Requirement | How it is met | Status |
|---|---|---|
| **A. Voice Conversation** | Real-time mic audio via FastRTC, Groq Whisper STT, Groq LLM, Sarvam Bulbul v3 streaming TTS (Indian voice). Persona "Aria": friendly, professional, concise. Conversation history kept for the whole call. Instant pre-recorded greeting. Unclear or mumbled audio gets "Sorry, I didn't quite catch that" instead of a guessed answer. | ✅ |
| **B. Brand Knowledge & Guardrails** | All policy numbers live in `policies.py`. Return, damage and cancellation decisions are made in code. Out-of-scope requests are politely declined. Invalid or missing order IDs return structured errors, so the agent asks the customer to verify the ID instead of guessing. | Policy engine ✅ / prompt _(in progress)_ |
| **C. Tool Calling** | `get_order_details`, `check_return_eligibility`, `cancel_order`. Order IDs heard by STT ("ord 101", "order one zero one") are normalised to `ORD-101`. | ✅ |
| **D. Post-Call Summary** | Chronological transcript, plus a JSON outcome (`customer_intent`, `order_id`, `resolution_status`, `call_summary`) from a separate LLM call over the transcript. | _(in progress)_ |
| **E. Testing Interface** | Start / End Call buttons, live Listening / Thinking / Speaking indicator, Test Orders panel, transcript and summary view. | _(in progress)_ |

**Mock data stays current.** Order dates are computed from the current time (IST) each time a call starts. ORD-102 is always "delivered 14 days ago" and ORD-103 is always "ordered 3 hours ago". ORD-101 is "expected by 6 PM today", which rolls over to 6 PM tomorrow when tested after 6 PM IST, so the promise is never in the past.

**Per-call isolation.** Each call gets its own copy of the order database. Cancelling ORD-103 in one call does not affect the next evaluator.

---

## Architecture

```mermaid
flowchart LR
    A[Browser mic] -->|WebRTC| B[FastRTC<br/>voice activity detection]
    B -->|speech segment| C[Groq Whisper<br/>speech-to-text]
    C -->|text| D[Groq LLM<br/>+ tool calling]
    D <-->|tool call / JSON result| E[tools.py<br/>+ policies.py]
    E <--> F[(Per-call<br/>order DB)]
    D -->|reply text| G[Sarvam Bulbul v3<br/>text-to-speech]
    G -->|audio| H[Browser speaker]
```

### One conversational turn

1. **FastRTC** streams the microphone over WebRTC. Its `ReplyOnPause` handler runs Silero VAD and fires once the customer stops speaking.
2. **`stt.py`** resamples the speech from 48 kHz to 16 kHz (a 3x smaller upload) and sends it to Groq Whisper. The request includes a spelling hint ("Aria", "Aura Skincare", "ORD-101") so brand words and order IDs come back correctly. Segments Whisper marks as likely silence (`no_speech_prob`) or low confidence (`avg_logprob`) are dropped, so noise and mumbling lead to "could you repeat that?" instead of a hallucinated answer.
3. **`agent.py`** keeps the call's message history and asks the Groq LLM for a reply. Low reasoning effort keeps replies fast.
4. **`tts.py`** calls Sarvam's **streaming** TTS endpoint for raw 24 kHz PCM. Audio is forwarded to the browser in 0.2 s pieces as it arrives, so Aria starts speaking before the whole sentence is synthesised.
5. Each browser call has its own `CallSession`, keyed by FastRTC's `webrtc_id`. It holds that call's history, transcript and order database.

**Measured latency** (end of customer speech → first audio from Aria, on a warm connection): **~0.65–0.9 s** (STT ~0.3 s, LLM ~0.2–0.35 s, TTS first audio ~0.2 s). The first turn of a session is slower (~1.5–2.5 s) while connections are opened.

### How the agent decides when to use a tool

_(in progress: completed in Step 3)_ The LLM gets JSON schemas for each tool (`TOOL_SCHEMAS` in `tools.py`). The tool descriptions say when each one applies, e.g. "use whenever the customer asks about a specific order". When the model returns a tool call, the app runs it, sends the result back to the model, and the model writes a short spoken reply.

### How guardrails are enforced

- **Code decides, LLM explains.** `policies.py` holds the rules:
  - 7-day return window for unopened products
  - 48-hour damage reporting
  - cancellation only while the order is Processing
  - free shipping from ₹499
  - COD up to ₹2,500

  The tools return a decision code such as `RETURN_WINDOW_CLOSED` or `ALREADY_SHIPPED` with a reason. The LLM cannot override these decisions.
- **No invented data.** Fields the brief does not specify are left out of tool results, and unknown order IDs return `ORDER_NOT_FOUND`.
- **Tools never crash.** Bad or unexpected LLM arguments, broken JSON and unknown tool names all return structured errors.
- **Assumption:** the brief says free delivery "above ₹499" and a ₹50 fee "below ₹499", which leaves exactly ₹499 undefined. This build treats ₹499 as free.

---

## Tech Stack

| Component | Choice | Why |
|---|---|---|
| Language | Python 3.11 | One language for the whole app |
| Real-time audio | FastRTC | WebRTC streaming with built-in voice activity detection, integrates directly with Gradio |
| UI | Gradio | Python-only UI, deploys natively on Hugging Face Spaces |
| Speech-to-text | Groq `whisper-large-v3-turbo` | Fastest hosted Whisper: ~0.3 s per turn in testing |
| LLM | Groq `openai/gpt-oss-120b` (`reasoning_effort="low"`) | Supports tool calling, ~0.2–0.35 s per reply in testing. Groq's Llama 3.x models were retired in 2026. |
| Text-to-speech | Sarvam `bulbul:v3`, voice `priya`, `en-IN` | Natural Indian voice; the streaming endpoint gives first audio in ~0.2–0.6 s |
| Hosting | Hugging Face Spaces | Free, public, first-class Gradio support |

All models can be changed in `.env` without code changes (`LLM_MODEL`, `STT_MODEL`, `TTS_SPEAKER`).

---

## Setup (run locally)

Requires Python 3.11.

```bash
git clone <your-repo-url>
cd <repo-folder>
python -m venv venv
# Windows:  venv\Scripts\activate
# Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then add your API keys
python test_tools.py        # tests tools & policies, no API keys needed
python app.py               # starts the app at http://127.0.0.1:7860
```

Open http://127.0.0.1:7860 in Chrome or Edge, click **Start Call** and allow microphone access. Headphones give the best experience.

## Environment Variables

See [`.env.example`](.env.example).

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Whisper STT and LLM |
| `SARVAM_API_KEY` | Bulbul TTS |
| `HF_TOKEN` | TURN server for WebRTC when deployed on Spaces |
| `LLM_MODEL`, `STT_MODEL`, `TTS_SPEAKER` | Optional overrides (defaults: `openai/gpt-oss-120b`, `whisper-large-v3-turbo`, `priya`) |

## Deployment

_(in progress: Step 6, Hugging Face Spaces)_

---

## How to Test

**Test orders**

| Order ID | Customer | Product | Value | Status | Notes |
|---|---|---|---|---|---|
| ORD-101 | Priya Sharma | Vitamin C Serum (30ml) | ₹699 | Out for Delivery | BlueDart BD-982103, expected by 6 PM today |
| ORD-102 | Rahul Verma | Hydrating Sunscreen SPF 50 | ₹499 | Delivered | Delhivery DL-441029, delivered 14 days ago |
| ORD-103 | Ananya Patel | Green Tea Face Wash + Toner | ₹850 | Processing | Ordered 3 hours ago, can be cancelled |

**Things to say**

| You say | Expected behaviour |
|---|---|
| "Where is my order ORD-101?" | Out for delivery with BlueDart, expected by 6 PM today |
| "I want to return ORD-102." | Politely refused: delivered 14 days ago, outside the 7-day window |
| "Please cancel ORD-103." | Cancelled (it is still Processing) |
| "Cancel ORD-101." | Refused: already out for delivery, can refuse at doorstep |
| "Check order ORD-999." | "I couldn't find that order, could you verify the ID?" |
| "I want to track my order." (no ID) | Asks for the order ID |
| "Can you book me a flight to Goa?" | Politely says it only helps with Aura Skincare queries |
| "Is COD available?" / "Is shipping free?" | Answers from brand policy |

**Without voice:** `python test_tools.py` runs 36 checks on the tools and policy rules.

---

## Tell Us How I Think

> _Draft answers. To be rewritten in my own words._

**1. Why did you choose your particular architecture and technology stack?**
I chose a modular STT → LLM → TTS pipeline over a speech-to-speech model because each step is visible and testable. I can read the transcript, see exactly which tool was called, and enforce policies in normal Python code. Groq gives very low latency for both Whisper and the LLM, and Sarvam Bulbul gives a natural Indian voice. FastRTC + Gradio kept everything in Python and deploys to Hugging Face Spaces in a few steps.

**2. What was the most difficult part of the assignment, and how did you solve it?**
_(to be filled in after building: likely candidates are latency tuning or reliable tool calling from noisy speech input)_

**3. If you had one more week, what would you improve first and why?**
_(draft)_ Stream LLM tokens straight into TTS sentence by sentence, so Aria starts speaking before the full reply is written. TTS audio already streams, but the reply text is still generated in full first. I would also tune interruption (barge-in) handling for laptop speakers without headphones. These two changes have the biggest effect on how natural a call feels.

**4. What would need to change at 1,000 conversations a day?**
_(draft)_
- Replace the in-memory mock data with a real order system API, with customer identity verification.
- Add monitoring of latency, tool errors and escalations.
- Run automated regression tests on recorded conversations.
- Add a human-handoff path.
- Manage API rate limits and cost per call.
- Store transcripts securely, with PII redaction.

---

## Author

**Himanshu Yadav**, [LinkedIn](https://www.linkedin.com/in/himanshu-yadav552003/)
