"""
tools.py - The tools (functions) the AI agent can call.

    get_order_details(order_id)         -> where is my order?
    check_return_eligibility(order_id)  -> can I return it?
    cancel_order(order_id)              -> please cancel it
    check_return_policy(days_since_delivery)  -> "I bought it 20 days ago, can I return it?" (no order ID)
    check_cod_and_shipping(order_value)       -> "Is COD / free delivery available for Rs.3,000?"

Each order tool:
  1. cleans up the order ID (speech-to-text may give "ord 101" or "order one zero one"),
  2. looks the order up in this call's OrderDatabase,
  3. asks policies.py for the decision,
  4. returns a small dict that the LLM turns into a spoken reply.

Tools never crash. Every problem (missing ID, unknown ID, bad arguments) comes
back as a clear "error" so the agent can ask the customer to repeat or verify.
"""

import json
import logging
import re
from datetime import datetime, timedelta

import policies
from orders import CANCELLED, DELIVERED, OrderDatabase, now_ist

log = logging.getLogger(__name__)

# Speech-to-text sometimes writes digits as words, e.g. "order one zero one".
_DIGIT_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


# ---- Helpers ----------------------------------------------------------------
def normalize_order_id(raw) -> str | None:
    """
    Turn whatever the customer said into the format "ORD-<digits>".
    "ord 101", "ORD101", "order number one zero one", "101" -> "ORD-101".
    Returns None if there are no digits at all.
    """
    if raw is None:
        return None
    text = str(raw).lower()
    for word, digit in _DIGIT_WORDS.items():
        text = re.sub(rf"\b{word}\b", digit, text)
    digits = "".join(re.findall(r"\d", text))
    return f"ORD-{digits}" if digits else None


def _find_order(db: OrderDatabase, order_id) -> tuple[dict | None, dict | None]:
    """Return (order, None) if found, or (None, error_dict) if not."""
    if order_id is None or not str(order_id).strip():
        return None, {"found": False, "error": "MISSING_ORDER_ID",
                      "message": "No order ID was given. Ask the customer for their order ID "
                                 "(it looks like ORD-101)."}

    clean_id = normalize_order_id(order_id)
    if clean_id is None:
        return None, {"found": False, "error": "INVALID_ORDER_ID", "heard": str(order_id),
                      "message": "That does not look like an order ID. Ask the customer to "
                                 "repeat it (it looks like ORD-101)."}

    order = db.get(clean_id)
    if order is None:
        return None, {"found": False, "error": "ORDER_NOT_FOUND", "order_id": clean_id,
                      "message": f"No order with ID {clean_id} exists. Ask the customer to "
                                 "repeat or verify the ID. Do not guess any details."}
    return order, None


def _clock(dt: datetime) -> str:
    """18:00 -> '6 PM'."""
    return dt.strftime("%I %p").lstrip("0")


