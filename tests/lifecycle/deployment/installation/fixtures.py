"""Installation transaction, native service and retained-generation fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from typing import override

import pytest

from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import install
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.lifecycle.transaction import execution as transaction
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving.handoff import transaction as handoff_transaction
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact


class FakeTransaction:
    """Behavioral double for the payload transaction protocol."""

    def __init__(self, ctx, *, events: list[object] | None = None) -> None:
        """Initialize an observable payload-transaction double."""
        transaction_id = "1" * 32
        self.expected = {
            "transaction_id": transaction_id,
            "release": "1.2.3",
            "manifest_sha256": "b" * 64,
            "serving_payload_sha256": "c" * 64,
            "release_receipt_sha256": "d" * 64,
        }
        self.events = events if events is not None else []
        self.context = generation.context(ctx, transaction_id)

    def commit_projection(self) -> None:
        self.events.append("commit")

    def activate(self) -> None:
        self.events.append("activate")

    def finalize(self, runtime: dict[str, object] | None = None) -> None:
        self.events.append(("finalize", runtime))

    def rollback(self) -> None:
        self.events.append("rollback")

    def preserve_for_recovery(self, reason: str) -> None:
        self.events.append(("preserve", reason))


def as_transaction(value: FakeTransaction) -> transaction.PayloadTransaction:
    return cast("transaction.PayloadTransaction", value)


def retained_identity(release: str) -> identity.LoadedPayloadIdentity:
    """Build the identity surface needed by reverse deployment tests."""
    return identity.LoadedPayloadIdentity(
        release=release,
        serving_payload_sha256="a" * 64,
        release_receipt_sha256="b" * 64,
        manifest_sha256="c" * 64,
        root=Path("/retained") / release,
    )


class FakeServiceAdapter:
    """Record native-service calls and expose a configured executable."""

    def __init__(
        self,
        *,
        failure: BaseException | None = None,
        configured: str | None = "canonical",
        mocker,
    ) -> None:
        """Initialize the adapter with an optional installation failure."""
        self.install_mock = mocker.Mock(side_effect=failure)
        self.uninstall_mock = mocker.Mock()
        self.configured = configured
        self.running_contexts: set[str] = set()

    def install(self, ctx) -> None:
        self.install_mock(ctx)
        self.configured = "canonical"
        self.running_contexts.add(ctx.executable)

    def uninstall(self, ctx) -> None:
        self.uninstall_mock(ctx)
        self.configured = None
        self.running_contexts.discard(ctx.executable)

    def runtime_pids(self, ctx) -> list[int]:
        return [222] if ctx.executable in self.running_contexts else []

    def terminate_runtime(self, ctx, *, timeout_seconds: float) -> int:
        del timeout_seconds
        running = self.runtime_pids(ctx)
        self.running_contexts.discard(ctx.executable)
        return len(running)

    def configured_executable(self, ctx) -> str | None:
        configured = ctx.executable if self.configured == "canonical" else self.configured
        return configured if isinstance(configured, str) else None

    def status(self, ctx) -> str:
        return "running" if ctx.executable in self.running_contexts else "absent"


class OrderedServiceAdapter(FakeServiceAdapter):
    """Record the service rebind in the same event stream as the transaction."""

    def __init__(self, events: list[object], *, mocker) -> None:
        super().__init__(mocker=mocker)
        self.events = events

    @override
    def install(self, ctx) -> None:
        self.events.append("service-install")
        super().install(ctx)

    @override
    def uninstall(self, ctx) -> None:
        self.events.append("service-uninstall")
        super().uninstall(ctx)


def _unsupervised_installation(root: Path, *, service: str, serving: bool = False, mocker):
    """Install one verified payload whose native supervision is absent or stopped."""
    ctx = install_context(root)
    install_payload(ctx, mocker=mocker)
    released = released_artifact()
    mocker.patch.object(install.artifact, "admit", return_value=released)
    control_ctx = generation.control_context(ctx)
    committed = identity.committed_payload(Path(ctx.executable))
    assert committed is not None
    bound = {"control": service == "installed"}
    native = mocker.Mock()
    native.status.return_value = service
    native.configured_executable.side_effect = lambda _ctx: (
        control_ctx.executable if bound["control"] else None
    )
    native.install.side_effect = lambda _ctx: bound.update(control=True)
    mocker.patch(
        "openai_responses_proxy.lifecycle.supervision.native_service.adapter", return_value=native
    )
    mocker.patch.object(control, "adapter", return_value=native)
    runtime = {
        "pid": 4242,
        **committed.handoff(),
        "payload_manifest_sha256": committed.manifest_sha256,
        "accepting": True,
        "draining": False,
    }
    listeners = [4242] if serving else []
    mocker.patch.object(control.process, "verified_proxy_listener_pids", return_value=listeners)
    mocker.patch.object(control.process, "listener_pids", return_value=listeners)
    mocker.patch.object(control, "read_runtime", return_value=runtime if serving else None)
    return ctx, control_ctx, native, committed, runtime


class ReleasedDeploymentFixture:
    """Admit one isolated installation and observe native deployment transitions."""

    @pytest.fixture(autouse=True)
    def _admit_current_listener(self, tmp_path: Path, mocker) -> None:
        """Own the isolated install root and exact predecessor identity."""
        self.ctx = install_context(tmp_path)
        mocker.patch.object(
            process,
            "capture_executable",
            return_value=process.OwnedProcess(111, self.ctx.executable, 1.0),
        )
        mocker.patch.object(process, "listener_pids", return_value=[111])
        mocker.patch.object(process, "owned_process_alive", return_value=True)

    @staticmethod
    def current_runtime(**changes: object) -> dict[str, object]:
        value: dict[str, object] = {
            "pid": 111,
            "handoff_protocol_version": 2,
            "handoff_capabilities": [
                handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY,
                handoff_transaction.ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
            ],
            "handoff_state": "idle",
            "handoff_transaction_id": None,
            "release": "1.2.2",
            "serving_payload_sha256": "1" * 64,
            "payload_manifest_sha256": "2" * 64,
            "release_receipt_sha256": "3" * 64,
            "accepting": True,
            "draining": False,
        }
        value.update(changes)
        return value

    @staticmethod
    def successor(**changes: object) -> dict[str, object]:
        value: dict[str, object] = {
            "pid": 222,
            "release": "1.2.3",
            "serving_payload_sha256": "c" * 64,
            "payload_manifest_sha256": "b" * 64,
            "release_receipt_sha256": "d" * 64,
            "accepting": True,
            "draining": False,
        }
        value.update(changes)
        return value

    def deploy(
        self,
        payload: FakeTransaction,
        current: dict[str, object] | None,
        *,
        adapter: FakeServiceAdapter | None = None,
        timeout_seconds: float = 30,
        mocker,
    ) -> dict[str, object]:
        mocker.patch.object(
            generation,
            "control_context",
            side_effect=[self.ctx, payload.context],
        )
        return apply.install(
            self.ctx,
            as_transaction(payload),
            adapter=adapter or FakeServiceAdapter(mocker=mocker),
            runtime_reader=lambda _ctx: current,
            timeout_seconds=timeout_seconds,
        )
