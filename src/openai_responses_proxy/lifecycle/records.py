"""Journal and installed-state persistence for payload transactions."""

from __future__ import annotations

import json
import re
from pathlib import Path

from openai_responses_proxy import errors
from openai_responses_proxy.json_value import JsonObject
from openai_responses_proxy.json_value import ReadOnlyJsonObject
from openai_responses_proxy.json_value import is_json_object
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import owned_files
from openai_responses_proxy.serving import digest
from openai_responses_proxy.serving import identity
from openai_responses_proxy.serving import inventory

INSTALLED_RELEASE_STATE_SCHEMA = 1
TRANSACTION_JOURNAL_SCHEMA = 6
TERMINAL_STATES = frozenset({"closed", "rolled_back", "finalized", "purged"})
_STRICT_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def transaction_root(ctx: runtime_context.RuntimeContext) -> Path:
    """Return the sibling directory used for payload transactions."""
    return Path(f"{ctx.install_dir}.transaction")


def installed_path(ctx: runtime_context.RuntimeContext) -> Path:
    """Return the finalized released-projection state path."""
    return Path(ctx.install_dir, inventory.INSTALLED_RELEASE_STATE_FILENAME)


def journal_path(ctx: runtime_context.RuntimeContext) -> Path:
    """Keep transaction authority outside its disposable snapshot directory."""
    return Path(f"{transaction_root(ctx)}.json")


def status(ctx: runtime_context.RuntimeContext) -> dict[str, object] | None:
    """Return the bounded identity of one active transaction."""
    root = transaction_root(ctx)
    journal = journal_path(ctx)
    if not any(path.exists() or path.is_symlink() for path in (root, journal)):
        return None
    try:
        journal = read_journal(ctx)
    except errors.InstallError as exc:
        return {"state": "invalid", "detail": str(exc)}
    allowed = (
        "transaction_id",
        "version",
        "receipt_sha256",
        "state",
        "fresh",
        "previous_generation",
        "previous_predecessor",
        "phase",
    )
    return {key: journal[key] for key in allowed if key in journal}


def read_journal(ctx: runtime_context.RuntimeContext) -> JsonObject:
    """Read one existing transaction through its strict current schema."""
    root = transaction_root(ctx)
    if root.is_symlink():
        raise errors.InstallError("payload transaction root is a symbolic link")
    if root.exists() and not root.is_dir():
        raise errors.InstallError("payload transaction root is not a directory")
    path = journal_path(ctx)
    if path.is_symlink():
        raise errors.InstallError("payload transaction journal is a symbolic link")
    if not path.exists():
        raise errors.InstallError("payload transaction journal is missing")
    if not path.is_file():
        raise errors.InstallError("payload transaction journal is not a regular file")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise errors.InstallError("payload transaction journal could not be read") from exc
    try:
        journal = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise errors.InstallError("payload transaction journal is malformed JSON") from exc
    if not is_json_object(journal):
        raise errors.InstallError("payload transaction journal fields are invalid")
    if digest.canonical_json(journal) != content:
        raise errors.InstallError("payload transaction journal is not canonical JSON")
    if journal.get("schema_version") != TRANSACTION_JOURNAL_SCHEMA:
        raise errors.InstallError("payload transaction journal schema is unsupported")
    required = {
        "schema_version",
        "state",
        "transaction_id",
        "version",
        "receipt_sha256",
        "fresh",
    }
    allowed = required | {"previous_generation", "previous_predecessor", "phase", "reason", "files"}
    transaction_id = journal.get("transaction_id")
    version = journal.get("version")
    receipt_sha256 = journal.get("receipt_sha256")
    fresh = journal.get("fresh")
    reason = journal.get("reason")
    previous_generation = journal.get("previous_generation")
    previous_predecessor = journal.get("previous_predecessor")
    phase = journal.get("phase")
    if (
        not required.issubset(journal)
        or set(journal) - allowed
        or journal.get("state")
        not in {"prepared", "materialized", "activated", "recovery_required"} | TERMINAL_STATES
        or not isinstance(transaction_id, str)
        or len(transaction_id) != 32
        or any(character not in "0123456789abcdef" for character in transaction_id)
        or not isinstance(version, str)
        or _STRICT_VERSION.fullmatch(version) is None
        or not isinstance(receipt_sha256, str)
        or len(receipt_sha256) != 64
        or any(character not in "0123456789abcdef" for character in receipt_sha256)
        or type(fresh) is not bool
        or (
            previous_generation is not None
            and (
                not isinstance(previous_generation, str)
                or len(previous_generation) != 32
                or any(character not in "0123456789abcdef" for character in previous_generation)
            )
        )
        or (
            previous_predecessor is not None
            and (
                not isinstance(previous_predecessor, str)
                or len(previous_predecessor) != 32
                or any(character not in "0123456789abcdef" for character in previous_predecessor)
            )
        )
        or (previous_predecessor is not None and previous_generation is None)
        or (reason is not None and (not isinstance(reason, str) or not reason))
        or (journal.get("state") == "recovery_required") != (reason is not None)
        or (
            (journal.get("state") == "recovery_required")
            != (phase in {"materialized", "activated"})
        )
        or (journal.get("state") != "recovery_required" and phase is not None)
    ):
        raise errors.InstallError("payload transaction journal fields are invalid")
    if journal["state"] == "purged":
        if root.exists():
            raise errors.InstallError("payload removal has an unexpected transaction directory")
        removal_files(journal)
    elif "files" in journal:
        files = removal_files(journal)
        if journal["state"] == "closed":
            raise errors.InstallError("payload transaction journal fields are invalid")
        if journal["state"] != "finalized" and any(
            not name.startswith(f"{identity.PAYLOAD_GENERATIONS_DIRNAME}/{transaction_id}/")
            for name in files
        ):
            raise errors.InstallError("payload transaction disposal generation is invalid")
    elif journal["state"] not in {"closed", "finalized"}:
        raise errors.InstallError("payload transaction disposal inventory is missing")
    if not root.exists() and journal["state"] not in TERMINAL_STATES:
        raise errors.InstallError("payload transaction root is not a directory")
    return journal


