"""Native teardown across control-flow failure and process ownership."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import psutil
import pytest

from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.runtime.process_environment import native_process_environment
from tests.release.fixtures import ROOT
from tests.release.fixtures import native_service_projection
from tests.release.fixtures import preserve_native_host_projection
from tests.release.fixtures import run_command
from tests.release.fixtures import runtime_context_for
from tests.release.fixtures import signed_asset
from tests.release.fixtures import signing_inputs
from tests.serving.handoff.fixtures import free_port
from tools.release import verification
from tools.release.verification import cleanup_runtime

pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


class TestSignedNativeLifecycle:
    @pytest.mark.parametrize(
        "interruption",
        [None, AssertionError, RuntimeError, TimeoutError, KeyboardInterrupt],
    )
    def test_native_teardown_survives_test_control_flow_failures(
        self,
        tmp_path: Path,
        interruption: type[BaseException] | None,
    ) -> None:
        """Purge the isolated installation even when test control flow aborts."""
        executable_value = os.environ.get("OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE")
        assert executable_value is not None, "native executable must be supplied by release session"
        executable = Path(executable_value).resolve(strict=True)
        workspace = TemporaryDirectory(dir=tmp_path, delete=False)
        with workspace as directory:
            root = Path(directory)
            home, install, state = (
                root / "home",
                root / "payload",
                root / "state",
            )
            home.mkdir()
            port = free_port()
            ctx = runtime_context_for(home, install, state, port)
            environment = native_process_environment(
                user_home=home,
                install_root=install,
                state_root=state,
            )
            isolated_before = native_service_projection(ctx)
            canonical_ctx = runtime_context.create()
            canonical_before = native_service_projection(canonical_ctx)

            key, trust, anchor = signing_inputs(root)
            current_version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
            current_asset = signed_asset(
                executable.parent,
                root / "current-assets",
                version=current_version,
                upstream_url="http://127.0.0.1:1",
                key=key,
                trust=trust,
            )

            owned_contexts: list[runtime_context.RuntimeContext] = []

            def interrupt_installed_lifecycle() -> None:
                with ExitStack() as cleanups:
                    cleanups.callback(cleanup_runtime, ctx)
                    cleanups.callback(
                        run_command,
                        executable,
                        environment,
                        "uninstall",
                        "--port",
                        str(port),
                        "--purge",
                        "--json",
                    )
                    installed = run_command(
                        executable,
                        environment,
                        "install",
                        "--asset",
                        str(current_asset),
                        "--trust-anchor",
                        str(anchor),
                        "--port",
                        str(port),
                        "--json",
                    )
                    assert installed["state"] == "installed"
                    owned_contexts.extend(generation.owned_contexts(ctx))
                    if interruption is not None:
                        raise interruption("simulated test control-flow failure")

            if interruption is None:
                interrupt_installed_lifecycle()
            else:
                with pytest.raises(interruption):
                    interrupt_installed_lifecycle()

            assert native_service_projection(ctx) == isolated_before
            assert native_service_projection(canonical_ctx) == canonical_before
            assert owned_contexts
            assert all(
                native_service_projection(owned)["processes"] == [] for owned in owned_contexts
            )
            assert all(
                not path.exists() and not path.is_symlink()
                for path in (
                    install,
                    Path(ctx.command),
                    records.journal_path(ctx),
                    records.transaction_root(ctx),
                )
            )
        workspace.cleanup()
        assert not Path(directory).exists()

    @pytest.mark.parametrize("phase", ["installed", "before-setup", "setup-failure"])
    @pytest.mark.parametrize("controller", ["pytest", "nox"])
    @pytest.mark.parametrize("fixture", ["signed-install", "detached-listener"])
    def test_native_teardown_reclaims_an_externally_killed_controller(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fixture: str,
        controller: str,
        phase: str,
    ) -> None:
        """Recover exact native custody after pytest or its Nox executor is killed."""
        executable = Path(os.environ["OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE"]).resolve(
            strict=True
        )
        workspace = TemporaryDirectory(dir=tmp_path.parent, prefix="killed-native-", delete=False)
        test_root = Path(workspace.name).resolve()
        contexts = tmp_path / "contexts.jsonl"
        contexts.touch(mode=0o600)
        home, install, state = (test_root / name for name in ("home", "payload", "state"))
        home.mkdir()
        port = free_port()
        ctx = runtime_context_for(home, install, state, port)
        before = native_service_projection(ctx)
        canonical = runtime_context.create()
        canonical_before = native_service_projection(canonical)
        key, trust, anchor = signing_inputs(tmp_path)
        asset = signed_asset(
            executable.parent,
            tmp_path / "killed-assets",
            version=(ROOT / "VERSION").read_text(encoding="ascii").strip(),
            upstream_url="http://127.0.0.1:1",
            key=key,
            trust=trust,
        )
        ready = tmp_path / "installed"
        registered = tmp_path / "registered"
        proceed = tmp_path / "proceed"
        source = tmp_path / "test_killed_native.py"
        setup = (
            f"    ctx = runtime_context_for(home, install, state, {port})\n"
            "    before = native_service_projection(ctx)\n"
            "    env = native_process_environment(user_home=home, install_root=install, state_root=state)\n"
            f"    result = run_command(Path({str(executable)!r}), env, 'install', '--asset', {str(asset)!r}, "
            f"'--trust-anchor', {str(anchor)!r}, '--port', {str(port)!r}, '--json')\n"
            "    assert result['state'] == 'installed'\n"
            if fixture == "signed-install"
            else "    detached = SubprocessFixture()\n"
            "    detached.setup_method()\n"
            f"    root, ctx, _owned = detached._installed_fixture(release='1.0.25', port={port}, upstream_url='http://127.0.0.1:1')\n"
            "    before = native_service_projection(ctx)\n"
            "    start_real_proxy(ctx, upstream_url='http://127.0.0.1:1', log_path=root / 'proxy.log')\n"
        )
        source.write_text(
            "import json, os\n"
            "from pathlib import Path\n"
            "from threading import Event\n"
            "from tests.release.fixtures import run_command, runtime_context_for\n"
            "from tests.release.fixtures import native_service_projection\n"
            "from tests.serving.handoff.subprocess.fixtures import SubprocessFixture\n"
            "from tests.serving.handoff.fixtures import start_real_proxy\n"
            "from openai_responses_proxy.runtime.process_environment import native_process_environment\n"
            "def test_killed_native():\n"
            f"    Path({str(registered)!r}).write_text(json.dumps({{'pid':os.getpid(), 'root':os.environ[{verification._TEST_ROOT!r}], 'custody':os.environ[{verification._CONTEXTS!r}]}}))\n"
            f"    while not Path({str(proceed)!r}).exists():\n"
            "        Event().wait(0.05)\n"
            f"    home, install, state = (Path(os.environ[{verification._TEST_ROOT!r}]) / name for name in ('home', 'payload', 'state'))\n"
            "    home.mkdir(parents=True, exist_ok=True)\n"
            + setup
            + (
                "    raise RuntimeError('setup failed before readiness')\n"
                if phase == "setup-failure"
                else ""
            )
            + f"    Path({str(ready)!r}).touch()\n"
            "    Event().wait(180)\n",
            encoding="utf-8",
        )
        environment = {
            **os.environ,
            verification._TEST_ROOT: str(test_root),
            verification._CONTEXTS: str(contexts),
            "OPENAI_RESPONSES_PROXY_EXECUTABLE": str(executable),
        }
        monkeypatch.delenv(verification._TEST_ROOT, raising=False)
        monkeypatch.delenv(verification._CONTEXTS, raising=False)
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--rootdir",
            str(ROOT),
            "-c",
            str(ROOT / ".config/checks/pytest/pytest.toml"),
            f"--basetemp={tmp_path / 'child-pytest'}",
            str(source),
        ]
        if controller == "nox":
            runner = tmp_path / "noxfile.py"
            runner.write_text(
                "import nox, sys\n"
                f"sys.path.insert(0, {str(ROOT)!r})\n"
                "@nox.session(python=False)\n"
                "def interrupted(session):\n"
                f"    session.chdir({str(ROOT)!r})\n"
                f"    session.run(*{tuple(command)!r})\n",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                "-m",
                "nox",
                "-f",
                str(runner),
                "-s",
                "interrupted",
            ]
        child_processes: dict[int, process.OwnedProcess] = {}
        with (tmp_path / "killed-child.log").open("w") as log:
            child = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            owned_contexts = ()
            try:
                deadline = time.monotonic() + 90
                while (
                    not registered.is_file()
                    and child.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.05)
                assert registered.is_file(), (tmp_path / "killed-child.log").read_text()
                assert child.poll() is None
                observed = json.loads(registered.read_text())
                assert observed["root"] == str(test_root)
                assert observed["custody"] == environment[verification._CONTEXTS]
                if controller == "nox":
                    assert observed["pid"] != child.pid
                pytest_process = process.capture_generation(observed["pid"], sys.executable)
                assert pytest_process is not None
                child_processes[pytest_process.pid] = pytest_process
                if phase != "before-setup":
                    proceed.touch()
                    while (
                        not ready.is_file() and child.poll() is None and time.monotonic() < deadline
                    ):
                        time.sleep(0.05)
                    if phase == "setup-failure":
                        assert child.wait(timeout=10) != 0
                        assert not ready.exists()
                    else:
                        assert ready.is_file(), (tmp_path / "killed-child.log").read_text()
                    retained = tuple(
                        runtime_context.RuntimeContext(**json.loads(line))
                        for line in contexts.read_bytes().splitlines()
                    )
                    assert retained
                    ctx = retained[-1]
                    before = {**before, "service_id": ctx.service_id}
                    install = Path(ctx.install_dir)
                    owned_contexts = generation.owned_contexts(ctx)
                    assert owned_contexts
                    if fixture == "signed-install":
                        assert native_service_projection(ctx)["status"] != "absent"
                    else:
                        assert native_service_projection(ctx)["processes"]
                    assert install.exists()
                else:
                    assert contexts.read_bytes() == b""
                    assert not ready.exists()
                    assert not install.exists()
                if child.poll() is None:
                    child.kill()
                    assert child.wait(timeout=10) != 0
            finally:
                if registered.is_file():
                    observed = json.loads(registered.read_text())
                    assert observed["root"] == str(test_root)
                    assert observed["custody"] == str(contexts)
                    if observed["pid"] not in child_processes:
                        owned = process.capture_generation(observed["pid"], sys.executable)
                        if owned is not None:
                            child_processes[owned.pid] = owned
                if child.poll() is None:
                    for descendant in psutil.Process(child.pid).children(recursive=True):
                        owned = process.capture_generation(descendant.pid, sys.executable)
                        if owned is not None:
                            child_processes.setdefault(owned.pid, owned)
                    child.kill()
                child.wait(timeout=10)
                for owned in child_processes.values():
                    assert process.terminate_owned_process(owned)
                assert not any(
                    process.owned_process_alive(owned) for owned in child_processes.values()
                )
                verification.reclaim(test_root, contexts)
                workspace.cleanup()
        assert native_service_projection(ctx) == before
        assert native_service_projection(canonical) == canonical_before
        assert all(native_service_projection(owned)["processes"] == [] for owned in owned_contexts)
        assert not test_root.exists()
        assert all(
            not path.exists() and not path.is_symlink()
            for path in (
                install,
                Path(ctx.command),
                records.journal_path(ctx),
                records.transaction_root(ctx),
            )
        )
