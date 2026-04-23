"""
Loads GraphQL query and mutation strings from .graphql files at import time.
Keeping queries in separate files allows syntax highlighting and editing
without escaping issues inside Python string literals.
"""

from pathlib import Path

_QUERIES_DIR = Path(__file__).parent


def load(name: str) -> str:
    """Read and return the contents of a .graphql file by name (without extension)."""
    return (_QUERIES_DIR / f"{name}.graphql").read_text(encoding="utf-8")
