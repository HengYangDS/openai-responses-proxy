#!/usr/bin/env python3
"""Fail closed before a Forge push when required CI cannot be scheduled."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Annotated
from typing import cast
from urllib.parse import quote

from cyclopts import App
from cyclopts import Parameter


class AdmissionError(RuntimeError):
    """Report unavailable or unschedulable hosted verification."""


def _command(*args: str) -> object:
    try:
        completed = subprocess.run(args, check=True, capture_output=True, text=True)
        return json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise AdmissionError("Forge scheduling evidence is unavailable") from error


def _records(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        raise AdmissionError("Forge scheduling evidence is malformed")
    return [
        cast("Mapping[str, object]", record)
        for record in value
        if isinstance(record, Mapping) and all(isinstance(key, str) for key in record)
    ]


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise AdmissionError("Forge scheduling evidence is malformed")
    return cast("Mapping[str, object]", value)


def gitlab_ready(runners: Sequence[Mapping[str, object]], runner_tag: str | None = None) -> bool:
    """Return whether at least one project runner accepts the selected jobs now."""

    def accepts(runner: Mapping[str, object]) -> bool:
        tags = runner.get("tag_list")
        tag_matches = (
            isinstance(tags, list)
            and all(isinstance(tag, str) for tag in tags)
            and runner_tag in cast("list[str]", tags)
        )
        return (
            runner.get("active") is True
            and runner.get("runner_type") == "project_type"
            and runner.get("online") is True
            and runner.get("paused") is False
            and (
                tag_matches if runner_tag not in (None, "") else runner.get("run_untagged") is True
            )
            and runner.get("access_level") in {"not_protected", "ref_protected"}
        )

    return any(accepts(runner) for runner in runners)


def github_ready(
    workflows: Sequence[Mapping[str, object]], permissions: Mapping[str, object]
) -> bool:
    """Return whether active Actions workflows may use GitHub-hosted runners."""
    paths = {workflow.get("path") for workflow in workflows if workflow.get("state") == "active"}
    return (
        permissions.get("enabled") is True
        and {
            ".github/workflows/admission.yml",
            ".github/workflows/verify.yml",
        }
        <= paths
    )


def _gitlab(project: str, runner_tag: str | None) -> dict[str, object]:
    project_coordinate = quote(project, safe="")
    listed = _records(
        _command("glab", "api", f"projects/{project_coordinate}/runners?per_page=100")
    )
    details = [_command("glab", "api", f"runners/{runner['id']}") for runner in listed]
    runners = [_mapping(runner) for runner in details]
    eligible = [runner for runner in runners if gitlab_ready([runner], runner_tag)]
    if not eligible:
        raise AdmissionError("GitLab has no online unpaused runner matching required jobs")
    return {"provider": "gitlab", "ready": True, "eligible_runner_count": len(eligible)}


def _github(repository: str) -> dict[str, object]:
    workflows = _command("gh", "api", f"repos/{repository}/actions/workflows")
    permissions = _command("gh", "api", f"repos/{repository}/actions/permissions")
    workflow_record = _mapping(workflows)
    permission_record = _mapping(permissions)
    records = _records(workflow_record.get("workflows"))
    if not github_ready(records, permission_record):
        raise AdmissionError("GitHub Actions or required workflows are disabled")
    return {"provider": "github", "ready": True, "workflow_count": len(records)}


def _run(
    *,
    provider: str,
    repository: str,
    runner_tag: str | None = None,
    as_json: Annotated[bool, Parameter(name="--json", negative=False)] = False,
) -> None:
    """Verify scheduling readiness without mutating either Forge."""
    if provider not in {"gitlab", "github"}:
        raise SystemExit(f"unsupported Forge provider: {provider}")
    try:
        result = _gitlab(repository, runner_tag) if provider == "gitlab" else _github(repository)
    except AdmissionError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    if as_json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{provider} runner admission: READY")


def main(argv: tuple[str, ...] | None = None) -> None:
    """Run Forge admission through the repository's single parser stack."""
    App(default_command=_run, help=__doc__, result_action="return_value")(
        tuple(sys.argv[1:] if argv is None else argv)
    )


if __name__ == "__main__":
    main()
