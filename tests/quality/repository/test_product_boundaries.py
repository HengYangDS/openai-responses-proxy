"""Product composition and package dependency ownership contracts."""

from __future__ import annotations

import ast
import importlib.util

from openai_responses_proxy.relay import responses
from openai_responses_proxy.relay import sse
from openai_responses_proxy.serving import control
from openai_responses_proxy.serving import server
from tests.quality.fixtures import ROOT

PACKAGE = ROOT / "src/openai_responses_proxy"


def test_payload_modules_never_reach_into_peer_private_symbols() -> None:
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
            and (node.value.id in peers)
            and node.attr.startswith("_")
        )
    assert violations == []


def test_payload_modules_never_forward_peer_symbols() -> None:
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
                and (value.value.id in peers)
                and (not names.issubset(allowed.get(path.name, set())))
            ):
                violations.append(
                    f"{path.relative_to(PACKAGE)}:{node.lineno}:{value.value.id}.{value.attr}"
                )
    assert violations == []


def test_semantic_owners_expose_direct_runtime_surfaces() -> None:
    assert callable(sse.relay)
    assert callable(responses.relay)
    assert callable(control.send_status)
    assert issubclass(server.Handler, object)


def test_entrypoint_composes_without_redefining_owner_symbols() -> None:
    tree = ast.parse((PACKAGE / "serving" / "entrypoint.py").read_text())
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {"create_server", "run"}.issubset(definitions)
    assert {"Handler", "main", "sanitize_responses_body", "stream_sanitized_sse"}.isdisjoint(
        definitions
    )


def test_owner_modules_never_import_the_entrypoint() -> None:
    for relative in ("lifecycle/control.py", "serving/server.py", "relay/sse.py"):
        tree = ast.parse((PACKAGE / relative).read_text())
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert "openai_responses_proxy.serving.entrypoint" not in imports, relative


def test_response_transport_has_no_listener_facade() -> None:
    assert not (PACKAGE / "serving" / "responses.py").exists()
    for relative in ("relay/responses.py", "relay/exchange.py", "relay/downstream.py"):
        assert (PACKAGE / relative).is_file(), relative


def test_packages_are_declarations_not_reexport_facades() -> None:
    for relative in (
        "__init__.py",
        "cli/__init__.py",
        "lifecycle/__init__.py",
        "protocol/__init__.py",
        "providers/__init__.py",
        "relay/__init__.py",
        "serving/__init__.py",
    ):
        tree = ast.parse((PACKAGE / relative).read_text())
        assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)), (
            relative
        )


def test_runtime_state_is_split_into_concrete_semantic_owners(subtests) -> None:
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


def test_protocol_packages_follow_replay_and_recovery_ownership() -> None:
    """Replay projection and failure recovery have explicit protocol owners."""
    concerns = {"replay": ("projection", "content", "items"), "recovery": ("input", "execution")}
    for package, modules in concerns.items():
        name = f"openai_responses_proxy.protocol.{package}"
        owner = importlib.util.find_spec(name)
        assert owner is not None
        assert owner.submodule_search_locations is not None
        for module in modules:
            assert importlib.util.find_spec(f"{name}.{module}") is not None


def test_verified_native_artifact_has_one_semantic_owner() -> None:
    """The runtime transaction consumes an admitted artifact, never source checkout state."""
    lifecycle = PACKAGE / "lifecycle"
    assert (lifecycle / "artifact.py").is_file()
    assert not (lifecycle / "source.py").exists()
    artifact = (lifecycle / "artifact.py").read_text(encoding="utf-8")
    installer = (lifecycle / "install.py").read_text(encoding="utf-8")
    assert "def admit(" in artifact
    for implementation_detail in ("tarfile", "ssh-keygen", "_verify_signature"):
        assert implementation_detail not in installer
