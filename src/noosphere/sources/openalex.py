"""OpenAlex client — Resolution's primary scholarly API (system of record).

Every request goes through the sidecar's immutable response cache first
(key = sha256 of "openalex:" + URL with sorted params, credentials excluded);
on a hit no HTTP request is made. All OpenAlex IDs are stored in short form ("W2741809807"),
DOIs as bare DOIs, and every parsed record carries Provenance.
"""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from noosphere.models import (
    Author,
    Authorship,
    Institution,
    Provenance,
    Source,
    Topic,
    Work,
    WorkTopic,
)
from noosphere.sources.ratelimit import RateLimiter

BASE_URL = "https://api.openalex.org"
BATCH_SIZE = 50  # OpenAlex caps OR-filters at 50 values per request
CITING_CAP = 500
CITING_PER_PAGE = 200
RETRY_BACKOFF_S = 1.0
REQUEST_DEADLINE_S = 90.0  # hard wall-clock cap per request (stall detection)
MAX_RETRY_AFTER_S = 30.0  # never honor a server Retry-After beyond this


class ResponseCache(Protocol):
    """The slice of the Sidecar interface this client depends on (#11)."""

    def cache_get(self, key: str) -> str | None: ...
    def cache_put(self, key: str, api: str, url: str, body: str) -> None: ...


def cache_key(url: str) -> str:
    return hashlib.sha256(f"openalex:{url}".encode()).hexdigest()


def short_id(openalex_url_or_id: str) -> str:
    """'https://openalex.org/W2741809807' -> 'W2741809807' (idempotent)."""
    return openalex_url_or_id.rstrip("/").rsplit("/", 1)[-1]


def bare_doi(doi: str | None) -> str | None:
    if doi is None:
        return None
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/"):
        if doi.startswith(prefix):
            return doi[len(prefix):]
    return doi


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    if not inverted_index:
        return None
    positions: dict[int, str] = {}
    for token, idxs in inverted_index.items():
        for i in idxs:
            positions[i] = token
    return " ".join(positions[i] for i in sorted(positions))


