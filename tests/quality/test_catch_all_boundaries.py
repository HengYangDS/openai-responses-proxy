"""A broad exception handler is allowed only at a declared catch-all boundary."""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize

import pytest

from tests.quality.fixtures import ROOT
from tools.quality import responsibilities

BROAD = frozenset({"BaseException", "Exception"})
# A boundary marker names Ruff's rule, then the declared boundary role.
MARKER = re.compile(r"#\s*noqa:\s*BLE001\s+(?P<role>[a-z][a-z-]*)\b")


def _boundary_roles() -> frozenset[str]:
    """Return the catch-all boundary roles that the responsibility map declares."""
    report = responsibilities.audit()
    assert report["ok"], report["errors"]
    return frozenset(report["catch_all_boundaries"])


def _broad(node: ast.expr | None) -> bool:
    """Return whether an except clause catches every exception, or every ordinary one."""
    if node is None:
        return True
    if isinstance(node, ast.Tuple):
        return any(_broad(element) for element in node.elts)
    name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
    return name in BROAD


def _reraises(handler: ast.ExceptHandler) -> bool:
    """Return whether the handler raises again, which Ruff's BLE001 also accepts."""
    return any(isinstance(node, ast.Raise) for node in ast.walk(handler))


def unmarked_catch_alls(source: str, filename: str, roles: frozenset[str]) -> list[str]:
    """Return each broad handler that neither raises again nor carries a declared marker."""
    comments = {
        token.start[0]: token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    }
    gaps = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.ExceptHandler) or not _broad(node.type) or _reraises(node):
            continue
        marker = MARKER.search(comments.get(node.lineno, ""))
        if marker is None or marker["role"] not in roles:
            gaps.append(f"{filename}:{node.lineno}")
    return gaps


def _tracked_python() -> list[str]:
    completed = subprocess.run(
        ("git", "ls-files", "-z", "--", "*.py"),
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return [path for path in completed.stdout.decode().split("\0") if path]


def test_every_catch_all_sits_at_a_declared_boundary() -> None:
    roles = _boundary_roles()
    gaps = [
        gap
        for relative in _tracked_python()
        for gap in unmarked_catch_alls(
            (ROOT / relative).read_text(encoding="utf-8"), relative, roles
        )
    ]
    assert gaps == []


def test_every_declared_boundary_role_is_used() -> None:
    used = set()
    for relative in _tracked_python():
        for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
            if (marker := MARKER.search(line)) is not None:
                used.add(marker["role"])
    assert used == _boundary_roles()


@pytest.mark.parametrize(
    ("handler", "allowed"),
    [
        ("except Exception:  # noqa: BLE001 declared-role\n        pass\n", True),
        ("except Exception:  # noqa: BLE001 undeclared-role\n        pass\n", False),
        ("except Exception:  # noqa: BLE001\n        pass\n", False),
        ("except Exception:\n        pass\n", False),
        ("except (ValueError, BaseException):\n        pass\n", False),
        ("except:  # noqa: E722\n        pass\n", False),
        ("except Exception:\n        raise\n", True),
        ("except ValueError:\n        pass\n", True),
    ],
)
def test_a_broad_handler_needs_a_marker_naming_a_declared_role(
    handler: str, *, allowed: bool
) -> None:
    source = f"try:\n    pass\n{handler}".replace("\n        ", "\n    ")

    gaps = unmarked_catch_alls(source, "probe.py", frozenset({"declared-role"}))

    assert (gaps == []) is allowed
