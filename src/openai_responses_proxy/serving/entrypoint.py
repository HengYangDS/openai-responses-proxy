"""Private listener composition for OpenAI Responses Proxy.

Pure request policies, SSE framing, upstream orchestration, lifecycle HTTP
controls, and server routing live in dedicated modules.  This file owns only
release identity, process bindings, and startup.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path

from openai_responses_proxy import product_identity
from openai_responses_proxy.providers import registry as provider_registry
from openai_responses_proxy.relay import admission
from openai_responses_proxy.relay import exchange
from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import sse
from openai_responses_proxy.relay import telemetry
from openai_responses_proxy.runtime import config as runtime_config
from openai_responses_proxy.serving import control
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import roles
from openai_responses_proxy.serving import server
from openai_responses_proxy.serving.handoff import transaction as handoff


@dataclass(frozen=True, slots=True)
class Bootstrap:
    """Verified release identity and routes frozen for one listener process."""

    executable: Path
    payload: identity.LoadedPayloadIdentity
    providers: provider_registry.Registry


def bootstrap(executable: Path | None = None) -> Bootstrap:
    """Verify one installed payload, load its routes, then reject identity drift."""
    selected = Path(roles.current_executable()) if executable is None else executable
    loaded = identity.freeze_loaded_payload(selected)
    if loaded is None:
        raise RuntimeError("installed payload identity is unavailable")
    providers = provider_registry.load(loaded.root / "providers.toml")
    providers = providers.with_upstream(
        runtime_config.os.environ.get(runtime_config.UPSTREAM_BASE_URL_ENV, "")
    )
    if identity.freeze_loaded_payload(selected) != loaded:
        raise RuntimeError("installed payload identity changed during startup")
    return Bootstrap(selected, loaded, providers)


@dataclass(slots=True)
class _ListenerProcess:
    """This listener process's verified bootstrap and its serving instance."""

    bootstrap: Bootstrap | None = None
    server: ThreadingHTTPServer | None = None


_PROCESS = _ListenerProcess()


def _payload() -> identity.LoadedPayloadIdentity | None:
    return None if _PROCESS.bootstrap is None else _PROCESS.bootstrap.payload


def loaded_serving_payload_sha256() -> str | None:
    """Return the aggregate serving identity frozen before listener startup."""
    payload = _payload()
    return None if payload is None else payload.serving_payload_sha256


def release_receipt_sha256() -> str | None:
    """Return the released-source receipt identity frozen before startup."""
    payload = _payload()
    return None if payload is None else payload.release_receipt_sha256


def payload_manifest_sha256() -> str | None:
    """Return the manifest identity frozen before listener startup."""
    payload = _payload()
    return None if payload is None else payload.manifest_sha256


def release_version() -> str:
    """Return the release identity frozen with the serving payload."""
    payload = _payload()
    return "0+unknown" if payload is None else payload.release


def runtime_providers() -> provider_registry.Registry:
    """Return routes frozen from the exact verified installation payload."""
    if _PROCESS.bootstrap is None:
        raise RuntimeError("listener startup has not verified an installed payload")
    return _PROCESS.bootstrap.providers


def _server_bindings(
    providers: provider_registry.Registry | None = None,
) -> server.Bindings:
    """Compose immutable dependencies for one listener generation."""
    return server.Bindings(
        control=control.Bindings(
            runtime_status=runtime_status,
            handoff_context=_handoff_context,
        ),
        providers=runtime_providers() if providers is None else providers,
        server_version=f"{product_identity.PRODUCT_SLUG}/{release_version()}",
    )


def _set_server_instance(server: ThreadingHTTPServer) -> None:
    _PROCESS.server = server


def _handoff_context() -> handoff.Context:
    """Bind the rolling-handoff state machine to process-owned primitives."""
    executable = (
        Path(roles.current_executable())
        if _PROCESS.bootstrap is None
        else _PROCESS.bootstrap.executable
    )
    return handoff.Context(
        executable=executable,
        state_root=Path(runtime_config.state_dir()),
        successor_executable=lambda: identity.selected_payload_executable(executable),
        release_version=release_version,
        serving_payload_sha256=loaded_serving_payload_sha256,
        release_receipt_sha256=release_receipt_sha256,
        payload_manifest_sha256=payload_manifest_sha256,
        committed_payload=lambda: identity.committed_payload(
            identity.selected_payload_executable(executable)
        ),
        response_gate_lock=admission.response_gate_lock(),
        draining=admission.is_draining,
        active_responses=admission.active_responses,
        active_handlers=admission.active_handlers,
        log=operational_log.log,
        server_factory=lambda listener: server.server_from_listener(listener, _server_bindings()),
        set_server_instance=_set_server_instance,
    )


def runtime_status() -> dict[str, object]:
    """Compose process-local state with rolling-handoff runtime identity."""
    return telemetry.status(
        release=release_version(),
        serving_payload_sha256=loaded_serving_payload_sha256(),
        release_receipt_sha256=release_receipt_sha256(),
        admission=admission.snapshot(),
        runtime_identity=handoff.runtime_identity(_handoff_context()),
    )


def create_server(
    address: tuple[str, int] | None = None,
    *,
    providers: provider_registry.Registry | None = None,
) -> server.ResilientProxyServer:
    """Create one configured loopback server for startup and integration tests."""
    return server.ResilientProxyServer(
        address or (runtime_config.listener_host(), runtime_config.listener_port()),
        server.Handler,
        _server_bindings(providers),
    )


def run(*, handoff_child: bool = False) -> int:
    """Run the normal listener or a protocol-v2 handoff child."""
    try:
        _PROCESS.bootstrap = bootstrap()
    except (OSError, RuntimeError, ValueError) as exc:
        operational_log.log(
            f"event=payload_identity_error exception={operational_log.safe_exception_label(exc)}"
        )
        return 2
    try:
        host = runtime_config.listener_host()
        port = runtime_config.listener_port()
    except runtime_config.ConfigurationError as exc:
        operational_log.log(f"event=configuration_error exception={exc.__class__.__name__}")
        return 2
    if handoff_child:
        return handoff.run_child(_handoff_context())
    operational_log.log(
        f"event=listener_starting listener={host}:{port} "
        f"upstream_timeout={exchange.UPSTREAM_TIMEOUT} "
        f"read_timeout={sse.UPSTREAM_READ_TIMEOUT} "
        f"log_max_bytes={operational_log.LOG_MAX_BYTES} "
        f"log_backup_count={operational_log.LOG_BACKUP_COUNT}"
    )
    listener = create_server()
    _PROCESS.server = listener
    try:
        handoff.serve_with_resume(listener, _handoff_context())
    except KeyboardInterrupt:
        pass
    finally:
        listener.server_close()
    return 0
