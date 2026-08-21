# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Sync the ideonomy method catalog into vendor/ideonomy/.

Copies methods/{operators,organons,dimension-prompts} from a local clone of
latentwill/ideonomy-skill (the ideonomy-rich variant) and records the source
repo's HEAD commit in vendor/ideonomy/UPSTREAM so the vendored catalog is
traceable to an exact upstream revision.

Usage:
  uv run scripts/sync_ideonomy.py
  uv run scripts/sync_ideonomy.py --source /path/to/ideonomy-skill
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_SOURCE = Path("/home/user/latentwill/ideonomy-skill")
SECTIONS = ("operators", "organons", "dimension-prompts")


def find_methods_dir(source: Path) -> Path:
    """Locate the methods/ catalog under the source dir."""
    candidates = (
        source / "ideonomy-rich" / "methods",
        source / "methods",
        source,
    )
    for candidate in candidates:
        if all((candidate / section).is_dir() for section in SECTIONS):
            return candidate
    raise SystemExit(
        f"error: no methods catalog ({'/'.join(SECTIONS)}) found under {source}"
    )


def upstream_commit(source: Path) -> str:
    """HEAD commit hash of the git repo containing the source dir."""
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def sync(source: Path, dest: Path) -> dict[str, int]:
    methods = find_methods_dir(source)
    commit = upstream_commit(source)

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    counts: dict[str, int] = {}
    for section in SECTIONS:
        section_src = methods / section
        section_dest = dest / section
        shutil.copytree(section_src, section_dest)
        counts[section] = sum(1 for _ in section_dest.rglob("*.md"))

    (dest / "UPSTREAM").write_text(commit + "\n")
    return counts


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"local clone of latentwill/ideonomy-skill (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=repo_root / "vendor" / "ideonomy",
        help="destination catalog dir (default: <repo>/vendor/ideonomy)",
    )
    args = parser.parse_args(argv)

    if not args.source.is_dir():
        raise SystemExit(f"error: source dir not found: {args.source}")

    counts = sync(args.source, args.dest)
    total = sum(counts.values())
    per_section = ", ".join(f"{n} {s}" for s, n in counts.items())
    print(f"synced {total} method files into {args.dest} ({per_section})")
    print(f"UPSTREAM = {(args.dest / 'UPSTREAM').read_text().strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
