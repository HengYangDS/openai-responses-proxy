"""The release age is declared once, and every resolver's own setting is filled from it."""

from __future__ import annotations

import json
import math
import tomllib
from typing import TYPE_CHECKING

import pytest

from tests.quality.fixtures import ROOT
from tools.quality import release_age

if TYPE_CHECKING:
    from pathlib import Path

RENOVATE = ".config/checks/renovate/renovate.json5"
UV = ".config/mise/uv.toml"
NPM = ".config/npm/npmrc"


def _copy(root: Path, declared: str | None) -> Path:
    """Copy the declaration and the resolvers' files, with the declaration set to declared."""
    for relative in (release_age.DECLARATION, *(setting.path for setting in release_age.SETTINGS)):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    declaration = root / release_age.DECLARATION
    lines = [
        line
        for line in declaration.read_text(encoding="utf-8").splitlines()
        if not line.startswith("minimum_release_age")
    ]
    if declared is not None:
        lines.insert(lines.index("[settings]") + 1, f"minimum_release_age = {declared}")
    declaration.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def _held(root: Path) -> tuple[object, object, object, object]:
    """The age in each resolver's file: Renovate's root and security rule, uv, and npm."""
    policy = json.loads((root / RENOVATE).read_text(encoding="utf-8"))
    uv = tomllib.loads((root / UV).read_text(encoding="utf-8"))
    npm = dict(
        line.split("=", 1)
        for line in (root / NPM).read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return (
        policy["minimumReleaseAge"],
        policy["vulnerabilityAlerts"]["minimumReleaseAge"],
        uv.get("exclude-newer"),
        npm.get("min-release-age"),
    )


def test_every_resolver_holds_the_declared_release_age() -> None:
    # The repository's own files: whatever age is declared, no file holds another.
    assert release_age.gaps() == ()
    hours = release_age.declared_hours()
    renovate, security, uv, npm = _held(ROOT)
    assert renovate == f"{hours} hours"
    assert security == "0 hours"
    assert (uv, npm) == ((f"{hours} hours", str(math.ceil(hours / 24))) if hours else (None, None))


@pytest.mark.parametrize("hours", [0, 1, 24, 25, 72])
def test_fill_follows_the_declaration(tmp_path: Path, hours: int) -> None:
    root = _copy(tmp_path, f'"{hours}h"')
    untouched = {
        path: [
            line
            for line in (root / path).read_text(encoding="utf-8").splitlines()
            if not any(setting.line.fullmatch(line) for setting in release_age.SETTINGS)
        ]
        for path in (RENOVATE, UV, NPM)
    }

    release_age.fill(root)

    renovate, security, uv, npm = _held(root)
    assert renovate == f"{hours} hours"
    # A security fix skips the age, whatever it is.
    assert security == "0 hours"
    # npm counts whole days, and an age of zero leaves uv's and npm's lines out.
    assert (uv, npm) == ((f"{hours} hours", str(math.ceil(hours / 24))) if hours else (None, None))
    # Nothing else in a file moves, a second fill writes nothing, and the check passes.
    for path, lines in untouched.items():
        text = (root / path).read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert not text.endswith("\n\n")
        assert [
            line
            for line in text.splitlines()
            if not any(setting.line.fullmatch(line) for setting in release_age.SETTINGS)
        ] == lines
    assert release_age.fill(root) == ()
    assert release_age.gaps(root) == ()


@pytest.mark.parametrize("declared", [None, '"1d"', '"24"', '"24 hours"', "24", '"-1h"', '"01h"'])
def test_an_age_that_is_not_whole_hours_is_refused(tmp_path: Path, declared: str | None) -> None:
    root = _copy(tmp_path, declared)
    before = {path: (root / path).read_bytes() for path in (RENOVATE, UV, NPM)}

    with pytest.raises(release_age.ReleaseAgeError, match="release_age_undeclared"):
        release_age.fill(root)
    with pytest.raises(release_age.ReleaseAgeError, match="release_age_undeclared"):
        release_age.gaps(root)

    assert {path: (root / path).read_bytes() for path in before} == before


def test_the_check_names_a_file_whose_age_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _copy(tmp_path, '"48h"')
    release_age.fill(root)
    npm = root / NPM
    npm.write_text(npm.read_text(encoding="utf-8").replace("=2", "=9"), encoding="utf-8")
    monkeypatch.setattr(release_age, "ROOT", root)

    assert release_age.gaps(root) == (NPM,)
    with pytest.raises(SystemExit) as refused:
        release_age.main(())
    assert refused.value.code == 1
    assert f"release_age_differs:{NPM}" in capsys.readouterr().err

    release_age.main(("--fix",))
    assert release_age.gaps(root) == ()
    release_age.main(())


def test_a_file_that_cannot_hold_the_age_is_refused_before_any_write(tmp_path: Path) -> None:
    root = _copy(tmp_path, '"24h"')
    policy = root / RENOVATE
    text = policy.read_text(encoding="utf-8")
    root_line = next(
        line for line in text.splitlines() if release_age.SETTINGS[0].line.fullmatch(line)
    )
    policy.write_text(text.replace(root_line + "\n", ""), encoding="utf-8")
    before = {path: (root / path).read_bytes() for path in (RENOVATE, UV, NPM)}

    refusal = f"release_age_setting_unreadable:{RENOVATE}"
    with pytest.raises(release_age.ReleaseAgeError, match=refusal):
        release_age.fill(root)

    assert {path: (root / path).read_bytes() for path in before} == before
