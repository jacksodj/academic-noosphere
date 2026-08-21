"""Tests for the OpenAlex client: parsing, cache-first requests, rate limiting.

Uses a minimal in-memory fake of the Sidecar cache API — never the real Sidecar.
"""

import asyncio
import time

import httpx
import pytest
import respx

from noosphere.sources.openalex import (
    BASE_URL,
    OpenAlexClient,
    bare_doi,
    reconstruct_abstract,
    short_id,
)
from noosphere.sources.ratelimit import RateLimiter


class FakeSidecar:
    """In-memory stand-in for Sidecar's immutable response cache (#11)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def cache_get(self, key: str) -> str | None:
        return self.store.get(key)

    def cache_put(self, key: str, api: str, url: str, body: str) -> None:
        self.store.setdefault(key, body)


RAW_WORK = {
    "id": "https://openalex.org/W2741809807",
    "doi": "https://doi.org/10.7717/peerj.4375",
    "display_name": "Episodic memory for software agents",
    "publication_year": 2018,
    "cited_by_count": 394,
    "abstract_inverted_index": {
        "Agent": [0],
        "memory": [1, 5],
        "systems": [2],
        "consolidate": [3],
        "episodic": [4],
        "traces.": [6],
    },
    "authorships": [
        {
            "author_position": "first",
            "author": {
                "id": "https://openalex.org/A1969205032",
                "display_name": "Heather Piwowar",
                "orcid": "https://orcid.org/0000-0003-1613-5981",
            },
            "institutions": [
                {
                    "id": "https://openalex.org/I4200000001",
                    "display_name": "OurResearch",
                    "ror": "https://ror.org/02nr0ka47",
                    "country_code": "CA",
                },
                {
                    "id": "https://openalex.org/I121332964",
                    "display_name": "University of Pittsburgh",
                    "ror": "https://ror.org/01an3r305",
                    "country_code": "US",
                },
            ],
        },
        {
            "author_position": "last",
            "author": {
                "id": "https://openalex.org/A2208157607",
                "display_name": "Jason Priem",
                "orcid": None,
            },
            "institutions": [
                {
                    "id": "https://openalex.org/I4200000001",
                    "display_name": "OurResearch",
                    "ror": "https://ror.org/02nr0ka47",
                    "country_code": "CA",
                }
            ],
        },
    ],
    "topics": [
        {
            "id": "https://openalex.org/T10102",
            "display_name": "Memory and Cognitive Architectures",
            "score": 0.9997,
        },
        {
            "id": "https://openalex.org/T13616",
            "display_name": "Scholarly Communication",
            "score": 0.5412,
        },
    ],
    "primary_location": {
        "source": {
            "id": "https://openalex.org/S1983995261",
            "display_name": "PeerJ",
            "type": "journal",
        }
    },
    "referenced_works": [
        "https://openalex.org/W1560783210",
        "https://openalex.org/W2058170566",
    ],
}


def make_client(sidecar: FakeSidecar | None = None, api_key: str | None = "k-test") -> OpenAlexClient:
    return OpenAlexClient(
        sidecar=sidecar or FakeSidecar(),
        api_key=api_key,
        rate=RateLimiter(1000.0),
    )


async def test_works_search_parses_realistic_payload(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(host="api.openalex.org", path="/works").mock(
        return_value=httpx.Response(
            200, json={"meta": {"count": 1, "next_cursor": None}, "results": [RAW_WORK]}
        )
    )
    async with make_client() as client:
        results = await client.works_search(
            "agent memory", per_page=10, filters={"from_publication_date": "2015-01-01"}
        )

    assert route.call_count == 1
    sent = route.calls.last.request.url
    assert sent.params["search"] == "agent memory"
    assert sent.params["per-page"] == "10"
    assert sent.params["filter"] == "from_publication_date:2015-01-01"
    assert sent.params["api_key"] == "k-test"
    assert len(results) == 1

    work, authors, authorships, work_topics, topics, source, institutions = (
        OpenAlexClient.parse_work(results[0])
    )
    assert work.openalex_id == "W2741809807"
    assert work.abstract == "Agent memory systems consolidate episodic memory traces."
    assert source is not None and source.openalex_id == "S1983995261"


async def test_cache_hit_prevents_second_http_call(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(host="api.openalex.org", path="/works").mock(
        return_value=httpx.Response(200, json={"meta": {}, "results": [RAW_WORK]})
    )
    sidecar = FakeSidecar()
    async with make_client(sidecar) as client:
        first = await client.works_search("agent memory")
        second = await client.works_search("agent memory")

    assert route.call_count == 1
    assert first == second
    assert len(sidecar.store) == 1


async def test_cache_key_includes_params(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(host="api.openalex.org", path="/works").mock(
        return_value=httpx.Response(200, json={"meta": {}, "results": []})
    )
    async with make_client() as client:
        await client.works_search("agent memory")
        await client.works_search("human memory")
    assert route.call_count == 2


async def test_rate_limiter_enforces_ordering_and_spacing() -> None:
    limiter = RateLimiter(20.0)
    order: list[int] = []

    async def worker(i: int) -> None:
        await limiter.acquire()
        order.append(i)

    start = time.monotonic()
    async with asyncio.TaskGroup() as tg:
        for i in range(4):
            tg.create_task(worker(i))
    elapsed = time.monotonic() - start

    assert order == [0, 1, 2, 3]
    # first token is free; three more at 20/s need >= 0.15s (scheduling tolerance)
    assert elapsed >= 0.14


async def test_429_gets_one_polite_retry(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(host="api.openalex.org", path="/works/W2741809807").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=RAW_WORK),
        ]
    )
    sidecar = FakeSidecar()
    async with make_client(sidecar) as client:
        raw = await client.work("W2741809807")

    assert route.call_count == 2
    assert raw is not None and raw["id"].endswith("W2741809807")
    assert len(sidecar.store) == 1


async def test_work_404_returns_none(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(host="api.openalex.org", path="/works/W999").mock(
        return_value=httpx.Response(404)
    )
    sidecar = FakeSidecar()
    async with make_client(sidecar) as client:
        assert await client.work("W999") is None
    assert sidecar.store == {}


async def test_works_batch_chunks_at_50(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(host="api.openalex.org", path="/works").mock(
        return_value=httpx.Response(200, json={"meta": {}, "results": [RAW_WORK]})
    )
    ids = [f"W{i}" for i in range(60)]
    async with make_client() as client:
        results = await client.works_batch(ids)

    assert route.call_count == 2
    first_filter = route.calls[0].request.url.params["filter"]
    assert first_filter.startswith("openalex_id:W0|")
    assert first_filter.count("|") == 49
    assert len(results) == 2


async def test_referenced_and_citing_paginates_and_caps(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(host="api.openalex.org", path="/works/W2741809807").mock(
        return_value=httpx.Response(200, json=RAW_WORK)
    )
    page1 = {
        "meta": {"next_cursor": "cur2"},
        "results": [{"id": f"https://openalex.org/W{i}"} for i in range(200)],
    }
    page2 = {
        "meta": {"next_cursor": None},
        "results": [{"id": f"https://openalex.org/W{200 + i}"} for i in range(30)],
    }
    citing_route = respx_mock.get(host="api.openalex.org", path="/works").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    async with make_client() as client:
        referenced, citing = await client.referenced_and_citing("W2741809807")

    assert referenced == ["W1560783210", "W2058170566"]
    assert citing_route.call_count == 2
    assert citing_route.calls[0].request.url.params["filter"] == "cites:W2741809807"
    assert citing_route.calls[0].request.url.params["cursor"] == "*"
    assert citing_route.calls[1].request.url.params["cursor"] == "cur2"
    assert len(citing) == 230
    assert citing[0] == "W0" and citing[-1] == "W229"


def test_parse_work_field_mapping() -> None:
    work, authors, authorships, work_topics, topics, source, institutions = (
        OpenAlexClient.parse_work(RAW_WORK)
    )

    assert work.openalex_id == "W2741809807"
    assert work.doi == "10.7717/peerj.4375"
    assert work.title == "Episodic memory for software agents"
    assert work.year == 2018
    assert work.cited_by_count == 394
    assert work.abstract == "Agent memory systems consolidate episodic memory traces."
    assert work.provenance.source_api == "openalex"
    assert work.provenance.source_id == "W2741809807"

    assert [a.openalex_id for a in authors] == ["A1969205032", "A2208157607"]
    assert authors[0].display_name == "Heather Piwowar"
    assert [(a.author_id, a.work_id, a.position) for a in authorships] == [
        ("A1969205032", "W2741809807", 0),
        ("A2208157607", "W2741809807", 1),
    ]

    assert [(t.openalex_id, t.level) for t in topics] == [
        ("T10102", "topic"),
        ("T13616", "topic"),
    ]
    assert [(wt.work_id, wt.topic_id, wt.score) for wt in work_topics] == [
        ("W2741809807", "T10102", 0.9997),
        ("W2741809807", "T13616", 0.5412),
    ]

    assert source is not None
    assert (source.openalex_id, source.display_name, source.type) == (
        "S1983995261",
        "PeerJ",
        "journal",
    )

    # institutions deduped: OurResearch appears under both authors
    assert [i.openalex_id for i in institutions] == ["I4200000001", "I121332964"]
    assert institutions[0].country_code == "CA"

    for model in [work, *authors, *authorships, *work_topics, *topics, source, *institutions]:
        assert model.provenance.source_api == "openalex"
        assert model.provenance.retrieved_at is not None


def test_parse_work_without_source_or_abstract() -> None:
    raw = dict(RAW_WORK)
    raw["primary_location"] = None
    raw["abstract_inverted_index"] = None
    work, _authors, _authorships, _wts, _topics, source, _insts = OpenAlexClient.parse_work(raw)
    assert work.abstract is None
    assert source is None


def test_helpers() -> None:
    assert short_id("https://openalex.org/W2741809807") == "W2741809807"
    assert short_id("W2741809807") == "W2741809807"
    assert bare_doi("https://doi.org/10.1000/xyz") == "10.1000/xyz"
    assert bare_doi("10.1000/xyz") == "10.1000/xyz"
    assert bare_doi(None) is None
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({"b": [1], "a": [0], "c": [2]}) == "a b c"
