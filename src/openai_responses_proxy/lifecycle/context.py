"""One portable projection of an installed native product runtime."""

from __future__ import annotations

import hashlib
import ntpath
import os
import posixpath
from dataclasses import dataclass
from dataclasses import field
from pathlib import PureWindowsPath

from openai_responses_proxy import errors
from openai_responses_proxy import product_identity
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.providers import registry
from openai_responses_proxy.runtime import config
from openai_responses_proxy.serving import inventory

SERVICE_ID = product_identity.SERVICE_ID
DEFAULT_PORT = config.DEFAULT_PORT


def service_id(install_dir: str) -> str:
    """Return one stable service identity bound to the installed payload root.

    The default user installation keeps the public service name.  Any explicit
    alternate root receives a deterministic suffix so isolated validation and
    parallel installations cannot unload or replace the user's live service.
    """
    installed = os.path.normcase(os.path.abspath(install_dir))
    canonical = os.path.normcase(os.path.abspath(config.default_data_dir()))
    if installed == canonical:
        return SERVICE_ID
    suffix = hashlib.sha256(installed.encode("utf-8")).hexdigest()[:12]
    return f"{SERVICE_ID}.{suffix}"


@dataclass(slots=True)
class RuntimeContext:
    """Absolute paths and validated settings for one installed projection."""

    install_dir: str
    executable: str
    command: str
    log_dir: str
    user_home: str = field(default_factory=config.home_dir)
    port: int = config.DEFAULT_PORT
    proxy_log_max_bytes: int = config.DEFAULT_PROXY_LOG_MAX_BYTES
    proxy_log_backup_count: int = config.DEFAULT_PROXY_LOG_BACKUP_COUNT
    watchdog_log_max_bytes: int = config.DEFAULT_WATCHDOG_LOG_MAX_BYTES
    watchdog_log_backup_count: int = config.DEFAULT_WATCHDOG_LOG_BACKUP_COUNT
    upstream_timeout: float = config.DEFAULT_UPSTREAM_TIMEOUT
    upstream_read_timeout: float = config.DEFAULT_UPSTREAM_READ_TIMEOUT
    request_body_max_bytes: int = config.DEFAULT_REQUEST_BODY_MAX_BYTES
    request_body_timeout: float = config.DEFAULT_REQUEST_BODY_TIMEOUT
    catalog_timeout: float = config.DEFAULT_CATALOG_TIMEOUT
    catalog_max_bytes: int = config.DEFAULT_CATALOG_MAX_BYTES
    watchdog_interval: float = config.DEFAULT_WATCHDOG_INTERVAL
    watchdog_max_backoff: float = config.DEFAULT_WATCHDOG_MAX_BACKOFF
    response_failed_compaction_budget: int = config.DEFAULT_RESPONSE_FAILED_COMPACTION_BUDGET
    response_failed_max_stages: int = config.DEFAULT_RESPONSE_FAILED_MAX_STAGES
    upstream_base_url: str = ""

    @property
    def service_id(self) -> str:
        """Return the supervision identity for this installed root."""
        return service_id(self.install_dir)

    @property
    def payload_dir(self) -> str:
        """Return the payload root that owns this context's executable."""
        path = ntpath if PureWindowsPath(self.executable).drive else posixpath
        return path.dirname(path.dirname(self.executable))


def validate_port(port: int) -> int:
    """Accept only a real TCP port before service rendering."""
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise errors.InstallError("port must be an integer in 1..65535")
    return port


def validate_log_retention(value: int, *, name: str, minimum: int, maximum: int) -> int:
    """Accept one bounded runtime-log retention setting."""
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise errors.InstallError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def create(
    *,
    executable: str | None = None,
    port: int = config.DEFAULT_PORT,
    proxy_log_max_bytes: int = config.DEFAULT_PROXY_LOG_MAX_BYTES,
    proxy_log_backup_count: int = config.DEFAULT_PROXY_LOG_BACKUP_COUNT,
    watchdog_log_max_bytes: int = config.DEFAULT_WATCHDOG_LOG_MAX_BYTES,
    watchdog_log_backup_count: int = config.DEFAULT_WATCHDOG_LOG_BACKUP_COUNT,
    upstream_base_url: str | None = None,
) -> RuntimeContext:
    """Validate command inputs and project all product-owned paths once."""
    install_dir = config.data_dir()
    projected = RuntimeContext(
        user_home=config.home_dir(),
        install_dir=install_dir,
        executable=executable
        or inventory.installed_executable(install_dir, windows=config.os.name == "nt"),
        command=str(
            command.path(config.home_dir(), config.os.environ, windows=config.os.name == "nt")
        ),
        log_dir=config.state_dir(),
        port=validate_port(port),
        proxy_log_max_bytes=validate_log_retention(
            proxy_log_max_bytes,
            name="proxy log max bytes",
            minimum=4 * 1024,
            maximum=64 * 1024 * 1024,
        ),
        proxy_log_backup_count=validate_log_retention(
            proxy_log_backup_count,
            name="proxy log backup count",
            minimum=0,
            maximum=10,
        ),
        watchdog_log_max_bytes=validate_log_retention(
            watchdog_log_max_bytes,
            name="watchdog log max bytes",
            minimum=4 * 1024,
            maximum=64 * 1024 * 1024,
        ),
        watchdog_log_backup_count=validate_log_retention(
            watchdog_log_backup_count,
            name="watchdog log backup count",
            minimum=0,
            maximum=10,
        ),
    )
    from openai_responses_proxy.lifecycle import generation
    from openai_responses_proxy.lifecycle import runtime_spec

    selected = generation.selected_context(projected)
    carrier = runtime_spec.path(selected)
    try:
        environment = runtime_spec.environment(carrier) if carrier.exists() else None
        settings = config.load(environment)
    except config.ConfigurationError as exc:
        raise errors.InstallError(str(exc)) from exc
    selected.request_body_max_bytes = settings.request_body_max_bytes
    selected.request_body_timeout = settings.request_body_timeout
    selected.catalog_timeout = settings.catalog_timeout
    selected.catalog_max_bytes = settings.catalog_max_bytes
    if upstream_base_url is None:
        upstream_base_url = (
            environment[config.UPSTREAM_BASE_URL_ENV]
            if environment is not None
            else config.os.environ.get(config.UPSTREAM_BASE_URL_ENV, "")
        )
    try:
        selected.upstream_base_url = (
            registry.normalize_upstream(upstream_base_url) if upstream_base_url else ""
        )
    except ValueError as exc:
        raise errors.InstallError(str(exc)) from exc
    return selected
