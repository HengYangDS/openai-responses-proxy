"""Executable source and function structure measurements."""

from __future__ import annotations

import pytest

from tests.quality.fixtures import audit_source


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
        ],
        ids=("inline-comment", "docstring-with-code", "function-docstring", "multiline-data"),
    )
    def test_file_and_function_eloc_share_executable_line_semantics(
        self, source: str, effective_lines: int, function_lines: int
    ) -> None:
        _, inventory = audit_source(source)
        assert inventory[0]["effective_lines"] == effective_lines
        assert inventory[0]["max_function_lines"] == function_lines

    def test_structural_metrics_are_observations_not_merge_thresholds(self) -> None:
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
