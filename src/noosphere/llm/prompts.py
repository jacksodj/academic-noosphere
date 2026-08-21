"""The two v1 prompt builders. Pure functions returning (system, user) tuples."""

import json


def narrative_extraction_prompt(snippets: list[dict]) -> tuple[str, str]:
    """Haiku: extract stated future-work/limitation/open-problem claims."""
    system = (
        "You extract Narrative Gap claims from academic text snippets: statements "
        "where authors explicitly name future work, limitations, or open problems. "
        "Extract only what is stated in the snippets — never invent or infer claims. "
        "Respond with JSON only, no prose, matching exactly:\n"
        '{"claims": [{"quote": "<verbatim quote from the snippet>", '
        '"source_index": <integer index of the snippet quoted>, '
        '"kind": "future_work" | "limitation" | "open_problem"}]}\n'
        'If no such claims are stated, respond {"claims": []}.'
    )
    numbered = [{"index": i, **snippet} for i, snippet in enumerate(snippets)]
    user = (
        "Snippets (JSON, each with its index):\n"
        f"{json.dumps(numbered, ensure_ascii=False, default=str)}"
    )
    return system, user


def gap_statement_prompt(candidate_json: str, evidence_json: str) -> tuple[str, str]:
    """Opus: write a grounded gap statement from a candidate and its evidence."""
    system = (
        "You write a grounded literature-gap statement for a confirmed Whitespace "
        "Candidate. Every factual claim in the statement must cite an item from the "
        "provided evidence by its index, inline as [0], [1], etc. Do not use any "
        "knowledge beyond the provided evidence; make no uncited claims. "
        "Respond with JSON only, no prose, matching exactly:\n"
        '{"statement": "<gap statement with inline [index] citations>", '
        '"kinds": ["structural" | "narrative" | "temporal", ...], '
        '"cited": [<indices of evidence items actually cited>]}'
    )
    user = (
        f"Whitespace Candidate (JSON):\n{candidate_json}\n\n"
        f"Evidence items (JSON array; cite by array index):\n{evidence_json}"
    )
    return system, user
