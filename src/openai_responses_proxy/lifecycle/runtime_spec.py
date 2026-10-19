"""Canonical, secret-free configuration for one installed native runtime."""

from __future__ import annotations

import os
from pathlib import Path
from pathlib import PurePosixPath
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING
from typing import Protocol

from openai_responses_proxy import errors
from openai_responses_proxy.json_value import JsonObject
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.providers import registry
from openai_responses_proxy.runtime import config
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import inventory

if TYPE_CHECKING:
    from openai_responses_proxy.lifecycle.context import RuntimeContext

FILENAME = inventory.RUNTIME_CONFIG_FILENAME
SCHEMA_VERSION = 1
_OPTIONAL_DEFAULTS = {
    "request_body_max_bytes": config.DEFAULT_REQUEST_BODY_MAX_BYTES,
    "request_body_timeout": config.DEFAULT_REQUEST_BODY_TIMEOUT,
    "sse_buffer_max_bytes": config.DEFAULT_SSE_BUFFER_MAX_BYTES,
    "catalog_timeout": config.DEFAULT_CATALOG_TIMEOUT,
    "catalog_max_bytes": config.DEFAULT_CATALOG_MAX_BYTES,
}

_FIELDS = (
    "install_dir",
    "log_dir",
    "port",
    "proxy_log_max_bytes",
    "proxy_log_backup_count",
    "watchdog_log_max_bytes",
    "watchdog_log_backup_count",
    "upstream_timeout",
    "upstream_read_timeout",
    "watchdog_interval",
    "watchdog_max_backoff",
    "response_failed_compaction_budget",
    "response_failed_max_stages",
    *_OPTIONAL_DEFAULTS,
)


class NativeServiceContext(Protocol):
    """Minimum context consumed by every native service adapter."""

    @property
    def user_home(self) -> str:
        """Return the operating-system user home that owns native service files."""
        ...

    @property
    def install_dir(self) -> str:
        """Return the immutable product installation directory."""
        ...

    @property
    def executable(self) -> str:
        """Return the installed executable selected for supervision."""
        ...

    @property
    def payload_dir(self) -> str:
        """Return the immutable payload generation selected for supervision."""
        ...

    @property
    def log_dir(self) -> str:
        """Return the product-owned runtime log directory."""
        ...

    @property
    def service_id(self) -> str:
        """Return the exact native service identity for this installation."""
        ...


def path(ctx: RuntimeContext) -> Path:
    """Return the product-owned configuration carrier for ``ctx``."""
    return Path(ctx.payload_dir, FILENAME)


def write(ctx: RuntimeContext) -> Path:
    """Atomically persist the exact native runtime contract."""
    target = path(ctx)
    payload = {"schema_version": SCHEMA_VERSION}
    payload.update({name: getattr(ctx, name) for name in _FIELDS})
    if ctx.upstream_base_url:
        payload["upstream_base_url"] = registry.normalize_upstream(ctx.upstream_base_url)
    owned_files.write_bytes(
        target,
        digest.canonical_json(payload),
        mode=0o600,
        root=Path(ctx.payload_dir),
    )
    return target


def environment(target: Path) -> dict[str, str]:
    """Validate one carrier and project it into process-local settings."""
    return _project(_read(target))


