"""
Low-level GraphQL client for the Shopify Admin API.
All Shopify communication flows through the single query() function here,
keeping auth headers and error handling in one place.
"""

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# All Admin API calls go to this single endpoint regardless of operation type.
_GRAPHQL_URL = f"https://{settings.shopify_store}/admin/api/2024-01/graphql.json"
_HEADERS = {"X-Shopify-Access-Token": settings.shopify_access_token}


async def query(gql: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Execute a GraphQL query or mutation against the Shopify Admin API.

    Args:
        gql: The GraphQL query or mutation string.
        variables: Optional dictionary of variable values referenced in the query.

    Returns:
        The contents of the top-level "data" key from the response.

    Raises:
        ShopifyError: If the HTTP response is an error, or if the response
                      body contains a top-level "errors" key.
    """
    logger.info("GraphQL POST %s", _GRAPHQL_URL)

    payload: dict[str, Any] = {"query": gql}
    if variables:
        payload["variables"] = variables

    async with httpx.AsyncClient() as client:
        response = await client.post(_GRAPHQL_URL, headers=_HEADERS, json=payload)

    logger.info("GraphQL POST %s -> %s", _GRAPHQL_URL, response.status_code)
    _raise_for_status(response)

    body = response.json()

    # Shopify returns HTTP 200 even for query-level errors, so the body must
    # also be checked for a top-level "errors" key.
    if "errors" in body:
        raise ShopifyError(str(body["errors"]))

    return body["data"]


def _raise_for_status(response: httpx.Response) -> None:
    """Raise ShopifyError for any non-2xx HTTP response."""
    if response.is_error:
        try:
            errors = response.json().get("errors", response.text)
        except Exception:
            errors = response.text
        raise ShopifyError(
            message=errors if isinstance(errors, str) else str(errors),
            status_code=response.status_code,
        )


class ShopifyError(Exception):
    """Raised when the Shopify API returns an error, either at HTTP or GraphQL level."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code
