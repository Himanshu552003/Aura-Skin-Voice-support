"""
policies.py - Aura Skincare's brand policies, written as Python rules.

This is the "guardrail" layer. The LLM never decides whether a return or a
cancellation is allowed. It calls a tool, the tool asks these functions, and
the LLM only explains the answer to the customer.

All policy numbers live here in one place (Section 3 of the assignment).
"""

from datetime import datetime

from orders import CANCELLED, DELIVERED, OUT_FOR_DELIVERY, PROCESSING, SHIPPED

# ---- Policy numbers ---------------------------------------------------------
FREE_SHIPPING_MIN = 499       # orders of Rs.499 or more ship free (see note below)
SHIPPING_FEE = 50             # fee for orders below Rs.499
DELIVERY_TIME = "3-5 business days"
RETURN_WINDOW_DAYS = 7        # returns: within 7 days of delivery, unopened & unused
DAMAGE_REPORT_HOURS = 48      # damaged/defective: report within 48 hours, with photos
COD_LIMIT = 2500              # Cash on Delivery for orders up to Rs.2,500

# Note: the brief says "free above Rs.499" and "Rs.50 fee below Rs.499", which
# leaves exactly Rs.499 undefined. We treat Rs.499 as free (it is not "below").

# The brand facts Aria is allowed to talk about (Section 3 of the assignment).
# Built from the numbers above, so the prompt and the code can never disagree.
BRAND_INFO = f"""Brand: Aura Skincare is a premium organic Indian skincare brand focused on simple, effective skincare products made with thoughtfully selected ingredients.
Shipping: Free delivery on orders of {FREE_SHIPPING_MIN} rupees or more. Orders below {FREE_SHIPPING_MIN} rupees have a {SHIPPING_FEE} rupee shipping fee. Standard delivery takes {DELIVERY_TIME}.
Returns and refunds: Returns are accepted within {RETURN_WINDOW_DAYS} days of delivery for unopened, unused products in original packaging. Damaged or defective products must be reported within {DAMAGE_REPORT_HOURS} hours of delivery, with photos, for a replacement.
Cancellation: Orders can be cancelled only while their status is Processing. Once an order is Shipped or Out for Delivery it cannot be cancelled, but the customer may refuse the delivery at the doorstep.
Cash on Delivery: COD is available for orders up to {COD_LIMIT} rupees. Customers can pay by cash or UPI at the doorstep."""


# ---- Shipping & COD ---------------------------------------------------------
def shipping_fee(order_value: int) -> int:
    """Return the shipping fee in rupees for an order of this value."""
    return 0 if order_value >= FREE_SHIPPING_MIN else SHIPPING_FEE


def cod_available(order_value: int) -> bool:
    """Cash on Delivery is allowed for orders up to Rs.2,500 (inclusive)."""
    return order_value <= COD_LIMIT


# ---- Cancellation -----------------------------------------------------------
def cancellation_decision(status: str) -> dict:
    """Orders can be cancelled ONLY while they are Processing."""
    if status == PROCESSING:
        return {"allowed": True, "decision": "CANCELLATION_ALLOWED"}
    if status == CANCELLED:
        return {"allowed": False, "decision": "ALREADY_CANCELLED",
                "reason": "This order is already cancelled."}
    if status in (SHIPPED, OUT_FOR_DELIVERY):
        return {"allowed": False, "decision": "ALREADY_SHIPPED",
                "reason": f"The order is '{status}', so it can no longer be cancelled. "
                          "The customer may refuse the delivery at the doorstep.",
                "note_for_agent": "Refusing at the doorstep is the ONLY option to offer. The "
                                  "policy says nothing about returns or refunds after refusing, "
                                  "so do not promise any."}
    if status == DELIVERED:
        return {"allowed": False, "decision": "ALREADY_DELIVERED",
                "reason": "The order is already delivered, so it cannot be cancelled. "
                          "The return policy applies instead."}
    return {"allowed": False, "decision": "NOT_CANCELLABLE",
            "reason": f"Orders with status '{status}' cannot be cancelled."}


# ---- Returns & damaged products ---------------------------------------------
def return_decision(status: str, delivered_at: datetime | None, now: datetime,
                    product_opened: bool | None = None,
                    is_damaged: bool = False) -> dict:
    """
    Decide if a delivered order can be returned (or replaced, if damaged).

    eligible is True, False, or None. None means "we need more information"
    (the customer has not told us whether the product is unopened).
    """
    if status != DELIVERED or delivered_at is None:
        return {"eligible": False, "decision": "NOT_DELIVERED_YET",
                "reason": f"Returns are only possible after delivery. Current status: '{status}'."}

    hours_since_delivery = (now - delivered_at).total_seconds() / 3600

    # Damaged or defective products: replacement if reported within 48 hours.
    if is_damaged:
        if hours_since_delivery <= DAMAGE_REPORT_HOURS:
            return {"eligible": True, "decision": "REPLACEMENT_ELIGIBLE",
                    "reason": "Reported within 48 hours of delivery. The customer must "
                              "share photos of the damage to get a replacement."}
        return {"eligible": False, "decision": "DAMAGE_REPORT_WINDOW_CLOSED",
                "reason": "Damaged or defective products must be reported within "
                          "48 hours of delivery. That window has passed."}

    # Normal returns: within 7 days, unopened, unused, original packaging.
    if hours_since_delivery > RETURN_WINDOW_DAYS * 24:
        return {"eligible": False, "decision": "RETURN_WINDOW_CLOSED",
                "reason": "Returns are accepted only within 7 days of delivery. "
                          "That window has passed."}
    if product_opened is True:
        return {"eligible": False, "decision": "PRODUCT_OPENED",
                "reason": "Only unopened, unused products in original packaging can be returned."}
    if product_opened is None:
        return {"eligible": None, "decision": "NEED_PRODUCT_CONDITION",
                "reason": "The order is within the 7-day window. Ask the customer if the "
                          "product is unopened, unused and in its original packaging."}
    return {"eligible": True, "decision": "RETURN_ELIGIBLE",
            "reason": "Within 7 days of delivery and the product is unopened."}