def removal_files(journal: ReadOnlyJsonObject) -> dict[str, str]:
    """Read one positive, root-relative deletion inventory from transaction authority."""
    files = journal.get("files")
    if not isinstance(files, dict) or (not files and journal.get("state") == "purged"):
        raise errors.InstallError("payload removal inventory is invalid")
    result = {}
    for name, expected in files.items():
        relative = owned_files.canonical_relative(name, "payload removal")
        parts = relative.split("/")
        if parts[0] == identity.PAYLOAD_GENERATIONS_DIRNAME:
            if len(parts) < 3 or re.fullmatch(r"[0-9a-f]{32}", parts[1]) is None:
                raise errors.InstallError("payload removal generation is invalid")
            member = "/".join(parts[2:])
        else:
            member = relative
        if (
            (
                member not in set(owned_files.OWNED_PAYLOAD_METADATA)
                and relative
                not in {
                    inventory.INSTALLED_RELEASE_STATE_FILENAME,
                    identity.PAYLOAD_SELECTOR_FILENAME,
                }
                and not inventory.is_runtime_file(member)
                and not inventory.is_runtime_file(member, windows=True)
            )
            or not isinstance(expected, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
        ):
            raise errors.InstallError("payload removal file identity is invalid")
        result[relative] = expected
    return result


def write_journal(
    ctx: runtime_context.RuntimeContext,
    *,
    transaction_id: str,
    version: str,
    receipt_sha256: str,
    state: str,
    fresh: bool,
    previous_generation: str | None = None,
    previous_predecessor: str | None = None,
    phase: str | None = None,
    reason: str | None = None,
    files: dict[str, str] | None = None,
) -> None:
    """Write one canonical secret-free transaction journal."""
    journal: JsonObject = {
        "schema_version": TRANSACTION_JOURNAL_SCHEMA,
        "transaction_id": transaction_id,
        "version": version,
        "receipt_sha256": receipt_sha256,
        "state": state,
        "fresh": fresh,
    }
    if reason is not None:
        journal["reason"] = reason
    if previous_generation is not None:
        journal["previous_generation"] = previous_generation
    if previous_predecessor is not None:
        journal["previous_predecessor"] = previous_predecessor
    if phase is not None:
        journal["phase"] = phase
    if files is not None:
        journal["files"] = files
        removal_files(journal)
    owned_files.write_bytes(journal_path(ctx), digest.canonical_json(journal), mode=0o600)


def read_installed(ctx: runtime_context.RuntimeContext) -> JsonObject | None:
    """Read and validate the installed release state when present."""
    path = installed_path(ctx)
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise errors.InstallError("installed release state is invalid")
    state = owned_files.read_canonical_json(path, "installed release state")
    if state.get("schema_version") != INSTALLED_RELEASE_STATE_SCHEMA:
        raise errors.InstallError("installed release state schema is unsupported")
    receipt_sha256 = state.get("receipt_sha256")
    if (
        not isinstance(state.get("transaction_id"), str)
        or not state["transaction_id"]
        or not isinstance(receipt_sha256, str)
        or len(receipt_sha256) != 64
        or any(character not in "0123456789abcdef" for character in receipt_sha256)
        or not isinstance(state.get("runtime"), dict)
    ):
        raise errors.InstallError("installed release state is invalid")
    require_version(state)
    require_command(state)
    return state


def require_version(state: ReadOnlyJsonObject) -> str:
    """Return a strict installed-state version."""
    version = state.get("version")
    if not isinstance(version, str) or _STRICT_VERSION.fullmatch(version) is None:
        raise errors.InstallError("installed release state version is invalid")
    return version


def require_command(state: ReadOnlyJsonObject) -> str:
    """Return the absolute command path recorded at installation."""
    command = state.get("command")
    if not isinstance(command, str) or not command or not Path(command).is_absolute():
        raise errors.InstallError("installed release state command path is invalid")
    return command


def compare_versions(left: str, right: str) -> int:
    """Compare two already validated semantic versions."""
    versions = tuple(version_key(version) for version in (left, right))
    return (versions[0] > versions[1]) - (versions[0] < versions[1])


def version_key(value: str) -> tuple[int, int, int]:
    """Return one strict release tuple shared by lifecycle ordering decisions."""
    if _STRICT_VERSION.fullmatch(value) is None:
        raise errors.InstallError("installed release state version is invalid")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)
