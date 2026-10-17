"""Git-index scope, source identity and fixture carrier admission."""

from __future__ import annotations

import subprocess
from functools import partial
from pathlib import Path

import pytest

from tests.quality.fixtures import git
from tests.quality.fixtures import quality_inventory
from tests.quality.fixtures import repository
from tools.quality import governance
from tools.quality import responsibilities
from tools.quality import text_layout
from tools.quality.repository import __main__ as repository_audit
from tools.quality.repository.names import semantic_name_gaps


class TestOwnedInventory:
    def test_quality_inventory_uses_index_ownership_with_staged_add_and_delete(
        self,
    ) -> None:
        with repository(("src/current.py", "tests/test_current.py")) as root:
            added = root / "src" / "added.py"
            added.write_text("pass\n", encoding="utf-8")
            git(root, "add", "--", "src/added.py")
            (root / "src/current.py").unlink()
            git(root, "add", "--", "src/current.py")
            inventory = quality_inventory(root)
        assert inventory.gaps == ()
        assert [path.relative_to(root).as_posix() for path in inventory.paths] == [
            "src/added.py",
            "tests/test_current.py",
        ]

    def test_quality_inventory_requires_its_own_repository_root(self) -> None:
        with repository(("nested/src/current.py", "nested/tests/test_current.py")) as parent:
            inventory = quality_inventory(parent / "nested")
        assert inventory.paths == ()
        assert any(gap.startswith("quality_inventory_git_failed:") for gap in inventory.gaps)

    @pytest.mark.parametrize("consumer", ["responsibilities", "text", "names", "governance"])
    def test_quality_consumers_require_their_own_repository_root(
        self, consumer: str, *, monkeypatch
    ) -> None:
        with repository(("nested/README.md",)) as parent:
            root = parent / "nested"
            monkeypatch.setattr(governance, "ROOT", root)
            read = {
                "responsibilities": partial(responsibilities._tracked_paths, root),
                "text": partial(text_layout._tracked, root),
                "names": partial(semantic_name_gaps, root),
                "governance": partial(governance._tracked_current, (".md",)),
            }[consumer]
            with pytest.raises(subprocess.CalledProcessError):
                read()

    def test_quality_inventory_reads_the_linked_worktree_index(self, tmp_path: Path) -> None:
        with repository(("src/current.py", "tests/test_current.py")) as root:
            git(
                root,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgSign=false",
                "commit",
                "-qm",
                "fixture",
            )
            lane = tmp_path / "linked-lane"
            try:
                git(root, "worktree", "add", "--detach", str(lane))
                (lane / "src/added.py").write_text("pass\n", encoding="utf-8")
                git(lane, "add", "src/added.py")
                inventory = quality_inventory(lane)
                assert inventory.gaps == ()
                assert [path.relative_to(lane).as_posix() for path in inventory.paths] == [
                    "src/added.py",
                    "src/current.py",
                    "tests/test_current.py",
                ]
                assert "src/added.py" not in git(root, "ls-files").stdout.decode()
            finally:
                git(root, "worktree", "remove", "--force", str(lane))

    def test_quality_inventory_rejects_untracked_missing_and_out_of_scope_is_ignored(
        self,
    ) -> None:
        files = ("src/tracked.py", "tests/test_current.py", "outside/foreign.py")
        with repository(files, tracked=files[1:]) as root:
            (root / "tests/test_current.py").unlink()
            inventory = quality_inventory(root)
        assert inventory.paths == ()
        assert inventory.gaps == (
            "quality_inventory_missing:tests/test_current.py",
            "quality_inventory_untracked:src/tracked.py",
        )

    def test_quality_inventory_glob_does_not_expand_beyond_its_configured_depth(
        self,
    ) -> None:
        assert repository_audit._in_scope("owner.py", ("*.py",))
        assert not repository_audit._in_scope("nested/foreign.py", ("*.py",))
        assert repository_audit._in_scope("tools/owner.py", ("tools/*.py",))
        assert not repository_audit._in_scope("tools/nested/foreign.py", ("tools/*.py",))

    def test_quality_inventory_rejects_symlink_misnamed_test_and_empty_tests(
        self,
    ) -> None:
        with repository(("src/current.py", "tests/helper.py")) as root:
            target = root / "target.py"
            target.write_text("pass\n", encoding="utf-8")
            link = root / "src" / "link.py"
            link.symlink_to(target)
            git(root, "add", "--", "src/link.py")
            inventory = quality_inventory(root)
        assert inventory.gaps == (
            "quality_inventory_symlink:src/link.py",
            "quality_inventory_test_empty",
            "quality_inventory_test_misnamed:tests/helper.py",
        )

    def test_quality_inventory_accepts_semantic_fixture_carriers_only(self) -> None:
        files = (
            "src/current.py",
            "tests/test_current.py",
            "tests/__init__.py",
            "tests/serving/conftest.py",
            "tests/serving/fixtures.py",
            "tests/protocol/replay_fixture.py",
            "tests/nested/helper.py",
        )
        with repository(files) as root:
            inventory = quality_inventory(root)
        assert inventory.gaps == (
            "quality_inventory_test_misnamed:tests/nested/helper.py,tests/protocol/replay_fixture.py",
        )
