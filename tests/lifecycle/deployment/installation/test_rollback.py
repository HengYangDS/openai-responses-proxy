"""Explicit rollback through the authoritative deployment transition."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.lifecycle.transaction import execution as transaction
from openai_responses_proxy.serving.handoff import transaction as handoff_transaction
from tests.lifecycle.deployment.installation.fixtures import FakeServiceAdapter
from tests.lifecycle.deployment.installation.fixtures import FakeTransaction
from tests.lifecycle.deployment.installation.fixtures import ReleasedDeploymentFixture
from tests.lifecycle.deployment.installation.fixtures import retained_identity
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.fixtures import retained_rollback

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from openai_responses_proxy.lifecycle import context as runtime_context


class TestReleasedDeployment(ReleasedDeploymentFixture):
    def test_explicit_rollback_reuses_upgrade_and_projects_precise_releases(
        self, *, mocker: MockerFixture
    ) -> None:
        retained = payload_rollback.RetainedRollback(
            root=Path("/retained/1.2.2"),
            predecessor=retained_identity("1.2.2"),
            successor=retained_identity("1.2.3"),
        )
        payload = FakeTransaction(self.ctx)
        mocker.patch.object(transaction, "begin_rollback_transaction", return_value=payload)
        install = mocker.patch.object(
            apply,
            "install",
            return_value={"state": "upgraded", "runtime": {"pid": 654}},
        )
        service = FakeServiceAdapter(mocker=mocker)

        def runtime_reader(_ctx: runtime_context.RuntimeContext) -> dict[str, object] | None:
            return {"pid": 321}

        result = apply.rollback(
            self.ctx,
            retained,
            adapter=service,
            runtime_reader=runtime_reader,
            timeout_seconds=12.5,
        )

        assert result == {
            "state": "rolled_back",
            "from_release": "1.2.3",
            "to_release": "1.2.2",
            "runtime": {"pid": 654},
        }
        install.assert_called_once_with(
            self.ctx,
            payload,
            adapter=service,
            runtime_reader=runtime_reader,
            timeout_seconds=12.5,
        )

    def test_explicit_rollback_refuses_before_any_change_when_no_runtime_answers(
        self, *, mocker: MockerFixture
    ) -> None:
        retained = payload_rollback.RetainedRollback(
            root=Path("/retained/1.2.2"),
            predecessor=retained_identity("1.2.2"),
            successor=retained_identity("1.2.3"),
        )
        begin = mocker.patch.object(transaction, "begin_rollback_transaction")
        install = mocker.patch.object(apply, "install")
        service = FakeServiceAdapter(mocker=mocker)

        with pytest.raises(errors.InstallError, match="verified running successor"):
            apply.rollback(
                self.ctx,
                retained,
                adapter=service,
                runtime_reader=lambda _ctx: None,
            )

        begin.assert_not_called()
        install.assert_not_called()
        service.install_mock.assert_not_called()
        service.uninstall_mock.assert_not_called()

    def test_explicit_rollback_uses_current_runtime_handoff_capability(
        self, *, mocker: MockerFixture
    ) -> None:
        """Rollback keeps admission open when the current listener supports handoff."""
        retained = payload_rollback.RetainedRollback(
            root=Path("/retained/1.2.2"),
            predecessor=retained_identity("1.2.2"),
            successor=retained_identity("1.2.3"),
        )
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime(release="1.2.3")
        runtime = self.successor(release="1.2.2")
        service = FakeServiceAdapter(mocker=mocker)
        source = process.OwnedProcess(111, self.ctx.executable, 1.0)
        mocker.patch.object(transaction, "begin_rollback_transaction", return_value=payload)
        mocker.patch.object(process, "capture_executable", return_value=source)
        request = mocker.patch.object(apply, "request_handoff", return_value=runtime)
        control = generation.context(self.ctx, "c" * 32)
        mocker.patch.object(generation, "control_context", return_value=control)

        result = apply.rollback(
            self.ctx,
            retained,
            adapter=service,
            runtime_reader=lambda _ctx: current,
        )

        assert result == {
            "state": "rolled_back",
            "from_release": "1.2.3",
            "to_release": "1.2.2",
            "runtime": runtime,
        }
        request.assert_called_once()
        service.install_mock.assert_called_once_with(control)

    @pytest.mark.parametrize(
        "capability",
        [
            handoff_transaction.SELECTED_GENERATION_HANDOFF_CAPABILITY,
            handoff_transaction.ADMISSION_PRESERVING_HANDOFF_CAPABILITY,
        ],
    )
    def test_explicit_rollback_refuses_a_runtime_without_both_handoff_capabilities(
        self, capability: str, *, mocker: MockerFixture
    ) -> None:
        """A verified runtime that cannot hand off is refused before any change."""
        retained = payload_rollback.RetainedRollback(
            root=Path("/retained/1.2.2"),
            predecessor=retained_identity("1.2.2"),
            successor=retained_identity("1.2.3"),
        )
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime(release="1.2.3", handoff_capabilities=[capability])
        service = FakeServiceAdapter(mocker=mocker)
        mocker.patch.object(transaction, "begin_rollback_transaction", return_value=payload)
        request = mocker.patch.object(apply, "request_handoff")
        control_request = mocker.patch.object(
            apply.handoff.loopback, "open_request", side_effect=AssertionError("control request")
        )
        terminate = mocker.patch.object(process, "terminate_owned_process")

        with pytest.raises(errors.InstallError) as refused:
            apply.rollback(
                self.ctx,
                retained,
                adapter=service,
                runtime_reader=lambda _ctx: current,
            )

        assert str(refused.value) == (
            "installed runtime is incompatible; remove it before installing this release"
        )
        assert payload.events == ["close_prepared"]
        service.install_mock.assert_not_called()
        service.uninstall_mock.assert_not_called()
        request.assert_not_called()
        control_request.assert_not_called()
        terminate.assert_not_called()

    def test_explicit_rollback_requires_an_upgrade_result(self, *, mocker: MockerFixture) -> None:
        retained = payload_rollback.RetainedRollback(
            root=Path("/retained/1.2.2"),
            predecessor=retained_identity("1.2.2"),
            successor=retained_identity("1.2.3"),
        )
        mocker.patch.object(
            transaction,
            "begin_rollback_transaction",
            return_value=FakeTransaction(self.ctx),
        )
        mocker.patch.object(
            apply,
            "install",
            return_value={"state": "installed", "runtime": {"pid": 654}},
        )

        with pytest.raises(errors.InstallError, match="verified running successor"):
            apply.rollback(
                self.ctx,
                retained,
                adapter=FakeServiceAdapter(mocker=mocker),
                runtime_reader=lambda _ctx: {"pid": 321},
            )


def test_refused_rollback_leaves_no_transaction(tmp_path: Path, *, mocker: MockerFixture) -> None:
    """A rollback refused before any projection closes the transaction it opened."""
    ctx = install_context(tmp_path)
    install_payload(ctx, "1.2.2", mocker=mocker)
    successor = begin_transaction(ctx, released_artifact("1.2.3"), mocker=mocker)
    successor.commit_projection()
    successor.activate()
    successor.finalize({"pid": 2})
    retained = retained_rollback(ctx)
    selection = generation.read(ctx)
    installed = records.read_installed(ctx)
    assert records.status(ctx) is None

    with pytest.raises(errors.InstallError, match="installed runtime is incompatible"):
        apply.rollback(
            ctx,
            retained,
            adapter=FakeServiceAdapter(mocker=mocker),
            runtime_reader=lambda _ctx: {"pid": 321},
        )

    assert records.status(ctx) is None
    assert generation.read(ctx) == selection
    assert records.read_installed(ctx) == installed
