"""
agent.py - The "brain" of Aria: talks to the Groq LLM and remembers the call.

Step 2 version: basic persona and memory only.
Step 3 will add the brand policies and tool calling here.
"""

import logging

from groq import Groq

import config
from orders import OrderDatabase

log = logging.getLogger(__name__)
client = Groq(api_key=config.GROQ_API_KEY)

GREETING = "Hi, this is Aria from Aura Skincare. How can I help you today?"
DIDNT_CATCH = "Sorry, I didn't quite catch that. Could you please say it again?"
TECH_TROUBLE = "Sorry, I'm having a little technical trouble. Could you please repeat that?"

SYSTEM_PROMPT = """You are Aria, a customer support specialist at Aura Skincare, a premium organic Indian skincare brand.
You are on a live VOICE call, so:
- Be friendly, professional and concise: usually 1-2 short sentences.
- Speak naturally, like a helpful Indian support agent. No lists, no markdown, no emojis.
- Do not repeat the customer's question back or repeat what you already said.
- If you don't know something, say so honestly. Never make up facts."""


class CallSession:
    """Everything we remember about ONE phone call."""

    def __init__(self):
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]  # what the LLM sees
        self.transcript = []  # what the customer and Aria said, in order (for the UI)
        self.db = OrderDatabase()  # this call's own copy of the orders (used from Step 3)

    def add_agent_line(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})
        self.transcript.append({"role": "assistant", "content": text})

    def add_customer_line(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self.transcript.append({"role": "user", "content": text})

    def reply(self) -> str:
        """Ask the LLM for Aria's answer to the conversation so far, and remember it."""
        try:
            response = client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=self.messages,
                reasoning_effort="low",      # think briefly: faster replies
                max_completion_tokens=400,
                temperature=0.3,
            )
            answer = (response.choices[0].message.content or "").strip() or TECH_TROUBLE
        except Exception:
            log.exception("LLM call failed")
            answer = TECH_TROUBLE
        self.add_agent_line(answer)
        return answer
