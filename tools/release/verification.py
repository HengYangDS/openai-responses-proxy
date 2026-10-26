"""Own native acceptance scratch and exact runtime teardown in the surviving executor."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
from collections.abc import Mapping
from dataclasses import asdict
from dataclasses import replace
from math import isfinite
from pathlib import Path
from pathlib import PurePosixPath
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from typing import get_type_hints

from openai_responses_proxy import errors
from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import artifact
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import projection
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle import uninstall
from openai_responses_proxy.lifecycle.supervision import native_service
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import inventory
from openai_responses_proxy.serving import roles as serving_roles

if TYPE_CHECKING:
    import nox

_TEST_ROOT = product_identity.environment_name("NATIVE_TEST_ROOT")
_CONTEXTS = product_identity.environment_name("NATIVE_TEST_CONTEXTS")
_CONTEXT_TYPES = get_type_hints(runtime_context.RuntimeContext)
_SERVICE_ROLES = frozenset(
    {
        serving_roles.LISTENER_MODE,
        serving_roles.HANDOFF_CHILD_MODE,
        serving_roles.WATCHDOG_MODE,
        serving_roles.PREWARM_MODE,
    }
)


def accept(
    session: nox.Session,
    command: tuple[str, ...],
    *,
    work: Path,
    environment: Mapping[str, str | None],
) -> None:
    """Keep exact teardown custody outside the child and its pytest temporary root."""
    selected = _basetemp(command)
    work.mkdir(parents=True, exist_ok=True)
    custody = TemporaryDirectory(prefix="native-test-", dir=work, delete=False)
    owner = Path(custody.name).resolve()
    root = selected if selected is not None else owner / "pytest"
    if root.is_symlink() or root.exists():
        custody.cleanup()
        raise errors.InstallError("native test root must be a fresh owned directory")
    if not root.parent.is_dir():
        custody.cleanup()
        raise errors.InstallError("native test root parent is unavailable")
    contexts = owner / "contexts.jsonl"
    contexts.touch(mode=0o600)
    try:
        session.run(
            *command,
            *((f"--basetemp={root}",) if selected is None else ()),
            env={**environment, _TEST_ROOT: str(root), _CONTEXTS: str(contexts)},
        )
    finally:
        reclaim(root, contexts)
        custody.cleanup()


def _basetemp(command: tuple[str, ...]) -> Path | None:
    selected: list[str] = []
    for index, argument in enumerate(command):
        if argument.startswith("--basetemp="):
            selected.append(argument.split("=", 1)[1])
        elif argument == "--basetemp":
            if index + 1 == len(command):
                raise errors.InstallError("native test root argument has no value")
            selected.append(command[index + 1])
    if len(selected) > 1:
        raise errors.InstallError("native acceptance requires one test root")
    if not selected:
        return None
    target = Path(selected[0]).absolute()
    if target.resolve() != target:
        raise errors.InstallError("native test root must not traverse a symlink")
    return target


def retain_context(ctx: runtime_context.RuntimeContext) -> None:
    """Persist the existing native context before its first installation effect."""
    selected = os.environ.get(_TEST_ROOT)
    if selected is None:
        return
    root = Path(selected)
    target = os.environ.get(_CONTEXTS)
    if target is None:
        raise errors.InstallError("native test teardown custody is unavailable")
    _require_owned_context(ctx, root)
    with Path(target).open("ab") as output:
        output.write(digest.canonical_json(asdict(ctx)))
        output.flush()
        os.fsync(output.fileno())


def temporary_home(*, delete: bool = True) -> TemporaryDirectory[str]:
    """Allocate a native fixture home inside its surviving executor's ownership."""
    selected = os.environ.get(_TEST_ROOT)
    root = Path(selected) if selected is not None else None
    if root is not None:
        if not root.is_absolute() or root.resolve() != root:
            raise errors.InstallError("native test root identity changed")
        if os.environ.get(_CONTEXTS) is None:
            raise errors.InstallError("native test teardown custody is unavailable")
        root.mkdir(exist_ok=True)
    return TemporaryDirectory(dir=root, delete=delete)


def _require_owned_context(ctx: runtime_context.RuntimeContext, root: Path) -> None:
    if not root.is_absolute() or root.resolve() != root:
        raise errors.InstallError("native test root identity changed")
    for name in ("user_home", "install_dir", "log_dir", "command", "executable"):
        value = getattr(ctx, name)
        if not isinstance(value, str):
            raise errors.InstallError("native test context paths are invalid")
        path = Path(value)
        if not path.is_absolute() or path == root or not path.resolve().is_relative_to(root):
            raise errors.InstallError("native test context is outside its owned root")
    if ctx.service_id == runtime_context.SERVICE_ID:
        raise errors.InstallError("native tests cannot own the canonical installation")
    runtime_context.validate_port(ctx.port)


def reclaim(root: Path, contexts: Path) -> None:
    """Stop exact recorded native resources before removing this attempt's scratch."""
    if contexts.is_symlink() or not contexts.is_file():
        raise errors.InstallError("native test teardown custody changed")
    retained: dict[str, runtime_context.RuntimeContext] = {}
    content = contexts.read_bytes()
    if content and not content.endswith(b"\n"):
        raise errors.InstallError("native test context custody is invalid")
    for line in content.splitlines():
        ctx = _decode_context(line)
        _require_owned_context(ctx, root)
        previous = retained.setdefault(ctx.install_dir, ctx)
        if previous != ctx:
            raise errors.InstallError("native test context ownership changed")
    failures: list[Exception] = []
    for ctx in retained.values():
        failures_before = len(failures)
        try:
            if records.status(ctx) is not None:
                control.recover(ctx)
        except Exception as error:
            failures.append(error)
        try:
            cleanup_runtime(ctx)
        except Exception as error:
            failures.append(error)
        if len(failures) != failures_before:
            continue
        try:
            uninstall.uninstall_product(ctx, purge=True)
        except Exception as error:
            failures.append(error)
    if failures:
        raise errors.InstallError(
            "native test teardown is unproved; scratch preserved"
        ) from failures[0]
    if root.is_symlink() or root.resolve() != root:
        raise errors.InstallError("native test root identity changed")
    if root.exists():
        shutil.rmtree(root)
    if root.exists() or root.is_symlink():
        raise errors.InstallError("native test temporary files remain")


