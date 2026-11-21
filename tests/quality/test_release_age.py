"""The release age is declared once, and every resolver's own setting is filled from it."""

from __future__ import annotations

import json
import math
import re
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
# npm counts its age in days.
HOURS_PER_DAY = 24


def _declare(root: Path, declared: str | None) -> None:
    """Write the copy's declaration as declared, or leave it out."""
    declaration = root / release_age.DECLARATION
    lines = [
        line
        for line in declaration.read_text(encoding="utf-8").splitlines()
        if not line.startswith("minimum_release_age")
    ]
    if declared is not None:
        lines.insert(lines.index("[settings]") + 1, f"minimum_release_age = {declared}")
    declaration.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _copy(root: Path, declared: str | None) -> Path:
    """Copy the declaration and the resolvers' files, with the declaration set to declared."""
    for relative in (release_age.DECLARATION, *(setting.path for setting in release_age.SETTINGS)):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    _declare(root, declared)
    return root


def _another_age(hours: int) -> int:
    """An age above zero that gives every resolver another value than hours does."""
    return hours + HOURS_PER_DAY


def _filled_copy(root: Path) -> tuple[Path, int]:
    """A copy filled from an age above zero, whatever age the repository declares."""
    hours = _another_age(release_age.declared_hours())
    _copy(root, f'"{hours}h"')
    release_age.fill(root)
    return root, hours


def _bytes(root: Path) -> dict[str, bytes | None]:
    """Every resolver's file as it is on disk."""
    return {
        setting.path: (root / setting.path).read_bytes() if (root / setting.path).exists() else None
        for setting in release_age.SETTINGS
    }


def _own_line(path: str, text: str) -> str:
    """The one line of a file's text that is its setting's own."""
    (setting,) = [setting for setting in release_age.SETTINGS if setting.path == path]
    (line,) = [line for line in text.split("\n") if setting.line.fullmatch(line)]
    return line


def _rewrite(root: Path, path: str, old: str, new: str) -> None:
    """Replace old by new in one file, which holds old."""
    text = (root / path).read_text(encoding="utf-8")
    assert old in text
    (root / path).write_text(text.replace(old, new), encoding="utf-8")


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


def _given(hours: int) -> tuple[str | None, str | None]:
    """What an age gives uv and npm: a span of hours, and the whole days that cover it."""
    return (f"{hours} hours", str(math.ceil(hours / HOURS_PER_DAY))) if hours else (None, None)


def test_every_resolver_holds_the_declared_release_age() -> None:
    # The repository's own files: whatever age is declared, no file holds another.
    assert release_age.gaps() == ()
    hours = release_age.declared_hours()
    renovate, security, uv, npm = _held(ROOT)
    assert renovate == f"{hours} hours"
    assert security == "0 hours"
    assert (uv, npm) == _given(hours)


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
    assert (uv, npm) == _given(hours)
    # Nothing else in a file moves, a second fill writes nothing, and the check passes.
    for path, lines in untouched.items():
        text = (root / path).read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert not text.endswith("\n\n")
        # Every platform writes line feeds, as the repository's text attributes ask.
        assert b"\r" not in (root / path).read_bytes()
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
    before = _bytes(root)

    with pytest.raises(release_age.ReleaseAgeError, match="release_age_undeclared"):
        release_age.fill(root)
    with pytest.raises(release_age.ReleaseAgeError, match="release_age_undeclared"):
        release_age.gaps(root)

    assert _bytes(root) == before