def describe_time(dt: datetime | None, now: datetime) -> str | None:
    """Turn a date into words the agent can say, e.g. '14 days ago (on 8 October 2026)'."""
    if dt is None:
        return None
    if dt > now:  # a future time, e.g. expected delivery
        if dt.date() == now.date():
            day = "today"
        elif dt.date() == (now + timedelta(days=1)).date():
            day = "tomorrow"
        else:
            day = f"on {dt.day} {dt.strftime('%B')}"
        return f"by {_clock(dt)} {day}"
    hours_ago = int((now - dt).total_seconds() // 3600)
    if hours_ago < 1:
        return "less than an hour ago"
    if hours_ago < 24:
        return f"{hours_ago} hours ago"
    days_ago = (now - dt).days
    return f"{days_ago} days ago (on {dt.day} {dt.strftime('%B %Y')})"


# ---- The 3 tools ------------------------------------------------------------
def get_order_details(db: OrderDatabase, order_id=None) -> dict:
    """Look up an order and return its status and delivery details."""
    order, error = _find_order(db, order_id)
    if error:
        return error

    now = now_ist()
    fee = policies.shipping_fee(order["value"])
    details = {
        "order_id": order["order_id"],
        "customer_name": order["customer_name"],
        "product": order["product"],
        "order_value": f"Rs.{order['value']}",
        "status": order["status"],
        "courier": order["courier"],
        "tracking_number": order["tracking_number"],
        "ordered": describe_time(order["ordered_at"], now),
        "delivered": describe_time(order["delivered_at"], now),
        "expected_delivery": describe_time(order["expected_delivery"], now),
        "shipping_fee": "Free" if fee == 0 else f"Rs.{fee}",
        "cash_on_delivery_allowed": policies.cod_available(order["value"]),
    }
    # Leave out empty fields so the LLM is not tempted to fill them in.
    details = {key: value for key, value in details.items() if value is not None}
    return {"found": True, "order": details}


def check_return_eligibility(db: OrderDatabase, order_id=None,
                             product_opened: bool | None = None,
                             is_damaged: bool = False) -> dict:
    """Check whether an order can be returned (or replaced, if damaged)."""
    order, error = _find_order(db, order_id)
    if error:
        return error

    now = now_ist()
    decision = policies.return_decision(
        status=order["status"],
        delivered_at=order["delivered_at"],
        now=now,
        product_opened=product_opened,
        is_damaged=is_damaged,
    )
    result = {"found": True, "order_id": order["order_id"], "product": order["product"],
              "status": order["status"], **decision}
    if order["delivered_at"]:
        result["delivered"] = describe_time(order["delivered_at"], now)
    return result


def cancel_order(db: OrderDatabase, order_id=None, confirmed: bool = False) -> dict:
    """
    Cancel an order, but ONLY if the policy allows it AND the customer confirmed.

    Cancelling can't be undone, so this is a 2-step process enforced in code:
      1st call -> never cancels. Says "can be cancelled, ask the customer to confirm".
      2nd call with confirmed=True -> actually cancels.
    Even if the LLM sends confirmed=True straight away, the code still asks first.
    """
    order, error = _find_order(db, order_id)
    if error:
        return error

    decision = policies.cancellation_decision(order["status"])
    order_id = order["order_id"]
    if decision["allowed"] and not (confirmed and order_id in db.awaiting_cancel_confirmation):
        db.awaiting_cancel_confirmation.add(order_id)
        return {"found": True, "order_id": order_id, "product": order["product"],
                "cancelled": False, "can_be_cancelled": True, "needs_confirmation": True,
                "message": "This order CAN be cancelled but has NOT been cancelled yet. Ask "
                           "the customer to confirm. If they say yes, call cancel_order "
                           "again with confirmed=true."}
    if decision["allowed"]:
        db.awaiting_cancel_confirmation.discard(order_id)
        db.set_status(order_id, CANCELLED)
        return {"found": True, "order_id": order["order_id"], "product": order["product"],
                "cancelled": True, "new_status": CANCELLED,
                "message": "The order has been cancelled successfully."}
    return {"found": True, "order_id": order["order_id"], "product": order["product"],
            "cancelled": False, "status": order["status"], **decision}


# ---- 2 extra tools for questions WITHOUT an order ID -------------------------
def check_return_policy(db: OrderDatabase, days_since_delivery=None,
                        product_opened: bool | None = None,
                        is_damaged: bool = False) -> dict:
    """
    Return check when the customer gives no order ID, only facts, e.g.
    "I bought this 20 days ago and opened it. Can I return it?"
    The same policy code decides, so the LLM never has to.
    """
    try:
        days = float(days_since_delivery)
    except (TypeError, ValueError):
        return {"error": "MISSING_DAYS",
                "message": "Ask the customer how many days ago the order was delivered, "
                           "or ask for their order ID."}
    if days < 0:
        return {"error": "INVALID_DAYS", "message": "Days since delivery cannot be negative."}

    now = now_ist()
    decision = policies.return_decision(
        status=DELIVERED,
        delivered_at=now - timedelta(days=days),
        now=now,
        product_opened=product_opened,
        is_damaged=is_damaged,
    )
    return {"days_since_delivery": days, **decision}


def check_cod_and_shipping(db: OrderDatabase, order_value=None) -> dict:
    """Shipping fee and Cash on Delivery check for any order amount (in rupees)."""
    try:
        value = float(str(order_value).replace(",", "").replace("₹", "").replace("Rs.", ""))
    except (TypeError, ValueError):
        return {"error": "MISSING_AMOUNT",
                "message": "Ask the customer for the order amount in rupees."}
    fee = policies.shipping_fee(value)
    result = {
        "order_value": f"Rs.{value:g}",
        "shipping_fee": "Free" if fee == 0 else f"Rs.{fee}",
        "cash_on_delivery_allowed": policies.cod_available(value),
        "cod_limit": f"Rs.{policies.COD_LIMIT}",
        "delivery_time": policies.DELIVERY_TIME,
    }
    if not result["cash_on_delivery_allowed"]:
        result["note_for_agent"] = ("We have no information about other payment methods, "
                                    "so do not name any. If asked, say you don't have that "
                                    "information.")
    return result


# ---- Tool descriptions for the LLM ------------------------------------------
# This is the JSON format Groq (OpenAI-compatible) uses for tool calling.
# The "description" text is what the LLM reads to decide WHEN to call a tool.
_ORDER_ID_PARAM = {
    "type": "string",
    "description": "The order ID the customer said, e.g. 'ORD-101'. Pass exactly what "
                   "the customer said; it is cleaned up automatically.",
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_order_details",
            "description": "Get the live status, courier, tracking number and delivery "
                           "dates of an order. Use whenever the customer asks about a "
                           "specific order.",
            "parameters": {"type": "object", "properties": {"order_id": _ORDER_ID_PARAM},
                           "required": ["order_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_return_eligibility",
            "description": "Check if an order can be returned, or replaced if it arrived "
                           "damaged or defective. Always use this before telling the "
                           "customer whether a return is possible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": _ORDER_ID_PARAM,
                    "product_opened": {
                        "type": "boolean",
                        "description": "True if the customer said the product is opened "
                                       "or used, False if unopened. Leave out if unknown.",
                    },
                    "is_damaged": {
                        "type": "boolean",
                        "description": "True if the customer says the product arrived "
                                       "damaged or defective.",
                    },
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": "Cancel an order. Call it when the customer wants to cancel or "
                           "asks if they can cancel. The tool checks the policy and, if "
                           "allowed, asks you to get the customer's confirmation first. "
                           "After the customer says yes, call it again with confirmed=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": _ORDER_ID_PARAM,
                    "confirmed": {
                        "type": "boolean",
                        "description": "True only after the customer said yes to your "
                                       "question about cancelling this order.",
                    },
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_return_policy",
            "description": "Check if a return or damage replacement is allowed when the "
                           "customer has NOT given an order ID but has said how long ago "
                           "the product was delivered.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_since_delivery": {
                        "type": "number",
                        "description": "How many days ago the product was delivered or bought.",
                    },
                    "product_opened": {
                        "type": "boolean",
                        "description": "True if opened or used, False if unopened. Leave out if unknown.",
                    },
                    "is_damaged": {
                        "type": "boolean",
                        "description": "True if the product arrived damaged or defective.",
                    },
                },
                "required": ["days_since_delivery"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_cod_and_shipping",
            "description": "Get the shipping fee and whether Cash on Delivery is allowed "
                           "for a given order amount. Use whenever the customer mentions "
                           "a specific amount.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_value": {"type": "number", "description": "Order amount in rupees."},
                },
                "required": ["order_value"],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "get_order_details": get_order_details,
    "check_return_eligibility": check_return_eligibility,
    "cancel_order": cancel_order,
    "check_return_policy": check_return_policy,
    "check_cod_and_shipping": check_cod_and_shipping,
}


def run_tool(db: OrderDatabase, name: str, arguments) -> dict:
    """
    Run a tool the LLM asked for. `arguments` can be a JSON string (what the LLM
    sends) or a dict. This function never raises: errors come back as dicts.
    """
    func = TOOL_FUNCTIONS.get(name)
    if func is None:
        return {"error": "UNKNOWN_TOOL", "message": f"There is no tool called '{name}'."}

    try:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except (json.JSONDecodeError, TypeError, ValueError):
        args = {}

    try:
        return func(db, **args)
    except TypeError:  # the LLM sent an argument the tool does not accept
        log.warning("Bad arguments for %s: %r", name, args)
        return {"error": "BAD_ARGUMENTS", "message": "The tool was called with wrong arguments."}
    except Exception:  # last safety net: never crash the call
        log.exception("Tool %s failed", name)
        return {"error": "TOOL_FAILED", "message": "Something went wrong while checking. "
                                                    "Apologise and ask the customer to try again."}