def _project(value: JsonObject) -> dict[str, str]:
    install_dir = value.get("install_dir")
    log_dir = value.get("log_dir")
    if not isinstance(install_dir, str) or not isinstance(log_dir, str):
        raise errors.InstallError("native runtime configuration paths are invalid")
    return {
        config.HOME_ENV: install_dir,
        config.STATE_HOME_ENV: log_dir,
        config.PROXY_PORT_ENV: str(value["port"]),
        config.PROXY_LOG_ENV: config.path_join(log_dir, "proxy.log"),
        config.WATCHDOG_LOG_ENV: config.path_join(log_dir, "watchdog.log"),
        config.PROXY_LOG_MAX_BYTES_ENV: str(value["proxy_log_max_bytes"]),
        config.PROXY_LOG_BACKUP_COUNT_ENV: str(value["proxy_log_backup_count"]),
        config.WATCHDOG_LOG_MAX_BYTES_ENV: str(value["watchdog_log_max_bytes"]),
        config.WATCHDOG_LOG_BACKUP_COUNT_ENV: str(value["watchdog_log_backup_count"]),
        config.UPSTREAM_TIMEOUT_ENV: str(value["upstream_timeout"]),
        config.UPSTREAM_READ_TIMEOUT_ENV: str(value["upstream_read_timeout"]),
        config.REQUEST_BODY_MAX_BYTES_ENV: str(value["request_body_max_bytes"]),
        config.REQUEST_BODY_TIMEOUT_ENV: str(value["request_body_timeout"]),
        config.SSE_BUFFER_MAX_BYTES_ENV: str(value["sse_buffer_max_bytes"]),
        config.CATALOG_TIMEOUT_ENV: str(value["catalog_timeout"]),
        config.CATALOG_MAX_BYTES_ENV: str(value["catalog_max_bytes"]),
        config.WATCHDOG_INTERVAL_ENV: str(value["watchdog_interval"]),
        config.WATCHDOG_MAX_BACKOFF_ENV: str(value["watchdog_max_backoff"]),
        config.RESPONSE_FAILED_COMPACTION_BUDGET_ENV: str(
            value["response_failed_compaction_budget"]
        ),
        config.RESPONSE_FAILED_MAX_STAGES_ENV: str(value["response_failed_max_stages"]),
        config.UPSTREAM_BASE_URL_ENV: str(value.get("upstream_base_url", "")),
    }


def from_executable(executable: str | os.PathLike[str]) -> Path:
    """Locate the carrier owned by the executable's installed payload root."""
    path = Path(executable).resolve(strict=True)
    if path.parent.name != "bin":
        raise errors.InstallError("native executable is outside the installed payload layout")
    return path.parent.parent / FILENAME


def activate(executable: str | os.PathLike[str]) -> Path:
    """Replace inherited product settings with the executable-owned carrier."""
    target = from_executable(executable)
    projected = environment(target)
    for name in config.RUNTIME_ENVIRONMENT:
        os.environ.pop(name, None)
    os.environ.update(projected)
    return target


def _read(target: Path) -> JsonObject:
    value = owned_files.read_canonical_json(target, "native runtime configuration")
    required_fields = {"schema_version", *_FIELDS} - _OPTIONAL_DEFAULTS.keys()
    optional_fields = {*_OPTIONAL_DEFAULTS, "upstream_base_url"}
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or not required_fields <= value.keys()
        or not value.keys() <= required_fields | optional_fields
    ):
        raise errors.InstallError("native runtime configuration schema is unsupported")
    value = {**_OPTIONAL_DEFAULTS, **value}
    if "upstream_base_url" in value:
        upstream = value["upstream_base_url"]
        if not isinstance(upstream, str) or not upstream:
            raise errors.InstallError("native runtime configuration upstream is invalid")
        try:
            if registry.normalize_upstream(upstream) != upstream:
                raise ValueError("upstream must be canonical")
        except ValueError as exc:
            raise errors.InstallError(str(exc)) from exc
    install_dir = value.get("install_dir")
    log_dir = value.get("log_dir")
    if not isinstance(install_dir, str) or not _absolute(install_dir):
        raise errors.InstallError("native runtime configuration install_dir is invalid")
    if not isinstance(log_dir, str) or not _absolute(log_dir):
        raise errors.InstallError("native runtime configuration log_dir is invalid")
    if not _owned_carrier_location(target, install_dir):
        raise errors.InstallError(
            "native runtime configuration is outside the installed payload layout"
        )
    try:
        config.load(_project(value))
    except config.ConfigurationError as exc:
        raise errors.InstallError(str(exc)) from exc
    return value


def _absolute(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _owned_carrier_location(target: Path, install_dir: str) -> bool:
    stable = Path(install_dir, FILENAME)
    generations = Path(install_dir, identity.PAYLOAD_GENERATIONS_DIRNAME)
    return normalized_path(target) == normalized_path(stable) or (
        target.name == FILENAME
        and normalized_path(target.parent.parent) == normalized_path(generations)
    )


def normalized_path(value: str | os.PathLike[str]) -> str:
    """Return one host-semantic canonical path identity."""
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(value))))
