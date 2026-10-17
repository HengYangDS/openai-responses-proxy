"""Decision Record registration and prose-boundary contracts."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.quality.fixtures import ROOT
from tools.quality.repository.decisions import decision_record_gaps


@pytest.mark.parametrize(
    "defect",
    [
        "complete",
        "metadata-navigation",
        "yaml-frontmatter",
        "yaml-frontmatter-title",
        "missing-register",
        "duplicate-sequence",
        "title",
        "status",
        "date",
        "section",
    ],
)
def test_decision_register_requires_unique_complete_records(tmp_path, defect):
    directory = tmp_path / "docs/decisions"
    directory.mkdir(parents=True)
    register = directory / "decision-register.md"
    if defect == "missing-register":
        assert decision_record_gaps(tmp_path) == ["decision_record_register_missing"]
        return
    register.write_text("[decision](dr-0001-owner.md)\n")
    text = "# DR-0001: Owner\n- Status: accepted\n- Date: 2026-01-01\n## Context\n## Decision\n## Consequences\n## Revisit Trigger\n"
    replacements = {
        "title": ("# DR-0001", "# Other"),
        "status": ("accepted", "invalid"),
        "date": ("- Date: 2026-01-01", ""),
        "section": ("## Context", ""),
    }
    if defect == "metadata-navigation":
        (directory / "README.md").write_text("# Decisions\n\n[Register](decision-register.md)\n")
        text = (
            "<!--\n---\nsubject: decision-owner\nrole: decision\n"
            "state: canonical\n---\n-->\n\n" + text
        )
    if defect.startswith("yaml-frontmatter"):
        text = "---\nsubject: decision-owner\nrole: decision\nstate: canonical\n---\n\n" + text
        if defect == "yaml-frontmatter-title":
            text = text.replace("# DR-0001", "# Other")
    if defect in replacements:
        text = text.replace(*replacements[defect])
    (directory / "dr-0001-owner.md").write_text(text)
    if defect == "duplicate-sequence":
        (directory / "dr-0001-other.md").write_text(text)
    if defect in {"complete", "metadata-navigation", "yaml-frontmatter"}:
        assert decision_record_gaps(tmp_path) == []
        return
    expected = {
        "duplicate-sequence": "sequence_duplicate",
        "title": "title_invalid",
        "yaml-frontmatter-title": "title_invalid",
        "status": "status_invalid",
        "date": "date_invalid",
        "section": "section_missing",
    }
    assert any(expected[defect] in gap for gap in decision_record_gaps(tmp_path))


def test_decision_records_have_one_register_and_semantic_names() -> None:
    assert decision_record_gaps(ROOT) == []


def test_decision_record_gate_rejects_numeric_and_unregistered_names() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        decisions = root / "docs/decisions"
        decisions.mkdir(parents=True)
        (decisions / "decision-register.md").write_text("# Decision Records\n", encoding="utf-8")
        (decisions / "0001-vague.md").write_text("# ADR-0001: Vague\n", encoding="utf-8")
        valid = decisions / "dr-0002-release-trust.md"
        valid.write_text(
            "# DR-0002: Release Trust\n\n"
            "- Status: accepted\n"
            "- Date: 2026-08-07\n\n"
            "## Context\n\nContext.\n\n"
            "## Decision\n\nDecision.\n\n"
            "## Consequences\n\nConsequences.\n\n"
            "## Revisit Trigger\n\nTrigger.\n",
            encoding="utf-8",
        )

        gaps = decision_record_gaps(root)

    assert "decision_record_name_invalid:docs/decisions/0001-vague.md" in gaps
    assert "decision_record_unregistered:docs/decisions/dr-0002-release-trust.md" in gaps


def test_decision_record_gate_requires_unique_registration_without_history_ratchets() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        decisions = root / "docs/decisions"
        decisions.mkdir(parents=True)
        first = decisions / "dr-0001-boundary.md"
        third = decisions / "dr-0003-release.md"
        body = (
            "- Status: accepted\n"
            "- Date: 2026-08-07\n\n"
            "## Context\n\nContext.\n\n"
            "## Decision\n\nDecision.\n\n"
            "## Consequences\n\nConsequences.\n\n"
            "## Revisit Trigger\n\nTrigger.\n"
        )
        first.write_text("# DR-0001: Boundary\n\n" + body, encoding="utf-8")
        third.write_text("# DR-0003: Release\n\n" + body, encoding="utf-8")
        (decisions / "decision-register.md").write_text(
            "# Decision Records\n\n"
            "[DR-0001](dr-0001-boundary.md)\n"
            "[duplicate](dr-0001-boundary.md)\n"
            "[DR-0003](dr-0003-release.md)\n",
            encoding="utf-8",
        )

        gaps = decision_record_gaps(root)

    assert "decision_record_sequence_gap:0002" not in gaps
    assert "decision_record_registration_duplicate:docs/decisions/dr-0001-boundary.md" in gaps
