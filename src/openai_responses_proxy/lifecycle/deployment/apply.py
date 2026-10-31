"""Apply one admitted release to a fresh or current native runtime."""

from __future__ import annotations

import time
from collections.abc import Callable
from collections.abc import Mapping
from typing import Protocol

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle.deployment import handoff
from openai_responses_proxy.lifecycle.deployment import strategy
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.lifecycle.transaction import execution as transaction
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import roles as serving_roles

RuntimeReader = Callable[[runtime_context.RuntimeContext], dict[str, object] | None]


class ServiceAdapter(Protocol):
    """Native supervision operations required by payload deployment."""

    def install(self, ctx: runtime_context.RuntimeContext) -> None:
        """Install service; NativeServiceUnavailableError guarantees no service mutation."""
        ...

    def uninstall(self, ctx: runtime_context.RuntimeContext) -> None:
        """Remove the exact native service owned by this runtime context."""
        ...

    def terminate_runtime(
        self,
        ctx: runtime_context.RuntimeContext,
        *,
        timeout_seconds: float,
    ) -> int:
        """Terminate and prove exit of this generation's native runtime processes."""
        ...

    def configured_executable(self, ctx: runtime_context.RuntimeContext) -> str | None:
        """Return the executable configured in the native service definition."""
        ...

    def status(self, ctx: runtime_context.RuntimeContext) -> str:
        """Return the native service state for this installation."""
        ...


class UnknownDeploymentOutcome(errors.InstallError):
    """The deployment controller cannot prove whether the successor committed."""


