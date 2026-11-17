"""Construction helpers for released-payload lifecycle tests."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from pathlib import PurePath
from pathlib import PurePosixPath
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING
from typing import cast

from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import projection
from openai_responses_proxy.lifecycle import rollback
from openai_responses_proxy.lifecycle.transaction import execution as payload_transaction
from openai_responses_proxy.serving import digest as payload_digest
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import inventory

if TYPE_CHECKING:
    from pytest_mock import MockerFixture
    from pytest_mock import MockType

ROOT = Path(__file__).resolve().parents[2]
# The one payload generation that every fixture context names below its control root.
FIXTURE_GENERATION = "5" * 32


def generation_executable(install_dir: PurePath, *, windows: bool = False) -> str:
    """Return the fixture generation's executable below one control root."""
    payload_dir = install_dir / generation.GENERATIONS_DIRNAME / FIXTURE_GENERATION
    return inventory.installed_executable(str(payload_dir), windows=windows)


def control_root(root: Path, *, windows: bool = False) -> runtime_context.InstallationContext:
    """Build an isolated control root alone, for a state in which no generation is selected.

    It names no payload, so a read of ``executable`` or ``payload_dir`` raises.
    """
    return runtime_context.InstallationContext(
        user_home=str(root),
        install_dir=str(root / "data" / "openai-responses-proxy"),
        command=str(
            root
            / "commands"
            / ("openai-responses-proxy.cmd" if windows else "openai-responses-proxy")
        ),
        log_dir=str(root / "state" / "openai-responses-proxy"),
        port=8791,
        windows=windows,
    )


def install_context(root: Path, *, windows: bool = False) -> runtime_context.RuntimeContext:
    """Build an isolated install context in the fixture generation of one modeled platform."""
    return generation.context(control_root(root, windows=windows), FIXTURE_GENERATION)


def selected_context(ctx: runtime_context.InstallationContext) -> runtime_context.RuntimeContext:
    """Return the selected generation's context of an installation that has a selection."""
    selected = generation.selected_context(ctx)
    assert selected is not None
    return selected


def platform_context(port: int = 8791, *, windows: bool = False) -> runtime_context.RuntimeContext:
    """Build a deterministic service-definition fixture for one modeled platform."""
    if windows:
        home = PureWindowsPath("C:/fixture-home")
        install_dir = home / "AppData" / "Local" / "openai-responses-proxy"
        log_dir = home / "AppData" / "Local" / "openai-responses-proxy-state"
    else:
        home = PurePosixPath("/fixture-home")
        install_dir = home / ".local" / "share" / "openai-responses-proxy"
        log_dir = home / ".local" / "state" / "openai-responses-proxy"
    return runtime_context.RuntimeContext(
        user_home=str(home),
        install_dir=str(install_dir),
        executable=generation_executable(install_dir, windows=windows),
        windows=windows,
        command=str(
            (
                home
                / "AppData"
                / "Local"
                / "Microsoft"
                / "WindowsApps"
                / "openai-responses-proxy.cmd"
            )
            if windows
            else (home / ".local" / "bin" / "openai-responses-proxy")
        ),
        log_dir=str(log_dir),
        port=port,
    )


def runtime_files(*, windows: bool = False) -> tuple[str, ...]:
    """Return the installed payload inventory for the modeled platform."""
    return tuple(sorted(inventory.required_runtime_files(windows=windows)))


def executable_relative(*, windows: bool = False) -> str:
    """Return the installed executable member for the modeled platform."""
    return inventory.executable_name(windows=windows)


def assert_private_log_mode(mode: int) -> None:
    """Assert the strongest portable privacy bits exposed by the host."""
    if os.name == "nt":
        assert mode & 384 == 384
    else:
        assert mode == 384


def released_artifact(
    version: str = "1.2.3",
    *,
    executable_content: bytes = b"native-executable-fixture",
    windows: bool = False,
) -> artifact.VerifiedArtifact:
    """Build an admitted release artifact for lifecycle behavior tests."""

    def blob(relative: str) -> artifact.ArtifactFile:
        content = (
            executable_content
            if relative in {inventory.EXECUTABLE, inventory.WINDOWS_EXECUTABLE}
            else (ROOT / "src/openai_responses_proxy/providers/manifest.toml").read_bytes()
        )
        return artifact.ArtifactFile(
            path=relative,
            mode="100755" if relative != inventory.PROVIDER_MANIFEST else "100644",
            blob_oid=hashlib.sha1(content, usedforsecurity=False).hexdigest(),
            sha256=hashlib.sha256(content).hexdigest(),
            content=content,
        )

    files = runtime_files(windows=windows)
    blobs = tuple(map(blob, files))
    serving = {item.path: item.sha256 for item in blobs if item.path in files}
    receipt = {
        "schema_version": 1,
        "version": version,
        "serving_payload_sha256": projection.manifest_serving_payload_sha256(serving),
        "serving_files": list(files),
        "payload": [
            {
                "path": item.path,
                "mode": item.mode,
                "blob_oid": item.blob_oid,
                "sha256": item.sha256,
            }
            for item in blobs
        ],
    }
    receipt_sha256 = hashlib.sha256(payload_digest.canonical_json(receipt)).hexdigest()
    return artifact.mint(
        blobs,
        receipt,
        {
            "schema_version": 1,
            "algorithm": "sha256",
            "receipt_sha256": receipt_sha256,
            "serving_payload_sha256": receipt["serving_payload_sha256"],
        },
    )


