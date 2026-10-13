"""Executable contracts for the serving package boundaries and identity SSOT."""

from __future__ import annotations

import ast
import dataclasses
import json
import socket
import sys
import tempfile
from pathlib import Path
from types import ModuleType

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle.supervision import native_service
from openai_responses_proxy.protocol.replay import projection as replay_request
from openai_responses_proxy.relay import responses
from openai_responses_proxy.relay import sse
from openai_responses_proxy.service import control
from openai_responses_proxy.service import digest
from openai_responses_proxy.service import identity
from openai_responses_proxy.service import inventory
from openai_responses_proxy.service import server

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "openai_responses_proxy"


class _NativeModuleFixture(ModuleType):
    """Minimal platform-module fixture for shared native ownership tests."""

    def install(self, _ctx: runtime_spec.NativeServiceContext) -> None:
        pass

    def configured_executable(self, ctx: runtime_spec.NativeServiceContext) -> str:
        return ctx.executable

    def uninstall(self, _ctx: runtime_spec.NativeServiceContext) -> None:
        pass

    def status(self, _ctx: runtime_spec.NativeServiceContext) -> str:
        return "running"


class ProxyOwnerBoundaryContracts:
    def test_payload_modules_never_reach_into_peer_private_symbols(self) -> None:
        payload = PACKAGE / "lifecycle"
        violations = []
        for path in sorted(payload.glob("*.py")):
            tree = ast.parse(path.read_text())
            peers = {
                alias.asname or alias.name
                for node in tree.body
                if isinstance(node, ast.ImportFrom)
                and node.module == "openai_responses_proxy.lifecycle"
                for alias in node.names
            }
            violations.extend(
                f"{path.relative_to(PACKAGE)}:{node.lineno}:{node.value.id}.{node.attr}"
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in peers
                and node.attr.startswith("_")
            )
        assert violations == []

    def test_payload_modules_never_forward_peer_symbols(self) -> None:
        payload = PACKAGE / "lifecycle"
        violations = []
        allowed = {
            "projection.py": {
                "RUNTIME_PAYLOAD_FILES",
                "SERVING_PAYLOAD_FILES",
                "PAYLOAD_MANIFEST_FILENAME",
                "RELEASE_RECEIPT_FILENAME",
            }
        }
        for path in sorted(payload.glob("*.py")):
            tree = ast.parse(path.read_text())
            peers = {
                alias.asname or alias.name
                for node in tree.body
                if isinstance(node, ast.ImportFrom)
                and node.module == "openai_responses_proxy.lifecycle"
                for alias in node.names
            }
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {target.id for target in targets if isinstance(target, ast.Name)}
                if (
                    isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and value.value.id in peers
                    and not names.issubset(allowed.get(path.name, set()))
                ):
                    violations.append(
                        f"{path.relative_to(PACKAGE)}:{node.lineno}:{value.value.id}.{value.attr}"
                    )
        assert violations == []

    def test_semantic_owners_expose_direct_runtime_surfaces(self) -> None:
        assert callable(sse.relay)
        assert callable(responses.relay)
        assert callable(control.send_status)
        assert issubclass(server.Handler, object)

    def test_entrypoint_composes_without_redefining_owner_symbols(self) -> None:
        tree = ast.parse((PACKAGE / "service" / "entrypoint.py").read_text())
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert {"create_server", "run"}.issubset(definitions)
        assert {
            "Handler",
            "main",
            "sanitize_responses_body",
            "stream_sanitized_sse",
        }.isdisjoint(definitions)

    def test_owner_modules_never_import_the_entrypoint(self) -> None:
        for relative in ("lifecycle/control.py", "service/server.py", "relay/sse.py"):
            tree = ast.parse((PACKAGE / relative).read_text())
            imports = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            assert "openai_responses_proxy.service.entrypoint" not in imports, relative

    def test_response_transport_has_no_listener_facade(self) -> None:
        assert not (PACKAGE / "service" / "responses.py").exists()
        for relative in ("relay/responses.py", "relay/exchange.py", "relay/relay.py"):
            assert (PACKAGE / relative).is_file(), relative

    def test_packages_are_declarations_not_reexport_facades(self) -> None:
        for relative in (
            "__init__.py",
            "cli/__init__.py",
            "lifecycle/__init__.py",
            "protocol/__init__.py",
            "providers/__init__.py",
            "relay/__init__.py",
            "service/__init__.py",
        ):
            tree = ast.parse((PACKAGE / relative).read_text())
            assert not any(
                isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)
            ), relative

    def test_runtime_state_is_split_into_concrete_semantic_owners(self, subtests) -> None:
        assert not (PACKAGE / "runtime" / "state.py").exists()
        owners = {
            "relay/admission.py": "admit_response",
            "relay/operational_log.py": "log",
            "relay/telemetry.py": "record_counter",
            "relay/cooldown.py": "remember_failure",
        }
        for relative, public_symbol in owners.items():
            with subtests.test(relative=relative):
                source = (PACKAGE / relative).read_text(encoding="utf-8")
                tree = ast.parse(source)
                definitions = {
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                }
                assert public_symbol in definitions

        offenders = []
        for path in sorted(PACKAGE.rglob("*.py")):
            if path.name == "__init__.py":
                continue
            source = path.read_text(encoding="utf-8")
            if "openai_responses_proxy.runtime import state" in source:
                offenders.append(path.relative_to(PACKAGE).as_posix())
        assert offenders == []

    def test_replay_projection_returns_structured_metrics(self) -> None:
        result = replay_request.sanitize_responses_body(
            json.dumps(
                {
                    "store": True,
                    "previous_response_id": "rs_private",
                    "input": [
                        {"type": "reasoning", "encrypted_content": "private"},
                        {"type": "message", "role": "user", "content": "hello"},
                    ],
                }
            ).encode()
        )
        assert isinstance(result, replay_request.ProjectionResult)
        assert result.status == "projected"
        assert result.metrics.reasoning_items == 1
        assert result.metrics.provider_bindings == 1
        assert "private" not in result.diagnostic()

    def test_server_bindings_are_required_and_immutable(self, *, mocker) -> None:
        providers = mocker.sentinel.providers
        bindings = server.Bindings(
            control=control.Bindings(mocker.Mock(return_value={}), mocker.Mock()),
            providers=providers,
            server_version="dmx/test",
        )
        assert bindings.providers is providers
        attribute = "server_version"
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(bindings, attribute, "dmx/other")

    def test_server_routes_each_method_to_one_owner(self, subtests, *, mocker) -> None:
        handler = object.__new__(server.Handler)
        bindings = control.Bindings(mocker.Mock(return_value={}), mocker.Mock())
        providers = mocker.sentinel.providers
        handler.server = mocker.Mock(
            bindings=server.Bindings(
                control=bindings,
                providers=providers,
                server_version="dmx/test",
            )
        )
        drain = mocker.patch.object(control, "set_drain")
        handoff = mocker.patch.object(control, "prepare_handoff")
        status = mocker.patch.object(control, "send_status")
        relay = mocker.patch.object(responses, "relay")
        for method, path, owner, arguments in (
            ("do_POST", "/control/drain", drain, (True,)),
            ("do_POST", "/control/handoff", handoff, (bindings,)),
            ("do_POST", "/v1/responses", relay, ("POST", providers)),
            ("do_GET", "/healthz", status, (bindings,)),
            ("do_GET", "/v1/responses", relay, ("GET", providers)),
            ("do_DELETE", "/control/drain", drain, (False,)),
            ("do_DELETE", "/v1/responses/resp", relay, ("DELETE", providers)),
            ("do_PATCH", "/v1/responses/resp", relay, ("PATCH", providers)),
            ("do_PUT", "/v1/responses", relay, ("PUT", providers)),
        ):
            with subtests.test(method=method, path=path):
                for candidate in (drain, handoff, status, relay):
                    candidate.reset_mock()
                handler.path = path
                getattr(handler, method)()
                owner.assert_called_once_with(handler, *arguments)

    def test_loaded_identity_accepts_only_exact_native_projection(self, subtests) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / inventory.EXECUTABLE
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"native executable\n")
            provider_manifest = root / inventory.PROVIDER_MANIFEST
            provider_manifest.write_text(
                'version = 1\n[providers.fixture]\nbase_url = "https://fixture.invalid/v1"\n',
                encoding="utf-8",
            )
            files = {
                relative: digest.sha256_file(root / relative)
                for relative in inventory.required_runtime_files()
            }
            manifest = {
                "release": "1.2.3",
                "release_receipt_sha256": "a" * 64,
                "serving_files": files,
                "serving_payload_sha256": inventory.validated_serving_payload_sha256(files),
            }
            _write_manifest(root, manifest)
            frozen = identity.freeze_loaded_payload(executable)
            assert frozen is not None
            wrong_executable = root / "other"
            wrong_executable.write_bytes(b"other")
            cases = (
                ("wrong executable", None, wrong_executable),
                ("non-object files", {"serving_files": []}, executable),
                (
                    "non-string digest",
                    {"serving_files": {inventory.EXECUTABLE: 1}},
                    executable,
                ),
                (
                    "incomplete files",
                    {"serving_files": {inventory.EXECUTABLE: files[inventory.EXECUTABLE]}},
                    executable,
                ),
                (
                    "wrong digest",
                    {"serving_files": {**files, inventory.EXECUTABLE: "0" * 64}},
                    executable,
                ),
                ("wrong aggregate", {"serving_payload_sha256": "0" * 64}, executable),
                ("bad receipt", {"release_receipt_sha256": "bad"}, executable),
                ("missing release", {"release": ""}, executable),
            )
            for name, changes, candidate in cases:
                with subtests.test(name=name):
                    if changes:
                        _write_manifest(root, {**manifest, **changes})
                    assert identity.freeze_loaded_payload(candidate) is None
                    _write_manifest(root, manifest)
            _write_manifest(root, [])
            assert identity.freeze_loaded_payload(executable) is None
            _write_manifest(root, manifest)
            assert identity.freeze_loaded_payload(executable) is not None

    def test_server_hardening_and_handoff_listener_adoption(self, *, mocker) -> None:
        connection = mocker.Mock()
        server._disable_nagle(connection)
        connection.setsockopt.assert_called_once_with(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        connection.setsockopt.side_effect = OSError
        server._disable_nagle(connection)
        mocker.patch.object(socket, "getfqdn", side_effect=AssertionError("DNS lookup"))
        created = server.ResilientProxyServer(
            ("127.0.0.1", 0), server.Handler, mocker.sentinel.bindings
        )
        try:
            assert created.server_name == "127.0.0.1"
            assert created.server_port == created.server_address[1]
        finally:
            created.server_close()

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        mocker.patch.object(socket, "getfqdn", side_effect=AssertionError("DNS lookup"))
        adopted = server.server_from_listener(listener, mocker.sentinel.bindings)
        assert adopted.socket is listener
        assert adopted.server_address == listener.getsockname()
        assert adopted.server_name == "127.0.0.1"
        assert adopted.server_port == adopted.server_address[1]
        adopted.server_close()

    def test_server_tracks_handlers_and_classifies_disconnects(self, *, mocker) -> None:
        proxy = object.__new__(server.ResilientProxyServer)
        process = mocker.patch("http.server.ThreadingHTTPServer.process_request_thread")
        begin = mocker.patch.object(server.admission, "begin_handler")
        end = mocker.patch.object(server.admission, "end_handler")
        server.ResilientProxyServer.process_request_thread(proxy, None, None)
        begin.assert_called_once()
        process.assert_called_once()
        end.assert_called_once()
        process.side_effect = RuntimeError
        with pytest.raises(RuntimeError):
            server.ResilientProxyServer.process_request_thread(proxy, None, None)
        assert end.call_count == 2
        log = mocker.patch.object(server.operational_log, "log")
        try:
            raise BrokenPipeError("closed")
        except BrokenPipeError:
            server.ResilientProxyServer.handle_error(proxy, None, None)
        log.assert_called_once()
        fallback = mocker.patch("http.server.ThreadingHTTPServer.handle_error")
        try:
            raise RuntimeError("failed")
        except RuntimeError:
            server.ResilientProxyServer.handle_error(proxy, None, None)
        fallback.assert_called_once()

    def test_native_service_selection_is_total_for_supported_hosts(
        self, subtests, *, mocker
    ) -> None:
        for platform, expected in (
            ("darwin", "macos"),
            ("linux", "linux"),
            ("linux2", "linux"),
            ("win32", "windows"),
            ("cygwin", "windows"),
        ):
            mocker.patch.object(sys, "platform", platform)
            with subtests.test(platform=platform):
                assert native_service.adapter().__name__.rsplit(".", 1)[-1] == expected
        mocker.patch.object(sys, "platform", "plan9")
        with pytest.raises(RuntimeError, match="unsupported platform: plan9"):
            native_service.adapter()

    def test_native_service_selection_reports_incomplete_product(self, *, mocker) -> None:
        real_import = __import__

        def missing_adapters(name, *args, **kwargs):
            if name.endswith("lifecycle.supervision"):
                raise ImportError("missing bundled adapter")
            return real_import(name, *args, **kwargs)

        mocker.patch("builtins.__import__", side_effect=missing_adapters)
        with pytest.raises(errors.ProductAssemblyError, match="installation is incomplete"):
            native_service._platform_adapters()

    def test_cygwin_selection_reports_missing_windows_adapter(self, *, mocker) -> None:
        real_import = __import__

        def missing_windows(name, globals_=None, locals_=None, fromlist=(), level=0):
            if name.endswith("lifecycle.supervision") and fromlist == ("windows",):
                raise ImportError("missing bundled Windows adapter")
            return real_import(name, globals_, locals_, fromlist, level)

        mocker.patch.object(native_service, "_platform_adapters", return_value=())
        mocker.patch.object(sys, "platform", "cygwin")
        mocker.patch("builtins.__import__", side_effect=missing_windows)
        with pytest.raises(errors.ProductAssemblyError, match="installation is incomplete"):
            native_service.adapter()

    @pytest.mark.parametrize(
        "role",
        [
            "--internal-listener",
            "--internal-handoff-child",
            "--internal-watchdog",
            "--internal-prewarm",
        ],
    )
    def test_retired_generation_stops_each_owned_service_role(self, role: str, *, mocker) -> None:
        ctx = mocker.Mock(executable="/installed/generations/retired/bin/proxy")
        runtime = native_service._NativeRuntime(_NativeModuleFixture("fixture.native"))
        live = {41}

        def inventory_for(executable, *, roles):
            assert executable == ctx.executable
            return sorted(live) if role in roles else []

        def stop(pid, executable, *, roles, timeout_seconds):
            assert executable == ctx.executable
            assert role in roles
            assert timeout_seconds == 2.5
            live.remove(pid)
            return True

        mocker.patch.object(native_service.process, "pids_naming_executable", inventory_for)
        mocker.patch.object(native_service.process, "terminate_executable", stop)

        assert runtime.terminate_runtime(ctx, timeout_seconds=2.5) == 1
        assert live == set()

    def test_native_runtime_owns_exact_private_product_processes(self, subtests, *, mocker) -> None:
        ctx = mocker.Mock(executable="/installed/openai-responses-proxy")
        implementation = _NativeModuleFixture("fixture.native")
        runtime = native_service._NativeRuntime(implementation)
        mocker.patch.object(
            native_service.process,
            "pids_naming_executable",
            return_value=[41, 73],
        )

        for terminated, inventories, expected, message in (
            (True, [[41, 73], []], 2, None),
            (False, [[41]], None, "exit is unproved"),
            (True, [[41], [73]], None, "processes remain"),
        ):
            with subtests.test(message=message or "success"):
                mocker.patch.object(
                    native_service.process,
                    "pids_naming_executable",
                    side_effect=inventories,
                )
                terminate = mocker.patch.object(
                    native_service.process,
                    "terminate_executable",
                    return_value=terminated,
                )
                if message is None:
                    assert runtime.terminate_runtime(ctx, timeout_seconds=2.5) == expected
                    assert terminate.call_count == 2
                else:
                    with pytest.raises(errors.InstallError, match=message):
                        runtime.terminate_runtime(ctx, timeout_seconds=2.5)
                mocker.stopall()


def _write_manifest(root: Path, manifest: object) -> None:
    (root / inventory.MANIFEST_FILENAME).write_text(json.dumps(manifest))