class OpenAlexClient:
    def __init__(
        self,
        sidecar: ResponseCache,
        api_key: str | None,
        rate: RateLimiter,
        mailto: str | None = None,
    ) -> None:
        self._sidecar = sidecar
        self._api_key = api_key
        self._rate = rate
        self._mailto = mailto
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    async def __aenter__(self) -> "OpenAlexClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _url(self, path: str, params: dict[str, Any], *, with_auth: bool) -> str:
        # Credentials never enter the cache key or the persisted URL: a key
        # rotation must not invalidate the immutable cache, and key material
        # must never land in the sidecar DB (BYO-credentials rule, #8).
        merged = dict(params)
        if with_auth:
            if self._api_key:
                merged["api_key"] = self._api_key
            if self._mailto:
                merged["mailto"] = self._mailto
        query = urlencode(sorted((k, str(v)) for k, v in merged.items()))
        return f"{BASE_URL}{path}?{query}" if query else f"{BASE_URL}{path}"

    async def _fetch(self, url: str) -> "httpx.Response":
        """One rate-limited GET under a hard wall-clock deadline.

        httpx's read timeout only fires on silence between chunks — a server
        that dribbles bytes (observed from OpenAlex under anonymous-traffic
        starvation) can hold a request open forever. The deadline converts
        that stall into an error; the client is reset so the dead connection
        is not reused.
        """
        await self._rate.acquire()
        try:
            async with asyncio.timeout(REQUEST_DEADLINE_S):
                return await self._client().get(url)
        except (TimeoutError, httpx.TimeoutException):
            await self.aclose()  # drop the stalled connection
            raise

    async def _get_json(self, path: str, params: dict[str, Any]) -> dict | None:
        """Cache-first GET. Returns parsed JSON, or None on HTTP 404."""
        url = self._url(path, params, with_auth=True)
        clean_url = self._url(path, params, with_auth=False)
        key = cache_key(clean_url)
        cached = self._sidecar.cache_get(key)
        if cached is not None:
            return json.loads(cached)

        try:
            resp = await self._fetch(url)
        except (TimeoutError, httpx.TimeoutException):
            await asyncio.sleep(RETRY_BACKOFF_S)
            try:
                resp = await self._fetch(url)
            except (TimeoutError, httpx.TimeoutException) as exc:
                raise RuntimeError(
                    f"OpenAlex request stalled twice (>{REQUEST_DEADLINE_S}s each): "
                    f"{clean_url} — if this persists, add an OpenAlex API key and "
                    "contact email in Settings (anonymous traffic is deprioritized)"
                ) from exc
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            try:
                backoff = float(retry_after) if retry_after else RETRY_BACKOFF_S
            except ValueError:
                backoff = RETRY_BACKOFF_S
            # A rate-jailed anonymous client can be told to wait hours; a run
            # sleeping that long is indistinguishable from a hang. Cap it and
            # fail loudly if the server still says no.
            await asyncio.sleep(min(backoff, MAX_RETRY_AFTER_S))
            resp = await self._fetch(url)
            if resp.status_code == 429:
                raise RuntimeError(
                    f"OpenAlex rate-limited twice (Retry-After {retry_after!r}) "
                    f"for {clean_url} — add an OpenAlex API key and contact email "
                    "in Settings; anonymous traffic is throttled hard"
                )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        body = resp.text
        self._sidecar.cache_put(key, "openalex", clean_url, body)
        return json.loads(body)

    @staticmethod
    def _filter_expr(filters: dict[str, Any]) -> str:
        return ",".join(f"{k}:{v}" for k, v in filters.items())

    async def works_search(
        self,
        query: str,
        per_page: int = 25,
        filters: dict | None = None,
    ) -> list[dict]:
        params: dict[str, Any] = {"search": query, "per-page": per_page}
        if filters:
            params["filter"] = self._filter_expr(filters)
        data = await self._get_json("/works", params)
        return data.get("results", []) if data else []

    async def work(self, openalex_id: str) -> dict | None:
        return await self._get_json(f"/works/{short_id(openalex_id)}", {})

    async def works_batch(self, openalex_ids: list[str]) -> list[dict]:
        results: list[dict] = []
        ids = [short_id(i) for i in openalex_ids]
        for start in range(0, len(ids), BATCH_SIZE):
            chunk = ids[start : start + BATCH_SIZE]
            params = {
                "filter": f"openalex_id:{'|'.join(chunk)}",
                "per-page": len(chunk),
            }
            data = await self._get_json("/works", params)
            if data:
                results.extend(data.get("results", []))
        return results

    async def referenced_and_citing(self, openalex_id: str) -> tuple[list[str], list[str]]:
        wid = short_id(openalex_id)
        raw = await self.work(wid)
        referenced = [short_id(r) for r in (raw or {}).get("referenced_works", [])]

        citing: list[str] = []
        cursor: str | None = "*"
        while cursor and len(citing) < CITING_CAP:
            params = {
                "filter": f"cites:{wid}",
                "per-page": CITING_PER_PAGE,
                "cursor": cursor,
                "select": "id",
            }
            data = await self._get_json("/works", params)
            if not data:
                break
            page = data.get("results", [])
            citing.extend(short_id(w["id"]) for w in page)
            cursor = data.get("meta", {}).get("next_cursor")
            if not page:
                break
        return referenced, citing[:CITING_CAP]

    @staticmethod
    def parse_work(
        raw: dict,
    ) -> tuple[
        Work,
        list[Author],
        list[Authorship],
        list[WorkTopic],
        list[Topic],
        Source | None,
        list[Institution],
    ]:
        now = datetime.now(timezone.utc)
        work_id = short_id(raw["id"])

        def prov(source_id: str) -> Provenance:
            return Provenance(source_api="openalex", source_id=source_id, retrieved_at=now)

        work = Work(
            openalex_id=work_id,
            doi=bare_doi(raw.get("doi")),
            title=raw.get("display_name") or raw.get("title") or "",
            year=raw.get("publication_year"),
            abstract=reconstruct_abstract(raw.get("abstract_inverted_index")),
            cited_by_count=raw.get("cited_by_count", 0),
            provenance=prov(work_id),
        )

        authors: list[Author] = []
        authorships: list[Authorship] = []
        institutions: list[Institution] = []
        seen_inst: set[str] = set()
        for position, a in enumerate(raw.get("authorships", [])):
            author_raw = a.get("author") or {}
            if not author_raw.get("id"):
                continue
            author_id = short_id(author_raw["id"])
            authors.append(
                Author(
                    openalex_id=author_id,
                    display_name=author_raw.get("display_name") or "",
                    orcid=author_raw.get("orcid"),
                    provenance=prov(author_id),
                )
            )
            authorships.append(
                Authorship(
                    author_id=author_id,
                    work_id=work_id,
                    position=position,
                    provenance=prov(work_id),
                )
            )
            for inst in a.get("institutions") or []:
                if not inst.get("id"):
                    continue
                inst_id = short_id(inst["id"])
                if inst_id in seen_inst:
                    continue
                seen_inst.add(inst_id)
                institutions.append(
                    Institution(
                        openalex_id=inst_id,
                        display_name=inst.get("display_name") or "",
                        ror=inst.get("ror"),
                        country_code=inst.get("country_code"),
                        provenance=prov(inst_id),
                    )
                )

        topics: list[Topic] = []
        work_topics: list[WorkTopic] = []
        for t in raw.get("topics") or []:
            if not t.get("id"):
                continue
            topic_id = short_id(t["id"])
            topics.append(
                Topic(
                    openalex_id=topic_id,
                    display_name=t.get("display_name") or "",
                    level="topic",
                    provenance=prov(topic_id),
                )
            )
            work_topics.append(
                WorkTopic(
                    work_id=work_id,
                    topic_id=topic_id,
                    score=float(t.get("score", 0.0)),
                    provenance=prov(work_id),
                )
            )

        source: Source | None = None
        src_raw = (raw.get("primary_location") or {}).get("source")
        if src_raw and src_raw.get("id"):
            src_id = short_id(src_raw["id"])
            source = Source(
                openalex_id=src_id,
                display_name=src_raw.get("display_name") or "",
                type=src_raw.get("type"),
                provenance=prov(src_id),
            )

        return work, authors, authorships, work_topics, topics, source, institutions
