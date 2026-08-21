"""Deterministic ideonomy method-tuple picker.

Mirrors upstream bin/pick semantics (latentwill/ideonomy-skill): a tuple is
n_operators operators + exactly 1 organon + n_dims dimension-prompts (upstream
default 2/1/3), drawn from the flat *.md catalogs under the vendored methods
dirs, skipping README.md and files whose names start with "_". Method names
are file stems. Same seed -> same tuple (random.Random(seed) over sorted
listings), matching upstream's --seed reproducible mode.
"""

from __future__ import annotations

import random
from pathlib import Path

from noosphere.models import IdeonomyTuple

OPERATORS_DIR = "operators"
ORGANONS_DIR = "organons"
DIMENSION_PROMPTS_DIR = "dimension-prompts"

_SECTION_LABELS = {
    OPERATORS_DIR: "OPERATOR",
    ORGANONS_DIR: "ORGANON",
    DIMENSION_PROMPTS_DIR: "DIMENSION-PROMPT",
}


def _catalog_names(catalog_dir: Path, section: str) -> list[str]:
    section_dir = catalog_dir / section
    if not section_dir.is_dir():
        raise FileNotFoundError(
            f"catalog section missing: {section_dir} (run scripts/sync_ideonomy.py?)"
        )
    names = sorted(
        p.stem
        for p in section_dir.rglob("*.md")
        if p.name != "README.md" and not p.name.startswith("_")
    )
    if not names:
        raise ValueError(f"catalog section empty: {section_dir}")
    return names


def pick_tuple(
    seed: str, catalog_dir: Path, n_operators: int = 2, n_dims: int = 3
) -> IdeonomyTuple:
    """Deterministically pick a method tuple from the vendored catalog."""
    rng = random.Random(seed)
    operators = rng.sample(_catalog_names(catalog_dir, OPERATORS_DIR), n_operators)
    organon = rng.choice(_catalog_names(catalog_dir, ORGANONS_DIR))
    dimension_prompts = rng.sample(
        _catalog_names(catalog_dir, DIMENSION_PROMPTS_DIR), n_dims
    )
    return IdeonomyTuple(
        operators=operators,
        organon=organon,
        dimension_prompts=dimension_prompts,
        seed=seed,
    )


def _method_body(catalog_dir: Path, section: str, name: str) -> str:
    path = catalog_dir / section / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"method file missing from catalog: {path}")
    return path.read_text()


def tuple_bodies(t: IdeonomyTuple, catalog_dir: Path) -> str:
    """Concatenate the picked method files' contents with section headers."""
    picks: list[tuple[str, str]] = [
        *((OPERATORS_DIR, name) for name in t.operators),
        (ORGANONS_DIR, t.organon),
        *((DIMENSION_PROMPTS_DIR, name) for name in t.dimension_prompts),
    ]
    parts = [
        f"=== IDEONOMY METHOD TUPLE (seed: {t.seed}) ===",
        "OPERATORS: " + ", ".join(t.operators),
        "ORGANON: " + t.organon,
        "DIMENSION-PROMPTS: " + ", ".join(t.dimension_prompts),
    ]
    for section, name in picks:
        parts.append(f"\n----- {_SECTION_LABELS[section]}: {name} -----")
        parts.append(_method_body(catalog_dir, section, name).rstrip("\n"))
    return "\n".join(parts) + "\n"
