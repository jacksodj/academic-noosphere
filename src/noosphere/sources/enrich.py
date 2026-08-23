"""Lazy metadata enrichment: recover fields missing from the primary source.

Title resolution chain for OpenAlex records that arrive titleless (observed on
heavily-merged records — BERT's W2963341956 is titleless in BOTH OpenAlex and
Crossref): Crossref by DOI first, then Semantic Scholar by DOI. Best-effort;
returns None when every source comes up empty.
"""

from __future__ import annotations

import asyncio

import httpx

from noosphere.sources.crossref import title_for_doi as crossref_title

S2_BASE = "https://api.semanticscholar.org/graph/v1/paper"
REQUEST_DEADLINE_S = 20.0


def _bare(doi: str) -> str:
    return doi.replace("https://doi.org/", "").replace("http://dx.doi.org/", "")


async def s2_title_for_doi(
    doi: str, api_key: str | None = None, client: httpx.AsyncClient | None = None
) -> str | None:
    """The work's title per Semantic Scholar, or None."""
    url = f"{S2_BASE}/DOI:{_bare(doi)}"
    headers = {"x-api-key": api_key} if api_key else {}
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        async with asyncio.timeout(REQUEST_DEADLINE_S):
            resp = await client.get(url, params={"fields": "title"}, headers=headers)
        if resp.status_code != 200:
            return None
        title = resp.json().get("title")
        return title.strip() if isinstance(title, str) and title.strip() else None
    except Exception:
        return None
    finally:
        if own_client:
            await client.aclose()


async def resolve_title(
    doi: str, *, mailto: str | None = None, s2_api_key: str | None = None
) -> str | None:
    """Crossref first (fast, polite pool), then Semantic Scholar."""
    title = await crossref_title(doi, mailto=mailto)
    if title:
        return title
    return await s2_title_for_doi(doi, api_key=s2_api_key)
