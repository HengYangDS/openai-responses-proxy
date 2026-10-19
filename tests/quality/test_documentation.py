"""Canonical documentation reachability contracts."""

from __future__ import annotations

import subprocess
from urllib.parse import unquote
from urllib.parse import urlsplit

from rich.markdown import Markdown

from tests.quality.fixtures import ROOT


def test_canonical_documents_are_reachable_from_the_documentation_index() -> None:
    """Follow parsed links over the tracked documentation and its entry points."""
    selected = subprocess.run(
        ("git", "ls-files", "docs/*.md", "README.md", "CONTRIBUTING.md"),
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    documents = {ROOT / path for path in selected.stdout.splitlines()}
    entry = ROOT / "docs/README.md"
    assert entry in documents
    reached = {entry}
    pending = [entry]
    while pending:
        source = pending.pop()
        for token in Markdown(source.read_text(encoding="utf-8")).parsed:
            for child in token.children or ():
                if child.type != "link_open":
                    continue
                href = child.attrGet("href")
                assert isinstance(href, str)
                link = urlsplit(href)
                if link.scheme or link.netloc or not link.path:
                    continue
                target = (source.parent / unquote(link.path)).resolve()
                if target in documents and target not in reached:
                    reached.add(target)
                    pending.append(target)
    missing = documents - reached
    assert not missing, sorted(path.relative_to(ROOT).as_posix() for path in missing)
