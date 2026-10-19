"""Contracts for the single bounded, secret-safe local log primitive."""

from __future__ import annotations

from pathlib import Path

import pytest

from openai_responses_proxy.runtime import bounded_log
from tests.lifecycle.fixtures import assert_private_log_mode


class BoundedLogTests:
    def test_append_redacts_bounds_rotates_and_mirrors(self, tmp_path: Path) -> None:
        path = tmp_path / "runtime.log"
        path.write_bytes(b"x" * 32)
        mirrored: list[str] = []

        bounded_log.append(
            path,
            "authorization: Bearer private-token encrypted=gAAAA-secret tail " + "x" * 2048,
            max_bytes=16,
            backup_count=1,
            mirror=mirrored.append,
        )

        text = path.read_text(encoding="utf-8")
        assert "private-token" not in text
        assert "gAAAA-secret" not in text
        assert "log_retention_discarded_oversized_bytes=32" in text
        assert "[truncated]" in text
        assert mirrored == [text]
        assert_private_log_mode(path.stat().st_mode & 0o777)

    def test_rotate_rejects_non_files_and_removes_oversized_backups(self, tmp_path: Path) -> None:
        path = tmp_path / "runtime.log"
        path.mkdir()
        with pytest.raises(OSError, match="not a regular file"):
            bounded_log.rotate(path, 1, max_bytes=4, backup_count=1)

        path.rmdir()
        path.write_text("current", encoding="utf-8")
        backup = path.with_name("runtime.log.1")
        backup.write_text("oversized", encoding="utf-8")

        discarded = bounded_log.rotate(path, 1, max_bytes=4, backup_count=1)

        assert discarded == len("current") + len("oversized")
        assert not backup.exists()

    def test_append_is_best_effort_for_file_and_mirror_failures(
        self, tmp_path: Path, *, mocker
    ) -> None:
        path = tmp_path / "runtime.log"
        path.mkdir()
        mirror = mocker.Mock(side_effect=OSError("closed"))

        bounded_log.append(path, "event=ignored", max_bytes=16, backup_count=0, mirror=mirror)

        mirror.assert_called_once()
