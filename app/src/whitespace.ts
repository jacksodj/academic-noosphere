/**
 * Structured view of a WhitespaceCandidate, recovered from the detector's
 * description templates (analysis/whitespace.py). The templates are the only
 * place the human-readable topic/community names live in v1, so both Triage
 * and the Explorer parse them; anything unparsed falls back gracefully.
 */

import type { WhitespaceCandidate } from "./types";

export interface ParsedCandidate {
  /** Human title: topic name for thin cells, "A ↔ B" for bridges. */
  title: string;
  /** "ws000"-style short id (last dash segment of the whitespace_id). */
  shortId: string;
  observed: number | null;
  expected: number | null;
  community: string | null;
}

const THIN_CELL = /^Thin cell: topic (.+?) in community (\d+) holds (\d+) works? vs ([\d.]+) expected/;
const BRIDGE = /^Bridge whitespace between community \d+ \((.+?)\) and community \d+ \((.+?)\):/;

export function parseCandidate(c: WhitespaceCandidate): ParsedCandidate {
  const shortId = c.whitespace_id.split("-").pop() ?? c.whitespace_id;
  const thin = THIN_CELL.exec(c.description);
  if (thin) {
    return {
      title: thin[1],
      shortId,
      community: thin[2],
      observed: Number(thin[3]),
      expected: Number(thin[4]),
    };
  }
  const bridge = BRIDGE.exec(c.description);
  if (bridge) {
    return {
      title: `${bridge[1]} ↔ ${bridge[2]}`,
      shortId,
      community:
        c.community_a !== null && c.community_b !== null
          ? `${c.community_a} ↔ ${c.community_b}`
          : null,
      observed: null,
      expected: null,
    };
  }
  return {
    title: c.description.length > 70 ? `${c.description.slice(0, 70)}…` : c.description,
    shortId,
    community: c.community_a !== null ? String(c.community_a) : null,
    observed: null,
    expected: null,
  };
}

/** One-line stat: "0 works where ~82 expected". */
export function candidateStat(p: ParsedCandidate): string | null {
  if (p.observed === null || p.expected === null) return null;
  return `${p.observed} works where ~${Math.round(p.expected)} expected`;
}

/** Sort key: most surprising hole first (largest expected-vs-observed gap). */
export function surpriseScore(c: WhitespaceCandidate): number {
  const p = parseCandidate(c);
  if (p.expected === null) return c.sparsity_score; // bridges: fall back
  return p.expected - (p.observed ?? 0);
}
