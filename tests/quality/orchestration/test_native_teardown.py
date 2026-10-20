"""Surviving native executor custody and externally killed pytest acceptance."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from dataclasses import replace
from pathlib import Path

import pytest
from nox.command import CommandFailed

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import generation
from tests.quality.fixtures import ROOT
from tests.release.fixtures import runtime_context_for
from tools.release import verification


def test_surviving_executor_reclaims_an_externally_killed_pytest_child(
    tmp_path: Path, mocker
) -> None:
    """Killing pytest skips its fixture cleanup, not the surviving native owner."""
    ready = tmp_path / "ready"
    source = tmp_path / "test_interrupted.py"
    source.write_text(
        "from pathlib import Path\n"
        "from threading import Event\n"
        "def test_interrupted(tmp_path):\n"
        "    (tmp_path / 'owned.txt').write_text('owned child scratch')\n"
        f"    Path({str(ready)!r}).write_text(str(tmp_path))\n"
        "    Event().wait(60)\n",
        encoding="utf-8",
    )
    session = mocker.Mock()
    failure = CommandFailed("pytest was externally killed", return_code=-9)
    basetemps: list[Path] = []

    def run_child(*command: str, env: dict[str, str | None]) -> None:
        basetemps.append(Path(command[-1].split("=", 1)[1]))
        with (tmp_path / "child.log").open("w") as log:
            child = subprocess.Popen(
                [sys.executable, *command[1:]],
                cwd=ROOT,
                env={
                    key: value for key, value in {**os.environ, **env}.items() if value is not None
                },
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 30
                while not ready.is_file() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert ready.is_file(), (tmp_path / "child.log").read_text()
                assert child.poll() is None
                child.kill()
                assert child.wait(timeout=10) != 0
                assert (Path(ready.read_text()) / "owned.txt").is_file()
                raise failure
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=10)

    session.run.side_effect = run_child
    with pytest.raises(CommandFailed) as raised:
        verification.accept(
            session,
            (
                "python",
                "-m",
                "pytest",
                "-q",
                "--rootdir",
                str(tmp_path),
                "-c",
                str(ROOT / ".config/checks/pytest/pytest.toml"),
                str(source),
            ),
            work=tmp_path / "work",
            environment={},
        )

    assert raised.value is failure
    assert len(basetemps) == 1
    assert not basetemps[0].exists()
    assert list((tmp_path / "work").iterdir()) == []


@pytest.mark.parametrize("root_argument", ["joined", "separate"])
def test_native_acceptance_preserves_an_explicit_fresh_test_root(
    tmp_path: Path, mocker, root_argument: str
) -> None:
    """The selected short native path stays selected and its residue is reclaimed."""
    root = tmp_path / "selected"
    arguments = (f"--basetemp={root}",) if root_argument == "joined" else ("--basetemp", str(root))
    session = mocker.Mock()

    def populate(*_command: str, **_kwargs: object) -> None:
        root.mkdir()
        (root / "owned.txt").write_text("native test scratch", encoding="utf-8")

    session.run.side_effect = populate
    command = ("python", "-m", "pytest", *arguments)
    verification.accept(session, command, work=tmp_path / "work", environment={})

    assert session.run.call_args.args == command
    assert not root.exists()
    assert list((tmp_path / "work").iterdir()) == []


def test_native_acceptance_refuses_existing_scratch_before_child_launch(
    tmp_path: Path, mocker
) -> None:
    """An explicit path cannot become disposal authority for pre-existing user bytes."""
    root = tmp_path / "existing"
    root.mkdir()
    retained = root / "operator.txt"
    retained.write_bytes(b"retained operator bytes")
    session = mocker.Mock()

    with pytest.raises(errors.InstallError, match="fresh owned directory"):
        verification.accept(
            session,
            ("python", "-m", "pytest", f"--basetemp={root}"),
            work=tmp_path / "work",
            environment={},
        )

    session.run.assert_not_called()
    assert retained.read_bytes() == b"retained operator bytes"
    assert list((tmp_path / "work").iterdir()) == []


def test_teardown_rejects_foreign_contexts_before_any_native_effect(tmp_path: Path, mocker) -> None:
    """Preflight every recorded context rather than cleaning a valid prefix first."""
    root = tmp_path / "owned"
    root.mkdir()
    scratch = root / "scratch.txt"
    scratch.write_bytes(b"preserved until complete ownership is proved")
    ctx = runtime_context_for(root / "home", root / "payload", root / "state", 43210)
    foreign = replace(ctx, user_home=str(tmp_path / "operator-home"))
    contexts = tmp_path / "contexts.jsonl"
    contexts.write_text(json.dumps(asdict(ctx)) + "\n" + json.dumps(asdict(foreign)) + "\n")
    cleanup = mocker.patch.object(verification, "cleanup_runtime")

    with pytest.raises(errors.InstallError, match="outside its owned root"):
        verification.reclaim(root, contexts)

    cleanup.assert_not_called()
    assert scratch.read_bytes() == b"preserved until complete ownership is proved"
    assert contexts.is_file()


def test_teardown_preserves_conflicting_contexts_for_one_installation(
    tmp_path: Path, mocker
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    ctx = runtime_context_for(root / "home", root / "payload", root / "state", 43210)
    changed = replace(ctx, port=43211)
    contexts = tmp_path / "contexts.jsonl"
    original = json.dumps(asdict(ctx)) + "\n" + json.dumps(asdict(changed)) + "\n"
    contexts.write_text(original)
    cleanup = mocker.patch.object(verification, "cleanup_runtime")
    with pytest.raises(errors.InstallError, match="ownership changed"):
        verification.reclaim(root, contexts)
    cleanup.assert_not_called()
    assert root.is_dir()
    assert contexts.read_text() == original


@pytest.mark.parametrize("invalid", [b'{"install_dir":', b"{}\n", b"\n"])
def test_teardown_preserves_malformed_custody_before_native_effects(
    tmp_path: Path, mocker, invalid: bytes
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    ctx = runtime_context_for(root / "home", root / "payload", root / "state", 43210)
    contexts = tmp_path / "contexts.jsonl"
    original = json.dumps(asdict(ctx)).encode() + b"\n" + invalid
    contexts.write_bytes(original)
    cleanup = mocker.patch.object(verification, "cleanup_runtime")

    with pytest.raises(errors.InstallError, match="context custody is invalid"):
        verification.reclaim(root, contexts)

    cleanup.assert_not_called()
    assert root.is_dir()
    assert contexts.read_bytes() == original


@pytest.mark.parametrize("invalid", [False, "30", None, float("nan")])
def test_teardown_validates_context_types_before_native_effects(
    tmp_path: Path, mocker, invalid: object
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    ctx = runtime_context_for(root / "home", root / "payload", root / "state", 43210)
    value = asdict(ctx)
    value["upstream_timeout"] = invalid
    contexts = tmp_path / "contexts.jsonl"
    original = json.dumps(value).encode() + b"\n"
    contexts.write_bytes(original)
    cleanup = mocker.patch.object(verification, "cleanup_runtime")

    with pytest.raises(errors.InstallError, match="context custody is invalid"):
        verification.reclaim(root, contexts)

    cleanup.assert_not_called()
    assert root.is_dir()
    assert contexts.read_bytes() == original


def test_surviving_executor_preserves_recovery_custody_after_cleanup_failure(
    tmp_path: Path, mocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = mocker.Mock()
    failure = errors.InstallError("native service observation is unavailable")
    cleanup = mocker.patch.object(verification, "cleanup_runtime", side_effect=failure)
    mocker.patch.object(verification.records, "status", return_value=None)
    purge = mocker.patch.object(verification.uninstall, "uninstall_product")
    retained: list[tuple[Path, Path]] = []

    def populate(*_command: str, env: dict[str, str | None]) -> None:
        selected_root, selected_contexts = env[verification._TEST_ROOT], env[verification._CONTEXTS]
        assert selected_root is not None
        assert selected_contexts is not None
        root, contexts = Path(selected_root), Path(selected_contexts)
        root.mkdir()
        with monkeypatch.context() as selected:
            selected.setenv(verification._TEST_ROOT, str(root))
            selected.setenv(verification._CONTEXTS, str(contexts))
            runtime_context_for(root / "home", root / "payload", root / "state", 43210)
        retained.append((root, contexts))

    session.run.side_effect = populate
    with pytest.raises(errors.InstallError, match="scratch preserved") as raised:
        verification.accept(
            session, ("python", "-m", "pytest"), work=tmp_path / "work", environment={}
        )

    assert raised.value.__cause__ is failure
    root, contexts = retained[0]
    assert root.is_dir()
    assert contexts.is_file()
    assert contexts.read_bytes().endswith(b"\n")
    assert b"\n\n" not in contexts.read_bytes()
    cleanup.assert_called_once()
    purge.assert_not_called()
    assert contexts.parent.is_dir()


def test_native_cleanup_separates_registration_and_process_ownership(
    tmp_path: Path, *, mocker
) -> None:
    """Teardown removes the stable service after its generation leaves the selector."""
    ctx = runtime_context_for(tmp_path / "home", tmp_path / "payload", tmp_path / "state", 43210)
    first = generation.context(ctx, "1" * 32)
    second = generation.context(ctx, "2" * 32)
    for owned in (first, second):
        Path(owned.payload_dir).mkdir(parents=True)
    service = mocker.Mock()
    service.status.return_value = "absent"
    service.configured_executable.side_effect = [second.executable, None]
    mocker.patch.object(verification.native_service, "adapter", return_value=service)
    mocker.patch.object(verification.process, "pids_naming_executable", return_value=[])

    verification.cleanup_runtime(ctx)

    service.uninstall.assert_called_once_with(ctx)


def test_native_cleanup_continues_independent_processes_after_unproved_exit(
    tmp_path: Path, *, mocker
) -> None:
    """Retain the safe failure while still stopping the second owned generation."""
    ctx = runtime_context_for(tmp_path / "home", tmp_path / "payload", tmp_path / "state", 43210)
    service = mocker.Mock()
    service.status.return_value = "absent"
    service.configured_executable.return_value = None
    mocker.patch.object(verification.native_service, "adapter", return_value=service)
    mocker.patch.object(verification, "owned_process_contexts", return_value=(ctx,))
    mocker.patch.object(verification.process, "pids_naming_executable", return_value=[41, 73])
    failure = errors.InstallError(
        "native process termination wait: access denied; exit is unproved"
    )
    terminate = mocker.patch.object(
        verification.process, "terminate_executable", side_effect=[failure, True]
    )

    with pytest.raises(errors.InstallError) as raised:
        verification.cleanup_runtime(ctx)

    assert raised.value is failure
    assert [call.args[0] for call in terminate.call_args_list] == [41, 73]


@pytest.mark.parametrize("arguments", [("--basetemp",), ("--basetemp=/a", "--basetemp=/b")])
def test_native_acceptance_refuses_ambiguous_roots_before_launch(
    tmp_path: Path, mocker, arguments: tuple[str, ...]
) -> None:
    session = mocker.Mock()
    with pytest.raises(errors.InstallError):
        verification.accept(
            session, ("python", "-m", "pytest", *arguments), work=tmp_path, environment={}
        )
    session.run.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_native_acceptance_refuses_missing_parent_before_launch(tmp_path: Path, mocker) -> None:
    session = mocker.Mock()
    with pytest.raises(errors.InstallError, match="parent is unavailable"):
        verification.accept(
            session,
            ("python", "-m", "pytest", f"--basetemp={tmp_path / 'absent' / 'pytest'}"),
            work=tmp_path / "work",
            environment={},
        )
    session.run.assert_not_called()
    assert list((tmp_path / "work").iterdir()) == []


def test_native_context_requires_caller_custody_before_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(verification._TEST_ROOT, str(tmp_path))
    monkeypatch.delenv(verification._CONTEXTS, raising=False)
    with pytest.raises(errors.InstallError, match="custody is unavailable"):
        runtime_context_for(tmp_path / "home", tmp_path / "payload", tmp_path / "state", 43210)
    assert list(tmp_path.iterdir()) == []


def test_native_teardown_recovers_pending_transaction_before_purge(tmp_path: Path, mocker) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    ctx = runtime_context_for(root / "home", root / "payload", root / "state", 43210)
    contexts = tmp_path / "contexts.jsonl"
    contexts.write_text(json.dumps(asdict(ctx)) + "\n")
    mocker.patch.object(verification.records, "status", return_value={"state": "pending"})
    order: list[str] = []
    mocker.patch.object(
        verification.control, "recover", side_effect=lambda _ctx: order.append("recover")
    )
    mocker.patch.object(
        verification, "cleanup_runtime", side_effect=lambda _ctx: order.append("stop")
    )
    mocker.patch.object(
        verification.uninstall,
        "uninstall_product",
        side_effect=lambda _ctx, **_kw: order.append("purge"),
    )
    verification.reclaim(root, contexts)
    assert order == ["recover", "stop", "purge"]
    assert not root.exists()


@pytest.mark.parametrize("wrapper_exit", [True, False, "denied"])
def test_native_cleanup_preserves_independent_exit_proof_after_service_failure(
    tmp_path: Path, mocker, wrapper_exit: bool | str
) -> None:
    ctx = runtime_context_for(tmp_path / "home", tmp_path / "payload", tmp_path / "state", 43210)
    wrapper = tmp_path / "wrapper"
    service = mocker.Mock()
    service.configured_executable.return_value = None
    denied = errors.InstallError("native service manager is unavailable")
    service.uninstall.side_effect = denied
    mocker.patch.object(verification.native_service, "adapter", return_value=service)
    mocker.patch.object(verification.process, "pids_naming_path", return_value=[41])
    terminate = mocker.patch.object(verification.process, "terminate_pid")
    if wrapper_exit == "denied":
        terminate.side_effect = errors.InstallError("wrapper process access denied")
    else:
        terminate.return_value = wrapper_exit
    mocker.patch.object(verification.process, "pids_naming_executable", return_value=[73])
    runtime_exit = mocker.patch.object(
        verification.process, "terminate_executable", return_value=True
    )
    with pytest.raises(errors.InstallError) as raised:
        verification.cleanup_runtime(ctx, wrapper)
    assert raised.value is denied
    terminate.assert_called_once_with(41, expected_path=str(wrapper))
    assert runtime_exit.call_args.args == (73, ctx.executable)
