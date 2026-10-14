"""Validate the repository quality responsibility map."""

from __future__ import annotations

import json
import subprocess
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[2]
MAP = ROOT / ".config/checks/responsibilities/policy.toml"
RATIONALE_FIELDS = (
    "risk_model",
    "measurement",
    "false_positive_cost",
    "remediation",
    "review_condition",
)

ROLE_GOVERNANCE_FIELDS = (
    "owner",
    "consumer",
    "source_of_truth",
    "change_condition",
    "dependency_direction",
    "retirement_condition",
)


def _strings(value: object, *, field: str, errors: list[str]) -> tuple[str, ...]:
    """Return one non-empty unique string sequence or record a precise error."""
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        errors.append(f"responsibility_map_{field}_must_be_nonempty_string_list")
        return ()
    values = tuple(item for item in value if isinstance(item, str))
    if len(values) != len(set(values)):
        errors.append(f"responsibility_map_{field}_must_be_unique")
    return values


def _tracked_paths(root: Path) -> tuple[str, ...]:
    """Return tracked and pending repository paths without ignored host state."""
    completed = subprocess.run(
        (
            "git",
            "--git-dir=.git",
            "--work-tree=.",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ),
        cwd=root,
        check=True,
        capture_output=True,
    )
    return tuple(sorted(path.decode() for path in completed.stdout.split(b"\0") if path))


def _role_matches(path: str, role: tuple[tuple[str, ...], tuple[str, ...]]) -> bool:
    """Return whether one path belongs to the role's exact positive scope."""
    files, prefixes = role
    return path in files or any(path.startswith(prefix) for prefix in prefixes)


def _declarations(value: object, *, field: str, errors: list[str]) -> dict[str, dict[str, object]]:
    """Index one nonempty declaration inventory by unique textual identity."""
    if not isinstance(value, list) or not value:
        errors.append(f"responsibility_map_{field}_must_be_nonempty_list")
        return {}
    kind = field.removesuffix("s")
    declarations: dict[str, dict[str, object]] = {}
    for index, raw in enumerate(value):
        entry: dict[str, object] = {}
        if isinstance(raw, dict) and all(isinstance(key, str) for key in raw):
            entry = dict(raw)
        else:
            errors.append(f"responsibility_map_{kind}_{index}_must_be_table")
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(f"responsibility_map_{kind}_{index}_id_must_be_nonempty_string")
        elif identifier in declarations:
            errors.append(f"responsibility_map_duplicate_{kind}:{identifier}")
        else:
            declarations[identifier] = entry
    return declarations


class AuditReport(TypedDict):
    """Exact carrier assignments and diagnostics from the responsibility map."""

    ok: bool
    errors: list[str]
    roles: list[str]
    scopes: list[str]
    concerns: list[str]
    assignments: dict[str, str]


def audit(root: Path = ROOT, policy_path: Path = MAP) -> AuditReport:
    """Return exact ownership gaps for carriers, scopes, concerns, and configuration."""
    policy = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    version = policy.get("schema_version")
    if type(version) is not int or version != 1:
        errors.append("responsibility_map_schema_version_must_be_1")
    for field in ("owner", "purpose"):
        value = policy.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"responsibility_map_{field}_must_be_nonempty_string")

    roles: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for identifier, role in _declarations(
        policy.get("roles"), field="roles", errors=errors
    ).items():
        description = role.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append(f"responsibility_map_role_description_missing:{identifier}")
        for field in ROLE_GOVERNANCE_FIELDS:
            value = role.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"responsibility_map_role_field_missing:{identifier}:{field}")
        files, prefixes = (
            _strings(role[field], field=f"role_{identifier}_{field}", errors=errors)
            if field in role
            else ()
            for field in ("files", "prefixes")
        )
        if not files and not prefixes:
            errors.append(f"responsibility_map_role_scope_missing:{identifier}")
        roles[identifier] = (files, prefixes)

    scopes: dict[str, tuple[str, ...]] = {}
    for identifier, scope in _declarations(
        policy.get("scopes"), field="scopes", errors=errors
    ).items():
        role_ids = _strings(scope.get("roles"), field=f"scope_{identifier}_roles", errors=errors)
        errors.extend(
            f"responsibility_map_unknown_role:{identifier}:{role_id}"
            for role_id in role_ids
            if role_id not in roles
        )
        scopes[identifier] = role_ids

    concerns: dict[str, dict[str, object]] = {}
    for identifier, concern in _declarations(
        policy.get("concerns"), field="concerns", errors=errors
    ).items():
        for field in ("owner", "scope", "session", *RATIONALE_FIELDS):
            value = concern.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.extend([f"responsibility_map_concern_field_missing:{identifier}:{field}"])
        scope = concern.get("scope")
        if isinstance(scope, str) and scope not in roles and scope not in scopes:
            errors.append(f"responsibility_map_unknown_scope:{identifier}:{scope}")
        configurations = _strings(
            concern.get("configuration"),
            field=f"concern_{identifier}_configuration",
            errors=errors,
        )
        errors.extend(
            f"responsibility_map_missing_configuration:{identifier}:{configuration}"
            for configuration in configurations
            if not (root / configuration).is_file()
        )
        concerns[identifier] = concern

    assignments: dict[str, str] = {}
    tracked = _tracked_paths(root)
    if not tracked:
        errors.append("responsibility_map_tracked_inventory_empty")
    for path in tracked:
        owners = [identifier for identifier, role in roles.items() if _role_matches(path, role)]
        if not owners:
            errors.append(f"responsibility_map_unowned_carrier:{path}")
        elif len(owners) > 1:
            errors.append(f"responsibility_map_multiple_roles:{path}:{','.join(sorted(owners))}")
        else:
            assignments[path] = owners[0]

    return {
        "ok": not errors,
        "errors": sorted(errors),
        "roles": sorted(roles),
        "scopes": sorted(scopes),
        "concerns": sorted(concerns),
        "assignments": assignments,
    }


def main(argv: Iterable[str] = ()) -> None:
    """Print the responsibility audit as stable JSON and fail on every gap."""
    if tuple(argv):
        raise SystemExit("responsibility audit accepts no arguments")
    report = audit()
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
