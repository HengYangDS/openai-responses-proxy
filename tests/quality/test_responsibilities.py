"""Exact scope, ownership, and failure contracts for repository quality."""

from __future__ import annotations

import json
import subprocess
import tomllib
from typing import TYPE_CHECKING
from typing import Literal

import pytest

from tests.quality.fixtures import ROOT
from tools.quality import governance
from tools.quality import responsibilities

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.fixture
def declarations() -> dict[str, dict[str, object]]:
    """Return the first role, scope and concern, which a test edits in place in the document."""
    return {
        "roles": {
            "id": "source",
            "description": "Source",
            "owner": "product",
            "consumer": "installed runtime",
            "source_of_truth": "tracked source",
            "change_condition": "the product contract changes",
            "dependency_direction": "interfaces toward implementation",
            "retirement_condition": "no runtime or release consumer remains",
            "prefixes": ["src/"],
        },
        "scopes": {"id": "all", "roles": ["source"]},
        "concerns": {
            "id": "syntax",
            "owner": "native",
            "scope": "all",
            "session": "quick",
            "configuration": ["policy.toml"],
            **dict.fromkeys(responsibilities.RATIONALE_FIELDS, "reviewed"),
        },
    }


@pytest.fixture
def policy_document(declarations: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "owner": "quality",
        "purpose": "admission",
        **{section: [declaration] for section, declaration in declarations.items()},
    }


