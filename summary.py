"""
summary.py - Builds the structured JSON summary shown after the call ends.

A SEPARATE LLM call reads the finished transcript and fills in:
    customer_intent, order_id, resolution_status, call_summary
Facts that code already knows (which tools ran, what they decided, how long
the call was) are added by code, not by the LLM, so they are always correct.
"""

import json
import logging
import time

from agent import CallSession, ask_llm
from tools import normalize_order_id

log = logging.getLogger(__name__)

INTENTS = ["ORDER_TRACKING", "RETURN_REQUEST", "DAMAGED_PRODUCT", "CANCELLATION",
           "POLICY_QUESTION", "OUT_OF_SCOPE", "OTHER"]
STATUSES = ["RESOLVED", "PARTIALLY_RESOLVED", "UNRESOLVED"]

SUMMARY_PROMPT = f"""You analyse finished customer support calls for Aura Skincare.
Read the transcript and the actions the system took, then return ONLY a JSON object with these keys:
- "customer_intent": the customer's MAIN reason for calling, one of {INTENTS}
- "order_id": the main order discussed, like "ORD-101", or null if none was given
- "resolution_status": one of {STATUSES}.
  RESOLVED = the customer got a clear final answer or the action was completed. A request refused because of policy, with the reason explained, also counts as RESOLVED.
  PARTIALLY_RESOLVED = some questions were answered but something was left open.
  UNRESOLVED = the customer's need was not met (e.g. the order could not be found, or the call ended before an answer).
- "call_summary": 1-2 sentences in the past tense describing what the customer wanted and the outcome.
Use only facts from the transcript and actions. Do not invent anything."""


def _outcome(result: dict) -> str:
    """A short label for what a tool decided, e.g. 'RETURN_WINDOW_CLOSED'."""
    if "error" in result:
        return result["error"]
    if result.get("cancelled"):
        return "CANCELLED"
    if result.get("needs_confirmation"):
        return "AWAITING_CONFIRMATION"
    if "decision" in result:
        return result["decision"]
    if "order" in result:
        return f"STATUS: {result['order']['status']}"
    if "cash_on_delivery_allowed" in result:
        return "COD_ALLOWED" if result["cash_on_delivery_allowed"] else "COD_NOT_ALLOWED"
    return "OK"


def _actions(session: CallSession) -> list[dict]:
    """Every tool Aria used during the call, and what it decided (from code, not the LLM)."""
    actions = []
    for used in session.tool_log:
        try:
            arguments = json.loads(used["arguments"] or "{}")
        except (json.JSONDecodeError, TypeError):
            arguments = {}
        action = {"tool": used["tool"], "outcome": _outcome(used["result"])}
        if arguments.get("order_id"):
            action["order_id"] = normalize_order_id(arguments["order_id"])
        actions.append(action)
    return actions


def summarize_call(session: CallSession) -> dict:
    """Return the post-call JSON summary for one finished call."""
    actions = _actions(session)
    facts_from_code = {
        "actions_taken": actions,
        "call_duration_seconds": round(time.time() - session.started_at),
        "customer_turns": sum(1 for line in session.timed_transcript
                              if line["speaker"] == "Customer"),
    }

    if facts_from_code["customer_turns"] == 0:  # nothing to summarise, so no LLM call
        return {"customer_intent": "OTHER", "order_id": None,
                "resolution_status": "UNRESOLVED",
                "call_summary": "The call ended before the customer said anything.",
                **facts_from_code}

    transcript = "\n".join(f"{line['speaker']}: {line['text']}"
                           for line in session.timed_transcript)
    try:
        response = ask_llm(
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": f"TRANSCRIPT:\n{transcript}\n\n"
                                            f"ACTIONS TAKEN BY THE SYSTEM:\n{json.dumps(actions)}"},
            ],
            response_format={"type": "json_object"},  # forces valid JSON back
            max_completion_tokens=400,
        )
        data = json.loads(response.choices[0].message.content)
    except Exception:
        log.exception("Summary LLM call failed")
        data = {"call_summary": "The summary could not be generated automatically."}

    # Never trust the LLM's format blindly: keep only allowed values.
    intent = data.get("customer_intent")
    status = data.get("resolution_status")
    order_id = data.get("order_id")
    return {
        "customer_intent": intent if intent in INTENTS else "OTHER",
        "order_id": normalize_order_id(order_id) if order_id else None,
        "resolution_status": status if status in STATUSES else "UNRESOLVED",
        "call_summary": str(data.get("call_summary", "")).strip(),
        **facts_from_code,
    }