@pytest.mark.parametrize("declaration", [b"[settings\n", b"\xff\n", None])
def test_a_declaration_that_cannot_be_read_is_refused_by_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    declaration: bytes | None,
) -> None:
    root = _copy(tmp_path, None)
    target = root / release_age.DECLARATION
    if declaration is None:
        target.unlink()
    else:
        target.write_bytes(declaration)
    before = _bytes(root)
    monkeypatch.setattr(release_age, "ROOT", root)

    # The command names the declaration and exits; neither parser's own error reaches the caller.
    for arguments in ((), ("--fix",)):
        with pytest.raises(SystemExit) as refused:
            release_age.main(arguments)
        assert refused.value.code == 1
        assert capsys.readouterr().err.startswith(
            f"release_age_undeclared:{release_age.DECLARATION}:"
        )

    assert _bytes(root) == before


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
    before = _bytes(root)

    refusal = f"release_age_setting_unreadable:{RENOVATE}"
    with pytest.raises(release_age.ReleaseAgeError, match=refusal):
        release_age.fill(root)

    assert _bytes(root) == before


@pytest.mark.parametrize("setting", release_age.SETTINGS, ids=lambda setting: setting.path)
@pytest.mark.parametrize(
    "content", [b"}{\n", b"\xff\n", None], ids=["syntax", "encoding", "absent"]
)
def test_a_file_that_its_resolver_cannot_read_is_refused_by_name(
    tmp_path: Path, setting: release_age.Setting, content: bytes | None
) -> None:
    root, _ = _filled_copy(tmp_path)
    target = root / setting.path
    if content is None:
        target.unlink()
    else:
        target.write_bytes(content)
    before = _bytes(root)

    refusal = re.escape(f"release_age_setting_unreadable:{setting.path}")
    with pytest.raises(release_age.ReleaseAgeError, match=refusal):
        release_age.gaps(root)
    with pytest.raises(release_age.ReleaseAgeError, match=refusal):
        release_age.fill(root)

    assert _bytes(root) == before


def test_a_refusal_of_the_last_file_leaves_the_earlier_ones_unwritten(tmp_path: Path) -> None:
    root = _copy(tmp_path, f'"{_another_age(release_age.declared_hours())}h"')
    *earlier, last = release_age.SETTINGS
    (root / last.path).write_bytes(b"}{\n")
    before = _bytes(root)

    refusal = re.escape(f"release_age_setting_unreadable:{last.path}")
    with pytest.raises(release_age.ReleaseAgeError, match=refusal):
        release_age.fill(root)

    assert _bytes(root) == before
    # Every earlier file was still to be written: a fill that wrote each file as it went would
    # have changed them before it reached the one that it refuses.
    (root / last.path).write_bytes((ROOT / last.path).read_bytes())
    assert set(release_age.fill(root)) >= {setting.path for setting in earlier}


@pytest.mark.parametrize(
    "written",
    ['exclude-newer="1 week"', '  exclude-newer = "1 week"', "exclude-newer=2026-01-01T00:00:00Z"],
)
def test_a_uv_age_written_another_way_becomes_the_one_declared_key(
    tmp_path: Path, written: str
) -> None:
    root, hours = _filled_copy(tmp_path)
    filled = (root / UV).read_text(encoding="utf-8")
    _rewrite(root, UV, _own_line(UV, filled), written)

    assert release_age.gaps(root) == (UV,)
    assert release_age.fill(root) == (UV,)

    # The line is replaced, never joined by a second key that uv would refuse.
    text = (root / UV).read_text(encoding="utf-8")
    assert tomllib.loads(text)["exclude-newer"] == f"{hours} hours"
    assert text == filled
    assert release_age.gaps(root) == ()


def test_a_setting_passes_by_the_value_that_its_resolver_reads(tmp_path: Path) -> None:
    root, hours = _filled_copy(tmp_path)
    filled = (root / UV).read_text(encoding="utf-8")
    _rewrite(root, UV, _own_line(UV, filled), f"exclude-newer='{hours} hours' # held")
    before = _bytes(root)

    assert release_age.gaps(root) == ()
    assert release_age.fill(root) == ()
    assert _bytes(root) == before


