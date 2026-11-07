"""Offline Git-object and external-anchor publication contracts."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import cast

import pytest

from tools.git_environment import immutable_remote_proof_environment
from tools.release.publication import git

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

ROOT = Path(__file__).resolve().parents[3]


class GitPublicationContracts:
    """Require an exact annotated tag verified by an external signer anchor."""

    def setup_method(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.remote = self.root / "remote.git"
        self.key = self.root / "signing"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", self.key], check=True)
        public = " ".join(self.key.with_suffix(".pub").read_text().split()[:2])
        self.anchor = self.root / "allowed-signers"
        self.anchor.write_text(
            f'release@example.test namespaces="git" {public}\n', encoding="utf-8"
        )
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "init",
                "-q",
                "-b",
                "main",
                self.repo,
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", self.repo, "config", "core.hooksPath", "/dev/null"],
            check=True,
        )
        subprocess.run(["git", "-C", self.repo, "config", "user.name", "Release Test"], check=True)
        subprocess.run(
            ["git", "-C", self.repo, "config", "user.email", "release@example.test"],
            check=True,
        )
        (self.repo / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        subprocess.run(["git", "-C", self.repo, "add", "VERSION"], check=True)
        subprocess.run(["git", "-C", self.repo, "commit", "-qm", "release"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                self.repo,
                "-c",
                "gpg.format=ssh",
                "-c",
                f"user.signingkey={self.key.with_suffix('.pub')}",
                "-c",
                "gpg.ssh.program=ssh-keygen",
                "tag",
                "-s",
                "-a",
                "v1.2.3",
                "-m",
                "OpenAI Responses Proxy v1.2.3",
            ],
            check=True,
        )
        subprocess.run(["git", "init", "-q", "--bare", self.remote], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                self.repo,
                "push",
                "-q",
                self.remote,
                "main",
                "refs/tags/v1.2.3",
            ],
            check=True,
        )

    def teardown_method(self) -> None:
        self.temp.cleanup()

    def test_collects_exact_signed_tag_identity(self) -> None:
        evidence = git.collect(
            provider="gitlab", remote=str(self.remote), tag="v1.2.3", anchor=self.anchor
        )
        assert evidence["provider"] == "gitlab"
        assert evidence["signature_verified"]
        assert len(cast("str", evidence["tag_object_oid"])) == 40
        assert len(cast("str", evidence["commit_oid"])) == 40
        assert len(cast("str", evidence["tree_oid"])) == 40

    def test_rejects_lightweight_tag_and_wrong_anchor(self) -> None:
        subprocess.run(["git", "-C", self.repo, "tag", "v1.2.4"], check=True)
        subprocess.run(
            ["git", "-C", self.repo, "push", "-q", self.remote, "refs/tags/v1.2.4"],
            check=True,
        )
        with pytest.raises(git.GitProofError):
            git.collect(
                provider="gitlab",
                remote=str(self.remote),
                tag="v1.2.4",
                anchor=self.anchor,
            )
        wrong = self.root / "wrong"
        wrong.write_text(
            "nobody@example.test ssh-ed25519 "
            "AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        )
        with pytest.raises(git.GitProofError):
            git.collect(provider="gitlab", remote=str(self.remote), tag="v1.2.3", anchor=wrong)

    def test_rejects_invalid_inputs_and_unavailable_tools(
        self, subtests: pytest.Subtests, *, mocker: MockerFixture
    ) -> None:
        for provider, tag, anchor in (
            ("other", "v1.2.3", self.anchor),
            ("gitlab", "latest", self.anchor),
            ("gitlab", "v1.2.3", self.root / "missing"),
        ):
            with (
                subtests.test(provider=provider, tag=tag),
                pytest.raises(git.GitProofError),
            ):
                git.collect(
                    provider=provider,
                    remote=str(self.remote),
                    tag=tag,
                    anchor=anchor,
                )
        for missing in ("ssh-keygen", "git"):
            mocker.patch.object(
                git.shutil,
                "which",
                side_effect=lambda name, missing=missing: None if name == missing else f"/{name}",
            )
            with pytest.raises(git.GitProofError):
                git.collect(
                    provider="gitlab",
                    remote=str(self.remote),
                    tag="v1.2.3",
                    anchor=self.anchor,
                )

    def test_environment_error_translation_and_ascii_boundary(
        self, *, mocker: MockerFixture
    ) -> None:
        mocker.patch.dict(
            os.environ,
            {"GIT_DIR": "foreign", "GIT_TERMINAL_PROMPT": "9"},
            clear=True,
        )
        environment = immutable_remote_proof_environment()
        assert "GIT_DIR" not in environment
        assert environment["GIT_TERMINAL_PROMPT"] == "0"

        assert "GIT_CONFIG_COUNT" not in environment
        assert "credential.helper" not in environment.values()

        completed = SimpleNamespace(stdout=b"\xff")
        non_ascii = mocker.patch.object(git, "_run", return_value=completed)
        with pytest.raises(git.GitProofError, match="not ASCII"):
            git._output(("git",), {})
        mocker.stop(non_ascii)
        mocker.patch.object(
            git.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, ["git"]),
        )
        mocker.patch.object(git.shutil, "which", side_effect=lambda name: f"/{name}")

        with pytest.raises(git.GitProofError, match="fetch, verification"):
            git.collect(
                provider="gitlab",
                remote=str(self.remote),
                tag="v1.2.3",
                anchor=self.anchor,
            )
