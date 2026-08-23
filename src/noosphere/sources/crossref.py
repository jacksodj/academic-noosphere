"""Crossref title lookup — lazy enrichment for OpenAlex records that arrive
with a null display_name (observed on heavily-merged records, e.g. BERT's
W2963341956). Best-effort: any failure returns None.
"""

from __future__ import annotations

import asyncio

import httpx

CROSSREF_BASE = "https://api.crossref.org/works"
REQUEST_DEADLINE_S = 20.0


def _bare(doi: str) -> str:
    return doi.replace("https://doi.org/", "").replace("http://dx.doi.org/", "")


async def title_for_doi(
    doi: str, mailto: str | None = None, client: httpx.AsyncClient | None = None
) -> str | None:
    """The work's title per Crossref, or None if unavailable."""
    url = f"{CROSSREF_BASE}/{_bare(doi)}"
    params = {"mailto": mailto} if mailto else {}
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        async with asyncio.timeout(REQUEST_DEADLINE_S):
            resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return None
        titles = resp.json().get("message", {}).get("title") or []
        title = titles[0].strip() if titles and isinstance(titles[0], str) else None
        return title or None
    except Exception:
        return None
    finally:
        if own_client:
            await client.aclose()
