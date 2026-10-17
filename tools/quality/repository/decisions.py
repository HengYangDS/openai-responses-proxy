"""Validate the repository Decision Record register and file grammar."""

from __future__ import annotations

import re
from pathlib import Path

from rich.markdown import Markdown

_DECISION_RECORD = re.compile(
    r"dr-(?P<sequence>[0-9]{4})-(?P<description>[a-z0-9]+(?:-[a-z0-9]+)*)\.md"
)
_DECISION_STATUSES = frozenset({"accepted", "amended", "deprecated", "proposed", "superseded"})
_DECISION_REGISTER = "decision-register.md"


def _record_body(text: str) -> str:
    """Select Markdown prose; the shared metadata gate owns YAML validation."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return text
    try:
        end = lines.index("---", 1)
    except ValueError:
        return text
    return "\n".join(lines[end + 1 :])


def decision_record_gaps(root: Path) -> list[str]:
    """Validate the one Decision Record register and its semantic file grammar."""
    directory = root / "docs/decisions"
    register = directory / _DECISION_REGISTER
    if not directory.is_dir() or not register.is_file():
        return ["decision_record_register_missing"]
    gaps: list[str] = []
    records: list[tuple[int, Path]] = []
    for path in sorted(directory.glob("*.md")):
        if path.name in {_DECISION_REGISTER, "README.md"}:
            continue
        match = _DECISION_RECORD.fullmatch(path.name)
        if match is None:
            gaps.append(f"decision_record_name_invalid:{path.relative_to(root).as_posix()}")
            continue
        records.append((int(match.group("sequence")), path))
    sequences = [sequence for sequence, _ in records]
    if len(sequences) != len(set(sequences)):
        gaps.append("decision_record_sequence_duplicate")
    register_text = register.read_text(encoding="utf-8")
    required_sections = (
        "## Context",
        "## Decision",
        "## Consequences",
        "## Revisit Trigger",
    )
    for sequence, path in records:
        text = _record_body(path.read_text(encoding="utf-8"))
        tokens = [token for token in Markdown(text).parsed if token.type != "html_block"]
        expected_title = f"DR-{sequence:04d}: "
        if (
            len(tokens) < 2
            or tokens[0].type != "heading_open"
            or tokens[0].tag != "h1"
            or not tokens[1].content.startswith(expected_title)
        ):
            gaps.append(f"decision_record_title_invalid:{path.relative_to(root).as_posix()}")
        status = re.search(r"^- Status: ([a-z]+)$", text, re.MULTILINE)
        if status is None or status.group(1) not in _DECISION_STATUSES:
            gaps.append(f"decision_record_status_invalid:{path.relative_to(root).as_posix()}")
        if re.search(r"^- Date: [0-9]{4}-[0-9]{2}-[0-9]{2}$", text, re.MULTILINE) is None:
            gaps.append(f"decision_record_date_invalid:{path.relative_to(root).as_posix()}")
        gaps.extend(
            f"decision_record_section_missing:{path.relative_to(root).as_posix()}:{section[3:]}"
            for section in required_sections
            if section not in text
        )
        registrations = register_text.count(f"({path.name})")
        if registrations == 0:
            gaps.append(f"decision_record_unregistered:{path.relative_to(root).as_posix()}")
        elif registrations > 1:
            gaps.append(
                f"decision_record_registration_duplicate:{path.relative_to(root).as_posix()}"
            )
    return sorted(gaps)
