"""Tests for the ideonomy sync script and deterministic tuple picker."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from noosphere.ideonomy.picker import pick_tuple, tuple_bodies

REPO_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_CLONE = Path("/home/user/latentwill/ideonomy-skill")
SECTIONS = ("operators", "organons", "dimension-prompts")


def _load_sync_module():
    spec = importlib.util.spec_from_file_location(
        "sync_ideonomy", REPO_ROOT / "scripts" / "sync_ideonomy.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_ideonomy"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def synced_catalog(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not UPSTREAM_CLONE.is_dir():
        pytest.skip(f"upstream clone not available at {UPSTREAM_CLONE}")
    sync_mod = _load_sync_module()
    dest = tmp_path_factory.mktemp("vendor") / "ideonomy"
    sync_mod.main(["--source", str(UPSTREAM_CLONE), "--dest", str(dest)])
    return dest


class TestSync:
    def test_produces_expected_structure(self, synced_catalog: Path) -> None:
        for section in SECTIONS:
            section_dir = synced_catalog / section
            assert section_dir.is_dir()
            assert list(section_dir.glob("*.md")), f"{section} has no method files"

    def test_upstream_hash_matches_source_head(self, synced_catalog: Path) -> None:
        import subprocess

        upstream = (synced_catalog / "UPSTREAM").read_text().strip()
        head = subprocess.run(
            ["git", "-C", str(UPSTREAM_CLONE), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert upstream == head
        assert len(upstream) == 40
        assert all(c in "0123456789abcdef" for c in upstream)

    def test_resync_is_idempotent(self, synced_catalog: Path) -> None:
        sync_mod = _load_sync_module()
        before = sorted(p.relative_to(synced_catalog) for p in synced_catalog.rglob("*"))
        sync_mod.main(["--source", str(UPSTREAM_CLONE), "--dest", str(synced_catalog)])
        after = sorted(p.relative_to(synced_catalog) for p in synced_catalog.rglob("*"))
        assert before == after

    def test_checked_in_vendor_catalog_exists(self) -> None:
        vendored = REPO_ROOT / "vendor" / "ideonomy"
        for section in SECTIONS:
            assert (vendored / section).is_dir()
        assert (vendored / "UPSTREAM").is_file()


class TestPickTuple:
    def test_same_seed_same_tuple(self, synced_catalog: Path) -> None:
        a = pick_tuple("run1:gap1:0", synced_catalog)
        b = pick_tuple("run1:gap1:0", synced_catalog)
        assert a == b

    def test_different_seed_different_tuple(self, synced_catalog: Path) -> None:
        tuples = {
            (
                tuple(t.operators),
                t.organon,
                tuple(t.dimension_prompts),
            )
            for t in (
                pick_tuple(f"run1:gap1:{attempt}", synced_catalog)
                for attempt in range(10)
            )
        }
        assert len(tuples) > 1

    def test_tuple_shape_matches_upstream_defaults(self, synced_catalog: Path) -> None:
        t = pick_tuple("seed", synced_catalog)
        assert len(t.operators) == 2
        assert len(set(t.operators)) == 2
        assert isinstance(t.organon, str)
        assert len(t.dimension_prompts) == 3
        assert len(set(t.dimension_prompts)) == 3
        assert t.seed == "seed"

    def test_custom_sizes(self, synced_catalog: Path) -> None:
        t = pick_tuple("seed", synced_catalog, n_operators=3, n_dims=5)
        assert len(t.operators) == 3
        assert len(t.dimension_prompts) == 5

    def test_picked_names_exist_in_catalog(self, synced_catalog: Path) -> None:
        t = pick_tuple("run2:gap9:1", synced_catalog)
        for name in t.operators:
            assert (synced_catalog / "operators" / f"{name}.md").is_file()
        assert (synced_catalog / "organons" / f"{t.organon}.md").is_file()
        for name in t.dimension_prompts:
            assert (synced_catalog / "dimension-prompts" / f"{name}.md").is_file()

    def test_missing_catalog_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            pick_tuple("seed", tmp_path / "nowhere")


class TestTupleBodies:
    def test_contains_each_picked_file_content(self, synced_catalog: Path) -> None:
        t = pick_tuple("run1:gap1:0", synced_catalog)
        bodies = tuple_bodies(t, synced_catalog)
        picked_paths = [
            *(synced_catalog / "operators" / f"{n}.md" for n in t.operators),
            synced_catalog / "organons" / f"{t.organon}.md",
            *(
                synced_catalog / "dimension-prompts" / f"{n}.md"
                for n in t.dimension_prompts
            ),
        ]
        for path in picked_paths:
            assert path.read_text().rstrip("\n") in bodies

    def test_has_section_headers_and_seed(self, synced_catalog: Path) -> None:
        t = pick_tuple("run1:gap1:0", synced_catalog)
        bodies = tuple_bodies(t, synced_catalog)
        assert f"seed: {t.seed}" in bodies
        for name in t.operators:
            assert f"----- OPERATOR: {name} -----" in bodies
        assert f"----- ORGANON: {t.organon} -----" in bodies
        for name in t.dimension_prompts:
            assert f"----- DIMENSION-PROMPT: {name} -----" in bodies
