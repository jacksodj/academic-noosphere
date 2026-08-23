"""Title-resolution chain: Crossref first, Semantic Scholar fallback."""

from __future__ import annotations

import httpx
import respx

from noosphere.sources.enrich import resolve_title, s2_title_for_doi

BERT_DOI = "10.18653/v1/n19-1423"


@respx.mock
async def test_crossref_wins_when_it_has_a_title() -> None:
    respx.get(url__regex=r"https://api\.crossref\.org/works/.*").mock(
        return_value=httpx.Response(200, json={"message": {"title": ["Real Title"]}})
    )
    s2 = respx.get(url__regex=r"https://api\.semanticscholar\.org/.*").mock(
        return_value=httpx.Response(200, json={"title": "S2 Title"})
    )
    assert await resolve_title(BERT_DOI) == "Real Title"
    assert not s2.called


@respx.mock
async def test_s2_fallback_when_crossref_title_is_empty() -> None:
    """The BERT case: both OpenAlex and Crossref carry an empty title."""
    respx.get(url__regex=r"https://api\.crossref\.org/works/.*").mock(
        return_value=httpx.Response(200, json={"message": {"title": [""]}})
    )
    respx.get(url__regex=r"https://api\.semanticscholar\.org/.*").mock(
        return_value=httpx.Response(
            200,
            json={"title": "BERT: Pre-training of Deep Bidirectional Transformers"},
        )
    )
    assert (
        await resolve_title(BERT_DOI)
        == "BERT: Pre-training of Deep Bidirectional Transformers"
    )


@respx.mock
async def test_every_source_failing_returns_none() -> None:
    respx.get(url__regex=r"https://api\.crossref\.org/works/.*").mock(
        return_value=httpx.Response(404)
    )
    respx.get(url__regex=r"https://api\.semanticscholar\.org/.*").mock(
        return_value=httpx.Response(500)
    )
    assert await resolve_title(BERT_DOI) is None


@respx.mock
async def test_s2_doi_url_shape_and_api_key_header() -> None:
    route = respx.get(url__regex=r"https://api\.semanticscholar\.org/.*").mock(
        return_value=httpx.Response(200, json={"title": "T"})
    )
    assert await s2_title_for_doi("https://doi.org/10.1/x", api_key="k") == "T"
    req = route.calls[0].request
    assert "DOI:10.1/x" in str(req.url)
    assert req.headers["x-api-key"] == "k"
