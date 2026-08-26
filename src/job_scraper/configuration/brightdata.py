"""Explicit activation controls for paid Bright Data requests."""

import os

BRIGHTDATA_DIRECT_COLLECTION_ENABLED = "BRIGHTDATA_DIRECT_COLLECTION_ENABLED"


def brightdata_direct_collection_enabled() -> bool:
    """Return whether an operator explicitly allowed direct Bright Data collection."""
    return os.getenv(BRIGHTDATA_DIRECT_COLLECTION_ENABLED, "").strip().lower() == "true"
