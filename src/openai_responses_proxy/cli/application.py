"""Single public command grammar for OpenAI Responses Proxy."""

from __future__ import annotations

import importlib.metadata
import json
import sys
from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated
from typing import Literal
from typing import TypedDict
from typing import overload

from cyclopts import App
from cyclopts import Parameter
from cyclopts.exceptions import CycloptsError
from cyclopts.validators import Number
from filelock import FileLock
from filelock import Timeout
from rich.console import Console

from openai_responses_proxy import errors
from openai_responses_proxy import product_identity
from openai_responses_proxy.cli import outcome
from openai_responses_proxy.cli import presentation
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import control
from openai_responses_proxy.lifecycle import install
from openai_responses_proxy.lifecycle import runtime_spec
from openai_responses_proxy.lifecycle import uninstall
from openai_responses_proxy.service import runtime as service_runtime

PUBLIC_COMMANDS = frozenset(
    {"install", "status", "doctor", "recover", "reload", "rollback", "uninstall"}
)
_FAILURE_STATUS = "failed"
type DoctorState = Literal[
    "running", "degraded", "stopped", "invalid", "not_installed", "recovery_required"
]
_JSON = Annotated[
    bool,
    Parameter(
        name="--json",
        negative=False,
        help="Emit stable JSON for automation.",
        show_default=False,
    ),
]
_PORT = Annotated[
    int,
    Parameter(
        help="Loopback listener port.",
        validator=Number(gte=1, lte=65535),
    ),
]
_TIMEOUT = Annotated[
    float,
    Parameter(
        name="--timeout-seconds",
        help="Native lifecycle deadline in seconds.",
        validator=Number(gt=0),
    ),
]
_PURGE = Annotated[
    bool,
    Parameter(
        name="--purge",
        negative=False,
        help="Remove verified product-owned data.",
        show_default=False,
    ),
]


class DoctorCheck(TypedDict):
    """One named diagnostic check."""

    status: Literal["passed", "failed"]
    detail: str


class DoctorReport(TypedDict):
    """Stable doctor result consumed by humans and automation."""

    ok: bool
    state: DoctorState
    next: str | None
    checks: dict[str, DoctorCheck]


type CommandResult = Mapping[str, object] | None


def _lifecycle_lock_path(ctx: runtime_context.RuntimeContext) -> Path:
    """Return the stable lock shared by all lifecycle writers for one installation."""
    return Path(ctx.log_dir, "lifecycle.lock")


def _acquire_lifecycle_lock(ctx: runtime_context.RuntimeContext) -> FileLock:
    """Acquire the installation's native writer lock or raise one public error."""
    lock = FileLock(
        _lifecycle_lock_path(ctx),
        timeout=0,
        fallback_to_soft=False,
        preserve_lock_file=True,
    )
    try:
        lock.acquire()
    except Timeout as exc:
        raise errors.InstallError("lifecycle mutation is already in progress") from exc
    except OSError as exc:
        raise errors.InstallError("lifecycle mutation lock is unavailable") from exc
    return lock


def _release_version() -> str:
    if bundle_root := getattr(sys, "_MEIPASS", None):
        return (Path(bundle_root) / "VERSION").read_text(encoding="utf-8").strip()
    if source_version := _source_version():
        return source_version
    return importlib.metadata.version(product_identity.PACKAGE_NAME)


def _source_version() -> str | None:
    """Read repository metadata only when this module is actually under its ``src`` root."""
    source_root = Path(__file__).resolve().parents[2]
    repository = source_root.parent
    if source_root.name != "src" or not (repository / "pyproject.toml").is_file():
        return None
    return (repository / "VERSION").read_text(encoding="utf-8").strip()


def _path_argument(arguments: Mapping[str, object], name: str) -> Path:
    value = arguments.get(name)
    if not isinstance(value, Path):
        raise TypeError(f"{name} must be a path")
    return value


def _port_argument(arguments: Mapping[str, object]) -> int:
    value = arguments.get("port")
    if type(value) is not int:
        raise TypeError("port must be an integer")
    return value


def _timeout_argument(arguments: Mapping[str, object]) -> float:
    value = arguments.get("timeout_seconds", 30.0)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError("timeout_seconds must be numeric")
    return float(value)


