"""Validate positive ownership of controlled repository values."""

from __future__ import annotations

import ast
import json
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / ".config/checks/hard-coding/policy.toml"
REQUIRED_FIELDS = ("id", "kind", "owner", "invariant", "rationale", "projections")
PROJECTION_MATCHES = {"toml-key", "toml-value"}
PUBLIC_SURFACES = frozenset(
    {
        "configuration-field",
        "documentation-entrypoint",
        "environment-variable",
        "native-resource",
        "network-control-route",
        "network-provider-route",
        "public-command",
        "public-result",
        "release-artifact",
    }
)


class AuditReport(TypedDict):
    """Typed controlled-value inventory and its admission diagnostics."""

    ok: bool
    errors: list[str]
    kinds: list[str]
    controls: list[str]
    surfaces: list[str]


def _exists(root: Path, value: str) -> bool:
    """Return whether one declared file or directory owner exists."""
    return (root / value).exists()


def _python_constants(path: Path) -> dict[str, object]:
    """Read simple module constants without executing repository code."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants: dict[str, object] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = ast.literal_eval(statement.value)
        except (ValueError, TypeError):
            if isinstance(statement.value, ast.Name) and statement.value.id in constants:
                value = constants[statement.value.id]
            else:
                continue
        constants[target.id] = value
    return constants


def _toml_selection(path: Path, selector: str) -> object:
    """Return one dotted TOML selection from a projection carrier."""
    selected: object = tomllib.loads(path.read_text(encoding="utf-8"))
    for segment in selector.split("."):
        if not isinstance(selected, dict) or segment not in selected:
            raise KeyError(selector)
        selected = selected[segment]
    return selected


def _projection_errors(
    *,
    root: Path,
    identifier: str,
    owner: str,
    checks: object,
) -> list[str]:
    """Return semantic drift between one owner and its declared projections."""
    if checks is None:
        return []
    if not isinstance(checks, list) or not checks:
        return [f"hard_coding_projection_checks_invalid:{identifier}"]
    owner_path = root / owner
    if owner_path.suffix != ".py" or not owner_path.is_file():
        return [f"hard_coding_projection_owner_unsupported:{identifier}:{owner}"]
    try:
        constants = _python_constants(owner_path)
    except (OSError, SyntaxError) as error:
        return [f"hard_coding_projection_owner_invalid:{identifier}:{error}"]
    errors: list[str] = []
    for index, raw_check in enumerate(checks):
        if not isinstance(raw_check, dict):
            errors.append(f"hard_coding_projection_check_must_be_table:{identifier}:{index}")
            continue
        check = {str(key): value for key, value in raw_check.items()}
        source = check.get("source")
        target = check.get("target")
        selector = check.get("selector")
        match = check.get("match")
        if not all(isinstance(value, str) and value for value in (source, target, selector)):
            errors.append(f"hard_coding_projection_check_invalid:{identifier}:{index}")
            continue
        if match not in PROJECTION_MATCHES:
            errors.append(f"hard_coding_projection_match_invalid:{identifier}:{index}")
            continue
        assert isinstance(source, str)
        assert isinstance(target, str)
        assert isinstance(selector, str)
        if source not in constants:
            errors.append(f"hard_coding_projection_source_missing:{identifier}:{source}")
            continue
        target_path = root / target
        try:
            selected = _toml_selection(target_path, selector)
        except (OSError, KeyError, tomllib.TOMLDecodeError):
            errors.append(
                f"hard_coding_projection_selector_missing:{identifier}:{target}:{selector}"
            )
            continue
        expected = constants[source]
        matches = (
            selected == expected
            if match == "toml-value"
            else (isinstance(selected, dict) and expected in selected)
        )
        if not matches:
            errors.append(
                f"hard_coding_projection_value_mismatch:{identifier}:{source}:{target}:{selector}"
            )
    return errors


def _register_surfaces(
    identifier: str,
    value: object,
    owners: dict[str, str],
) -> list[str]:
    """Register one control's public surfaces and return ownership gaps."""
    if not isinstance(value, list) or not all(
        isinstance(surface, str) and surface for surface in value
    ):
        return [f"hard_coding_control_surfaces_invalid:{identifier}"]
    errors: list[str] = []
    for surface in value:
        if surface not in PUBLIC_SURFACES:
            errors.append(f"hard_coding_surface_unknown:{identifier}:{surface}")
        elif surface in owners:
            errors.append(f"hard_coding_surface_multiple_owners:{surface}")
        else:
            owners[surface] = identifier
    return errors