def begin_transaction(
    ctx: runtime_context.InstallationContext,
    candidate: artifact.VerifiedArtifact,
    *,
    mocker: MockerFixture,
) -> payload_transaction.PayloadTransaction:
    """Begin a transaction through the artifact claim authority boundary."""
    blobs = candidate.peek_blobs()
    receipt = candidate.receipt
    claimed = (blobs, candidate.version, candidate.receipt_sha256, receipt, {})
    mocker.patch.object(artifact, "claim", return_value=claimed)
    mocker.patch.object(payload_transaction.payload_candidate, "prewarm")
    return payload_transaction.begin_transaction(ctx, candidate)


def install_payload(
    ctx: runtime_context.RuntimeContext, version: str = "1.2.3", *, mocker: MockerFixture
) -> payload_transaction.PayloadTransaction:
    """Install and finalize one released payload in the generation that the context names."""
    candidate = released_artifact(version, windows=ctx.windows)
    naming = mocker.patch.object(payload_transaction, "uuid")
    naming.uuid4.return_value.hex = Path(ctx.payload_dir).name
    try:
        transaction = begin_transaction(ctx, candidate, mocker=mocker)
    finally:
        mocker.stop(naming)
    transaction.commit_projection()
    transaction.activate()
    transaction.finalize({"pid": 1})
    return transaction


def root_level_payload(
    control: runtime_context.InstallationContext, *, mocker: MockerFixture
) -> runtime_context.InstallationContext:
    """Lay one verified payload at the control root's own level, where no release writes one.

    The control root then holds an executable, a valid manifest, a runtime carrier and an
    installed record, with no selector, no generation store and no journal, and the command
    names the root-level executable. The control root's context is returned as it was given:
    it names no payload, as the product's own context names none.
    """
    assert type(control) is runtime_context.InstallationContext
    ctx = generation.context(control, FIXTURE_GENERATION)
    install_payload(ctx, mocker=mocker)
    install_root = Path(ctx.install_dir)
    generation_root = Path(ctx.payload_dir)
    for child in tuple(generation_root.iterdir()):
        child.rename(install_root / child.name)
    generation.clear(ctx)
    generation_root.rmdir()
    generation.root(ctx).rmdir()
    command.remove(Path(ctx.command), Path(ctx.executable))
    command.project(
        Path(ctx.command),
        Path(inventory.installed_executable(ctx.install_dir, windows=ctx.windows)),
    )
    return control


def listing_with_digests(root: Path) -> tuple[tuple[str, str], ...]:
    """Return every path below ``root`` with its kind, its link target or its content digest."""
    entries = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append((relative, f"link to {path.readlink()}"))
        elif path.is_dir():
            entries.append((relative, "directory"))
        else:
            entries.append((relative, payload_digest.sha256_file(path)))
    return tuple(entries)


def retained_rollback(ctx: runtime_context.InstallationContext) -> rollback.RetainedRollback:
    """Return the verified predecessor that the test retained, failing on clean absence."""
    retained = rollback.load_retained_or_none(ctx)
    assert retained is not None
    return retained


def running_supervisor(
    ctx: runtime_context.InstallationContext, *, mocker: MockerFixture
) -> MockType:
    """Project a running supervisor whose current declaration binds lifecycle control."""
    native = cast("MockType", mocker.patch.object(control, "adapter").return_value)
    native.status.return_value = "running"
    native.configured_executable.return_value = generation.require_control_context(ctx).executable
    native.projection_is_current.return_value = True
    return native


def healthy_status_dependencies(
    ctx: runtime_context.InstallationContext, *, mocker: MockerFixture
) -> None:
    """Project verified listener and supervisor evidence for one installed generation."""
    runtime = identity.committed_payload(Path(selected_context(ctx).executable))
    assert runtime is not None
    runtime_evidence = {
        "pid": 321,
        "release": runtime.release,
        "serving_payload_sha256": runtime.serving_payload_sha256,
        "release_receipt_sha256": runtime.release_receipt_sha256,
        "payload_manifest_sha256": runtime.manifest_sha256,
        "accepting": True,
        "draining": False,
    }
    running_supervisor(ctx, mocker=mocker)
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=[321])
    mocker.patch.object(control, "read_runtime", return_value=runtime_evidence)
