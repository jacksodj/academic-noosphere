# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Backfill empty work titles in the graph via Crossref, then Semantic Scholar (by DOI).

OpenAlex serves some heavily-merged records with a null display_name (e.g.
BERT's W2963341956); this repairs any such works already persisted. Run from
the repo root with the app STOPPED (the graph takes a single-process lock):

  uv run python scripts/backfill_titles.py
"""

import asyncio
import sys


async def main() -> int:
    from noosphere.config import data_dir, get_credential
    from noosphere.graph import GraphStore
    from noosphere.sources.enrich import resolve_title

    graph = GraphStore(data_dir() / "graph")
    mailto = get_credential("crossref_mailto")
    s2_key = get_credential("s2_api_key")
    broken = [
        w
        for wid in graph.work_ids()
        if (w := graph.get_work(wid)) is not None and not w.title.strip() and w.doi
    ]
    print(f"works with empty titles and a DOI: {len(broken)}")
    repaired = 0
    for w in broken:
        title = await resolve_title(w.doi, mailto=mailto, s2_api_key=s2_key)
        if title:
            w.title = title
            graph.upsert_works([w])
            repaired += 1
            print(f"  {w.openalex_id}: {title[:80]}")
    graph.checkpoint()
    graph.close()
    print(f"repaired {repaired}/{len(broken)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
