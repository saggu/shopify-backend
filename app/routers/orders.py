"""
Order calculation and submission endpoints.

Order creation follows a two-step Shopify pattern:
  1. draftOrderCreate  — persists the order as a draft
  2. draftOrderComplete — converts the draft into a real order

A separate draftOrderCalculate mutation is used for the pricing preview step,
which returns full pricing without writing anything to Shopify.

Discount codes are resolved before each calculate or submit call so that
expired codes are caught even if the user sits on the review screen for a while.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app import shopify
from app.models import CalculateRequest, Customer, LineItem, ShippingOption, SubmitRequest
from app.queries import load
from app.shopify import ShopifyError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orders", tags=["orders"])

# Loaded once at startup — avoids repeated disk reads per request.
_CALCULATE_MUTATION = load("calculate_draft_order")
_CREATE_MUTATION = load("create_draft_order")
_COMPLETE_MUTATION = load("complete_draft_order")
_DISCOUNT_QUERY = load("discount_by_code")

# Sentinel returned by _resolve_discount for free shipping codes.
# Handled separately from order-level discounts because it zeroes out the
# shipping line price rather than applying an appliedDiscount to line items.
_FREE_SHIPPING: dict[str, str] = {"type": "free_shipping"}


async def _resolve_discount(code: str) -> dict[str, Any] | None:
    """
    Look up a Shopify discount code and return the appropriate discount input.

    Returns:
        - A DraftOrderAppliedDiscountInput dict for percentage or fixed amount codes.
        - _FREE_SHIPPING sentinel for free shipping codes.
        - None if the code does not exist, is expired, or the lookup fails.
    """
    try:
        data = await shopify.query(_DISCOUNT_QUERY, {"code": code})
        node = data.get("codeDiscountNodeByCode")

        if not node:
            return None

        discount: dict[str, Any] = node.get("codeDiscount") or {}

        if discount.get("status") != "ACTIVE":
            return None

        # DiscountCodeFreeShipping has no customerGets — identify it by absence.
        if "customerGets" not in discount:
            return _FREE_SHIPPING

        value_obj: dict[str, Any] = (discount.get("customerGets") or {}).get("value") or {}

        if "percentage" in value_obj:
            # Shopify returns percentage as a decimal (0.1 = 10%).
            # DraftOrderAppliedDiscountInput.value expects 0–100.
            return {
                "valueType": "PERCENTAGE",
                "value": round(value_obj["percentage"] * 100, 4),
                "title": code,
                "description": code,
            }

        if "amount" in value_obj:
            return {
                "valueType": "FIXED_AMOUNT",
                "value": float(value_obj["amount"]["amount"]),
                "title": code,
                "description": code,
            }

    except Exception as exc:
        logger.warning("Discount lookup failed for %r: %s", code, exc)

    return None


def _build_draft_input(
    customer: Customer,
    line_items: list[LineItem],
    applied_discount: dict[str, Any] | None = None,
    shipping_line: ShippingOption | None = None,
    free_shipping: bool = False,
) -> dict[str, Any]:
    """
    Build the DraftOrderInput dict used by both draftOrderCalculate and draftOrderCreate.

    Args:
        customer: Customer details including shipping and billing addresses.
        line_items: Variants and quantities to include in the order.
        applied_discount: A resolved DraftOrderAppliedDiscountInput, or None.
        shipping_line: The shipping option selected by the user.
        free_shipping: When True, overrides the shipping line price to zero.
    """
    inp: dict[str, Any] = {
        # Shopify requires variant IDs in Global ID format.
        "lineItems": [
            {
                "variantId": f"gid://shopify/ProductVariant/{li.variant_id}",
                "quantity": li.quantity,
            }
            for li in line_items
        ],
        "email": customer.email,
        "shippingAddress": {
            "firstName": customer.first_name,
            "lastName": customer.last_name,
            "address1": customer.shipping_address.address1,
            "address2": customer.shipping_address.address2 or "",
            "city": customer.shipping_address.city,
            "provinceCode": customer.shipping_address.province,
            "countryCode": customer.shipping_address.country,
            "zip": customer.shipping_address.zip,
            **({"phone": customer.phone} if customer.phone else {}),
        },
        "billingAddress": {
            "firstName": customer.first_name,
            "lastName": customer.last_name,
            "address1": customer.billing_address.address1,
            "address2": customer.billing_address.address2 or "",
            "city": customer.billing_address.city,
            "provinceCode": customer.billing_address.province,
            "countryCode": customer.billing_address.country,
            "zip": customer.billing_address.zip,
        },
    }

    if applied_discount:
        inp["appliedDiscount"] = applied_discount

    if shipping_line:
        inp["shippingLine"] = {
            "title": shipping_line.title,
            # Free shipping codes zero out the price here rather than via appliedDiscount,
            # because appliedDiscount only applies to line items, not the shipping line.
            "price": "0.00" if free_shipping else shipping_line.price,
        }

    return inp


def _format_calculation(calc: dict[str, Any]) -> dict[str, Any]:
    """Reshape the draftOrderCalculate response into the format expected by the frontend."""
    discount_amount = float(
        calc.get("totalDiscountsSet", {}).get("shopMoney", {}).get("amount", 0)
    )
    return {
        "line_items": [
            {
                "variant_id": int(li["variant"]["id"].split("/")[-1]) if li.get("variant") else None,
                "product_title": li["title"],
                "variant_title": li.get("variantTitle") or li["title"],
                "price": li["originalUnitPrice"]["amount"],
                "quantity": li["quantity"],
                "line_price": li["discountedTotal"]["amount"],
            }
            for li in calc["lineItems"]
        ],
        "subtotal_price": calc["subtotalPrice"],
        "total_discounts": str(discount_amount),
        "total_tax": calc["totalTax"],
        "tax_lines": [
            {
                "title": t["title"],
                "rate": t["ratePercentage"] / 100,
                "price": t["price"],
            }
            for t in calc.get("taxLines", [])
        ],
        # discount_applied is True only when Shopify confirms the discount was
        # applied AND the resulting amount is non-zero.
        "discount_applied": bool(calc.get("appliedDiscount")) and discount_amount > 0,
        "shipping_options": [
            {
                "handle": r["handle"],
                "title": r["title"],
                "price": r["price"]["amount"],
            }
            for r in calc.get("availableShippingRates") or []
        ],
    }


@router.post("/calculate")
async def calculate_order(request: CalculateRequest) -> dict[str, Any]:
    """
    Return a full pricing preview for the order without creating anything in Shopify.
    If a discount code is provided, it is resolved first and included in the calculation.
    """
    try:
        applied_discount: dict[str, Any] | None = None
        if request.discount_code:
            applied_discount = await _resolve_discount(request.discount_code)

        is_free_shipping = applied_discount == _FREE_SHIPPING
        inp = _build_draft_input(
            customer=request.customer,
            line_items=request.line_items,
            # Free shipping is handled via the shipping line price, not appliedDiscount.
            applied_discount=None if is_free_shipping else applied_discount,
        )

        data = await shopify.query(_CALCULATE_MUTATION, {"input": inp})
        result = data["draftOrderCalculate"]

        if result["userErrors"]:
            raise ShopifyError(str(result["userErrors"]))

        formatted = _format_calculation(result["calculatedDraftOrder"])
        formatted["free_shipping"] = is_free_shipping
        return formatted

    except ShopifyError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.post("")
async def submit_order(request: SubmitRequest) -> dict[str, Any]:
    """
    Create and complete a Shopify order.

    Runs two mutations sequentially:
      1. draftOrderCreate — saves the order as a draft, returns a draft GID
      2. draftOrderComplete — converts the draft to a real order, returns the order name

    The discount code is re-resolved here (not trusted from the frontend) to
    guard against codes that expired between the review and confirmation steps.
    """
    try:
        applied_discount: dict[str, Any] | None = None
        if request.discount_code:
            applied_discount = await _resolve_discount(request.discount_code)

        is_free_shipping = applied_discount == _FREE_SHIPPING
        inp = _build_draft_input(
            customer=request.customer,
            line_items=request.line_items,
            applied_discount=None if is_free_shipping else applied_discount,
            shipping_line=request.shipping_line,
            free_shipping=is_free_shipping,
        )

        # Step 1 — create the draft
        create_data = await shopify.query(_CREATE_MUTATION, {"input": inp})
        create_result = create_data["draftOrderCreate"]

        if create_result["userErrors"]:
            raise ShopifyError(str(create_result["userErrors"]))

        draft_id: str = create_result["draftOrder"]["id"]

        # Step 2 — complete the draft into a real order
        complete_data = await shopify.query(_COMPLETE_MUTATION, {"id": draft_id})
        complete_result = complete_data["draftOrderComplete"]

        if complete_result["userErrors"]:
            raise ShopifyError(str(complete_result["userErrors"]))

        draft = complete_result["draftOrder"]
        return {
            "order_id": draft["order"]["id"] if draft.get("order") else None,
            "name": draft["order"]["name"] if draft.get("order") else draft["name"],
        }

    except ShopifyError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
