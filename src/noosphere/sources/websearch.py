"""Discovery client for AgentCore Web Search (Gateway MCP, connector 1.2.0).

Discovery only: the output of a search is candidate identifiers (titles, URLs,
raw published dates) that move forward to Resolution — result *content* is
never persisted (acceptable-use constraint). Snippet text is dropped at parse
time unless the caller explicitly asks for it (`want_snippets=True`, used by
the narrative booster which reads snippets transiently and never persists
them).

`mcp` and `mcp_proxy_for_aws` are the optional `websearch` extra and are
imported lazily at call time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>#?]+", re.IGNORECASE)
_DOI_TRAILING = ".,;:)]}"


@dataclass(frozen=True)
class DiscoveredRef:
    """One Discovery hit: identifiers only.

    `snippet` is populated only when a caller passes `want_snippets=True` and
    must never be persisted anywhere.
    """

    title: str
    url: str
    published_date_raw: str | None
    snippet: str | None = None


def extract_doi(text: str | None) -> str | None:
    """Pull a bare DOI out of a URL or title-like string, if present."""
    if not text:
        return None
    match = _DOI_RE.search(text)
    if match is None:
        return None
    return match.group(0).rstrip(_DOI_TRAILING)


def pick_websearch_tool(names: Iterable[str]) -> str | None:
    """Pick the Gateway tool whose name contains "websearch" (any case).

    Gateway prefixes target names, e.g. "web-search-tool___WebSearch".
    """
    for name in names:
        if "websearch" in name.replace("-", "").replace("_", "").lower():
            return name
    return None


def parse_result_items(payload_text: str) -> list[dict[str, Any]]:
    """Parse the JSON text block of a WebSearch tool result into result dicts."""
    try:
        data = json.loads(payload_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    results = data.get("results")
    if not isinstance(results, list):
        return []
    return [r for r in results if isinstance(r, dict)]


def refs_from_items(
    items: Sequence[dict[str, Any]], want_snippets: bool = False
) -> list[DiscoveredRef]:
    """Result dicts -> DiscoveredRefs. publishedDate is kept as a raw string."""
    refs: list[DiscoveredRef] = []
    for item in items:
        title = str(item.get("title") or "")
        url = str(item.get("url") or "")
        if not title and not url:
            continue
        published = item.get("publishedDate")
        refs.append(
            DiscoveredRef(
                title=title,
                url=url,
                published_date_raw=str(published) if published is not None else None,
                snippet=(str(item["text"]) if want_snippets and item.get("text") else None),
            )
        )
    return refs


def parse_tool_content(blocks: Iterable[Any], want_snippets: bool = False) -> list[DiscoveredRef]:
    """Extract refs from MCP tool-result content blocks (first parseable text block)."""
    for block in blocks:
        if getattr(block, "type", None) != "text":
            continue
        items = parse_result_items(getattr(block, "text", "") or "")
        if items:
            return refs_from_items(items, want_snippets=want_snippets)
    return []


class WebSearchClient:
    def __init__(self, gateway_url: str, region: str) -> None:
        self._gateway_url = gateway_url
        self._region = region

    @staticmethod
    def _lazy_imports() -> tuple[Any, Any]:
        try:
            from mcp import ClientSession
            from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
        except ImportError as exc:
            raise RuntimeError(
                "Web Search discovery requires the optional `websearch` extra "
                "(mcp + mcp-proxy-for-aws); install it with "
                "`uv sync --extra websearch`"
            ) from exc
        return ClientSession, aws_iam_streamablehttp_client

    async def search(
        self,
        query: str,
        max_results: int = 10,
        include_domains: Sequence[str] | None = None,
        want_snippets: bool = False,
    ) -> list[DiscoveredRef]:
        client_session, iam_transport = self._lazy_imports()
        transport = iam_transport(
            endpoint=self._gateway_url,
            aws_region=self._region,
            aws_service="bedrock-agentcore",
        )
        arguments: dict[str, Any] = {"query": query, "maxResults": max_results}
        if include_domains:
            arguments["filters"] = {"domainFilter": {"include": list(include_domains)}}
        async with transport as (read, write, *_), client_session(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            tool_name = pick_websearch_tool(names)
            if tool_name is None:
                raise RuntimeError(f"no WebSearch tool on Gateway; tools/list -> {names}")
            result = await session.call_tool(tool_name, arguments)
            return parse_tool_content(result.content, want_snippets=want_snippets)
