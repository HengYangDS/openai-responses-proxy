"""Native release lifecycle fixtures shared by distribution acceptance tests."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle.supervision import linux
from openai_responses_proxy.lifecycle.supervision import macos
from openai_responses_proxy.lifecycle.supervision import native_service
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.runtime.process_environment import native_process_environment
from openai_responses_proxy.service import inventory
from openai_responses_proxy.service import runtime as service_runtime
from tools.release.artifact import bundle as release_assembly
from tools.release.artifact import format as product_assets
from tools.release.artifact import signing

ROOT = Path(__file__).resolve().parents[2]
COMMAND_TIMEOUT_SECONDS = 180
_SERVICE_ROLES = frozenset(
    {
        service_runtime.LISTENER_MODE,
        service_runtime.HANDOFF_CHILD_MODE,
        service_runtime.WATCHDOG_MODE,
        service_runtime.PREWARM_MODE,
    }
)


def _macos_service_projection() -> tuple[
    frozenset[str], frozenset[tuple[str, str]], tuple[tuple[str, str], ...]
]:
    """Return every persistent launchd surface owned by this product."""
    labels: set[str] = set()
    overrides: set[tuple[str, str]] = set()
    for domain in macos._domains():
        completed = subprocess.run(
            ["/bin/launchctl", "print", domain],
            capture_output=True,
            check=True,
            text=True,
        )
        labels.update(
            f"{domain}/{match.group('label')}"
            for match in re.finditer(
                rf"(?m)^\s*[0-9]+\s+\S+\s+(?P<label>"
                rf"{re.escape(runtime_context.SERVICE_ID)}(?:\.[0-9a-f]{{12}})?)\s*$",
                completed.stdout,
            )
        )
        disabled = subprocess.run(
            ["/bin/launchctl", "print-disabled", domain],
            capture_output=True,
            check=True,
            text=True,
        )
        overrides.update(
            (f"{domain}/{match.group('label')}", str(match.group("state")))
            for match in re.finditer(
                rf'"(?P<label>{re.escape(runtime_context.SERVICE_ID)}(?:\.[0-9a-f]{{12}})?)"'
                r"\s*=>\s*(?P<state>enabled|disabled|true|false)",
                disabled.stdout,
            )
        )
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    plists = tuple(
        (path.name, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(launch_agents.glob(f"{runtime_context.SERVICE_ID}*.plist"))
    )
    return frozenset(labels), frozenset(overrides), plists


@pytest.fixture(scope="module")
def preserve_native_host_projection():
    """Prove one native test module leaves the host projection unchanged."""
    if sys.platform != "darwin" or "OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE" not in os.environ:
        yield
        return
    before = _macos_service_projection()
    yield
    assert _macos_service_projection() == before


def run_command(
    executable: Path,
    environment: dict[str, str],
    *arguments: str,
    expected: int = 0,
) -> dict[str, object]:
    """Run one native command and require clean machine-readable output."""
    result = subprocess.run(
        [str(executable), *arguments],
        env=environment,
        text=True,
        capture_output=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == expected, result.stderr or result.stdout
    assert "Traceback" not in result.stderr
    assert "Warning" not in result.stderr
    output = result.stdout or result.stderr
    value: object = json.loads(output)
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return {key: item for key, item in value.items() if isinstance(key, str)}


def signed_asset(
    bundle: Path,
    output: Path,
    *,
    version: str,
    upstream_url: str,
    key: Path,
    trust: str,
    preserve_manifest: bool = False,
) -> Path:
    """Build one route-controlled asset from exact native bundle bytes."""
    platform_id = product_identity.native_release_platform(platform.system(), platform.machine())
    executable_name = product_identity.executable_name(windows=platform_id.startswith("windows-"))
    executable = bundle / executable_name
    files: dict[str, bytes | product_assets.ArchiveFile] = {
        f"bin/{executable_name}": product_assets.ArchiveFile(executable.read_bytes(), 0o755),
        "providers.toml": (
            (ROOT / "src/openai_responses_proxy/providers/manifest.toml").read_bytes()
            if preserve_manifest
            else (
                f'version = 1\n\n[providers.dmxapi]\nbase_url = "{upstream_url}"\npolicy = "dmxapi"\n'
            ).encode()
        ),
        "LICENSE": (ROOT / "LICENSE").read_bytes(),
    }
    for relative, source in release_assembly.bundle_files(bundle):
        if relative == Path(executable.name):
            continue
        files[f"bin/{relative.as_posix()}"] = source.read_bytes()
    archive_name = product_assets.archive_name(version, platform_id)
    archive = product_assets.archive_bytes(files, version, platform_id)
    manifest_name = product_assets.manifest_name(platform_id)
    manifest = product_assets.asset_manifest(
        version=version,
        platform=platform_id,
        archive_name=archive_name,
        archive=archive,
        files=files,
    )
    output.mkdir()
    release_files = {archive_name: archive, manifest_name: manifest}
    for name, content in {
        **release_files,
        product_assets.CHECKSUM_NAME: product_assets.checksums(release_files),
    }.items():
        (output / name).write_bytes(content)
    signing.sign_and_verify(assets=output, key=key, trust=trust)
    return output / archive_name


def post_response(
    port: int, *, stream: bool = False, timeout: float = 15, route: str = "dmxapi"
) -> bytes:
    """Send one unproxied Responses request to an isolated listener."""
    body = b'{"stream": true, "input": []}' if stream else b'{"stream": false, "input": []}'
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/{route}/v1/responses",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        content: object = response.read()
        assert isinstance(content, bytes)
        return content


def legacy_published_bundle(asset: Path, anchor: Path, work: Path) -> tuple[Path, str]:
    """Admit historical assets with their exact immutable original source."""
    archive = work / "predecessor-source.tar"
    source = work / "predecessor-source"
    source.mkdir()
    subprocess.run(
        [
            "git",
            "archive",
            "refs/tags/v4.0.6",
            "src/codex_responses_proxy",
            "--output",
            str(archive),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=30,
    )
    with tarfile.open(archive) as snapshot:
        snapshot.extractall(source, filter="data")
    bundle = work / "predecessor-native"
    probe = (
        "import sys; from pathlib import Path; "
        "sys.path.insert(0, sys.argv[1]); "
        "from codex_responses_proxy.lifecycle import artifact; "
        "item=artifact.admit(Path(sys.argv[2]), trust_anchor=Path(sys.argv[3])); "
        "root=Path(sys.argv[4]); root.mkdir(); "
        "[(p.parent.mkdir(parents=True, exist_ok=True), p.write_bytes(b.content), "
        "p.chmod(0o755 if b.mode=='100755' else 0o644)) "
        "for b in item.peek_blobs() if b.path.startswith('bin/') "
        "for p in [root / b.path.removeprefix('bin/')]]; print(item.version)"
    )
    admitted = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            probe,
            str(source / "src"),
            str(asset),
            str(anchor),
            str(bundle),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    archive.unlink()
    return bundle, admitted.stdout.strip()


def runtime_context_for(
    home: Path, install: Path, state: Path, port: int
) -> runtime_context.RuntimeContext:
    """Return one isolated native lifecycle context."""
    environment = native_process_environment(
        user_home=home,
        install_root=install,
        state_root=state,
    )
    windows = platform.system() == "Windows"

    return runtime_context.RuntimeContext(
        user_home=str(home),
        install_dir=str(install),
        executable=inventory.installed_executable(str(install), windows=windows),
        command=str(command.path(str(home), environment, windows=windows)),
        log_dir=str(state),
        port=port,
    )


def native_service_projection(ctx: runtime_context.RuntimeContext) -> dict[str, object]:
    """Return the exact native-service and process state owned by one context."""
    service = native_service.adapter()
    return {
        "service_id": ctx.service_id,
        "status": service.status(ctx),
        "configured_executable": service.configured_executable(ctx),
        "processes": process.pids_naming_executable(ctx.executable, roles=_SERVICE_ROLES),
    }


def stop_native_supervision(
    ctx: runtime_context.RuntimeContext, *, keep_runtime: bool = False
) -> None:
    """Keep the owned service carrier while its supervisor stops, as after a restart.

    ``keep_runtime`` leaves the detached listener serving, as after an operator bootout
    or a lost supervisor registration.
    """
    service = native_service.adapter()
    if sys.platform == "win32":
        operations = [("/change", "/disable")]
        if not keep_runtime:
            operations.append(("/end",))
        for arguments in operations:
            subprocess.run(
                ["schtasks", *arguments, "/tn", ctx.service_id],
                check=True,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
        if keep_runtime:
            control = generation.control_context(ctx)
            watchdog = {service_runtime.WATCHDOG_MODE}
            for pid in process.pids_naming_executable(control.executable, roles=watchdog):
                assert process.terminate_executable(pid, control.executable, roles=watchdog)
    else:
        carrier = Path(
            macos._plist_path(ctx) if sys.platform == "darwin" else linux._unit_path(ctx)
        )
        content = carrier.read_bytes()
        mode = carrier.stat().st_mode & 0o777
        service.uninstall(ctx)
        carrier.write_bytes(content)
        carrier.chmod(mode)
    if keep_runtime:
        return
    for owned_ctx in _process_contexts(ctx):
        service.terminate_runtime(owned_ctx, timeout_seconds=5.0)


def owned_runtime_contexts(
    ctx: runtime_context.RuntimeContext,
) -> tuple[runtime_context.RuntimeContext, ...]:
    """Return every payload context still owned by one stable installation root."""
    return generation.owned_contexts(ctx) or (ctx,)


def _process_contexts(
    ctx: runtime_context.RuntimeContext,
) -> tuple[runtime_context.RuntimeContext, ...]:
    """Include selected, unselected, and legacy executable identities for teardown."""
    contexts = (*owned_runtime_contexts(ctx), ctx)
    unique: dict[str, runtime_context.RuntimeContext] = {}
    for owned_ctx in contexts:
        unique.setdefault(owned_ctx.executable, owned_ctx)
    return tuple(unique.values())


def cleanup_runtime(ctx: runtime_context.RuntimeContext, wrapper: Path | None = None) -> None:
    """Stop only processes and launch configuration owned by an isolated test."""
    service = native_service.adapter()
    owned_contexts = _process_contexts(ctx)
    configured = service.configured_executable(ctx)
    process_contexts = owned_contexts
    if configured is not None and all(
        os.path.normcase(os.path.abspath(owned_ctx.executable))
        != os.path.normcase(os.path.abspath(configured))
        for owned_ctx in process_contexts
    ):
        configured_path = Path(configured).resolve()
        install_root = Path(ctx.install_dir).resolve()
        assert configured_path.is_relative_to(install_root), (
            "native service executable is outside the owned installation"
        )
        process_contexts = (*process_contexts, replace(ctx, executable=configured))
    failure: Exception | None = None
    try:
        service.uninstall(ctx)
        assert service.status(ctx) == "absent"
    except Exception as error:
        failure = error
    finally:
        if wrapper is not None:
            for pid in process.pids_naming_path(str(wrapper)):
                try:
                    if not process.terminate_pid(pid, expected_path=str(wrapper)):
                        raise errors.InstallError("test wrapper exit is unproved")
                except errors.InstallError as error:
                    failure = failure or error
        for owned_ctx in process_contexts:
            for pid in process.pids_naming_executable(owned_ctx.executable, roles=_SERVICE_ROLES):
                try:
                    if not process.terminate_executable(
                        pid, owned_ctx.executable, roles=_SERVICE_ROLES
                    ):
                        raise errors.InstallError("test runtime process exit is unproved")
                except errors.InstallError as error:
                    failure = failure or error
    if failure is not None:
        raise failure
    assert service.status(ctx) == "absent"
    assert service.configured_executable(ctx) is None
    assert all(
        process.pids_naming_executable(owned_ctx.executable, roles=_SERVICE_ROLES) == []
        for owned_ctx in process_contexts
    )
