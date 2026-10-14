"""Installation and recovery operations at the public command boundary."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai_responses_proxy.cli import application
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.supervision import process
from tests.cli.fixtures import invoke
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload


def test_install_delegates_exact_asset_trust_anchor_and_port(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    create = mocker.patch.object(
        application.runtime_context,
        "create",
        return_value=ctx,
    )
    install = mocker.patch.object(
        application.install,
        "install_asset",
        return_value={"state": "installed", "release": "2.0.8"},
    )
    code, stdout, stderr = invoke(
        "install",
        "--asset",
        "/release/proxy.tar.gz",
        "--trust-anchor",
        "/release/trust.json",
        "--port",
        "8801",
    )
    assert code == 0
    assert "Installed" in stdout
    assert "2.0.8" in stdout
    assert not stdout.lstrip().startswith("{")
    assert stderr == ""
    install.assert_called_once_with(
        ctx,
        Path("/release/proxy.tar.gz"),
        trust_anchor=Path("/release/trust.json"),
        timeout_seconds=30.0,
    )
    create.assert_called_once_with(port=8801)


@pytest.mark.parametrize("json_output", [False, True])
def test_unexpected_install_failure_has_a_bounded_public_result(
    tmp_path: Path, json_output: bool, *, mocker
) -> None:
    mocker.patch.object(
        application.runtime_context, "create", return_value=install_context(tmp_path)
    )
    mocker.patch.object(
        application.install,
        "install_asset",
        side_effect=OSError("cannot write /Users/private/payload"),
    )
    arguments = ["install", "--asset", "release.tar.gz", "--trust-anchor", "trust.json"]
    if json_output:
        arguments.append("--json")

    code, stdout, stderr = invoke(*arguments)

    assert code == 2
    assert stdout == ""
    assert "/Users/private/payload" not in stderr
    assert "Traceback" not in stderr
    if json_output:
        assert json.loads(stderr)["error"]["code"] == "internal_error"
    else:
        assert "Action required" in stderr


def test_unexpected_presentation_failure_has_a_bounded_public_result(
    tmp_path: Path, *, mocker
) -> None:
    mocker.patch.object(
        application.runtime_context, "create", return_value=install_context(tmp_path)
    )
    mocker.patch.object(application.install, "install_asset", return_value={"release": "4.0.4"})
    mocker.patch.object(
        application.presentation,
        "render",
        side_effect=RuntimeError("renderer at /Users/private/module.py"),
    )

    code, stdout, stderr = invoke(
        "install", "--asset", "release.tar.gz", "--trust-anchor", "trust.json"
    )

    assert code == 2
    assert stdout == ""
    assert "Action required" in stderr
    assert "/Users/private/module.py" not in stderr
    assert "Traceback" not in stderr


def test_reload_delegates_transactionally_with_an_explicit_timeout(
    tmp_path: Path, *, mocker
) -> None:
    result = {"state": "reloaded", "old_pid": 321, "new_pid": 654}
    ctx = install_context(tmp_path)
    mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    reload = mocker.patch.object(application.control, "reload", return_value=result)
    code, stdout, stderr = invoke("reload", "--json", "--port", "8801", "--timeout-seconds", "12.5")
    assert code == 0
    assert json.loads(stdout) == {**result, "next": "openai-responses-proxy status"}
    assert stderr == ""
    reload.assert_called_once_with(ctx, timeout_seconds=12.5)


def test_install_delegates_with_an_explicit_timeout(tmp_path: Path, *, mocker) -> None:
    asset = tmp_path / "release.tar.gz"
    trust = tmp_path / "release.pub"
    ctx = install_context(tmp_path)
    mocker.patch.object(
        application.runtime_context,
        "create",
        return_value=ctx,
    )
    install = mocker.patch.object(
        application.install,
        "install_asset",
        return_value={"state": "installed", "release": "2.0.8"},
    )

    invoke(
        "install",
        "--asset",
        str(asset),
        "--trust-anchor",
        str(trust),
        "--timeout-seconds",
        "45",
    )

    install.assert_called_once_with(ctx, asset, trust_anchor=trust, timeout_seconds=45.0)


def test_install_of_the_active_artifact_reports_no_change(tmp_path: Path, *, mocker):
    ctx = install_context(tmp_path)
    mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    unchanged = {"state": "unchanged", "release": "3.1.16"}
    mocker.patch.object(application.install, "install_asset", return_value=unchanged)
    arguments = ("install", "--asset", "release.tar.gz", "--trust-anchor", "allowed-signers")

    code, stdout, stderr = invoke(*arguments)
    assert code == 0
    assert "Already installed" in stdout
    assert stderr == ""
    code, stdout, stderr = invoke(*arguments, "--json")
    assert code == 0
    assert json.loads(stdout) == {**unchanged, "next": "openai-responses-proxy status"}
    assert stderr == ""


def test_recover_restores_only_the_runtime_bound_retained_transaction(
    tmp_path: Path, *, mocker
) -> None:
    runtime = {"pid": 321, "release": "2.0.10", "accepting": True}
    ctx = install_context(tmp_path)
    context = mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    mocker.patch.object(application.control, "read_runtime", return_value=runtime)
    recover = mocker.patch.object(
        application.control,
        "recover",
        return_value={"version": "2.0.13", "state": "rolled_back", "transaction_id": "tx"},
    )

    code, stdout, stderr = invoke("recover", "--json", "--port", "8801")

    assert code == 0
    assert stderr == ""
    assert json.loads(stdout) == {
        "state": "rolled_back",
        "version": "2.0.13",
        "transaction_id": "tx",
        "next": "openai-responses-proxy status",
    }
    context.assert_called_once_with(port=8801)
    recover.assert_called_once_with(ctx)


def test_rollback_is_discoverable_and_delegates_to_the_installed_lifecycle(
    tmp_path: Path, *, mocker
) -> None:
    result = {
        "state": "rolled_back",
        "from_release": "3.0.6",
        "to_release": "3.0.5",
    }
    ctx = install_context(tmp_path)
    context = mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    rollback = mocker.patch.object(application.control, "rollback", return_value=result)

    code, stdout, stderr = invoke(
        "rollback",
        "--to-release",
        "3.0.5",
        "--json",
        "--port",
        "8801",
        "--timeout-seconds",
        "12.5",
    )

    assert code == 0
    assert stderr == ""
    assert json.loads(stdout) == {**result, "next": "openai-responses-proxy status"}
    context.assert_called_once_with(port=8801)
    rollback.assert_called_once_with(
        ctx,
        to_release="3.0.5",
        timeout_seconds=12.5,
    )


def test_rollback_requires_an_explicit_target_before_lifecycle_dispatch(*, mocker) -> None:
    rollback = mocker.patch.object(application.control, "rollback")

    code, stdout, stderr = invoke("rollback", "--json")

    assert code == 2
    assert stdout == ""
    assert "requires an argument" in stderr.casefold()
    assert "to-release" in stderr
    rollback.assert_not_called()


def test_rollback_to_the_current_release_reports_an_unchanged_terminal_state(
    tmp_path: Path, *, mocker
) -> None:
    unchanged = {"state": "unchanged", "release": "3.0.6"}
    ctx = install_context(tmp_path)
    mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    mocker.patch.object(application.control, "rollback", return_value=unchanged)

    json_code, json_stdout, json_stderr = invoke("rollback", "--to-release", "3.0.6", "--json")
    human_code, human_stdout, human_stderr = invoke("rollback", "--to-release", "3.0.6")

    assert json_code == human_code == 0
    assert json.loads(json_stdout) == {**unchanged, "next": "openai-responses-proxy status"}
    assert "Already selected" in human_stdout
    assert "3.0.6" in human_stdout
    assert json_stderr == human_stderr == ""


@pytest.mark.parametrize("json_output", [False, True])
def test_recover_projects_one_precise_invalid_state_contract(
    tmp_path: Path,
    json_output: bool,
    *,
    mocker,
) -> None:
    ctx = install_context(tmp_path)
    transaction_root = Path(records.transaction_root(ctx))
    transaction_root.mkdir(parents=True)
    journal = Path(records.journal_path(ctx))
    journal.write_bytes(b"{not-json\n")
    before = journal.read_bytes()
    mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    mocker.patch.object(application.control, "read_runtime", return_value={"pid": 321})

    arguments = ("recover", "--json") if json_output else ("recover",)
    code, stdout, stderr = invoke(*arguments)

    assert code == 2
    assert stdout == ""
    assert "Traceback" not in stderr
    assert "warning" not in stderr.casefold()
    if json_output:
        assert json.loads(stderr) == {
            "error": {
                "code": "recovery_state_invalid",
                "message": "payload transaction journal is malformed JSON",
                "next": "openai-responses-proxy status --json",
            }
        }
    else:
        assert "payload transaction journal is malformed JSON" in stderr
        assert "openai-responses-proxy status --json" in stderr
    assert journal.read_bytes() == before


def test_uninstall_removes_only_the_owned_service_unless_purge_is_requested(
    tmp_path: Path, *, mocker
) -> None:
    ctx = install_context(tmp_path)
    mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    uninstall = mocker.patch.object(
        application.uninstall,
        "uninstall_product",
        return_value={
            "state": "uninstalled",
            "stopped": 1,
            "command_removed": True,
        },
    )
    code, stdout, stderr = invoke("uninstall", "--port", "8801")
    assert code == 0
    assert "Uninstalled" in stdout
    assert "1" in stdout
    assert not stdout.lstrip().startswith("{")
    assert stderr == ""
    uninstall.assert_called_once_with(ctx, purge=False)
    uninstall = mocker.patch.object(
        application.uninstall,
        "uninstall_product",
        return_value={"state": "purged", "stopped": 0, "command_removed": True},
    )
    code, stdout, stderr = invoke("uninstall", "--purge")
    assert code == 0
    assert stderr == ""
    assert "Purged" in stdout
    assert not stdout.lstrip().startswith("{")
    uninstall.assert_called_once_with(ctx, purge=True)


def test_pristine_recover_and_uninstall_are_explicit_no_ops(tmp_path: Path, *, mocker) -> None:
    ctx = install_context(tmp_path)
    context = mocker.patch.object(application.runtime_context, "create", return_value=ctx)
    mocker.patch.object(application.control, "read_runtime", return_value=None)
    recover = mocker.patch.object(
        application.control, "recover", return_value={"state": "not_required"}
    )

    code, stdout, stderr = invoke("recover", "--json")

    assert code == 0
    assert json.loads(stdout) == {"state": "not_required", "next": None}
    assert stderr == ""
    context.assert_called_once_with(port=8792)
    recover.assert_called_once_with(ctx)

    uninstall = mocker.patch.object(
        application.uninstall,
        "uninstall_product",
        return_value={
            "state": "not_installed",
            "stopped": 0,
            "command_removed": False,
        },
    )
    code, stdout, stderr = invoke("uninstall", "--purge", "--json")
    assert code == 0
    assert json.loads(stdout)["state"] == "not_installed"
    assert stderr == ""
    uninstall.assert_called_once_with(ctx, purge=True)


@pytest.mark.parametrize("windows", [False, True], ids=["unix", "windows"])
@pytest.mark.parametrize("interruption", ["payload", "journal"])
def test_public_recovery_reconstructs_context_after_interrupted_purge(
    tmp_path: Path, interruption: str, *, windows: bool, mocker
) -> None:
    """The public lifecycle reconstructs removal from declared roots, not old objects."""
    ctx = install_context(tmp_path, windows=windows)
    install_payload(ctx, mocker=mocker)
    config = application.runtime_context.config
    mocker.patch.object(
        config,
        "os",
        SimpleNamespace(name="nt" if windows else "posix", environ=config.os.environ),
    )
    mocker.patch.object(config, "data_dir", return_value=ctx.install_dir)
    mocker.patch.object(config, "state_dir", return_value=ctx.log_dir)
    mocker.patch.object(config, "home_dir", return_value=ctx.user_home)
    service = mocker.Mock()
    service.status.return_value = "absent"
    service.terminate_runtime.return_value = 0
    mocker.patch.object(application.uninstall, "adapter", return_value=service)
    mocker.patch.object(application.control, "adapter", return_value=service)
    mocker.patch.object(process, "verified_proxy_listener_pids", return_value=[])
    mocker.patch.object(application.control, "read_runtime", return_value=None)
    unlink = Path.unlink
    interrupted = Path(ctx.executable) if interruption == "payload" else records.journal_path(ctx)

    def fail(path: Path, *args, **kwargs) -> None:
        if path == interrupted:
            raise PermissionError("interrupted purge")
        unlink(path, *args, **kwargs)

    failure = mocker.patch.object(Path, "unlink", fail)
    code, _stdout, stderr = invoke("uninstall", "--purge", "--json")
    assert code == 2
    assert "failed" in stderr
    mocker.stop(failure)

    code, stdout, stderr = invoke("status", "--json")
    assert code == 0
    assert json.loads(stdout)["state"] == "recovery_required"
    assert stderr == ""
    code, stdout, stderr = invoke("recover", "--json")
    assert code == 0
    assert json.loads(stdout)["state"] == "purged"
    assert stderr == ""
    assert not Path(ctx.install_dir).exists()
    assert records.status(ctx) is None
    code, stdout, stderr = invoke("uninstall", "--purge", "--json")
    assert code == 0
    assert json.loads(stdout)["state"] == "not_installed"
    assert stderr == ""


def test_install_dispatches_to_its_single_owner(tmp_path: Path, *, mocker) -> None:
    asset = Path("release.tar.gz")
    anchor = Path("allowed-signers")
    ctx = install_context(tmp_path)
    mocker.patch.object(
        application.runtime_context,
        "create",
        return_value=ctx,
    )
    install = mocker.patch.object(application.install, "install_asset", return_value={"ok": True})
    assert application.dispatch("install", asset=asset, trust_anchor=anchor, port=8801) == {
        "ok": True
    }
    install.assert_called_once_with(ctx, asset, trust_anchor=anchor, timeout_seconds=30.0)
