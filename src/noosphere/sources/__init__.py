"""Scholarly API clients (Resolution). OpenAlex is the system of record."""

from noosphere.sources.openalex import OpenAlexClient
from noosphere.sources.ratelimit import RateLimiter

__all__ = ["OpenAlexClient", "RateLimiter"]
