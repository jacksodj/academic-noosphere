# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp-proxy-for-aws", "mcp", "boto3"]
# ///
"""Phase-0 spike (ticket #7): does the Amazon web index cover academic sources?

Runs ~30 hand-written queries against an AgentCore Gateway's WebSearch tool with
a scholarly domain include-list, spanning both sides of the first Field
(agent memory architectures x human memory science), and writes:

  - phase0_results.jsonl   raw results, one line per (query, result)
  - stdout                 a per-domain / per-query coverage summary

Prereqs (see docs/phase0-spike.md): a Gateway in us-east-1 with the
`web-search` connector target pinned to version 1.2.0, IAM inbound auth,
and an AWS profile whose identity has bedrock-agentcore:InvokeGateway.

Usage:
  AWS_PROFILE=noosphere uv run scripts/phase0_spike.py \
      --gateway-url https://gateway-<id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp

Cost: ~30 queries ~= $0.21 at $7/1,000.
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from urllib.parse import urlparse

from mcp import ClientSession
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

INCLUDE_DOMAINS = [
    "arxiv.org",
    "pubmed.ncbi.nlm.nih.gov",
    "semanticscholar.org",
    "openreview.net",
    "aclanthology.org",
    "nature.com",
    "biorxiv.org",
]

# ~30 queries, <=200 chars each: agent-memory (arXiv-side), memory-science
# (PubMed-side), and the intersection. Wide enough to judge coverage, not depth.
QUERIES = [
    # Agent memory / agentic AI (arXiv-heavy)
    "LLM agent memory architecture survey",
    "long-term memory for large language model agents",
    "episodic memory in LLM agents",
    "semantic memory retrieval augmented agents",
    "memory consolidation LLM agent",
    "agent memory forgetting mechanism",
    "hierarchical memory multi-agent systems",
    "vector database agent memory retrieval",
    "working memory context window agents",
    "memory-augmented neural networks 2026",
    "reflection memory generative agents",
    "agent memory benchmark evaluation",
    # Memory science (PubMed-heavy)
    "episodic memory consolidation hippocampus review",
    "systems consolidation theory memory",
    "forgetting adaptive function memory",
    "memory reconsolidation update mechanism",
    "schema memory encoding prefrontal",
    "sleep memory consolidation replay",
    "semantic memory neural representation",
    "pattern separation completion hippocampus",
    "predictive coding memory retrieval",
    "why we remember Ranganath memory",
    # Intersection
    "cognitive architecture memory inspired AI agents",
    "hippocampal replay artificial intelligence",
    "complementary learning systems deep learning",
    "biologically inspired memory language models",
    "catastrophic forgetting continual learning agents",
    "human memory principles agent design",
    "episodic control reinforcement learning",
    "memory schemas knowledge graphs agents",
]


async def run(gateway_url: str, region: str, max_results: int, out_path: str) -> None:
    transport = aws_iam_streamablehttp_client(
        endpoint=gateway_url, aws_region=region, aws_service="bedrock-agentcore"
    )
    per_domain: Counter[str] = Counter()
    per_query: dict[str, int] = {}
    dated = 0
    total = 0

    async with transport as (read, write, *_), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        names = [t.name for t in tools.tools]
        print(f"tools/list -> {names}", file=sys.stderr)
        tool = next((n for n in names if "websearch" in n.lower()), names[0])

        with open(out_path, "w") as out:
            for q in QUERIES:
                res = await session.call_tool(
                    tool,
                    {
                        "query": q,
                        "maxResults": max_results,
                        "filters": {"domainFilter": {"include": INCLUDE_DOMAINS}},
                    },
                )
                results = []
                for block in res.content:
                    if getattr(block, "type", None) != "text":
                        continue
                    try:
                        results = json.loads(block.text).get("results", [])
                    except (json.JSONDecodeError, AttributeError):
                        continue
                per_query[q] = len(results)
                for r in results:
                    total += 1
                    url = r.get("url") or ""
                    host = urlparse(url).netloc.removeprefix("www.") if url else "(knowledge-graph)"
                    per_domain[host] += 1
                    if r.get("publishedDate"):
                        dated += 1
                    out.write(json.dumps({"query": q, **r}) + "\n")
                print(f"  {len(results):2d} results  {q}", file=sys.stderr)

    print("\n=== Phase-0 coverage summary ===")
    print(f"queries: {len(QUERIES)}   results: {total}   with publishedDate: {dated}")
    print("\nresults per domain:")
    for host, n in per_domain.most_common():
        print(f"  {n:4d}  {host}")
    zero = [q for q, n in per_query.items() if n == 0]
    if zero:
        print(f"\nqueries with ZERO results ({len(zero)}):")
        for q in zero:
            print(f"  - {q}")
    print(
        "\nVerdict guide: healthy coverage looks like most queries returning >5"
        " results with arxiv.org and pubmed.ncbi.nlm.nih.gov both well represented."
        " Many zero-result queries => index is thin for academic sources =>"
        " Web Search demotes to enrichment-only (see ticket #7)."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gateway-url", required=True, help="Gateway MCP endpoint URL")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--max-results", type=int, default=10)
    ap.add_argument("--out", default="phase0_results.jsonl")
    args = ap.parse_args()
    asyncio.run(run(args.gateway_url, args.region, args.max_results, args.out))


if __name__ == "__main__":
    main()
