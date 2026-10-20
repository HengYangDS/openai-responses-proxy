"""Own native acceptance scratch and exact runtime teardown in the surviving executor."""

from __future__ import annotations

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
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle import uninstall
from openai_responses_proxy.lifecycle.supervision import native_service
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.serving import digest
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
