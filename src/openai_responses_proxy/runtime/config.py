"""Single owner for portable paths and validated process-local settings."""

from __future__ import annotations

import math
import ntpath
import os
import posixpath
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from pathlib import PureWindowsPath

from openai_responses_proxy import product_identity

HOME_ENV = product_identity.environment_name("HOME")
STATE_HOME_ENV = product_identity.environment_name("STATE_HOME")
PROXY_PORT_ENV = product_identity.environment_name("PROXY_PORT")
PROXY_LOG_ENV = product_identity.environment_name("PROXY_LOG")
WATCHDOG_LOG_ENV = product_identity.environment_name("WATCHDOG_LOG")
PROXY_LOG_MAX_BYTES_ENV = product_identity.environment_name("PROXY_LOG_MAX_BYTES")
PROXY_LOG_BACKUP_COUNT_ENV = product_identity.environment_name("PROXY_LOG_BACKUP_COUNT")
WATCHDOG_LOG_MAX_BYTES_ENV = product_identity.environment_name("WATCHDOG_LOG_MAX_BYTES")
WATCHDOG_LOG_BACKUP_COUNT_ENV = product_identity.environment_name("WATCHDOG_LOG_BACKUP_COUNT")
UPSTREAM_TIMEOUT_ENV = product_identity.environment_name("UPSTREAM_TIMEOUT")
UPSTREAM_READ_TIMEOUT_ENV = product_identity.environment_name("UPSTREAM_READ_TIMEOUT")
REQUEST_BODY_MAX_BYTES_ENV = product_identity.environment_name("REQUEST_BODY_MAX_BYTES")
REQUEST_BODY_TIMEOUT_ENV = product_identity.environment_name("REQUEST_BODY_TIMEOUT")
CATALOG_TIMEOUT_ENV = product_identity.environment_name("CATALOG_TIMEOUT")
CATALOG_MAX_BYTES_ENV = product_identity.environment_name("CATALOG_MAX_BYTES")
UPSTREAM_BASE_URL_ENV = product_identity.environment_name("UPSTREAM_BASE_URL")
WATCHDOG_INTERVAL_ENV = product_identity.environment_name("WATCHDOG_INTERVAL")
WATCHDOG_MAX_BACKOFF_ENV = product_identity.environment_name("WATCHDOG_MAX_BACKOFF")
RESPONSE_FAILED_COMPACTION_BUDGET_ENV = product_identity.environment_name(
    "RESPONSE_FAILED_COMPACTION_BUDGET"
)
RESPONSE_FAILED_MAX_STAGES_ENV = product_identity.environment_name("RESPONSE_FAILED_MAX_STAGES")
RUNTIME_ENVIRONMENT = frozenset(
    (
        HOME_ENV,
        STATE_HOME_ENV,
        PROXY_PORT_ENV,
        PROXY_LOG_ENV,
        WATCHDOG_LOG_ENV,
        PROXY_LOG_MAX_BYTES_ENV,
        PROXY_LOG_BACKUP_COUNT_ENV,
        WATCHDOG_LOG_MAX_BYTES_ENV,
        WATCHDOG_LOG_BACKUP_COUNT_ENV,
        UPSTREAM_TIMEOUT_ENV,
        UPSTREAM_READ_TIMEOUT_ENV,
        REQUEST_BODY_MAX_BYTES_ENV,
        REQUEST_BODY_TIMEOUT_ENV,
        CATALOG_TIMEOUT_ENV,
        CATALOG_MAX_BYTES_ENV,
        UPSTREAM_BASE_URL_ENV,
        WATCHDOG_INTERVAL_ENV,
        WATCHDOG_MAX_BACKOFF_ENV,
        RESPONSE_FAILED_COMPACTION_BUDGET_ENV,
        RESPONSE_FAILED_MAX_STAGES_ENV,
    )
)

DEFAULT_PORT = 8792
DEFAULT_PROXY_LOG_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_PROXY_LOG_BACKUP_COUNT = 3
DEFAULT_WATCHDOG_LOG_MAX_BYTES = 512 * 1024
DEFAULT_WATCHDOG_LOG_BACKUP_COUNT = 2
DEFAULT_UPSTREAM_TIMEOUT = 900.0
DEFAULT_UPSTREAM_READ_TIMEOUT = 240.0
DEFAULT_REQUEST_BODY_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_REQUEST_BODY_TIMEOUT = 30.0
DEFAULT_CATALOG_TIMEOUT = 30.0
DEFAULT_CATALOG_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_WATCHDOG_INTERVAL = 15.0
DEFAULT_WATCHDOG_MAX_BACKOFF = 120.0
DEFAULT_RESPONSE_FAILED_COMPACTION_BUDGET = 512 * 1024
DEFAULT_RESPONSE_FAILED_MAX_STAGES = 3


