"""Executable source and function structure measurements."""

from __future__ import annotations

import tomllib

import pytest

from tests.quality.fixtures import audit_source
from tools.quality.repository import __main__ as repository_audit


class TestStructuralMeasurement:
    @pytest.mark.parametrize(
        "source",
        ["value = call(first, second)\n", "value = call(\n first,\n second,\n)\n"],
    )
    def test_logical_statement_metric_is_invariant_to_formatter_wrapping(self, source: str) -> None:
        gaps, inventory = audit_source(source)
        assert gaps == []
        assert inventory[0]["logical_statements"] == 1

    def test_structural_inventory_exposes_eloc_function_size_and_nesting(self) -> None:
        gaps, inventory = audit_source(
            "def owner(value):\n    if value:\n        return value\n    return None\n"
        )
        assert gaps == []
        assert inventory[0]["effective_lines"] == 4
        assert inventory[0]["max_function_lines"] == 4
        assert inventory[0]["max_nesting_depth"] == 1

    @pytest.mark.parametrize(
        ("source", "effective_lines", "function_lines"),
        [
            ("value = 1  # inline comment\n# comment-only line\n", 1, 0),
            ('"""Module documentation."""; value = "retained"\n', 1, 0),
            (
                'def owner():\n    """Summary.\n\n    Detail.\n    """\n\n'
                "    value = 1  # inline comment\n    # comment-only line\n    return value\n",
                3,
                3,
            ),
            ('value = """first\n\nlast"""\n', 2, 0),
            ("@decorate\ndef owner():\n    return 1\n", 3, 3),
            ("@(\n    decorate\n)\ndef owner():\n    return 1\n", 5, 5),
            ("@(\n    first\n    @ second\n)\ndef owner():\n    return 1\n", 6, 6),
            (
                "class Owner:\n    @(\n        decorate\n    )\n"
                "    async def method():\n        return 1\n",
                6,
                5,
            ),
        ],
        ids=(
            "inline-comment",
            "docstring-with-code",
            "function-docstring",
            "multiline-data",
            "executable-decorator",
            "parenthesized-decorator",
            "decorator-with-matrix-expression",
            "decorated-async-method",
        ),
    )
    def test_file_and_function_eloc_share_executable_line_semantics(
        self, source: str, effective_lines: int, function_lines: int
    ) -> None:
        _, inventory = audit_source(source)
        assert inventory[0]["effective_lines"] == effective_lines
        assert inventory[0]["max_function_lines"] == function_lines

    @pytest.mark.parametrize(
        ("source", "depth"),
        [
            (
                "def owner(value):\n"
                "    if value == 1:\n        return 1\n"
                "    elif value == 2:\n        return 2\n"
                "    elif value == 3:\n        return 3\n",
                1,
            ),
            (
                "def owner(value):\n"
                "    if value == 1:\n        return 1\n"
                "    else:\n        if value == 2:\n            return 2\n",
                2,
            ),
            (
                "def owner(value):\n"
                "    if value:\n"
                "        def nested():\n"
                "            if value == 1:\n                return 1\n",
                1,
            ),
        ],
        ids=("sibling-elif", "nested-else-if", "independent-function"),
    )
    def test_nesting_follows_control_blocks_not_ast_encoding(self, source: str, depth: int) -> None:
        _, inventory = audit_source(source)
        assert inventory[0]["max_nesting_depth"] == depth

    @pytest.mark.parametrize(
        ("source", "statements"),
        [
            ("first = 1; second = 2\n", 2),
            ('"""Module documentation."""; first = 1; second = 2\n', 2),
            ("if ready:\n    first = 1; second = 2\n", 3),
        ],
    )
    def test_logical_statements_follow_syntax_not_line_compaction(
        self, source: str, statements: int
    ) -> None:
        _, inventory = audit_source(source)
        assert inventory[0]["logical_statements"] == statements

    @pytest.mark.parametrize("container", ["module", "class"])
    def test_nesting_includes_module_and_class_bodies(self, container: str) -> None:
        prefix = "class Owner:\n" if container == "class" else ""
        indent = "    " if container == "class" else ""
        _, inventory = audit_source(
            prefix
            + indent
            + "if ready:\n"
            + indent
            + "    if available:\n"
            + indent
            + "        value = 1\n"
        )
        assert inventory[0]["max_nesting_depth"] == 2

    @pytest.mark.parametrize(
        "relative",
        [
            "src/openai_responses_proxy/relay/probe.py",
            "tools/probe.py",
            "tests/test_probe.py",
            "noxfile.py",
        ],
    )
    @pytest.mark.parametrize("excess", [False, True])
    def test_control_depth_boundary_is_enforced_for_every_role(
        self, relative: str, excess: bool
    ) -> None:
        policy = tomllib.loads(repository_audit.CONFIG.read_text(encoding="utf-8"))
        limit = policy["control_nesting"]["max_depth"]
        depth = limit + int(excess)
        source = "\n".join(
            [
                "def owner(value):",
                *["    " * (level + 1) + f"if value > {level}:" for level in range(depth)],
                "    " * (depth + 1) + "return value",
                "    return 0",
                "",
            ]
        )
        gaps, inventory = audit_source(source, relative)
        assert inventory[0]["max_nesting_depth"] == depth
        assert gaps == (
            [f"quality_nesting_depth_exceeds_limit:{relative}:{depth}>{limit}"] if excess else []
        )

    def test_linear_sequence_remains_admitted_when_control_depth_is_bounded(self) -> None:
        source = "\n".join(
            [
                "def owner(value):",
                *[f"    value += {index}" for index in range(120)],
                "    return value",
            ]
        )
        gaps, inventory = audit_source(source)
        assert gaps == []
        assert inventory[0]["logical_statements"] == 122
        assert inventory[0]["effective_lines"] == 122

    @pytest.mark.parametrize("value", [None, True, 0, -1, "5"])
    def test_missing_or_invalid_control_depth_policy_cannot_admit_source(
        self, value: object, monkeypatch
    ) -> None:
        policy = tomllib.loads(repository_audit.CONFIG.read_text(encoding="utf-8"))
        if value is None:
            del policy["control_nesting"]
        else:
            policy["control_nesting"]["max_depth"] = value
        original = repository_audit.tomllib.loads
        monkeypatch.setattr(
            repository_audit.tomllib,
            "loads",
            lambda text: policy if "[product_concepts]" in text else original(text),
        )
        report = repository_audit.audit()
        assert report["ok"] is False
        assert (
            "quality_policy_control_nesting_max_depth_must_be_positive_integer"
            in report["policy_errors"]
        )
