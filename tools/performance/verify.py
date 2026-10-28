"""Verify pyperf evidence against the reviewed product budgets."""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Collection
from collections.abc import Mapping
from math import ceil
from math import isfinite
from pathlib import Path

import pyperf
from cyclopts import App

from openai_responses_proxy.serving import identity
from tools.performance import payload as performance_payload


class PerformanceError(RuntimeError):
    """Measured product overhead exceeds or omits its reviewed contract."""


def _measurements(
    path: Path,
    *,
    unit: str,
    minimum_samples: int,
    percentile: int,
    packaged: Collection[str] = (),
    payload: Mapping[str, str] | None = None,
) -> dict[str, float]:
    """Return an observed percentile from complete pyperf sample distributions.

    Every run of a packaged metric must name the measured payload.
    """
    suite = pyperf.BenchmarkSuite.load(str(path))
    measurements: dict[str, float] = {}
    for benchmark in suite:
        if benchmark.get_unit() != unit:
            raise PerformanceError(
                f"performance evidence unit mismatch: {benchmark.get_name()}={benchmark.get_unit()}"
            )
        values = sorted(benchmark.get_values())
        if any(isinstance(value, bool) or not isfinite(value) or value < 0 for value in values):
            raise PerformanceError(
                f"performance evidence contains invalid samples: {benchmark.get_name()}"
            )
        if len(values) < minimum_samples:
            raise PerformanceError(
                f"performance evidence has too few samples: {benchmark.get_name()}={len(values)}"
            )
        if benchmark.get_name() in packaged and (
            payload is None
            or any(
                run.get_metadata().get(name) != value
                for run in benchmark.get_runs()
                for name, value in payload.items()
            )
        ):
            raise PerformanceError(
                f"performance evidence does not name the measured payload: {benchmark.get_name()}"
            )
        index = max(0, ceil(percentile / 100 * len(values)) - 1)
        measurements[benchmark.get_name()] = values[index]
    return measurements


def _measured_payload(executable: Path) -> dict[str, str]:
    """Return the identity of the committed payload installed at ``executable``."""
    committed = identity.committed_payload(executable)
    if committed is None:
        raise PerformanceError(f"the measured executable is not a committed payload: {executable}")
    return performance_payload.metadata(committed.handoff(), executable)


def _budgets(contract: Mapping[str, object], group: str) -> dict[str, float]:
    """Return one positive numeric budget group."""
    raw = contract.get(group)
    if not isinstance(raw, Mapping) or not raw:
        raise PerformanceError(f"performance policy section is invalid: {group}")
    budgets: dict[str, float] = {}
    for name, maximum in raw.items():
        if (
            not isinstance(name, str)
            or isinstance(maximum, bool)
            or not isinstance(maximum, int | float)
            or not isfinite(maximum)
            or maximum <= 0
        ):
            raise PerformanceError(f"performance budget is invalid: {group}.{name}")
        budgets[name] = float(maximum)
    return budgets


def verify(*, policy: Path, latency: Path, memory: Path, payload: Mapping[str, str]) -> None:
    """Require every declared metric and reject values above its explicit budget.

    Every run of a packaged metric must name the native payload that was measured.
    """
    document = tomllib.loads(policy.read_text(encoding="utf-8"))
    execution = document.get("execution")
    if not isinstance(execution, Mapping):
        raise PerformanceError("performance execution policy is unavailable")
    minimum_latency_samples = execution.get("minimum_latency_samples")
    minimum_memory_samples = execution.get("minimum_memory_samples")
    percentile = execution.get("percentile")
    packaged = execution.get("packaged_metrics")
    if (
        not isinstance(minimum_latency_samples, int)
        or isinstance(minimum_latency_samples, bool)
        or minimum_latency_samples <= 0
        or not isinstance(minimum_memory_samples, int)
        or isinstance(minimum_memory_samples, bool)
        or minimum_memory_samples <= 0
        or not isinstance(percentile, int)
        or isinstance(percentile, bool)
        or not 50 <= percentile <= 99
        or not isinstance(packaged, list)
        or not all(isinstance(name, str) for name in packaged)
    ):
        raise PerformanceError("performance distribution policy is invalid")
    raw_contract = document.get("budgets")
    if not isinstance(raw_contract, Mapping):
        raise PerformanceError("performance policy budgets are unavailable")
    seconds = _budgets(raw_contract, "maximum_seconds")
    bytes_ = _budgets(raw_contract, "maximum_bytes")
    declared = seconds.keys() | bytes_.keys()
    undeclared = sorted(set(packaged) - declared)
    if undeclared:
        raise PerformanceError(f"performance packaged metric is undeclared: {undeclared}")
    if set(payload) != set(performance_payload.FIELDS) or not all(payload.values()):
        raise PerformanceError("performance payload identity is incomplete")
    observed = _measurements(
        latency,
        unit="second",
        minimum_samples=minimum_latency_samples,
        percentile=percentile,
        packaged=packaged,
        payload=payload,
    ) | _measurements(
        memory,
        unit="byte",
        minimum_samples=minimum_memory_samples,
        percentile=percentile,
        packaged=packaged,
        payload=payload,
    )
    failures: list[str] = []
    for budgets, unit in ((seconds, "seconds"), (bytes_, "bytes")):
        for name, maximum in budgets.items():
            value = observed.get(name)
            if value is None:
                failures.append(f"{name}: missing")
            elif value > maximum:
                failures.append(f"{name}: {value:.9g} {unit} exceeds {maximum:.9g}")
    failures.extend(f"{name}: undeclared benchmark" for name in sorted(observed.keys() - declared))
    if failures:
        raise PerformanceError("performance contract failed: " + "; ".join(failures))


def _command(*, policy: Path, latency: Path, memory: Path, executable: Path) -> None:
    verify(policy=policy, latency=latency, memory=memory, payload=_measured_payload(executable))


def main(argv: tuple[str, ...] | None = None) -> None:
    """Run performance verification through the repository parser stack."""
    try:
        App(default_command=_command, help=__doc__, result_action="return_value")(
            tuple(sys.argv[1:] if argv is None else argv)
        )
    except (OSError, KeyError, TypeError, ValueError, PerformanceError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
