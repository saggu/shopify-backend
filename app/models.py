"""
Pydantic request models for the orders and products API.
These models validate and deserialise the JSON bodies sent by the frontend.
"""

from typing import Optional

from pydantic import BaseModel, EmailStr


class Address(BaseModel):
    address1: str
    address2: Optional[str] = ""
    city: str
    province: str
    country: str
    zip: str


class Customer(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    phone: Optional[str] = None
    shipping_address: Address
    billing_address: Address


class LineItem(BaseModel):
    # Only variant_id and quantity are sent to Shopify — all other display
    # fields (title, SKU, price) are returned by draftOrderCalculate.
    variant_id: int
    quantity: int


class ShippingOption(BaseModel):
    # handle is an opaque token from Shopify's availableShippingRates —
    # it identifies the rate selected by the user during order review.
    handle: str
    title: str
    price: str


class CalculateRequest(BaseModel):
    customer: Customer
    line_items: list[LineItem]
    discount_code: Optional[str] = None


class SubmitRequest(BaseModel):
    customer: Customer
    line_items: list[LineItem]
    discount_code: Optional[str] = None
    shipping_line: ShippingOption
