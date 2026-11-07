"""Tracked text admission through the actual Git and byte-reading boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.quality.fixtures import repository
from tools.quality import text_layout

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("content", "errors"),
    [
        (b"plain\n", ()),
        (b"", ()),
        (b"\xff", ("text_encoding_invalid:note.md",)),
        (b"plain\r\n", ("text_line_ending_invalid:note.md",)),
        (b"plain", ("text_final_newline_missing:note.md",)),
        (b"plain \n", ("text_trailing_whitespace:note.md:1",)),
    ],
)
def test_exact_tracked_text_is_checked(content: bytes, errors: tuple[str, ...]) -> None:
    with repository(
        ("note.md", "image.bin", "untracked.md"), tracked=("note.md", "image.bin")
    ) as root:
        (root / "note.md").write_bytes(content)
        (root / "image.bin").write_bytes(b"\xff")
        (root / "untracked.md").write_bytes(b"\xff")
        assert text_layout.audit(root) == errors


def test_missing_and_linked_files_do_not_read_foreign_content(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"\xff")
    (tmp_path / "link.md").symlink_to(target)
    mocker.patch.object(text_layout, "_tracked", return_value=("absent.md", "link.md"))
    assert text_layout.audit(tmp_path) == ()


def test_cli_preserves_the_selected_policy_and_failed_exit(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    audit = mocker.patch.object(text_layout, "audit", return_value=())
    policy = tmp_path / "policy.toml"
    text_layout.main(("--policy", str(policy)))
    audit.assert_called_once_with(policy_path=policy)
    audit.return_value = ("invalid-text",)
    with pytest.raises(SystemExit, match="invalid-text"):
        text_layout.main(("--policy", str(policy)))