def test_a_uv_age_that_the_fill_cannot_replace_is_refused(tmp_path: Path) -> None:
    root, _ = _filled_copy(tmp_path)
    filled = (root / UV).read_text(encoding="utf-8")
    # uv reads the quoted key as the same setting, so a second key beside it is no valid file.
    _rewrite(root, UV, _own_line(UV, filled), '"exclude-newer" = "1 week"')
    before = _bytes(root)

    refusal = re.escape(f"release_age_setting_unreadable:{UV}")
    with pytest.raises(release_age.ReleaseAgeError, match=refusal):
        release_age.fill(root)

    assert _bytes(root) == before


def test_a_missing_uv_age_is_inserted_above_the_first_table(tmp_path: Path) -> None:
    root, hours = _filled_copy(tmp_path)
    table = '\n[pip]\nno-binary = ["certifi"]\n'
    filled = (root / UV).read_text(encoding="utf-8")
    own = _own_line(UV, filled)
    (root / UV).write_text(filled.replace(own + "\n", "") + table, encoding="utf-8")

    assert release_age.fill(root) == (UV,)

    # Appended to the file, the line would be the table's key and uv would hold no age.
    text = (root / UV).read_text(encoding="utf-8")
    document = tomllib.loads(text)
    assert document["exclude-newer"] == f"{hours} hours"
    assert document["pip"] == {"no-binary": ["certifi"]}
    lines = text.split("\n")
    assert lines.index(own) < lines.index("[pip]")
    assert [line for line in lines if line != own] == (
        filled.replace(own + "\n", "") + table
    ).split("\n")
    assert release_age.gaps(root) == ()


def test_the_fill_never_writes_the_age_into_the_security_rule(tmp_path: Path) -> None:
    root, _ = _filled_copy(tmp_path)
    text = (root / RENOVATE).read_text(encoding="utf-8")
    root_line = _own_line(RENOVATE, text)
    (nested,) = [
        line
        for line in text.split("\n")
        if line.strip().startswith('"minimumReleaseAge"') and line != root_line
    ]
    # The root line is gone and the security rule's line stands as the root's would.
    indentation = root_line[: len(root_line) - len(root_line.lstrip())]
    moved = indentation + nested.strip()
    assert release_age.SETTINGS[0].line.fullmatch(moved)
    (root / RENOVATE).write_text(
        text.replace(root_line + "\n", "").replace(nested, moved), encoding="utf-8"
    )
    before = _bytes(root)

    refusal = re.escape(f"release_age_setting_unreadable:{RENOVATE}")
    with pytest.raises(release_age.ReleaseAgeError, match=refusal):
        release_age.fill(root)

    assert _bytes(root) == before
    policy = json.loads((root / RENOVATE).read_text(encoding="utf-8"))
    assert policy["vulnerabilityAlerts"]["minimumReleaseAge"] == "0 hours"


def test_a_security_rule_with_an_age_of_its_own_is_refused(tmp_path: Path) -> None:
    root, hours = _filled_copy(tmp_path)
    text = (root / RENOVATE).read_text(encoding="utf-8")
    root_line = _own_line(RENOVATE, text)
    # The security rule's line is the root's, nested: with the root's value it holds the age.
    _rewrite(root, RENOVATE, f"  {root_line.replace(str(hours), '0')}", f"  {root_line}")
    before = _bytes(root)

    refusal = re.escape(f"release_age_setting_unreadable:{RENOVATE}")
    with pytest.raises(release_age.ReleaseAgeError, match=refusal):
        release_age.gaps(root)
    with pytest.raises(release_age.ReleaseAgeError, match=refusal):
        release_age.fill(root)

    assert _bytes(root) == before


@pytest.mark.parametrize("path", [RENOVATE, UV, NPM])
def test_a_setting_written_twice_is_refused(tmp_path: Path, path: str) -> None:
    root, _ = _filled_copy(tmp_path)
    # Both lines hold the declared age, and a JSON reader takes the second without a word.
    line = _own_line(path, (root / path).read_text(encoding="utf-8"))
    _rewrite(root, path, line, f"{line}\n{line}")
    before = _bytes(root)

    refusal = re.escape(f"release_age_setting_unreadable:{path}")
    with pytest.raises(release_age.ReleaseAgeError, match=refusal):
        release_age.gaps(root)
    with pytest.raises(release_age.ReleaseAgeError, match=refusal):
        release_age.fill(root)

    assert _bytes(root) == before


