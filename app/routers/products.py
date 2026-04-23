"""
Product search endpoint.

Runs two GraphQL queries in parallel against Shopify:
  - SearchProducts: searches by product title and product type (product-level fields)
  - SearchVariants: searches by SKU (variant-level field)

Results from both queries are merged and deduplicated by variant ID before
being returned to the frontend.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app import shopify
from app.queries import load
from app.shopify import ShopifyError

router = APIRouter(prefix="/products", tags=["products"])

# Loaded once at startup — avoids repeated disk reads per request.
_SEARCH_PRODUCTS_QUERY = load("search_products")
_SEARCH_VARIANTS_QUERY = load("search_variants")


def _parse_gid(gid: str) -> int:
    """Extract the numeric ID from a Shopify Global ID string (e.g. 'gid://shopify/Product/123' → 123)."""
    return int(gid.split("/")[-1])


def _normalise_variant_title(product_title: str, raw_title: str) -> str:
    """
    Return the variant title, falling back to the product title when Shopify
    assigns 'Default Title' to products that have only one variant with no options.
    """
    return product_title if raw_title == "Default Title" else raw_title


def _variant_from_product_node(prod: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a product node's variants into a list of variant dicts."""
    prod_id = _parse_gid(prod["id"])
    prod_title: str = prod["title"]
    variants: list[dict[str, Any]] = []

    for var_edge in prod["variants"]["edges"]:
        v = var_edge["node"]
        variants.append({
            "variant_id": _parse_gid(v["id"]),
            "product_id": prod_id,
            "product_title": prod_title,
            "variant_title": _normalise_variant_title(prod_title, v["title"]),
            "sku": v.get("sku") or "",
            "price": v["price"],
            "inventory": v["inventoryQuantity"],
        })

    return variants


def _variant_from_variant_node(v: dict[str, Any]) -> dict[str, Any]:
    """Build a variant dict from a productVariants query node."""
    prod = v["product"]
    prod_title: str = prod["title"]
    return {
        "variant_id": _parse_gid(v["id"]),
        "product_id": _parse_gid(prod["id"]),
        "product_title": prod_title,
        "variant_title": _normalise_variant_title(prod_title, v["title"]),
        "sku": v.get("sku") or "",
        "price": v["price"],
        "inventory": v["inventoryQuantity"],
    }


@router.get("/search")
async def search_products(q: str = Query(..., min_length=1)) -> dict[str, list[dict[str, Any]]]:
    """
    Search the product catalog by product title, product type, or SKU.

    Fires two Shopify GraphQL queries in parallel:
      - products(query: "title:{q}* OR product_type:{q}*") — title and type
      - productVariants(query: "sku:{q}*") — SKU

    Results are merged by variant ID so duplicates are excluded.
    """
    try:
        products_data, variants_data = await asyncio.gather(
            shopify.query(_SEARCH_PRODUCTS_QUERY, {"query": f"title:{q}* OR product_type:{q}*"}),
            shopify.query(_SEARCH_VARIANTS_QUERY, {"query": f"sku:{q}*"}),
            return_exceptions=True,
        )

        # Use a dict keyed by variant_id to deduplicate across both result sets.
        seen: dict[int, dict[str, Any]] = {}

        if not isinstance(products_data, Exception):
            for prod_edge in products_data.get("products", {}).get("edges", []):
                for variant in _variant_from_product_node(prod_edge["node"]):
                    seen.setdefault(variant["variant_id"], variant)

        if not isinstance(variants_data, Exception):
            for edge in variants_data.get("productVariants", {}).get("edges", []):
                variant = _variant_from_variant_node(edge["node"])
                seen.setdefault(variant["variant_id"], variant)

        return {"variants": list(seen.values())}

    except ShopifyError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
