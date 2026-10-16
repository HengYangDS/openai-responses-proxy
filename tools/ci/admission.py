"""Validate branch proof from the CUE-projected event and job contract."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

JOB_RESULTS = frozenset({"success", "failure", "cancelled", "skipped"})


@dataclass(frozen=True, slots=True)
class Rule:
    """One declared event identity and its required successful jobs."""

    when: tuple[tuple[str, str], ...]
    jobs: tuple[str, ...]

    def matches(self, environment: Mapping[str, str]) -> bool:
        """Require every declared identity field to match the caller."""
        return all(environment.get(name) == value for name, value in self.when)


def _decode(environment: Mapping[str, str], name: str) -> object:
    """Read one required JSON input without exposing its contents on failure."""
    try:
        value: object = json.loads(environment[name])
    except (KeyError, json.JSONDecodeError) as error:
        raise ValueError("branch admission JSON input is unavailable or invalid") from error
    return value


def _rule(value: object) -> Rule:
    """Validate a nonempty exact-match identity and unique required job names."""
    if not isinstance(value, dict) or set(value) != {"when", "jobs"}:
        raise ValueError("branch admission rule is invalid")
    when, jobs = value["when"], value["jobs"]
    if not isinstance(when, dict) or not when:
        raise ValueError("branch admission rule identity is invalid")
    if any(not isinstance(name, str) or not isinstance(match, str) for name, match in when.items()):
        raise ValueError("branch admission rule identity is invalid")
    if not isinstance(jobs, list) or any(not isinstance(name, str) or not name for name in jobs):
        raise ValueError("branch admission required jobs are invalid")
    if len(set(jobs)) != len(jobs):
        raise ValueError("branch admission required jobs are duplicated")
    return Rule(tuple(when.items()), tuple(jobs))


def _results(value: object) -> dict[str, str]:
    """Accept only the native needs mapping and its declared terminal results."""
    if not isinstance(value, dict):
        raise ValueError("branch admission job results are invalid")
    results: dict[str, str] = {}
    for name, job in value.items():
        if not isinstance(name, str) or not isinstance(job, dict):
            raise ValueError("branch admission job results are invalid")
        result = job.get("result")
        if not isinstance(result, str) or result not in JOB_RESULTS:
            raise ValueError("branch admission job results are invalid")
        results[name] = result
    return results


def admit(environment: Mapping[str, str]) -> tuple[str, ...]:
    """Require one unambiguous declared event and complete successful proof."""
    raw_rules = _decode(environment, "BRANCH_ADMISSION_RULES")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("branch admission rules are invalid")
    rules = tuple(_rule(value) for value in raw_rules)
    matches = tuple(rule for rule in rules if rule.matches(environment))
    if len(matches) != 1:
        raise ValueError("unsupported or ambiguous verification event")
    results = _results(_decode(environment, "NEEDS_JSON"))
    required = matches[0].jobs
    missing = tuple(job for job in required if results.get(job) != "success")
    if missing:
        raise ValueError("required branch proof failed: " + ", ".join(missing))
    return required


def main() -> None:
    """Run the dependency-free native proof command with bounded diagnostics."""
    try:
        admit(os.environ)
    except ValueError as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