def test_a_line_separator_inside_a_json_string_does_not_end_a_line(tmp_path: Path) -> None:
    root, hours = _filled_copy(tmp_path)
    # JSON admits U+2028 inside a string; only a line feed ends a line of the file.
    note = "one\u2028two"
    held = (root / RENOVATE).read_text(encoding="utf-8")
    held = held.replace("{\n", f'{{\n  "note": "{note}",\n', 1)
    (root / RENOVATE).write_text(held, encoding="utf-8")
    again = _another_age(hours)
    _declare(root, f'"{again}h"')

    assert RENOVATE in release_age.fill(root)

    text = (root / RENOVATE).read_text(encoding="utf-8")
    policy = json.loads(text)
    assert policy["note"] == note
    assert policy["minimumReleaseAge"] == f"{again} hours"
    own = _own_line(RENOVATE, text)
    assert [line for line in text.split("\n") if line != own] == [
        line for line in held.split("\n") if line != _own_line(RENOVATE, held)
    ]


# One of uv's indexes, as an array of tables holds it.
_INDEX = '[[index]]\nurl = "https://mirror.test/simple"\n'


@pytest.mark.parametrize(
    ("path", "written", "key"),
    [
        (NPM, "before=2026-01-01", "before"),
        (NPM, "min-release-age-exclude=prettier", "min-release-age-exclude"),
        (NPM, "min-release-age-exclude[]=prettier", "min-release-age-exclude"),
        # A name of uv's age family that is neither the filled key nor the exemption.
        (UV, 'exclude-newer-span = "PT1H"', "exclude-newer-span"),
        (UV, '[pip]\nexclude-newer = "1 week"', "pip.exclude-newer"),
        (UV, "[pip.exclude-newer-package]\ncertifi = false", "pip.exclude-newer-package"),
        # One index's own age, which uv reads in place of the top-level one for that index.
        (UV, f'{_INDEX}exclude-newer = "0 days"', "index[].exclude-newer"),
        (UV, f"{_INDEX}exclude-newer = false", "index[].exclude-newer"),
    ],
)
def test_another_age_rule_is_refused_by_file_and_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    path: str,
    written: str,
    key: str,
) -> None:
    root, _ = _filled_copy(tmp_path)
    target = root / path
    target.write_text(f"{target.read_text(encoding='utf-8')}{written}\n", encoding="utf-8")
    before = _bytes(root)
    monkeypatch.setattr(release_age, "ROOT", root)

    gap = f"release_age_other_rule:{path}:{key}"
    assert release_age.gaps(root) == (gap,)
    with pytest.raises(release_age.ReleaseAgeError, match=re.escape(gap)):
        release_age.fill(root)
    for arguments in ((), ("--fix",)):
        with pytest.raises(SystemExit) as refused:
            release_age.main(arguments)
        assert refused.value.code == 1
        assert gap in capsys.readouterr().err.splitlines()

    assert _bytes(root) == before


def test_uv_exemption_of_single_packages_is_admitted(tmp_path: Path) -> None:
    root, hours = _filled_copy(tmp_path)
    exemption = "exclude-newer-package = { certifi = false }"
    uv = root / UV
    uv.write_text(f"{uv.read_text(encoding='utf-8')}{exemption}\n", encoding="utf-8")

    # A security fix younger than the age needs it, so it stands beside the declared age.
    assert release_age.gaps(root) == ()
    assert release_age.fill(root) == ()
    again = _another_age(hours)
    _declare(root, f'"{again}h"')
    assert UV in release_age.fill(root)

    text = uv.read_text(encoding="utf-8")
    assert exemption in text.split("\n")
    assert tomllib.loads(text)["exclude-newer"] == f"{again} hours"
    assert release_age.gaps(root) == ()
