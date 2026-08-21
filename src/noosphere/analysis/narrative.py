"""Corpus-first Narrative Gap mining.

Abstracts are batched through Haiku with ``narrative_extraction_prompt``;
extracted claims map back to ``EvidenceItem(kind="work")`` grounded on the
quoted work's OpenAlex ID. Works without an abstract are skipped.

Web Search booster stream: the caller may hand over pre-fetched snippets as
``{"url": ..., "retrieved_at": ..., "text": ...}`` dicts — this module never
calls the network. Per the grounding rule, nothing from Web Search results is
persisted except identifiers: web claims are emitted as
``EvidenceItem(kind="web")`` carrying only the URL and retrieval date (no
quote text).
"""

from collections.abc import Iterator, Sequence

from noosphere.graph import GraphStore
from noosphere.llm.bedrock import LlmClient
from noosphere.llm.prompts import narrative_extraction_prompt
from noosphere.models import EvidenceItem


def _chunks(items: list, size: int) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def mine_narrative(
    work_ids: list[str],
    graph: GraphStore,
    llm: LlmClient,
    *,
    batch: int = 20,
    web_snippets: Sequence[dict] | None = None,
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []

    snippets: list[dict] = []
    for wid in work_ids:
        work = graph.get_work(wid)
        if work is None or not work.abstract:
            continue
        snippets.append({"text": work.abstract, "work_id": work.openalex_id})

    for chunk in _chunks(snippets, batch):
        system, user = narrative_extraction_prompt(chunk)
        result = await llm.haiku_json(system, user)
        for claim in result.get("claims", []):
            idx = claim.get("source_index")
            if not isinstance(idx, int) or not 0 <= idx < len(chunk):
                continue
            items.append(
                EvidenceItem(
                    kind="work",
                    work_id=chunk[idx]["work_id"],
                    quote=claim.get("quote"),
                )
            )

    if web_snippets:
        for chunk in _chunks(list(web_snippets), batch):
            payload = [{"text": s.get("text", ""), "url": s.get("url")} for s in chunk]
            system, user = narrative_extraction_prompt(payload)
            result = await llm.haiku_json(system, user)
            for claim in result.get("claims", []):
                idx = claim.get("source_index")
                if not isinstance(idx, int) or not 0 <= idx < len(chunk):
                    continue
                source = chunk[idx]
                items.append(
                    EvidenceItem(
                        kind="web",
                        url=source.get("url"),
                        retrieved_at=source.get("retrieved_at"),
                        quote=None,  # identifiers only for Web Search results
                    )
                )

    return items
