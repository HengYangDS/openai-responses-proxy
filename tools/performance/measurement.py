"""Measure startup readiness and request overhead from one private installed generation."""

from __future__ import annotations

import shutil
import socket
import tomllib
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

import nox

from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import candidate as payload_candidate
from openai_responses_proxy.lifecycle import context as runtime_context

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / ".config/checks/performance/policy.toml"
PROVIDERS = 'version = 1\n\n[providers.dmxapi]\nbase_url = "https://example.invalid/v1"\npolicy = "dmxapi"\n'


def measure(
    session: nox.Session,
    executable: Path,
    output: Path,
    *,
    base_environment: Mapping[str, str],
    verification: ModuleType,
) -> None:
    """Measure one private installed generation of the native candidate, verify the evidence, then retire it."""
    output.mkdir(parents=True, exist_ok=True)
    benchmark_log = output / "benchmark.log"
    home, state = output / "payload", output / "state"
    private = {
        product_identity.environment_name("HOME"): str(home),
        product_identity.environment_name("STATE_HOME"): str(state),
    }
    environment = {**base_environment, **private}
    execution = tomllib.loads(POLICY.read_text(encoding="utf-8"))["execution"]
    common = [
        "--quiet",
        "--processes",
        str(execution["processes"]),
        "--values",
        str(execution["values"]),
        "--warmups",
        str(execution["warmups"]),
        "--min-time",
        str(execution["minimum_time_seconds"]),
        # pyperf workers keep only a minimal environment unless told otherwise.
        "--inherit-environ",
        ",".join(private),
        "--output",
    ]
    ctx = verification.install_private_generation(
        verification.private_installation(home, state),
        executable,
        port=_free_loopback_port(),
        release=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
        providers=PROVIDERS,
        receipt=b'{"performance":"startup-to-ready"}\n',
    )
    try:
        # A newly copied frozen payload can pay a one-time host assessment
        # before its first launch; pay it outside every measurement.
        payload_candidate.prewarm(ctx)
        with benchmark_log.open("w", encoding="utf-8") as log:
            session.run(
                "python",
                "-m",
                "tools.performance.benchmark",
                *common,
                str(output / "latency.json"),
                env=environment,
                stderr=log,
            )
            session.run(
                "python",
                "-m",
                "tools.performance.memory",
                "--track-memory",
                *common,
                str(output / "memory.json"),
                env=environment,
                stderr=log,
            )
        # Verification reads the installed payload's identity, which every
        # packaged run must name, so it runs before the installation is retired.
        session.run(
            "python",
            "-m",
            "tools.performance.verify",
            "--policy",
            str(POLICY),
            "--latency",
            str(output / "latency.json"),
            "--memory",
            str(output / "memory.json"),
            "--executable",
            str(ctx.executable),
            env=environment,
        )
    finally:
        _retire_private_installation(session, verification, ctx, home)


def _free_loopback_port() -> int:
    """Return a loopback port that is free now for one private listener."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _retire_private_installation(
    session: nox.Session, verification: ModuleType, ctx: runtime_context.RuntimeContext, home: Path
) -> None:
    """Prove that no private listener outlived the measurements, then remove its payload."""
    leftover = verification.private_processes(ctx)
    if leftover:
        session.error(f"private packaged listeners outlived the performance session: {leftover}")
    shutil.rmtree(home)