def _purge_argument(arguments: Mapping[str, object]) -> bool:
    value = arguments.get("purge")
    if not isinstance(value, bool):
        raise TypeError("purge must be a boolean")
    return value


@overload
def dispatch(
    command: Literal["install", "status", "recover", "reload", "rollback", "uninstall"],
    **arguments: object,
) -> dict[str, object]: ...


@overload
def dispatch(command: Literal["doctor"], **arguments: object) -> DoctorReport: ...


@overload
def dispatch(command: str, **arguments: object) -> CommandResult: ...


def dispatch(command: str, **arguments: object) -> CommandResult:
    """Execute one parsed command through its semantic owner."""
    port = _port_argument(arguments)
    upstream = arguments.get("upstream_base_url")
    if upstream is not None and not isinstance(upstream, str):
        raise TypeError("upstream_base_url must be a string")
    context = (
        runtime_context.create(port=port, upstream_base_url=upstream)
        if upstream is not None
        else runtime_context.create(port=port)
    )
    if command == "status":
        return control.status(context)
    if command == "doctor":
        return _doctor(control.status(context))
    if command in PUBLIC_COMMANDS:
        lock = _acquire_lifecycle_lock(context)
        try:
            if command == "install":
                return install.install_asset(
                    context,
                    _path_argument(arguments, "asset"),
                    trust_anchor=_path_argument(arguments, "trust_anchor"),
                    timeout_seconds=_timeout_argument(arguments),
                )
            if command == "uninstall":
                return uninstall.uninstall_product(
                    context,
                    purge=_purge_argument(arguments),
                )
            if command == "recover":
                return control.recover(context)
            if command == "reload":
                return control.reload(context, timeout_seconds=_timeout_argument(arguments))
            to_release = arguments.get("to_release")
            if not isinstance(to_release, str):
                raise TypeError("to_release must be a string")
            return control.rollback(
                context,
                to_release=to_release,
                timeout_seconds=_timeout_argument(arguments),
            )
        finally:
            lock.release()
    raise ValueError(f"{command} is not implemented")


def _doctor(evidence: Mapping[str, object]) -> DoctorReport:
    """Classify installed state without introducing another observation path."""
    state = evidence.get("state")
    if state == "not_installed":
        return {
            "ok": False,
            "state": "not_installed",
            "next": product_identity.command("install", "--help"),
            "checks": {
                "installation": {
                    "status": _FAILURE_STATUS,
                    "detail": "not installed",
                }
            },
        }
    integrity = evidence.get("payload_integrity")
    integrity_ok = isinstance(integrity, dict) and integrity.get("ok") is True
    service = evidence.get("service")
    service_detail = (
        service
        if isinstance(service, str)
        and service in {"running", "installed", "outdated", "absent", "unknown"}
        else "unknown"
    )
    runtime = evidence.get("runtime")
    listener_ok = (
        isinstance(runtime, dict)
        and type(runtime.get("pid")) is int
        and runtime.get("accepting") is not False
    )
    command = evidence.get("command")
    command_ok = isinstance(command, dict) and command.get("state") == "owned"
    transaction_state = evidence.get("payload_transaction")
    transaction_ok = transaction_state is None
    transaction_value = (
        transaction_state.get("state") if isinstance(transaction_state, dict) else None
    )
    transaction_detail = (
        transaction_value
        if isinstance(transaction_value, str)
        and transaction_value
        in {
            "prepared",
            "materialized",
            "activated",
            "recovery_required",
            "closed",
            "rolled_back",
            "finalized",
            "purged",
            "invalid",
        }
        else "invalid"
    )
    rollback = evidence.get("rollback")
    rollback_value = rollback.get("state") if isinstance(rollback, dict) else "unavailable"
    rollback_detail = (
        rollback_value
        if isinstance(rollback_value, str)
        and rollback_value in {"available", "unavailable", "deferred", "invalid"}
        else "invalid"
    )
    rollback_ok = rollback_detail != "invalid"
    checks: dict[str, DoctorCheck] = {
        "payload": {
            "status": "passed" if integrity_ok else _FAILURE_STATUS,
            "detail": "verified" if integrity_ok else "unavailable or invalid",
        },
        "service": {
            "status": "passed" if service == "running" else _FAILURE_STATUS,
            "detail": service_detail,
        },
        "listener": {
            "status": "passed" if listener_ok else _FAILURE_STATUS,
            "detail": "accepting" if listener_ok else "unavailable or identity mismatch",
        },
        "command": {
            "status": "passed" if command_ok else _FAILURE_STATUS,
            "detail": "owned" if command_ok else "absent or foreign",
        },
        "transaction": {
            "status": "passed" if transaction_ok else _FAILURE_STATUS,
            "detail": "none" if transaction_ok else transaction_detail,
        },
        "rollback": {
            "status": "passed" if rollback_ok else _FAILURE_STATUS,
            "detail": rollback_detail,
        },
    }
    checks_passed = all(check["status"] == "passed" for check in checks.values())
    if state != "running" and checks_passed:
        installation_detail = (
            state
            if isinstance(state, str) and state in {"degraded", "invalid", "recovery_required"}
            else "invalid"
        )
        checks["installation"] = {
            "status": _FAILURE_STATUS,
            "detail": installation_detail,
        }
        checks_passed = False
    next_command: str | None
    if state == "invalid":
        next_command = product_identity.command("status", "--json")
    elif state == "recovery_required":
        next_command = product_identity.command("recover")
    elif not integrity_ok or not command_ok:
        next_command = product_identity.command("install", "--help")
    elif state == "stopped":
        next_command = product_identity.command("reload")
    elif service_detail == "absent":
        next_command = product_identity.command("install", "--help")
    elif not listener_ok:
        next_command = product_identity.command("status", "--json")
    elif service_detail in {"installed", "outdated"}:
        next_command = product_identity.command("reload")
    elif not checks_passed:
        next_command = product_identity.command("status", "--json")
    else:
        next_command = None
    report_state: DoctorState
    match state:
        case "recovery_required":
            report_state = "recovery_required"
        case "stopped":
            report_state = "stopped"
        case "running" | "degraded":
            report_state = "running" if checks_passed else "degraded"
        case _:
            report_state = "invalid"
    return {
        "ok": checks_passed,
        "state": report_state,
        "next": next_command,
        "checks": checks,
    }