class ConfigurationError(ValueError):
    """A supplied runtime setting is invalid or unsafe."""


@dataclass(frozen=True, slots=True)
class LogRetention:
    """One bounded rotating-log policy."""

    path: str
    max_bytes: int
    backup_count: int


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated process-local configuration shared by every runtime owner."""

    host: str
    port: int
    proxy_log: LogRetention
    watchdog_log: LogRetention
    upstream_timeout: float
    upstream_read_timeout: float
    request_body_max_bytes: int
    request_body_timeout: float
    catalog_timeout: float
    catalog_max_bytes: int
    watchdog_interval: float
    watchdog_max_backoff: float
    response_failed_compaction_budget: int
    response_failed_max_stages: int

    @property
    def listener(self) -> tuple[str, int]:
        """Return the immutable local listener address."""
        return self.host, self.port


def home_dir() -> str:
    """Return the active user's expanded home directory."""
    return os.path.expanduser("~")


def path_join(root: str, *parts: str) -> str:
    """Join an owned path without reinterpreting its existing platform syntax."""
    if PureWindowsPath(root).drive or "\\" in root:
        return ntpath.join(root, *parts)
    return posixpath.join(root, *parts)


def _absolute_override(source: Mapping[str, str], name: str) -> str | None:
    raw = source.get(name)
    if not raw:
        return None
    if raw == "~" or raw.startswith(("~/", "~\\")):
        home = source.get("HOME") or source.get("USERPROFILE") or home_dir()
        raw = path_join(home, raw[2:].replace("\\", "/")) if len(raw) > 1 else home
    if PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).is_absolute():
        return raw
    return os.path.abspath(raw)


def data_dir(environment: Mapping[str, str] | None = None) -> str:
    """Return the portable product data root, allowing an explicit override."""
    source = os.environ if environment is None else environment
    if override := _absolute_override(source, HOME_ENV):
        return override
    return default_data_dir(source)


def default_data_dir(environment: Mapping[str, str] | None = None) -> str:
    """Return the platform data root without consulting the product override."""
    source = os.environ if environment is None else environment
    home = home_dir()
    if os.name == "nt":
        base = source.get("LOCALAPPDATA", path_join(home, "AppData", "Local"))
        return path_join(base, product_identity.PRODUCT_SLUG)
    if sys.platform == "darwin":
        return path_join(home, "Library", "Application Support", product_identity.PRODUCT_SLUG)
    base = source.get("XDG_DATA_HOME", path_join(home, ".local", "share"))
    return path_join(base, product_identity.PRODUCT_SLUG)


def state_dir(environment: Mapping[str, str] | None = None) -> str:
    """Return the portable product state root, allowing an explicit override.

    Every default lies outside the default data root, which installation and
    purge verify, so taking the lifecycle lock never creates content there.
    """
    source = os.environ if environment is None else environment
    if override := _absolute_override(source, STATE_HOME_ENV):
        return override
    home = home_dir()
    if os.name == "nt":
        base = source.get("LOCALAPPDATA", path_join(home, "AppData", "Local"))
        return path_join(base, f"{product_identity.PRODUCT_SLUG}-state")
    if sys.platform == "darwin":
        return path_join(home, "Library", "Logs", product_identity.PRODUCT_SLUG)
    base = source.get("XDG_STATE_HOME", path_join(home, ".local", "state"))
    return path_join(base, product_identity.PRODUCT_SLUG)


def listener_host() -> str:
    """Return the product-invariant loopback listener host."""
    return "127.0.0.1"


def loopback_url(port: int, path: str) -> str:
    """Build one internal HTTP URL from the invariant listener host."""
    return f"http://{listener_host()}:{port}{path}"


