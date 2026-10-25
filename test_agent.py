"""
test_agent.py - Talk to Aria by TYPING (no microphone, no voice). Needs GROQ_API_KEY.

    python test_agent.py          -> runs ALL the assignment's test conversations
    python test_agent.py 8        -> runs only conversation number 8
    python test_agent.py --chat   -> type your own messages (type "quit" to stop)

Each conversation is a NEW call, so the orders start fresh every time.
You can see which tools Aria used and what they returned.

NOTE: Groq's free plan has a daily token limit (200,000 per model). A full run uses
about 40,000 tokens, so don't run everything many times a day. Run single ones instead.
"""

import json
import sys
import time

from agent import GREETING, CallSession

sys.stdout.reconfigure(encoding="utf-8")

# (name, what the customer says in that call)
SCENARIOS = [
    ("Order tracking", ["Hi, can you tell me where my order ORD-101 is?"]),
    ("Return refused (outside 7 days)", ["I want to return my sunscreen, order ORD-102."]),
    ("Cancel allowed (Processing)", ["Please cancel my order ORD-103.",
                                     "Yes, cancel it.",
                                     "What's the status of that order now?"]),
    ("'Can I cancel?' asks before cancelling", ["Can I cancel my order ORD-103?",
                                                "Yes, please go ahead."]),
    ("Cancel refused (already shipped)", ["Cancel order ORD-101 please.",
                                          "Please, just make an exception this once."]),
    ("Order not found", ["Can you check order ORD-999?"]),
    ("Missing order ID", ["I want to track my order.", "Oh sorry, it's ORD-101."]),
    ("Damaged after 48 hours", ["My order ORD-102 arrived damaged, I want a replacement."]),
    ("PDF example: 20 days, opened", ["I bought this 20 days ago and have opened it. "
                                      "Can I return it?"]),
    ("COD and shipping", ["Do you have cash on delivery?",
                          "My order is 3000 rupees, can I pay cash?"]),
    ("Out of scope", ["Can you book me a flight to Goa?"]),
    ("Unknown info (no guessing)", ["Is your Vitamin C serum good for oily skin?"]),
]


def run_turn(session: CallSession, text: str) -> None:
    print(f"  Customer: {text}")
    tools_before = len(session.tool_log)
    session.add_customer_line(text)
    answer = session.reply()
    for used in session.tool_log[tools_before:]:
        print(f"     [tool] {used['tool']}({used['arguments']})")
        print(f"            -> {json.dumps(used['result'], ensure_ascii=False)}")
    print(f"  Aria:     {answer}")


def run_scenarios(only: int | None = None) -> None:
    for number, (name, lines) in enumerate(SCENARIOS, start=1):
        if only and number != only:
            continue
        print(f"\n=== {number}. {name} ===")
        session = CallSession()
        session.add_agent_line(GREETING)
        for line in lines:
            run_turn(session, line)
        if not only:
            time.sleep(10)  # stay under the free plan's tokens-per-minute limit


def chat() -> None:
    session = CallSession()
    session.add_agent_line(GREETING)
    print(f"Aria:     {GREETING}   (type 'quit' to stop)")
    while (text := input("You:      ").strip()).lower() not in ("quit", "exit"):
        if text:
            run_turn(session, text)


if __name__ == "__main__":
    if "--chat" in sys.argv:
        chat()
    else:
        numbers = [int(arg) for arg in sys.argv[1:] if arg.isdigit()]
        run_scenarios(numbers[0] if numbers else None)
