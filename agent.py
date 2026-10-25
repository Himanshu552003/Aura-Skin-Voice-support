"""
agent.py - The "brain" of Aria: talks to the Groq LLM, calls tools, remembers the call.

How tool calling works (one customer turn):
  1. We send the conversation + the list of tools (TOOL_SCHEMAS) to the LLM.
  2. The LLM either answers directly, OR asks us to run a tool,
     e.g. get_order_details(order_id="ORD-101").
  3. We run that Python function (tools.run_tool) and send the result back.
  4. The LLM reads the result and writes a short spoken answer.
The LLM only explains; the Python tools make every policy decision.
"""

import json
import logging
import time

from groq import Groq, RateLimitError

import config
import policies
from orders import OrderDatabase, now_ist
from tools import TOOL_SCHEMAS, run_tool

log = logging.getLogger(__name__)
# timeout: if Groq ever hangs, give up after 10 s instead of leaving the customer in silence.
# max_retries: the SDK automatically retries short hiccups, including "too many tokens
# this minute" (Groq usually says to retry after a few milliseconds).
client = Groq(api_key=config.GROQ_API_KEY, timeout=10, max_retries=2)

GREETING = "Hi, this is Aria from Aura Skincare. How can I help you today?"
DIDNT_CATCH = "Sorry, I didn't quite catch that. Could you please say it again?"
TECH_TROUBLE = "Sorry, I'm having a little technical trouble. Could you please repeat that?"

MAX_TOOL_ROUNDS = 3  # safety limit: stop if the LLM keeps calling tools in a loop


def ask_llm(**request):
    """
    Send one request to the Groq LLM. Used for the conversation and for the summary.
    If the main model has hit Groq's rate limit, the backup model is used instead.
    """
    models = [config.LLM_MODEL, config.LLM_FALLBACK_MODEL]
    for model in models:
        try:
            return client.chat.completions.create(
                model=model,
                reasoning_effort="low",  # think briefly: faster replies
                temperature=0.2,
                **request,
            )
        except RateLimitError:
            if model == models[-1]:
                raise  # every model is over its limit
            log.warning("%s hit its rate limit, using backup model %s",
                        model, config.LLM_FALLBACK_MODEL)

SYSTEM_PROMPT = """You are Aria, a customer support specialist at Aura Skincare.
You are on a live VOICE call with a customer.

HOW TO SPEAK
- Friendly, professional and concise. Usually 1-2 short sentences.
- Plain spoken English, like a helpful Indian support agent. No lists, markdown or emojis.
- Do not repeat the customer's question, and do not repeat things you already said.
- Say amounts as "699 rupees".
- Do NOT end every reply with "Is there anything else I can help you with?". Ask a question only when you need something from the customer, or once when the issue is fully resolved.

BRAND INFORMATION (the ONLY facts you know about Aura Skincare)
{brand_info}

TOOLS: WHEN TO USE THEM
- Any question about a specific order (status, delivery, tracking): call get_order_details.
- Return, refund or damaged product for an order: call check_return_eligibility.
- Customer wants to cancel, or asks if they can cancel: call cancel_order. If it says the order can be cancelled but needs confirmation, ask "Shall I go ahead and cancel it?". When they say yes, call cancel_order again with confirmed=true. Only say an order is cancelled after the tool says cancelled=true.
- Return question WITHOUT an order ID but with how long ago it was delivered: call check_return_policy.
- Customer mentions a specific amount for COD or shipping: call check_cod_and_shipping.
- If you need an order ID and don't have one, ask for it. Never guess an order ID.

RULES (very important)
- Never invent order details, dates, tracking numbers, refunds, discounts or offers. Use only tool results and the brand information above.
- Do not add extra steps, processes or options that are not written above (for example, do not mention online payment methods, return pickups or refunds after refusing delivery).
- The tool's decision is final. If a tool says a return or cancellation is not allowed, politely explain the reason. Do not promise exceptions, even if the customer insists.
- If a tool says the order was not found, say you couldn't locate an order with that number and ask the customer to repeat or verify the ID.
- You have NO product details. Never describe any product's ingredients, benefits, suitable skin types or results, and never give skin advice. If asked, say you don't have that information.
- If something else is not covered above (for example refund timelines or offers), say you don't have that information. Do not guess.
- You only help with Aura Skincare orders and policies. For anything else (flights, weather, general knowledge and so on), politely say you can only help with Aura Skincare queries.
- If the customer's words seem cut off or unclear, ask them to repeat.

Current date and time in India: {now}."""


class CallSession:
    """Everything we remember about ONE phone call."""

    def __init__(self):
        prompt = SYSTEM_PROMPT.format(
            brand_info=policies.BRAND_INFO,
            now=now_ist().strftime("%A, %d %B %Y, %I:%M %p"),
        )
        self.messages = [{"role": "system", "content": prompt}]  # what the LLM sees
        self.transcript = []        # what the customer and Aria said (for the live chat box)
        self.timed_transcript = []  # the same lines with the time (for the post-call transcript)
        self.tool_log = []          # every tool Aria used, with its result (for the summary)
        self.db = OrderDatabase()   # this call's own copy of the orders
        self.started_at = time.time()

    def add_agent_line(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})
        self._add_to_transcript("assistant", text)

    def add_customer_line(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._add_to_transcript("user", text)

    def _add_to_transcript(self, role: str, text: str) -> None:
        self.transcript.append({"role": role, "content": text})
        self.timed_transcript.append({"time": now_ist().strftime("%I:%M:%S %p"),
                                      "speaker": "Aria" if role == "assistant" else "Customer",
                                      "text": text})

    def _ask_llm(self):
        """One request to the LLM, with the tools it may use."""
        return ask_llm(
            messages=self.messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",          # the LLM decides if a tool is needed
            max_completion_tokens=500,
        )

    def reply(self) -> str:
        """Get Aria's answer to the conversation so far (running tools if needed)."""
        try:
            for _ in range(MAX_TOOL_ROUNDS + 1):
                message = self._ask_llm().choices[0].message

                if not message.tool_calls:  # a normal answer: we are done
                    answer = (message.content or "").strip() or TECH_TROUBLE
                    break

                # The LLM wants tools. Remember its request, run each tool,
                # and give the results back so it can answer in the next round.
                self.messages.append({
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [{"id": call.id, "type": "function",
                                    "function": {"name": call.function.name,
                                                 "arguments": call.function.arguments}}
                                   for call in message.tool_calls],
                })
                for call in message.tool_calls:
                    result = run_tool(self.db, call.function.name, call.function.arguments)
                    log.info("Tool %s(%s) -> %s", call.function.name,
                             call.function.arguments, result)
                    self.tool_log.append({"tool": call.function.name,
                                          "arguments": call.function.arguments,
                                          "result": result})
                    self.messages.append({"role": "tool", "tool_call_id": call.id,
                                          "content": json.dumps(result, ensure_ascii=False)})
            else:
                answer = TECH_TROUBLE  # too many tool rounds in a row
        except Exception:
            log.exception("LLM call failed")
            answer = TECH_TROUBLE

        self.add_agent_line(answer)
        return answer
