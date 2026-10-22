"""
test_tools.py - Test the tools and policies WITHOUT voice or API keys.

Run:  python test_tools.py

Each check prints PASS or FAIL. At the end you see a total.
"""

import json
import sys
from datetime import timedelta

import policies
from orders import CANCELLED, DELIVERED, PROCESSING, OrderDatabase, now_ist
from tools import (cancel_order, check_return_eligibility, get_order_details,
                   normalize_order_id, run_tool)

# Windows terminals sometimes cannot print symbols like ₹ or —. This fixes that.
sys.stdout.reconfigure(encoding="utf-8")

passed = 0
failed = 0


def check(name: str, condition: bool, result=None):
    """Print PASS/FAIL for one test, and show the tool result."""
    global passed, failed
    if condition:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}")
    if result is not None:
        print("      " + json.dumps(result, ensure_ascii=False))


print("\n=== get_order_details ===")
db = OrderDatabase()

r = get_order_details(db, "ORD-101")
check("ORD-101 is Out for Delivery, expected by 6 PM",
      r["found"] and r["order"]["status"] == "Out for Delivery"
      and "6 PM" in r["order"]["expected_delivery"], r)

r = get_order_details(db, "ORD-102")
check("ORD-102 was delivered 14 days ago",
      r["found"] and r["order"]["delivered"].startswith("14 days ago"), r)

r = get_order_details(db, "ORD-103")
check("ORD-103 is Processing, ordered 3 hours ago",
      r["found"] and r["order"]["status"] == PROCESSING
      and r["order"]["ordered"] == "3 hours ago", r)

r = get_order_details(db, "ORD-999")
check("ORD-999 -> ORDER_NOT_FOUND", r.get("error") == "ORDER_NOT_FOUND", r)

r = get_order_details(db, "")
check("Empty order ID -> MISSING_ORDER_ID", r.get("error") == "MISSING_ORDER_ID", r)

r = get_order_details(db, "my order")
check("'my order' (no digits) -> INVALID_ORDER_ID", r.get("error") == "INVALID_ORDER_ID", r)


print("\n=== Order ID clean-up (speech-to-text variations) ===")
for spoken in ["ORD-101", "ord 101", "ORD101", "order number 101",
               "order one zero one", "101"]:
    check(f"'{spoken}' -> ORD-101", normalize_order_id(spoken) == "ORD-101")


print("\n=== check_return_eligibility ===")
r = check_return_eligibility(db, "ORD-102")
check("ORD-102 return refused (14 days > 7-day window)",
      r["eligible"] is False and r["decision"] == "RETURN_WINDOW_CLOSED", r)

r = check_return_eligibility(db, "ORD-102", is_damaged=True)
check("ORD-102 damage claim refused (14 days > 48 hours)",
      r["eligible"] is False and r["decision"] == "DAMAGE_REPORT_WINDOW_CLOSED", r)

r = check_return_eligibility(db, "ORD-101")
check("ORD-101 cannot be returned (not delivered yet)",
      r["eligible"] is False and r["decision"] == "NOT_DELIVERED_YET", r)

r = check_return_eligibility(db, "ORD-999")
check("ORD-999 return -> ORDER_NOT_FOUND", r.get("error") == "ORDER_NOT_FOUND", r)


print("\n=== cancel_order ===")
r = cancel_order(db, "ORD-101")
check("ORD-101 cancel refused (Out for Delivery)",
      r["cancelled"] is False and r["decision"] == "ALREADY_SHIPPED", r)

r = cancel_order(db, "ORD-102")
check("ORD-102 cancel refused (Delivered)",
      r["cancelled"] is False and r["decision"] == "ALREADY_DELIVERED", r)

r = cancel_order(db, "ORD-103")
check("ORD-103 cancel succeeds (Processing)", r["cancelled"] is True, r)

r = get_order_details(db, "ORD-103")
check("ORD-103 now shows Cancelled", r["order"]["status"] == CANCELLED, r)

r = cancel_order(db, "ORD-103")
check("ORD-103 second cancel -> ALREADY_CANCELLED",
      r["cancelled"] is False and r["decision"] == "ALREADY_CANCELLED", r)

fresh_db = OrderDatabase()
check("A NEW call starts with ORD-103 back in Processing",
      fresh_db.get("ORD-103")["status"] == PROCESSING)


print("\n=== run_tool (how the LLM calls tools) ===")
r = run_tool(fresh_db, "get_order_details", '{"order_id": "ord 101"}')
check("JSON string arguments work", r.get("found") is True)

r = run_tool(fresh_db, "get_order_details", '{"order_id": "ORD-101", "colour": "red"}')
check("Unexpected argument -> BAD_ARGUMENTS (no crash)", r.get("error") == "BAD_ARGUMENTS", r)

r = run_tool(fresh_db, "get_order_details", "this is not json")
check("Broken JSON -> MISSING_ORDER_ID (no crash)", r.get("error") == "MISSING_ORDER_ID", r)

r = run_tool(fresh_db, "book_flight", "{}")
check("Unknown tool -> UNKNOWN_TOOL (no crash)", r.get("error") == "UNKNOWN_TOOL", r)


print("\n=== Policy rules (direct checks) ===")
check("Rs.300 order -> Rs.50 shipping", policies.shipping_fee(300) == 50)
check("Rs.499 order -> free shipping", policies.shipping_fee(499) == 0)
check("Rs.699 order -> free shipping", policies.shipping_fee(699) == 0)
check("Rs.2,500 order -> COD allowed", policies.cod_available(2500) is True)
check("Rs.2,501 order -> COD not allowed", policies.cod_available(2501) is False)

now = now_ist()
two_days_ago = now - timedelta(days=2)
one_day_ago = now - timedelta(days=1)

r = policies.return_decision(DELIVERED, two_days_ago, now, product_opened=False)
check("Delivered 2 days ago + unopened -> return OK", r["eligible"] is True, r)

r = policies.return_decision(DELIVERED, two_days_ago, now, product_opened=True)
check("Delivered 2 days ago + opened -> refused", r["eligible"] is False, r)

r = policies.return_decision(DELIVERED, two_days_ago, now)
check("Delivered 2 days ago + condition unknown -> ask customer",
      r["eligible"] is None and r["decision"] == "NEED_PRODUCT_CONDITION", r)

r = policies.return_decision(DELIVERED, one_day_ago, now, is_damaged=True)
check("Damaged, reported after 24 hours -> replacement OK", r["eligible"] is True, r)

r = policies.return_decision(DELIVERED, now - timedelta(hours=50), now, is_damaged=True)
check("Damaged, reported after 50 hours -> refused", r["eligible"] is False, r)


print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
