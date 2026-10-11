"""Publication identity and noninteractive signing-capability boundaries."""

import subprocess
from pathlib import Path

import pytest

from tools.forge import context


@pytest.mark.parametrize("version", [True, 1.0, 2])
def test_schema_requires_the_exact_integer_version(version, tmp_path: Path, mocker) -> None:
    path = tmp_path / "publication.toml"
    path.touch()
    mocker.patch.object(
        context.tomllib,
        "loads",
        return_value={
            "schema-version": version,
            "product": {
                "actor-name": "Publisher",
                "actor-email": "test@example.test",
                "active-signing-fingerprint": "SHA256:abcd",
            },
        },
    )
    with pytest.raises(context.PublicationContextError, match="schema"):
        context.load(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("actor-name", ""),
        ("actor-email", "\t"),
        ("active-signing-fingerprint", "invalid"),
    ],
)
def test_identity_requires_complete_fields(field, value, tmp_path: Path, mocker) -> None:
    path = tmp_path / "publication.toml"
    path.touch()
    fields = {
        "actor-name": "Publisher",
        "actor-email": "test@example.test",
        "active-signing-fingerprint": "SHA256:abcd",
        field: value,
    }
    mocker.patch.object(
        context.tomllib, "loads", return_value={"schema-version": 1, "product": fields}
    )
    with pytest.raises(context.PublicationContextError):
        context.load(path)


def test_load_accepts_one_complete_external_identity(tmp_path: Path) -> None:
    """Return the one declared identity from a complete external context."""
    path = tmp_path / "publication.toml"
    path.write_text(
        'schema-version = 1\n\n[product]\nactor-name = "Publisher"\n'
        'actor-email = "publisher@example.test"\n'
        'active-signing-fingerprint = "SHA256:selected"\n\n[other]\nvalue = 1\n',
        encoding="utf-8",
    )
    assert context.load(path) == context.PublicationContext(
        "Publisher", "publisher@example.test", "SHA256:selected"
    )


def test_load_reports_one_bounded_error_for_malformed_context(tmp_path: Path) -> None:
    """Translate a malformed or unreadable context file into one refusal."""
    malformed = tmp_path / "malformed.toml"
    malformed.write_text("this is not a context =\n", encoding="utf-8")
    with pytest.raises(
        context.PublicationContextError, match="invalid product publication context"
    ):
        context.load(malformed)
    with pytest.raises(
        context.PublicationContextError, match="invalid product publication context"
    ):
        context.load(tmp_path / "absent.toml")


@pytest.fixture
def signing_inputs(tmp_path, monkeypatch, mocker):
    monkeypatch.setenv("SSH_AUTH_SOCK", "isolated-agent")
    mocker.patch.object(context.shutil, "which", side_effect=lambda name: str(tmp_path / name))
    identity = context.PublicationContext("Publisher", "test@example.test", "SHA256:selected")
    return identity, tmp_path / "selected.pub"


def test_missing_agent_or_tools_never_attempts_credential_access(
    signing_inputs, monkeypatch, mocker
):
    run = mocker.patch.object(context.subprocess, "run")
    monkeypatch.delenv("SSH_AUTH_SOCK")
    with pytest.raises(context.PublicationContextError, match="agent is unavailable"):
        context.select_signing_key(*signing_inputs)
    monkeypatch.setenv("SSH_AUTH_SOCK", "isolated-agent")
    mocker.patch.object(context.shutil, "which", return_value=None)
    with pytest.raises(context.PublicationContextError, match="tools are unavailable"):
        context.select_signing_key(*signing_inputs)
    run.assert_not_called()


@pytest.mark.parametrize(
    "failure", [OSError("absent"), subprocess.CalledProcessError(1, ["ssh-add"])]
)
def test_unavailable_agent_has_one_bounded_failure(signing_inputs, failure, mocker):
    run = mocker.patch.object(context.subprocess, "run", side_effect=failure)
    with pytest.raises(context.PublicationContextError, match="capability is invalid"):
        context.select_signing_key(*signing_inputs)
    assert run.call_count == 1
    assert not signing_inputs[1].exists()


@pytest.mark.parametrize("selected", [False, True])
def test_selection_uses_only_exact_advertised_fingerprint(signing_inputs, selected, mocker):
    outputs = ["invalid\nssh-ed25519 rejected\n", "256 SHA256:other comment\n"]
    if selected:
        outputs[0] += "ssh-ed25519 accepted\n"
        outputs.extend(["256 SHA256:selected comment\n", ""])
    run = mocker.patch.object(
        context.subprocess,
        "run",
        side_effect=[subprocess.CompletedProcess([], 0, stdout=value) for value in outputs],
    )
    if selected:
        result = context.select_signing_key(*signing_inputs)
        assert result.public_key == signing_inputs[1]
        assert result.public_key.read_text() == "ssh-ed25519 accepted\n"
        assert run.call_args.args[0][1:] == ("-T", str(result.public_key))
    else:
        with pytest.raises(context.PublicationContextError, match="fingerprint is not loaded"):
            context.select_signing_key(*signing_inputs)
        assert not signing_inputs[1].exists()