def audit_document(
    document: dict[str, object],
    tmp_path: Path,
    mocker: MockerFixture,
    *,
    paths: tuple[str, ...] = ("src/a.py",),
) -> responsibilities.AuditReport:
    policy = tmp_path / "policy.toml"
    policy.write_text("")
    mocker.patch.object(responsibilities.tomllib, "loads", return_value=document)
    mocker.patch.object(responsibilities, "_tracked_paths", return_value=paths)
    return responsibilities.audit(tmp_path, policy)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("roles", []),
        ("scopes", []),
        ("concerns", []),
        ("roles", 1),
        ("concerns", "bad"),
    ],
)
def test_empty_and_malformed_inventory_cannot_pass(
    field: Literal["schema_version", "roles", "scopes", "concerns"],
    value: object,
    policy_document: dict[str, object],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    policy_document[field] = value
    report = audit_document(policy_document, tmp_path, mocker)
    assert not report["ok"]
    assert report["errors"]


def test_empty_tracked_inventory_does_not_prove_coverage(
    policy_document: dict[str, object], tmp_path: Path, mocker: MockerFixture
) -> None:
    report = audit_document(policy_document, tmp_path, mocker, paths=())
    assert not report["ok"]
    assert "responsibility_map_tracked_inventory_empty" in report["errors"]


@pytest.mark.parametrize(("field", "value"), [("owner", ""), ("purpose", None)])
def test_map_identity_requires_meaningful_text(
    field: Literal["owner", "purpose"],
    value: str | None,
    policy_document: dict[str, object],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    policy_document[field] = value
    assert not audit_document(policy_document, tmp_path, mocker)["ok"]


@pytest.mark.parametrize(
    "field",
    [
        "owner",
        "consumer",
        "source_of_truth",
        "change_condition",
        "dependency_direction",
        "retirement_condition",
    ],
)
def test_each_role_declares_its_complete_lifecycle(
    field: Literal[
        "owner",
        "consumer",
        "source_of_truth",
        "change_condition",
        "dependency_direction",
        "retirement_condition",
    ],
    *,
    declarations: dict[str, dict[str, object]],
    policy_document: dict[str, object],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    declarations["roles"].pop(field, None)

    report = audit_document(policy_document, tmp_path, mocker)

    assert f"responsibility_map_role_field_missing:source:{field}" in report["errors"]


@pytest.mark.parametrize("collection", ["roles", "scopes", "concerns"])
@pytest.mark.parametrize("change", ["non-table", "empty-id", "duplicate"])
def test_every_declaration_has_one_unique_identity(
    collection: Literal["roles", "scopes", "concerns"],
    change: Literal["non-table", "empty-id", "duplicate"],
    *,
    declarations: dict[str, dict[str, object]],
    policy_document: dict[str, object],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    entries = policy_document[collection]
    assert isinstance(entries, list)
    if change == "non-table":
        entries.append(1)
    elif change == "empty-id":
        declarations[collection]["id"] = ""
    else:
        entries.append(dict(declarations[collection]))
    assert not audit_document(policy_document, tmp_path, mocker)["ok"]


@pytest.mark.parametrize(
    ("field", "value"), [("description", ""), ("prefixes", []), ("prefixes", [""]), ("files", [3])]
)
def test_role_selectors_are_explicit_and_valid(
    field: Literal["description", "prefixes", "files"],
    value: object,
    *,
    declarations: dict[str, dict[str, object]],
    policy_document: dict[str, object],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    declarations["roles"][field] = value
    assert not audit_document(policy_document, tmp_path, mocker)["ok"]


@pytest.mark.parametrize("roles", [[], ["source", "source"], ["absent"], [""]])
def test_scopes_reference_nonempty_unique_roles(
    roles: object,
    *,
    declarations: dict[str, dict[str, object]],
    policy_document: dict[str, object],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    declarations["scopes"]["roles"] = roles
    assert not audit_document(policy_document, tmp_path, mocker)["ok"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("owner", ""), ("scope", "absent"), ("configuration", []), ("configuration", ["missing"])],
)
def test_concerns_have_live_owners_and_configuration(
    field: Literal["owner", "scope", "configuration"],
    value: object,
    *,
    declarations: dict[str, dict[str, object]],
    policy_document: dict[str, object],
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    declarations["concerns"][field] = value
    assert not audit_document(policy_document, tmp_path, mocker)["ok"]


def test_unowned_carrier_is_visible(
    policy_document: dict[str, object], tmp_path: Path, mocker: MockerFixture
) -> None:
    report = audit_document(policy_document, tmp_path, mocker, paths=("unknown.bin",))
    assert report["errors"] == ["responsibility_map_unowned_carrier:unknown.bin"]


def test_tracked_code_intelligence_paths_are_not_silently_removed(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch.object(
        responsibilities.subprocess,
        "run",
        return_value=subprocess.CompletedProcess(
            [], 0, stdout=b"src/a.py\0.codebase-memory/tracked.json\0"
        ),
    )
    assert responsibilities._tracked_paths(tmp_path) == (
        ".codebase-memory/tracked.json",
        "src/a.py",
    )


def test_gate_cli_returns_json_and_preserves_failed_exit(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    report = {"ok": True, "errors": []}
    mocker.patch.object(responsibilities, "audit", return_value=report)
    responsibilities.main()
    assert json.loads(capsys.readouterr().out) == report
    report["ok"] = False
    with pytest.raises(SystemExit) as stopped:
        responsibilities.main()
    assert stopped.value.code == 1
    with pytest.raises(SystemExit, match="accepts no arguments"):
        responsibilities.main(("unexpected",))


@pytest.mark.parametrize(
    ("scope", "suffixes"),
    [
        ("prettier-formatted", (".md", ".yaml", ".yml")),
        ("toml-formatted", (".toml",)),
    ],
)
def test_formatter_inputs_have_complete_declared_roles(
    scope: Literal["prettier-formatted", "toml-formatted"], suffixes: tuple[str, ...]
) -> None:
    report = responsibilities.audit()
    assert report["ok"], report["errors"]
    policy = tomllib.loads(responsibilities.MAP.read_text(encoding="utf-8"))
    roles = set(next(item["roles"] for item in policy["scopes"] if item["id"] == scope))
    declared = {
        path
        for path, role in report["assignments"].items()
        if role in roles
        and path.endswith(suffixes)
        and not path.startswith("openspec/changes/archive/")
    }

    selected = set(governance._tracked_current(suffixes))

    assert selected
    assert selected == declared, {
        "undeclared_inputs": sorted(selected - declared),
        "unexecuted_inputs": sorted(declared - selected),
    }


class TestResponsibilityMap:
    def test_responsibility_map_covers_every_carrier_exactly_once(self) -> None:
        report = responsibilities.audit()

        assert report["errors"] == []
        assert report["ok"] is True
        assert len(report["assignments"]) > 1000

    def test_responsibility_map_rejects_missing_concern_rationale(self, tmp_path: Path) -> None:
        source = (ROOT / ".config/checks/responsibilities/policy.toml").read_text(encoding="utf-8")
        malformed = source.replace(
            'risk_model = "Python correctness, import, modernization, security, performance, '
            'and maintainability defects escape review."\n',
            "",
            1,
        )
        policy = tmp_path / "policy.toml"
        policy.write_text(malformed, encoding="utf-8")

        report = responsibilities.audit(ROOT, policy)

        assert "responsibility_map_concern_field_missing:python-lint:risk_model" in report["errors"]

    def test_responsibility_map_rejects_overlapping_roles(self, tmp_path: Path) -> None:
        source = (ROOT / ".config/checks/responsibilities/policy.toml").read_text(encoding="utf-8")
        malformed = source.replace(
            'prefixes = ["tools/"]\n',
            'prefixes = ["tools/", "src/"]\n',
            1,
        )
        policy = tmp_path / "policy.toml"
        policy.write_text(malformed, encoding="utf-8")

        report = responsibilities.audit(ROOT, policy)

        assert any(
            error.startswith("responsibility_map_multiple_roles:src/") for error in report["errors"]
        )