def audit(root: Path = ROOT, policy_path: Path = POLICY) -> AuditReport:
    """Return ownership and projection gaps in the controlled-value registry."""
    errors: list[str] = []
    try:
        policy = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        return {
            "ok": False,
            "errors": [f"hard_coding_policy_invalid:{error}"],
            "kinds": [],
            "controls": [],
            "surfaces": [],
        }
    version = policy.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        errors.append("hard_coding_schema_version_must_be_1")
    allowed = policy.get("allowed_kinds")
    allowed_kinds = (
        {item for item in allowed if isinstance(item, str)} if isinstance(allowed, list) else set()
    )
    if (
        not isinstance(allowed, list)
        or not allowed
        or len(allowed_kinds) != len(allowed)
        or "" in allowed_kinds
    ):
        errors.append("hard_coding_allowed_kinds_must_be_nonempty_unique_strings")
    declarations = policy.get("controls")
    if not isinstance(declarations, list) or not declarations:
        errors.append("hard_coding_controls_must_be_nonempty_list")
        declarations = []
    controls: dict[str, dict[str, object]] = {}
    owners: dict[str, str] = {}
    surface_owners: dict[str, str] = {}
    for index, raw in enumerate(declarations):
        if not isinstance(raw, dict):
            errors.append(f"hard_coding_control_must_be_table:{index}")
            continue
        control = {str(key): value for key, value in raw.items()}
        identifier = control.get("id")
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"hard_coding_control_id_invalid:{index}")
            continue
        if identifier in controls:
            errors.append(f"hard_coding_duplicate_control:{identifier}")
            continue
        controls[identifier] = control
        errors.extend(
            f"hard_coding_control_field_missing:{identifier}:{field}"
            for field in REQUIRED_FIELDS
            if field not in control
        )
        kind = control.get("kind")
        if not isinstance(kind, str) or kind not in allowed_kinds:
            errors.append(f"hard_coding_control_kind_invalid:{identifier}")
        owner = control.get("owner")
        if not isinstance(owner, str) or not owner:
            errors.append(f"hard_coding_control_owner_invalid:{identifier}")
        elif owner in owners:
            errors.append(f"hard_coding_owner_reused:{owner}:{owners[owner]}:{identifier}")
        else:
            owners[owner] = identifier
            if not _exists(root, owner):
                errors.append(f"hard_coding_owner_missing:{identifier}:{owner}")
        for field in ("rationale", "invariant"):
            value = control.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"hard_coding_control_{field}_missing:{identifier}")
        errors.extend(_register_surfaces(identifier, control.get("surfaces", []), surface_owners))
        projections = control.get("projections")
        if not isinstance(projections, list) or not projections:
            errors.append(f"hard_coding_control_projections_invalid:{identifier}")
        elif any(not isinstance(path, str) or not _exists(root, path) for path in projections):
            errors.append(f"hard_coding_control_projection_missing:{identifier}")
        if isinstance(owner, str) and owner:
            errors.extend(
                _projection_errors(
                    root=root,
                    identifier=identifier,
                    owner=owner,
                    checks=control.get("projection_checks"),
                )
            )
    errors.extend(
        f"hard_coding_surface_unowned:{surface}"
        for surface in sorted(PUBLIC_SURFACES - surface_owners.keys())
    )
    return {
        "ok": not errors,
        "errors": sorted(errors),
        "kinds": sorted(allowed_kinds),
        "controls": sorted(controls),
        "surfaces": sorted(surface_owners),
    }


def main(argv: Iterable[str] = ()) -> None:
    """Print a stable machine report and fail when ownership is incomplete."""
    if tuple(argv):
        raise SystemExit("hard-coding audit accepts no arguments")
    report = audit()
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