def _render(result: outcome.PublicOutcome, *, as_json: bool) -> None:
    if isinstance(result, outcome.Failure):
        if as_json:
            print(
                json.dumps(
                    {
                        "error": {
                            "code": result.code,
                            "message": result.problem,
                            "next": result.next_command,
                        }
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
        else:
            print(
                presentation.error(result.problem, next_command=result.next_command),
                file=sys.stderr,
            )
        return
    if as_json:
        print(json.dumps(result.evidence, sort_keys=True))
        return
    rendered = presentation.render(result.command, result.evidence)
    if rendered:
        print(rendered)


def _error(
    message: str,
    *,
    as_json: bool,
    next_command: str = product_identity.command("doctor"),
    code: str = "usage_error",
) -> int:
    failure = outcome.Failure(code, message, next_command)
    _render(failure, as_json=as_json)
    return failure.exit_code


def _execute(command: str, *, as_json: bool = False, **arguments: object) -> int:
    try:
        result = outcome.admit(command, dispatch(command, **arguments))
        _render(result, as_json=as_json)
        return result.exit_code
    except errors.ProductError as error:
        return _error(
            str(error),
            as_json=as_json,
            next_command=error.next_command,
            code=error.code,
        )
    except Exception:
        return _error("product command failed unexpectedly", as_json=as_json, code="internal_error")


def _app() -> App:
    app = App(
        name=product_identity.COMMAND_NAME,
        help=__doc__,
        version=_release_version,
        print_error=False,
        exit_on_error=False,
        result_action="return_value",
        help_format="rich",
    )

    @app.command(name="install")
    def install_command(
        *,
        asset: Annotated[
            Path,
            Parameter(
                help="Native release archive; sibling manifest, checksums, and signature required.",
            ),
        ],
        trust_anchor: Annotated[
            Path,
            Parameter(
                name="--trust-anchor",
                help="Trusted SSH allowed-signers file.",
            ),
        ],
        json_output: _JSON = False,
        port: _PORT = runtime_context.DEFAULT_PORT,
        timeout_seconds: _TIMEOUT = 30.0,
        upstream_base_url: Annotated[
            str | None,
            Parameter(
                name="--upstream-base-url",
                help="Explicit ordinary Responses upstream exposed at /upstream/v1.",
            ),
        ] = None,
    ) -> int:
        """Install or upgrade the native user service."""
        return _execute(
            "install",
            asset=asset,
            as_json=json_output,
            trust_anchor=trust_anchor,
            port=port,
            timeout_seconds=timeout_seconds,
            upstream_base_url=upstream_base_url,
        )

    @app.command(name="status")
    def status_command(
        *, json_output: _JSON = False, port: _PORT = runtime_context.DEFAULT_PORT
    ) -> int:
        """Show installed state and listener health."""
        return _execute("status", as_json=json_output, port=port)

    @app.command(name="doctor")
    def doctor(*, json_output: _JSON = False, port: _PORT = runtime_context.DEFAULT_PORT) -> int:
        """Diagnose the installed product without mutation."""
        return _execute("doctor", as_json=json_output, port=port)

    @app.command(name="recover")
    def recover(*, json_output: _JSON = False, port: _PORT = runtime_context.DEFAULT_PORT) -> int:
        """Resolve an interrupted installation transaction."""
        return _execute("recover", as_json=json_output, port=port)

    @app.command(name="reload")
    def reload_command(
        *,
        json_output: _JSON = False,
        port: _PORT = runtime_context.DEFAULT_PORT,
        timeout_seconds: _TIMEOUT = 30.0,
    ) -> int:
        """Hand off the installed service, or start it when stopped."""
        return _execute("reload", as_json=json_output, port=port, timeout_seconds=timeout_seconds)

    @app.command(name="rollback")
    def rollback_command(
        *,
        to_release: Annotated[
            str,
            Parameter(
                name="--to-release",
                help="Exact installed or retained release to select.",
            ),
        ],
        json_output: _JSON = False,
        port: _PORT = runtime_context.DEFAULT_PORT,
        timeout_seconds: _TIMEOUT = 30.0,
    ) -> int:
        """Converge on one explicit installed or retained release."""
        return _execute(
            "rollback",
            as_json=json_output,
            to_release=to_release,
            port=port,
            timeout_seconds=timeout_seconds,
        )

    @app.command(name="uninstall")
    def uninstall_command(
        *,
        json_output: _JSON = False,
        port: _PORT = runtime_context.DEFAULT_PORT,
        purge: _PURGE = False,
    ) -> int:
        """Remove the native service and optionally its owned state."""
        return _execute("uninstall", as_json=json_output, port=port, purge=purge)

    return app


def main(argv: Sequence[str] | None = None) -> int:
    """Run one public command without leaking expected exceptions or warnings."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0].startswith("--internal-"):
        return _run_internal(arguments)
    if arguments and not arguments[0].startswith("-") and arguments[0] not in PUBLIC_COMMANDS:
        return _error(
            f"unknown command: {arguments[0]}",
            as_json="--json" in arguments,
            next_command=product_identity.command("--help"),
        )
    try:
        result = _app()(arguments, console=Console(), error_console=Console(stderr=True))
    except CycloptsError as error:
        command = arguments[0] if arguments and arguments[0] in PUBLIC_COMMANDS else None
        return _error(
            str(error),
            as_json="--json" in arguments,
            next_command=product_identity.command(command, "--help")
            if command
            else product_identity.command("--help"),
        )
    return result if isinstance(result, int) else 0


def _run_internal(arguments: list[str]) -> int:
    """Dispatch one exact private service role without adding it to public help."""
    if len(arguments) != 1:
        return _error("internal service mode accepts no additional arguments", as_json=False)
    mode = arguments[0]
    if mode == service_runtime.PREWARM_MODE:
        return 0
    runtime_spec.activate(service_runtime.current_executable())
    if mode == service_runtime.LISTENER_MODE:
        from openai_responses_proxy.service import entrypoint

        return entrypoint.run()
    if mode == service_runtime.HANDOFF_CHILD_MODE:
        from openai_responses_proxy.service import entrypoint

        return entrypoint.run(handoff_child=True)
    if mode == service_runtime.WATCHDOG_MODE:
        from openai_responses_proxy.lifecycle.supervision import watchdog

        watchdog.run()
        return 0
    return _error("unknown internal service mode", as_json=False)
