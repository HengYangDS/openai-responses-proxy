"""Published predecessor journey across the payload-shape change of 8.0.0.

A predecessor's payload holds a provider manifest that this release no longer ships, so its
listener cannot hand off to this release. This release refuses such an installation, names its
removal and changes nothing; the release that wrote it removes it; this release then installs
fresh and relays by the upstream that the request path names.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.lifecycle.supervision import process
from openai_responses_proxy.runtime import config as runtime_config
from openai_responses_proxy.runtime.process_environment import native_process_environment
from tests.relay.fixtures import proxy_path
from tests.release import fixtures as native_release
from tests.release.compatibility.fixtures import _assert_same_runtime_identity
from tests.release.compatibility.fixtures import _required_path
from tests.release.compatibility.fixtures import admitted_predecessor
from tests.release.fixtures import preserve_native_host_projection
from tests.serving.handoff.fixtures import ScriptedUpstream
from tests.serving.handoff.fixtures import free_port
from tools.release import verification

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.usefixtures(preserve_native_host_projection.__name__),
    pytest.mark.native_distribution,
]


def test_predecessor_installation_is_refused_then_replaced_by_a_fresh_install(
    tmp_path: Path,
) -> None:
    current_executable = _required_path("OPENAI_RESPONSES_PROXY_NATIVE_EXECUTABLE")
    current_bundle = _required_path("OPENAI_RESPONSES_PROXY_NATIVE_BUNDLE")
    previous_asset = _required_path("OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_ASSET")
    previous_trust = _required_path("OPENAI_RESPONSES_PROXY_PREVIOUS_RELEASE_TRUST_ANCHOR")
    current_version = (native_release.ROOT / "VERSION").read_text(encoding="ascii").strip()
    previous_version, previous_executable = admitted_predecessor(
        tmp_path,
        previous_asset=previous_asset,
        previous_trust=previous_trust,
        current_version=current_version,
    )
    home, install, state = (tmp_path / name for name in ("home", "payload", "state"))
    home.mkdir()
    port = free_port()
    ctx = native_release.runtime_context_for(home, install, state, port)
    environment = native_process_environment(user_home=home, install_root=install, state_root=state)
    canonical_before = process.listener_pids(runtime_config.DEFAULT_PORT)
    output = ("--port", str(port), "--json")
    key, trust, anchor = native_release.signing_inputs(tmp_path)
    current_asset = native_release.signed_asset(
        current_bundle, tmp_path / "current-assets", version=current_version, key=key, trust=trust
    )
    candidate_install = (
        "install",
        "--asset",
        str(current_asset),
        "--trust-anchor",
        str(anchor),
        *output,
    )
    upstream = ScriptedUpstream()
    upstream.start()
    upstream_url = upstream.base_url().replace("127.0.0.1", "localhost")

    def run_previous(*arguments: str, expected: int = 0) -> dict[str, object]:
        return native_release.run_command(
            previous_executable, environment, *arguments, expected=expected
        )

    def run_candidate(*arguments: str, expected: int = 0) -> dict[str, object]:
        return native_release.run_command(
            current_executable, environment, *arguments, expected=expected
        )

    with ExitStack() as cleanups:
        cleanups.callback(upstream.close)
        cleanups.callback(verification.cleanup_runtime, ctx)
        # A real installation's shape: the predecessor's own routes and no upstream override.
        installed = run_previous(
            "install",
            "--asset",
            str(previous_asset),
            "--trust-anchor",
            str(previous_trust),
            *output,
        )
        assert installed["state"] == "installed"
        before = run_previous("status", *output)
        assert before["release"] == previous_version
        selection = generation.read(ctx)
        assert selection is not None
        metadata = records.installed_path(ctx).read_bytes()
        supervision = native_release.native_service_projection(ctx)

        refused = run_candidate(*candidate_install, expected=2)
        error = refused["error"]
        assert isinstance(error, dict)
        assert error["code"] == "lifecycle_error"
        assert "earlier release with another payload shape" in str(error["message"])
        assert error["next"] == "openai-responses-proxy uninstall --purge"
        _assert_same_runtime_identity(before, run_previous("status", *output))
        assert generation.read(ctx) == selection
        assert records.installed_path(ctx).read_bytes() == metadata
        assert native_release.native_service_projection(ctx) == supervision
        assert not records.transaction_root(ctx).exists()

        removed = run_previous("uninstall", "--port", str(port), "--purge", "--json")
        assert removed["state"] == "purged"
        assert not install.exists()
        assert process.listener_pids(port) == []

        fresh = run_candidate(*candidate_install)
        assert fresh["state"] == "installed"
        after = run_candidate("status", *output)
        assert after["release"] == current_version
        assert after["payload_transaction"] is None
        assert run_candidate("doctor", *output)["ok"] is True
        upstream.push((200, b'{"id":"after","status":"completed"}'))
        assert native_release.post_response(port, proxy_path(upstream_url)) == (
            b'{"id":"after","status":"completed"}'
        )

        purged = run_candidate("uninstall", "--port", str(port), "--purge", "--json")
        assert purged["state"] == "purged"
        projection = native_release.native_service_projection(ctx)
        assert projection["status"] == "absent"
        assert projection["configured_executable"] is None
        assert not install.exists()
        assert not records.transaction_root(ctx).exists()
        assert process.listener_pids(port) == []
    assert process.listener_pids(runtime_config.DEFAULT_PORT) == canonical_before
