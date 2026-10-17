"""Explicit rollback through the authoritative deployment transition."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import rollback as payload_rollback
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.lifecycle.transaction import execution as transaction
from tests.lifecycle.deployment.installation.fixtures import FakeServiceAdapter
from tests.lifecycle.deployment.installation.fixtures import FakeTransaction
from tests.lifecycle.deployment.installation.fixtures import ReleasedDeploymentFixture
from tests.lifecycle.deployment.installation.fixtures import retained_identity


class TestReleasedDeployment(ReleasedDeploymentFixture):
    def test_explicit_rollback_reuses_upgrade_and_projects_precise_releases(
        self, *, mocker
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

        def runtime_reader(_ctx):
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
        self, *, mocker
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

    def test_explicit_rollback_uses_current_runtime_handoff_capability(self, *, mocker) -> None:
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
        drain = mocker.patch.object(apply.handoff, "drain_responses")
        replace = mocker.patch.object(
            apply,
            "_replace_native_generation",
            return_value=runtime,
        )
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
        drain.assert_not_called()
        replace.assert_not_called()
        request.assert_called_once()
        service.install_mock.assert_called_once_with(control)

    def test_explicit_rollback_replaces_a_runtime_without_handoff_capability(
        self, *, mocker
    ) -> None:
        """A reverse transition keeps the newest control supervisor authoritative."""
        retained = payload_rollback.RetainedRollback(
            root=Path("/retained/1.2.2"),
            predecessor=retained_identity("1.2.2"),
            successor=retained_identity("1.2.3"),
        )
        payload = FakeTransaction(self.ctx)
        current = self.current_runtime(
            release="1.2.3",
            handoff_capabilities=["repeatable"],
        )
        runtime = self.successor(release="1.2.2")
        service = FakeServiceAdapter(mocker=mocker)
        source = process.OwnedProcess(111, self.ctx.executable, 1.0)
        mocker.patch.object(transaction, "begin_rollback_transaction", return_value=payload)
        mocker.patch.object(process, "capture_executable", return_value=source)
        terminate = mocker.patch.object(process, "terminate_owned_process", return_value=True)
        drain = mocker.patch.object(apply.handoff, "drain_responses")
        mocker.patch.object(apply, "wait_for_serving_runtime", return_value=runtime)
        request = mocker.patch.object(apply, "request_handoff")
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
        drain.assert_called_once()
        terminate.assert_called_once_with(source, timeout_seconds=30)
        request.assert_not_called()
        service.install_mock.assert_called_once_with(control)

    def test_explicit_rollback_requires_an_upgrade_result(self, *, mocker) -> None:
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