def install(
    ctx: runtime_context.RuntimeContext,
    payload: transaction.PayloadTransaction,
    *,
    adapter: ServiceAdapter,
    runtime_reader: RuntimeReader,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    """Install fresh bytes or hand off one verified current native runtime."""
    current = runtime_reader(ctx)
    if current is None and not process.listener_pids(ctx.port):
        return _fresh_install(
            payload,
            adapter=adapter,
            runtime_reader=runtime_reader,
            timeout_seconds=timeout_seconds,
        )
    if current is None:
        raise errors.InstallError("installed runtime identity is not verified")
    pid = current.get("pid")
    if type(pid) is not int:
        raise errors.InstallError("installed runtime identity is not verified")
    selected = strategy.deployment_strategy(current)
    if selected == "unsupported":
        raise errors.InstallError(
            "installed runtime is incompatible; remove it before installing this release"
        )
    source_listener = handoff.capture_source_listener(ctx, current)
    control = generation.control_context(ctx)
    configured = adapter.configured_executable(control)
    if configured is None or runtime_spec.normalized_path(
        configured
    ) != runtime_spec.normalized_path(control.executable):
        raise errors.InstallError(
            "native supervisor is not bound to the lifecycle control executable"
        )
    return _upgrade(
        ctx,
        payload,
        adapter=adapter,
        current=current,
        source_listener=source_listener,
        native_generation=selected == "native_generation",
        runtime_reader=runtime_reader,
        timeout_seconds=timeout_seconds,
    )


def rollback(
    ctx: runtime_context.RuntimeContext,
    retained: payload_rollback.RetainedRollback,
    *,
    adapter: ServiceAdapter,
    runtime_reader: RuntimeReader,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    """Apply one retained predecessor through the ordinary upgrade machinery."""
    if runtime_reader(ctx) is None:
        raise errors.InstallError("rollback requires a verified running successor")
    payload = transaction.begin_rollback_transaction(ctx, retained)
    result = install(
        ctx,
        payload,
        adapter=adapter,
        runtime_reader=runtime_reader,
        timeout_seconds=timeout_seconds,
    )
    if result.get("state") != "upgraded":
        raise errors.InstallError("rollback requires a verified running successor")
    return {
        "state": "rolled_back",
        "from_release": retained.successor.release,
        "to_release": retained.predecessor.release,
        "runtime": result["runtime"],
    }


def _fresh_install(
    payload: transaction.PayloadTransaction,
    *,
    adapter: ServiceAdapter,
    runtime_reader: RuntimeReader,
    timeout_seconds: float,
) -> dict[str, object]:
    payload.commit_projection()
    candidate = payload.context
    try:
        payload.activate()
        adapter.install(candidate)
        runtime = wait_for_serving_runtime(
            candidate,
            payload.expected,
            runtime_reader=runtime_reader,
            timeout_seconds=timeout_seconds,
        )
    except BaseException as install_error:
        service_unavailable = isinstance(install_error, errors.NativeServiceUnavailableError)
        if not service_unavailable:
            try:
                discard_runtime(
                    candidate,
                    adapter=adapter,
                    timeout_seconds=timeout_seconds,
                )
            except UnknownDeploymentOutcome as cleanup_error:
                cleanup_cause = cleanup_error.__cause__ or cleanup_error
                detail = (
                    f"fresh installation failed: {errors.failure_summary(install_error)}; "
                    "candidate runtime cleanup is unconfirmed; "
                    f"cleanup failure: {errors.failure_summary(cleanup_cause)}"
                )
                unknown = UnknownDeploymentOutcome(f"{detail}; transaction preserved for recovery")
                try:
                    payload.preserve_for_recovery(str(unknown))
                except BaseException as recovery_error:
                    raise UnknownDeploymentOutcome(
                        f"{detail}; recovery record failed: {errors.failure_summary(recovery_error)}"
                    ) from recovery_error
                raise unknown from cleanup_error
        try:
            payload.rollback()
        except BaseException as rollback_error:
            failure = (
                errors.NativeServiceUnavailableError if service_unavailable else errors.InstallError
            )
            raise failure(
                f"fresh installation failed: {errors.failure_summary(install_error)}; "
                f"payload rollback failed: {errors.failure_summary(rollback_error)}"
            ) from rollback_error
        raise
    payload.finalize(runtime)
    return {"state": "installed", "runtime": runtime}


def _upgrade(
    ctx: runtime_context.RuntimeContext,
    payload: transaction.PayloadTransaction,
    *,
    adapter: ServiceAdapter,
    current: dict[str, object],
    source_listener: process.OwnedProcess,
    native_generation: bool,
    runtime_reader: RuntimeReader,
    timeout_seconds: float,
) -> dict[str, object]:
    payload.commit_projection()
    candidate = payload.context
    admission_may_be_closed = False
    successor_committed = False
    try:
        if native_generation:
            admission_may_be_closed = True
            handoff.drain_responses(
                candidate,
                source_listener=source_listener,
                source_runtime=current,
                runtime_reader=runtime_reader,
                timeout_seconds=timeout_seconds,
            )
        payload.activate()
        admission_may_be_closed = True
        runtime = (
            _replace_native_generation(
                candidate,
                payload.expected,
                adapter=adapter,
                source_listener=source_listener,
                runtime_reader=runtime_reader,
                timeout_seconds=timeout_seconds,
            )
            if native_generation
            else request_handoff(
                candidate,
                payload.expected,
                current=current,
                source_listener=source_listener,
                runtime_reader=runtime_reader,
                timeout_seconds=timeout_seconds,
            )
        )
        successor_committed = True
        if not native_generation:
            bind_control_supervisor(adapter, generation.control_context(ctx))
    except UnknownDeploymentOutcome as exc:
        payload.preserve_for_recovery(str(exc))
        raise
    except BaseException as upgrade_error:
        if successor_committed:
            try:
                payload.preserve_for_recovery(
                    "successor committed but native supervisor rebind failed; "
                    f"failure: {errors.failure_summary(upgrade_error)}"
                )
            except errors.InstallError as preserve_error:
                unknown = UnknownDeploymentOutcome(
                    "successor committed but native supervisor recovery is unconfirmed"
                )
                raise unknown from preserve_error
            raise UnknownDeploymentOutcome(
                "successor committed but native supervisor rebind failed; "
                "transaction preserved for recovery"
            ) from upgrade_error
        if admission_may_be_closed:
            try:
                if not handoff.resume_responses(
                    ctx,
                    source_listener=source_listener,
                    source_runtime=current,
                    runtime_reader=runtime_reader,
                ):
                    raise errors.InstallError(
                        "predecessor listener no longer owns Responses admission"
                    )
            except BaseException as restore_error:
                unknown = UnknownDeploymentOutcome(
                    "native supervisor rollback could not restore predecessor admission; "
                    f"upgrade failure: {errors.failure_summary(upgrade_error)}; "
                    f"admission recovery failure: {errors.failure_summary(restore_error)}"
                )
                payload.preserve_for_recovery(str(unknown))
                raise unknown from restore_error
        payload.rollback()
        raise
    payload.finalize(runtime)
    return {"state": "upgraded", "runtime": runtime}


def bind_control_supervisor(
    adapter: ServiceAdapter,
    control: runtime_context.RuntimeContext,
) -> None:
    """Bind durable supervision to the newest lifecycle-capable generation."""
    adapter.install(control)
    configured = adapter.configured_executable(control)
    if configured is None or runtime_spec.normalized_path(
        configured
    ) != runtime_spec.normalized_path(control.executable):
        raise errors.InstallError("native supervisor did not bind the lifecycle control executable")


def discard_runtime(
    candidate: runtime_context.RuntimeContext,
    *,
    adapter: ServiceAdapter,
    timeout_seconds: float,
) -> None:
    """Remove candidate supervision and processes before payload rollback."""
    try:
        configured = adapter.configured_executable(candidate)
        if configured is None:
            if adapter.status(candidate) != "absent":
                raise errors.InstallError("candidate native supervisor identity is unproved")
        else:
            if runtime_spec.normalized_path(configured) != runtime_spec.normalized_path(
                candidate.executable
            ):
                raise errors.InstallError("candidate native supervisor identity is unproved")
            adapter.uninstall(candidate)
        if adapter.status(candidate) != "absent":
            raise errors.InstallError("candidate native supervision is not absent")
        adapter.terminate_runtime(candidate, timeout_seconds=timeout_seconds)
    except BaseException as cleanup_error:
        raise UnknownDeploymentOutcome(
            "candidate runtime cleanup is unconfirmed; transaction preserved for recovery"
        ) from cleanup_error


def _replace_native_generation(
    ctx: runtime_context.RuntimeContext,
    expected: Mapping[str, object],
    *,
    adapter: ServiceAdapter,
    source_listener: process.OwnedProcess,
    runtime_reader: RuntimeReader,
    timeout_seconds: float,
) -> dict[str, object]:
    """Cross the exact predecessor exit barrier and prove its successor."""
    try:
        source_exited = process.terminate_owned_process(
            source_listener, timeout_seconds=timeout_seconds
        )
    except errors.InstallError as error:
        raise UnknownDeploymentOutcome(
            f"native generation replacement outcome is unconfirmed; {errors.failure_summary(error)}"
        ) from error
    if not source_exited:
        raise UnknownDeploymentOutcome("native generation replacement outcome is unconfirmed")
    try:
        bind_control_supervisor(adapter, generation.control_context(ctx))
        return wait_for_serving_runtime(
            ctx,
            expected,
            runtime_reader=runtime_reader,
            timeout_seconds=timeout_seconds,
            old_pid=source_listener.pid,
        )
    except BaseException as startup_error:
        raise UnknownDeploymentOutcome(
            "native successor startup is unconfirmed after predecessor exit"
        ) from startup_error


def request_handoff(
    ctx: runtime_context.RuntimeContext,
    expected: Mapping[str, object],
    *,
    current: dict[str, object],
    source_listener: process.OwnedProcess,
    runtime_reader: RuntimeReader,
    timeout_seconds: float,
) -> dict[str, object]:
    """Request handoff and resolve controller failure from runtime evidence."""
    lease_seconds = handoff.predecessor_lease_seconds(ctx, timeout_seconds)
    try:
        result = handoff.request(
            ctx,
            dict(expected),
            runtime_reader=runtime_reader,
            timeout_seconds=timeout_seconds,
            lease_seconds=lease_seconds,
            source_listener=source_listener,
            source_runtime=current,
        )
        runtime = result.get("runtime")
        if not isinstance(runtime, dict) or not all(isinstance(key, str) for key in runtime):
            raise errors.InstallError("handoff did not return successor runtime proof")
        return {key: value for key, value in runtime.items() if isinstance(key, str)}
    except BaseException as error:
        try:
            resolution, runtime = handoff.resolve_after_controller_failure(
                ctx,
                current,
                dict(expected),
                runtime_reader=runtime_reader,
                timeout_seconds=timeout_seconds,
                lease_seconds=lease_seconds,
                source_listener=source_listener,
            )
        except (OSError, errors.InstallError):
            resolution, runtime = "unknown", None
        if resolution == "finalized" and runtime is not None:
            return runtime
        if resolution == "unknown":
            raise UnknownDeploymentOutcome(
                "handoff outcome is unconfirmed; transaction preserved for recovery"
            ) from error
        raise


def wait_for_serving_runtime(
    ctx: runtime_context.RuntimeContext,
    expected: Mapping[str, object],
    *,
    runtime_reader: RuntimeReader,
    timeout_seconds: float,
    old_pid: int | None = None,
) -> dict[str, object]:
    """Wait for one accepting listener with the exact release identity."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        runtime = runtime_reader(ctx)
        if isinstance(runtime, dict):
            pid = runtime.get("pid")
            runtime_process = (
                process.capture_executable(
                    pid,
                    ctx.executable,
                    roles={
                        serving_roles.LISTENER_MODE,
                        serving_roles.HANDOFF_CHILD_MODE,
                    },
                )
                if type(pid) is int
                else None
            )
            if (
                type(pid) is int
                and pid > 0
                and (
                    process.verified_proxy_listener_pids(ctx) == [pid]
                    or runtime_process is not None
                )
                and pid != old_pid
                and _runtime_matches(runtime, expected)
            ):
                return runtime
        time.sleep(0.1)
    raise errors.InstallError("released successor did not prove SERVING identity")


def _runtime_matches(runtime: Mapping[str, object], expected: Mapping[str, object]) -> bool:
    return (
        identity.runtime_payload_matches(runtime, expected)
        and runtime.get("accepting") is True
        and runtime.get("draining") is not True
    )
