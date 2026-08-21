# Academic Noosphere

A local macOS app for a single researcher that surveys an academic field, builds an
author/topic/citation graph from scholarly APIs, surfaces literature gaps, and
recommends co-authors.

## Status: planning (wayfinding)

This effort is being planned as a **wayfinder map** on the issue tracker:

- **Map**: [Wayfinder map: Academic Noosphere — Mac-local research mapping app](https://github.com/jacksodj/academic-noosphere/issues/1)
- Open decision tickets are the map's child issues. A ticket is on the **frontier**
  when it is open, unassigned, and everything on its `Blocked by:` line is closed.
- Work a ticket by invoking `/wayfinder` with the map URL (mattpocock-skills plugin).

## Core architectural constraints (from the reference docs)

- **AgentCore Web Search is discovery, never the graph source.** It returns snippets
  only (no authors, no citation edges) and its acceptable-use terms prohibit bulk
  extraction and building a database from Search Results.
- **The graph comes from scholarly APIs.** OpenAlex is primary (CC0, disambiguated
  author IDs, citation edges, Topics taxonomy); Semantic Scholar, Crossref, arXiv,
  PubMed are secondary. Every stored node/edge must trace to a DOI or OpenAlex ID.
- **Local-first.** The app runs on the Mac; AWS is used only where unavoidable
  (the Web Search connector lives behind an AgentCore Gateway).

See [`docs/reference-notes.md`](docs/reference-notes.md) for the condensed reference
material every planning ticket should be read against.