def _decode_context(line: bytes) -> runtime_context.RuntimeContext:
    try:
        value = json.loads(line)
    except (ValueError, UnicodeError) as error:
        raise errors.InstallError("native test context custody is invalid") from error
    if not isinstance(value, dict) or set(value) != _CONTEXT_TYPES.keys():
        raise errors.InstallError("native test context custody is invalid")
    for name, expected in _CONTEXT_TYPES.items():
        item = value[name]
        accepted = (int, float) if expected is float else (expected,)
        if type(item) not in accepted or (type(item) is float and not isfinite(item)):
            raise errors.InstallError("native test context custody is invalid")
    return runtime_context.RuntimeContext(**value)


def owned_runtime_contexts(
    ctx: runtime_context.RuntimeContext,
) -> tuple[runtime_context.RuntimeContext, ...]:
    """Return every payload context still owned by one stable installation root."""
    return generation.owned_contexts(ctx) or (ctx,)


def owned_process_contexts(
    ctx: runtime_context.RuntimeContext,
) -> tuple[runtime_context.RuntimeContext, ...]:
    """Include selected, unselected, and legacy executable identities for teardown."""
    contexts = (*owned_runtime_contexts(ctx), ctx)
    unique: dict[str, runtime_context.RuntimeContext] = {}
    for owned_ctx in contexts:
        unique.setdefault(owned_ctx.executable, owned_ctx)
    return tuple(unique.values())


def private_installation(home: Path, state: Path) -> runtime_context.RuntimeContext:
    """Project a private installation whose data and state roots the caller owns."""
    windows = os.name == "nt"
    return runtime_context.RuntimeContext(
        user_home=str(home.parent),
        install_dir=str(home),
        executable=inventory.installed_executable(str(home), windows=windows),
        command=str(home.parent / "commands" / product_identity.executable_name(windows=windows)),
        log_dir=str(state),
    )


def install_private_generation(
    control_ctx: runtime_context.RuntimeContext,
    source: Path,
    *,
    port: int,
    release: str,
    providers: str,
    receipt: bytes,
) -> runtime_context.RuntimeContext:
    """Install one built native bundle as the selected generation of a private root.

    The bundle directory of ``source`` becomes the generation's ``bin`` with its
    provider manifest, release receipt, payload manifest and runtime contract, so
    the listener verifies and loads it exactly as an installed product.
    """
    ctx = replace(generation.context(control_ctx, "0" * 32), port=port)
    retain_context(ctx)
    install_dir = Path(ctx.payload_dir)
    target = Path(ctx.executable)
    # Preserve executable modes, not host-local extended metadata. On macOS,
    # copy2 propagates provenance attributes to every frozen-runtime file and
    # turns each isolated copy into a fresh Gatekeeper scan. Published archives
    # do not carry those checkout-local attributes.
    shutil.copytree(source.parent, target.parent, copy_function=shutil.copy)
    provider_manifest = install_dir / inventory.PROVIDER_MANIFEST
    provider_manifest.write_text(providers, encoding="utf-8")
    (install_dir / inventory.RELEASE_RECEIPT_FILENAME).write_bytes(receipt)
    files = [provider_manifest, *(path for path in target.parent.rglob("*") if path.is_file())]
    manifest = projection.manifest_for_digests(
        release,
        {
            path.relative_to(install_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files
        },
        hashlib.sha256(receipt).hexdigest(),
    )
    projection.payload_manifest_path(ctx).write_bytes(projection.manifest_bytes(manifest))
    runtime_spec.write(ctx)
    generation.select(control_ctx, active="0" * 32, predecessor=None)
    return ctx


def private_processes(ctx: runtime_context.RuntimeContext) -> list[int]:
    """Return the processes that still run an executable of a private installation."""
    return sorted(
        {
            pid
            for owned_ctx in owned_process_contexts(ctx)
            for pid in process.pids_naming_executable(owned_ctx.executable, roles=_SERVICE_ROLES)
        }
    )


def cleanup_runtime(ctx: runtime_context.RuntimeContext, wrapper: Path | None = None) -> None:
    """Stop only processes and launch configuration owned by an isolated test."""
    service = native_service.adapter()
    owned_contexts = owned_process_contexts(ctx)
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


def materialize_published_bundle(
    session: nox.Session,
    release_asset: Path,
    trust_anchor: Path,
    work: Path,
    *,
    version: str,
) -> tuple[Path, Path]:
    """Materialize one verified published bundle for native execution."""
    released = artifact.admit(release_asset, trust_anchor=trust_anchor)
    if released.version != version:
        session.error("published release asset version does not match the checkout")
    expected_platform = product_identity.native_release_platform(
        platform.system(), platform.machine()
    )
    if released.receipt.get("platform") != expected_platform:
        session.error("published release asset does not match the native host")
    bundle = work / "published-release"
    bundle.mkdir()
    for blob in released.peek_blobs():
        target = bundle.joinpath(*PurePosixPath(blob.path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.content)
        target.chmod(0o755 if blob.mode == "100755" else 0o644)
    executable = bundle / "bin" / product_identity.executable_name(windows=os.name == "nt")
    if not executable.is_file():
        session.error("published release executable was not materialized")
    return executable.parent, executable
