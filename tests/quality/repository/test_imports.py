"""Import resolution, typing and Git environment ownership."""

from __future__ import annotations

import ast
import io
import tokenize

from tests.quality.fixtures import ROOT


class TestImportIntegrity:
    def test_python_data_models_do_not_escape_through_any(self) -> None:
        offenders = []
        for root in (ROOT / "src", ROOT / "tools", ROOT / "tests"):
            for path in root.rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                if any(
                    isinstance(node, ast.ImportFrom)
                    and node.module == "typing"
                    and any(alias.name == "Any" for alias in node.names)
                    for node in ast.walk(tree)
                ):
                    offenders.append(path.relative_to(ROOT).as_posix())
        assert offenders == []

    def test_product_and_tests_never_mutate_import_resolution_or_suppress_analysis(
        self,
    ) -> None:
        offenders = []
        for root in (ROOT / "tests",):
            for path in root.rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
                if any(
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "sys"
                    and node.attr == "path"
                    for node in ast.walk(tree)
                ):
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:sys.path")
                comments = (
                    token.string
                    for token in tokenize.generate_tokens(io.StringIO(source).readline)
                    if token.type == tokenize.COMMENT
                )
                if any("noqa" in comment or "type: ignore" in comment for comment in comments):
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:suppression")
        assert offenders == []

    def test_git_subprocess_isolation_has_one_semantic_owner(self) -> None:
        owner = ROOT / "tools/git_environment.py"
        isolated_config_consumers = (
            ROOT / "tools/forge/audit.py",
            ROOT / "tests/forge/test_audit.py",
            ROOT / "tests/quality/fixtures.py",
        )
        immutable_proof_consumers = (ROOT / "tools/release/publication/git.py",)

        assert owner.is_file()
        assert owner.read_text(encoding="utf-8").count("GIT_CONFIG_NOSYSTEM") == 1
        for consumer in isolated_config_consumers:
            source = consumer.read_text(encoding="utf-8")
            assert "from tools.git_environment import isolated_config_environment" in source
            assert "GIT_CONFIG_NOSYSTEM" not in source
            assert "GIT_CONFIG_GLOBAL" not in source
        for consumer in immutable_proof_consumers:
            source = consumer.read_text(encoding="utf-8")
            assert "from tools.git_environment import immutable_remote_proof_environment" in source
            assert "GIT_CONFIG_NOSYSTEM" not in source
            assert "GIT_CONFIG_GLOBAL" not in source