def _integer(source: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = source.get(name, str(default))
    if isinstance(raw, bool):
        raise ConfigurationError(f"{name} must be an integer in {minimum}..{maximum}")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ConfigurationError(f"{name} must be an integer in {minimum}..{maximum}") from None
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _number(
    source: Mapping[str, str], name: str, default: float, minimum: float, maximum: float
) -> float:
    raw = source.get(name, str(default))
    if isinstance(raw, bool):
        raise ConfigurationError(f"{name} must be a finite number in {minimum:g}..{maximum:g}")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ConfigurationError(
            f"{name} must be a finite number in {minimum:g}..{maximum:g}"
        ) from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be a finite number in {minimum:g}..{maximum:g}")
    return value


def listener_port(environment: Mapping[str, str] | None = None) -> int:
    """Return the configured TCP port after strict range validation."""
    source = os.environ if environment is None else environment
    return _integer(source, PROXY_PORT_ENV, DEFAULT_PORT, 1, 65535)


def proxy_log_path(environment: Mapping[str, str] | None = None) -> str:
    """Return the proxy log path owned by the portable state root."""
    source = os.environ if environment is None else environment
    return source.get(PROXY_LOG_ENV, path_join(state_dir(source), "proxy.log"))


def watchdog_log_path(environment: Mapping[str, str] | None = None) -> str:
    """Return the watchdog log path owned by the portable state root."""
    source = os.environ if environment is None else environment
    return source.get(WATCHDOG_LOG_ENV, path_join(state_dir(source), "watchdog.log"))


def load(environment: Mapping[str, str] | None = None) -> Settings:
    """Load every process-local setting once and reject unsafe values."""
    source = os.environ if environment is None else environment
    return Settings(
        host=listener_host(),
        port=listener_port(source),
        proxy_log=LogRetention(
            path=proxy_log_path(source),
            max_bytes=_integer(
                source,
                PROXY_LOG_MAX_BYTES_ENV,
                DEFAULT_PROXY_LOG_MAX_BYTES,
                4 * 1024,
                64 * 1024 * 1024,
            ),
            backup_count=_integer(
                source,
                PROXY_LOG_BACKUP_COUNT_ENV,
                DEFAULT_PROXY_LOG_BACKUP_COUNT,
                0,
                10,
            ),
        ),
        watchdog_log=LogRetention(
            path=watchdog_log_path(source),
            max_bytes=_integer(
                source,
                WATCHDOG_LOG_MAX_BYTES_ENV,
                DEFAULT_WATCHDOG_LOG_MAX_BYTES,
                4 * 1024,
                64 * 1024 * 1024,
            ),
            backup_count=_integer(
                source,
                WATCHDOG_LOG_BACKUP_COUNT_ENV,
                DEFAULT_WATCHDOG_LOG_BACKUP_COUNT,
                0,
                10,
            ),
        ),
        upstream_timeout=_number(
            source, UPSTREAM_TIMEOUT_ENV, DEFAULT_UPSTREAM_TIMEOUT, 0.001, 3600
        ),
        upstream_read_timeout=_number(
            source,
            UPSTREAM_READ_TIMEOUT_ENV,
            DEFAULT_UPSTREAM_READ_TIMEOUT,
            0.001,
            3600,
        ),
        request_body_max_bytes=_integer(
            source,
            REQUEST_BODY_MAX_BYTES_ENV,
            DEFAULT_REQUEST_BODY_MAX_BYTES,
            4096,
            256 * 1024 * 1024,
        ),
        request_body_timeout=_number(
            source, REQUEST_BODY_TIMEOUT_ENV, DEFAULT_REQUEST_BODY_TIMEOUT, 0.001, 300
        ),
        catalog_timeout=_number(source, CATALOG_TIMEOUT_ENV, DEFAULT_CATALOG_TIMEOUT, 0.001, 300),
        catalog_max_bytes=_integer(
            source, CATALOG_MAX_BYTES_ENV, DEFAULT_CATALOG_MAX_BYTES, 4096, 64 * 1024 * 1024
        ),
        watchdog_interval=_number(
            source, WATCHDOG_INTERVAL_ENV, DEFAULT_WATCHDOG_INTERVAL, 0.1, 3600
        ),
        watchdog_max_backoff=_number(
            source, WATCHDOG_MAX_BACKOFF_ENV, DEFAULT_WATCHDOG_MAX_BACKOFF, 0.1, 86400
        ),
        response_failed_compaction_budget=_integer(
            source,
            RESPONSE_FAILED_COMPACTION_BUDGET_ENV,
            DEFAULT_RESPONSE_FAILED_COMPACTION_BUDGET,
            4 * 1024,
            64 * 1024 * 1024,
        ),
        response_failed_max_stages=_integer(
            source,
            RESPONSE_FAILED_MAX_STAGES_ENV,
            DEFAULT_RESPONSE_FAILED_MAX_STAGES,
            0,
            8,
        ),
    )
