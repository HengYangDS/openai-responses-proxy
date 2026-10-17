"""Semantic carrier names without compatibility aliases."""

from __future__ import annotations

from tests.quality.fixtures import ROOT
from tests.quality.fixtures import repository as _test_repository
from tools.quality.repository.names import semantic_name_gaps


def test_tracked_project_files_follow_semantic_type_grammars() -> None:
    assert semantic_name_gaps(ROOT) == []


def test_semantic_name_gate_rejects_numeric_and_cross_language_grammar() -> None:
    with _test_repository(
        (
            "src/valid_name.py",
            "src/invalid-name.py",
            "scripts/release/check_release.sh",
            "docs/2026-plan.md",
        )
    ) as root:
        gaps = semantic_name_gaps(root)

    assert "semantic_name_invalid:python:src/invalid-name.py" in gaps
    assert "semantic_name_invalid:shell:scripts/release/check_release.sh" in gaps
    assert "semantic_name_invalid:markdown:docs/2026-plan.md" in gaps


def test_semantic_name_gate_exempts_only_openspec_history() -> None:
    with _test_repository(
        (
            "docs/2026-plan.md",
            "openspec/changes/archive/2026-08-07-release/specs/product/spec.md",
        )
    ) as root:
        gaps = semantic_name_gaps(root)

    assert gaps == ["semantic_name_invalid:markdown:docs/2026-plan.md"]


def test_semantic_name_gate_accepts_official_pyinstaller_hook_modules() -> None:
    with _test_repository(("tools/release/hooks/hook-ctypes.py",)) as root:
        gaps = semantic_name_gaps(root)

    assert gaps == []
