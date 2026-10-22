"""
orders.py - The mock order database for Aura Skincare.

Dates are NOT hard-coded. They are calculated from the current time
(India Standard Time) whenever a new database is created, so
"delivered 14 days ago" and "expected by 6 PM today" stay true no matter
when the app is tested.

Every call gets its OWN copy of the orders (see OrderDatabase). That way, if one
evaluator cancels ORD-103, the next evaluator still finds it in "Processing".
"""

from datetime import datetime, timedelta, timezone

# India Standard Time is UTC+5:30. We use a fixed offset instead of a timezone
# name so it works the same on Windows, Linux and Hugging Face Spaces.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# The order statuses used in this project.
PROCESSING = "Processing"
SHIPPED = "Shipped"
OUT_FOR_DELIVERY = "Out for Delivery"
DELIVERED = "Delivered"
CANCELLED = "Cancelled"


def now_ist() -> datetime:
    """Return the current date and time in India."""
    return datetime.now(IST)


def _next_6pm(now: datetime) -> datetime:
    """6 PM today. If it is already past 6 PM, the courier's promise becomes 6 PM tomorrow."""
    six_pm = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if now >= six_pm:
        six_pm += timedelta(days=1)
    return six_pm


def build_orders(now: datetime | None = None) -> dict:
    """
    Build a fresh copy of the 3 mock orders from the assignment (Section 4).
    Fields the assignment does not give us (e.g. ORD-101's order date) are None,
    so the agent can never "invent" them.
    """
    now = now or now_ist()
    return {
        "ORD-101": {
            "order_id": "ORD-101",
            "customer_name": "Priya Sharma",
            "product": "Vitamin C Serum (30ml)",
            "value": 699,
            "status": OUT_FOR_DELIVERY,
            "courier": "BlueDart",
            "tracking_number": "BD-982103",
            "ordered_at": None,
            "delivered_at": None,
            "expected_delivery": _next_6pm(now),
        },
        "ORD-102": {
            "order_id": "ORD-102",
            "customer_name": "Rahul Verma",
            "product": "Hydrating Sunscreen SPF 50",
            "value": 499,
            "status": DELIVERED,
            "courier": "Delhivery",
            "tracking_number": "DL-441029",
            "ordered_at": None,
            "delivered_at": now - timedelta(days=14),
            "expected_delivery": None,
        },
        "ORD-103": {
            "order_id": "ORD-103",
            "customer_name": "Ananya Patel",
            "product": "Green Tea Face Wash + Toner",
            "value": 850,
            "status": PROCESSING,
            "courier": None,  # not shipped yet
            "tracking_number": None,
            "ordered_at": now - timedelta(hours=3),
            "delivered_at": None,
            "expected_delivery": None,
        },
    }


class OrderDatabase:
    """A private copy of the orders for ONE call."""

    def __init__(self, now: datetime | None = None):
        self.orders = build_orders(now)

    def get(self, order_id: str) -> dict | None:
        """Return the order, or None if it does not exist."""
        return self.orders.get(order_id)

    def set_status(self, order_id: str, status: str) -> None:
        self.orders[order_id]["status"] = status
