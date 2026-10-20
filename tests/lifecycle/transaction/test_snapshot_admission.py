"""Transaction effects require the original, unchanged command snapshot."""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import command
from openai_responses_proxy.lifecycle import records
from openai_responses_proxy.serving import identity
from tests.lifecycle.fixtures import begin_transaction
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import released_artifact
from tests.lifecycle.transaction.fixtures import _retained_carrier_snapshot
from tests.lifecycle.transaction.fixtures import recover_transaction
from tests.lifecycle.transaction.fixtures import recovery_runtime


@pytest.mark.parametrize("operation", ["activate", "rollback", "recover"])
@pytest.mark.parametrize("replacement", ["canonical_bytes", "file_link", "directory_link"])
def test_transaction_refuses_replaced_snapshot_before_any_effect(
    tmp_path: Path, operation: str, replacement: str, *, mocker
) -> None:
    """Preserve every carrier and external command before rejecting snapshot drift."""
    ctx = install_context(tmp_path)
    transaction = begin_transaction(ctx, released_artifact(), mocker=mocker)
    transaction.commit_projection()
    if operation == "rollback":
        transaction.activate()
    rollback = records.transaction_root(ctx) / "rollback"
    snapshot = rollback / command.SNAPSHOT_FILENAME
    original = snapshot.read_bytes()
    external = tmp_path / "external"
    external.mkdir()
    target = external / "runtime"
    target.write_bytes(b"operator runtime")
    operator_command = external / command.COMMAND_NAME
    command.project(operator_command, target)
    if replacement == "canonical_bytes":
        snapshot.write_bytes(command.snapshot_bytes(command.snapshot(operator_command, target)))
    elif replacement == "file_link":
        retained = external / command.SNAPSHOT_FILENAME
        retained.write_bytes(original)
        snapshot.unlink()
        snapshot.symlink_to(retained)
    else:
        snapshot.unlink()
        rollback.rmdir()
        (external / command.SNAPSHOT_FILENAME).write_bytes(original)
        rollback.symlink_to(external, target_is_directory=True)
    before = _retained_carrier_snapshot(tmp_path, tmp_path / "unused")
    binding = mocker.Mock()
    disposal = mocker.Mock()
    if operation == "activate":
        effect = transaction.activate
    elif operation == "rollback":
        effect = transaction.rollback
    else:
        candidate = identity.committed_payload(Path(transaction.context.executable))
        assert candidate is not None
        effect = partial(
            recover_transaction,
            ctx,
            runtime=recovery_runtime(candidate),
            bind_terminal=binding,
            discard_native=disposal,
        )

    with pytest.raises(errors.InstallError, match="command snapshot"):
        effect()

    assert _retained_carrier_snapshot(tmp_path, tmp_path / "unused") == before
    binding.assert_not_called()
    disposal.assert_not_called()
    if replacement == "directory_link":
        rollback.unlink()
        rollback.mkdir()
    elif replacement == "file_link":
        snapshot.unlink()
    snapshot.write_bytes(original)
    transaction.rollback()
    assert records.status(ctx) is None
