"""
ui.py - The pieces of the web page that are not the call itself:
    - the live status badge (Listening / Thinking / Speaking)
    - the Test Orders panel
    - the post-call transcript
"""

from orders import build_orders, now_ist
from tools import describe_time

# state name -> (label, hint shown under it)
STATES = {
    "idle": ("Ready", "Click Start Call and allow the microphone."),
    "connecting": ("Connecting...", "Setting up the call."),
    "listening": ("Listening", "Go ahead and speak. Aria replies when you pause."),
    "thinking": ("Thinking", "Aria is working on your request."),
    "speaking": ("Speaking", "Aria is talking."),
    "ended": ("Call ended", "The transcript and summary are below."),
}

CSS = """
.status-badge {display:flex; align-items:center; gap:12px; padding:14px 18px;
               border-radius:12px; border:1px solid var(--border-color-primary);
               background: var(--block-background-fill);}
.status-dot {width:16px; height:16px; border-radius:50%; flex-shrink:0;}
.status-label {font-size:1.25rem; font-weight:700;}
.status-hint {font-size:0.85rem; opacity:0.75;}
.state-idle .status-dot, .state-ended .status-dot {background:#9ca3af;}
.state-connecting .status-dot {background:#a78bfa; animation:pulse 1s infinite;}
.state-listening .status-dot {background:#22c55e; animation:pulse 1.4s infinite;}
.state-thinking .status-dot {background:#f59e0b; animation:pulse 0.7s infinite;}
.state-speaking .status-dot {background:#3b82f6; animation:pulse 1s infinite;}
@keyframes pulse {0% {box-shadow:0 0 0 0 rgba(128,128,128,0.6);}
                  70% {box-shadow:0 0 0 10px rgba(128,128,128,0);}
                  100% {box-shadow:0 0 0 0 rgba(128,128,128,0);}}
"""


def status_html(state: str) -> str:
    """The coloured status badge for one state."""
    label, hint = STATES.get(state, STATES["idle"])
    return (f'<div class="status-badge state-{state}"><div class="status-dot"></div>'
            f'<div><div class="status-label">{label}</div>'
            f'<div class="status-hint">{hint}</div></div></div>')


def orders_panel() -> str:
    """The Test Orders table. Built fresh on every page load, so the times are current."""
    now = now_ist()
    rows = []
    for order in build_orders(now).values():
        if order["status"] == "Out for Delivery":
            details = (f"{order['courier']} {order['tracking_number']}, expected "
                       f"{describe_time(order['expected_delivery'], now)}")
        elif order["status"] == "Delivered":
            details = (f"{order['courier']} {order['tracking_number']}, delivered "
                       f"{describe_time(order['delivered_at'], now)}")
        else:
            details = f"Ordered {describe_time(order['ordered_at'], now)}, can be cancelled"
        rows.append(f"| **{order['order_id']}** | {order['customer_name']} | {order['product']} "
                    f"| ₹{order['value']} | {order['status']} | {details} |")

    return "\n".join([
        "| Order ID | Customer | Product | Value | Status | Details |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        "**Try saying:** *\"Where is my order ORD-101?\"* · *\"I want to return ORD-102.\"* · "
        "*\"Please cancel ORD-103.\"* · *\"Check order ORD-999.\"* · "
        "*\"Can you book me a flight to Goa?\"*",
    ])


def transcript_markdown(timed_transcript: list[dict]) -> str:
    """The full call, in order, with the time of each line."""
    if not timed_transcript:
        return "_Nothing was said during this call._"
    return "\n\n".join(f"`{line['time']}` **{line['speaker']}:** {line['text']}"
                       for line in timed_transcript)
