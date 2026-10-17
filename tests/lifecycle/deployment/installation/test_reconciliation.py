"""Same-artifact preservation and native-supervision reconciliation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import install
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.deployment import apply
from openai_responses_proxy.lifecycle.transaction import execution as transaction
from tests.lifecycle.deployment.installation.fixtures import _unsupervised_installation
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload
from tests.lifecycle.fixtures import released_artifact


@pytest.mark.parametrize(
    "evidence",
    [
        "active",
        "degraded",
        "runtime_absent",
        "different_artifact",
        "different_payload",
        "supervisor_absent",
        "supervisor_replaced",
        "pending_transaction",
    ],
)
def test_install_of_exact_active_artifact_preserves_every_projection(
    tmp_path: Path, evidence: str, *, mocker
):
    ctx = install_context(tmp_path)
    installed = install_payload(ctx, mocker=mocker)
    released = released_artifact(
        executable_content=(
            b"different-native-executable"
            if evidence == "different_artifact"
            else b"native-executable-fixture"
        )
    )
    mocker.patch.object(install.artifact, "admit", return_value=released)
    native = mocker.Mock()
    native.configured_executable.return_value = {
        "supervisor_absent": None,
        "supervisor_replaced": str(tmp_path / "unrelated-executable"),
    }.get(evidence, ctx.executable)
    mocker.patch(
        "openai_responses_proxy.lifecycle.supervision.native_service.adapter", return_value=native
    )
    mocker.patch.object(
        control,
        "status",
        return_value={
            "state": "degraded" if evidence == "degraded" else "running",
            "release": released.version,
            "runtime": (
                None
                if evidence == "runtime_absent"
                else {
                    "release_receipt_sha256": installed.receipt_sha256,
                    "serving_payload_sha256": (
                        "0" * 64
                        if evidence == "different_payload"
                        else installed.expected["serving_payload_sha256"]
                    ),
                }
            ),
        },
    )
    pending = evidence == "pending_transaction"
    if pending:
        mocker.patch.object(records, "status", return_value={"state": "prepared"})
    begin = mocker.spy(transaction, "begin_transaction")
    applied = mocker.patch.object(apply, "install")
    projections = (
        records.installed_path(ctx),
        generation.selector_path(ctx),
        Path(ctx.command),
    )
    before = {path: path.read_bytes() for path in projections}

    if evidence == "active":
        assert install.install_asset(
            ctx, tmp_path / "release.tar.gz", trust_anchor=tmp_path / "allowed-signers"
        ) == {"state": "unchanged", "release": installed.release}
    else:
        error = errors.RecoveryRequiredError if pending else errors.InstallError
        message = "complete payload recovery" if pending else "not the verified active installation"
        with pytest.raises(error, match=message):
            install.install_asset(
                ctx, tmp_path / "release.tar.gz", trust_anchor=tmp_path / "allowed-signers"
            )

    assert {path: path.read_bytes() for path in projections} == before
    assert not records.journal_path(ctx).exists()
    begin.assert_not_called()
    applied.assert_not_called()
    native.install.assert_not_called()
    native.uninstall.assert_not_called()


@pytest.mark.parametrize(
    ("service", "serving", "command_removed"),
    [
        ("absent", False, True),
        ("absent", False, False),
        ("installed", False, False),
        ("absent", True, False),
        ("installed", True, False),
    ],
)
def test_install_of_the_exact_artifact_restores_an_unsupervised_verified_installation(
    tmp_path: Path, service: str, serving: bool, command_removed: bool, *, mocker
) -> None:
    ctx, control_ctx, native, committed, runtime = _unsupervised_installation(
        tmp_path, service=service, serving=serving, mocker=mocker
    )
    if command_removed:
        Path(ctx.command).unlink()
    wait = mocker.patch.object(control.apply, "wait_for_serving_runtime", return_value=runtime)
    begin = mocker.spy(transaction, "begin_transaction")
    applied = mocker.patch.object(apply, "install")
    installed_bytes = records.installed_path(ctx).read_bytes()
    selector_bytes = generation.selector_path(ctx).read_bytes()

    assert install.install_asset(
        ctx,
        tmp_path / "release.tar.gz",
        trust_anchor=tmp_path / "allowed-signers",
        timeout_seconds=12.5,
    ) == {"state": "installed", "runtime": runtime}

    assert command.status(Path(ctx.command), Path(control_ctx.executable))["state"] == "owned"
    native.install.assert_called_once_with(control_ctx)
    wait.assert_called_once_with(
        ctx,
        committed.handoff(),
        runtime_reader=control.read_runtime,
        timeout_seconds=12.5,
    )
    assert records.installed_path(ctx).read_bytes() == installed_bytes
    assert generation.selector_path(ctx).read_bytes() == selector_bytes
    assert not records.journal_path(ctx).exists()
    begin.assert_not_called()
    applied.assert_not_called()
    native.uninstall.assert_not_called()


@pytest.mark.parametrize(
    "evidence",
    [
        "different_artifact",
        "foreign_listener",
        "service_unknown",
        "service_running",
        "other_port",
        "foreign_command",
        "changed_payload",
    ],
)
def test_install_of_the_exact_artifact_refuses_an_unproved_unsupervised_installation(
    tmp_path: Path, evidence: str, *, mocker
) -> None:
    ctx, _control_ctx, native, _committed, _runtime = _unsupervised_installation(
        tmp_path,
        service={"service_unknown": "unknown", "service_running": "running"}.get(
            evidence, "absent"
        ),
        mocker=mocker,
    )
    if evidence == "different_artifact":
        mocker.patch.object(
            install.artifact,
            "admit",
            return_value=released_artifact(executable_content=b"different-native-executable"),
        )
    if evidence == "foreign_listener":
        mocker.patch.object(control.process, "listener_pids", return_value=[99])
    if evidence == "foreign_command":
        Path(ctx.command).unlink()
        Path(ctx.command).write_text("another owner\n", encoding="utf-8")
    if evidence == "changed_payload":
        Path(ctx.executable).write_bytes(b"changed-native-executable")
    requested = replace(ctx, port=ctx.port + 1) if evidence == "other_port" else ctx
    wait = mocker.patch.object(control.apply, "wait_for_serving_runtime")
    begin = mocker.spy(transaction, "begin_transaction")
    command_bytes = Path(ctx.command).read_bytes()
    message = (
        "selected control-plane generation identity is invalid"
        if evidence == "changed_payload"
        else "not the verified active installation"
    )

    with pytest.raises(errors.InstallError, match=message):
        install.install_asset(
            requested, tmp_path / "release.tar.gz", trust_anchor=tmp_path / "allowed-signers"
        )

    assert Path(ctx.command).read_bytes() == command_bytes
    assert not records.journal_path(ctx).exists()
    native.install.assert_not_called()
    native.uninstall.assert_not_called()
    wait.assert_not_called()
    begin.assert_not_called()


def test_install_of_the_exact_artifact_keeps_restored_supervision_when_no_listener_is_proved(
    tmp_path: Path, *, mocker
) -> None:
    ctx, control_ctx, native, _committed, _runtime = _unsupervised_installation(
        tmp_path, service="absent", mocker=mocker
    )
    Path(ctx.command).unlink()
    mocker.patch.object(
        control.apply,
        "wait_for_serving_runtime",
        side_effect=errors.InstallError("released successor did not prove SERVING identity"),
    )

    with pytest.raises(
        errors.InstallError,
        match=r"native supervisor started but no listener proved SERVING identity within 3s",
    ):
        install.install_asset(
            ctx,
            tmp_path / "release.tar.gz",
            trust_anchor=tmp_path / "allowed-signers",
            timeout_seconds=3.0,
        )

    assert command.status(Path(ctx.command), Path(control_ctx.executable))["state"] == "owned"
    native.install.assert_called_once_with(control_ctx)
    native.uninstall.assert_not_called()
    assert not records.journal_path(ctx).exists()
