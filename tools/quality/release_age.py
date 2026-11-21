"""Fill every resolver's release age from the one that the Mise configuration declares."""

from __future__ import annotations

import json
import math
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Annotated
from typing import NamedTuple

from cyclopts import App
from cyclopts import Parameter

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable

ROOT = Path(__file__).resolve().parents[2]
# Mise's own setting is the declaration: `[settings] minimum_release_age = "<hours>h"`.
DECLARATION = ".config/mise/config.toml"
_DECLARED = re.compile(r"(0|[1-9]\d*)h")
_HOURS_PER_DAY = 24
_RENOVATE_AGE = "minimumReleaseAge"
_UV_AGE = "exclude-newer"
# Beside its age, uv's top level may name single packages that a security fix needs younger.
_UV_ADMITTED = frozenset({_UV_AGE, "exclude-newer-package"})
_NPM_AGE = "min-release-age"
# npm's other rule over a release's time, which takes no release after a date.
_NPM_BEFORE = "before"


class ReleaseAgeError(RuntimeError):
    """The age is not declared, a resolver's file cannot hold it, or a file holds another rule."""


class Held(NamedTuple):
    """What a resolver reads in its file."""

    # The setting's own value at the top level; None when the file leaves it out.
    age: object
    # The age that a security fix follows, where the file names one.
    security: tuple[object, ...] = ()
    # Every other key that holds or admits releases.
    other: tuple[str, ...] = ()


@dataclass(frozen=True)
class Setting:
    """One resolver's own release-age setting: its line, and the value that hours give it."""

    path: str
    # The setting's line as a file may hold it, and as the fill writes it around a value.
    line: re.Pattern[str]
    write: Callable[[str], str]
    # The one rendering of a number of hours; None leaves the setting out.
    render: Callable[[int], str | None]
    # The text as the resolver reads it; ValueError for a text that it cannot read.
    read: Callable[[str], Held]
    # A line that the file may lack is inserted at the top level; another is only replaced.
    inserted: bool

    def holds(self, held: Held, hours: int) -> bool:
        """Return whether a resolver reads the declared age, and zero for a security fix."""
        zero = self.render(0)
        return held.age == self.render(hours) and all(age == zero for age in held.security)


def _lines(text: str) -> list[str]:
    """The text's lines without its final newlines; only a line feed ends a line."""
    body = text.rstrip("\n")
    return body.split("\n") if body else []


def _table(value: object) -> dict[str, object]:
    """A parsed table by its keys; any other value holds none."""
    return {str(key): item for key, item in value.items()} if isinstance(value, dict) else {}


def _renovate(text: str) -> Held:
    policy = _table(json.loads(text))
    security = _table(policy.get("vulnerabilityAlerts"))
    return Held(policy.get(_RENOVATE_AGE), security=(security.get(_RENOVATE_AGE),))


def _uv_others(table: dict[str, object], path: str = "") -> list[str]:
    """Each key of uv's age family, at any depth, that the top level does not admit."""
    found: list[str] = []
    for key, value in table.items():
        if key.startswith(_UV_AGE):
            found += [] if not path and key in _UV_ADMITTED else [path + key]
        else:
            found += _uv_others(_table(value), f"{path}{key}.")
    return found


def _uv(text: str) -> Held:
    document = tomllib.loads(text)
    return Held(document.get(_UV_AGE), other=tuple(_uv_others(document)))


def _npm(text: str) -> Held:
    keys: list[tuple[str, str]] = []
    for line in _lines(text):
        entry = line.strip()
        if not entry or entry.startswith(("#", ";")):
            continue
        key, assigned, value = entry.partition("=")
        # This reader knows keys with values only; npm reads any other line as a section or flag.
        if not assigned or not key.strip() or key.startswith("["):
            raise ValueError(entry)
        keys.append((key.strip().strip("\"'").removesuffix("[]"), value.strip()))
    ages = [value for key, value in keys if key == _NPM_AGE]
    if len(ages) > 1:
        raise ValueError(_NPM_AGE)
    others = {key for key, _ in keys if key == _NPM_BEFORE or key.startswith(f"{_NPM_AGE}-")}
    return Held(ages[0] if ages else None, other=tuple(sorted(others)))


SETTINGS = (
    # Renovate's root value, at the root's indentation. The security-fix rule's own age is
    # nested deeper and stays zero.
    Setting(
        ".config/checks/renovate/renovate.json5",
        re.compile(rf'  "{_RENOVATE_AGE}": "[^"]*",'),
        f'  "{_RENOVATE_AGE}": "{{}}",'.format,
        lambda hours: f"{hours} hours",
        _renovate,
        inserted=False,
    ),
    # uv holds a release for a span; an age of zero leaves the setting out.
    Setting(
        ".config/mise/uv.toml",
        re.compile(rf"\s*{_UV_AGE}\s*=.*"),
        f'{_UV_AGE} = "{{}}"'.format,
        lambda hours: f"{hours} hours" if hours else None,
        _uv,
        inserted=True,
    ),
    # npm counts whole days, so part of a day holds a release for the whole day.
    Setting(
        ".config/npm/npmrc",
        re.compile(rf"\s*{_NPM_AGE}\s*=.*"),
        f"{_NPM_AGE}={{}}".format,
        lambda hours: str(math.ceil(hours / _HOURS_PER_DAY)) if hours else None,
        _npm,
        inserted=True,
    ),
)


def _text(path: Path, refusal: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError) as error:
        raise ReleaseAgeError(refusal) from error


def declared_hours(root: Path = ROOT) -> int:
    """Return the repository's release age in hours, as its Mise configuration declares it."""
    refusal = (
        f"release_age_undeclared:{DECLARATION}: "
        '[settings] minimum_release_age takes whole hours, such as "24h"'
    )
    try:
        settings = tomllib.loads(_text(root / DECLARATION, refusal)).get("settings")
    except ValueError as error:
        raise ReleaseAgeError(refusal) from error
    value = _table(settings).get("minimum_release_age")
    declared = _DECLARED.fullmatch(value) if isinstance(value, str) else None
    if declared is None:
        raise ReleaseAgeError(refusal)
    return int(declared[1])


def _top_level(lines: list[str]) -> int:
    """The number of lines above the first table; a file without tables is all top level."""
    return next((index for index, line in enumerate(lines) if line.startswith("[")), len(lines))


def _own(setting: Setting, lines: list[str]) -> list[int]:
    """Where the top level holds the setting's line."""
    top_level = lines[: _top_level(lines)]
    return [index for index, line in enumerate(top_level) if setting.line.fullmatch(line)]


def _held(setting: Setting, text: str) -> Held:
    """What the resolver reads in a text; one that it cannot read is refused."""
    refusal = f"release_age_setting_unreadable:{setting.path}"
    try:
        held = setting.read(text)
    except ValueError as error:
        raise ReleaseAgeError(refusal) from error
    # Readers differ over a key that is written twice, so no file holds the line twice.
    if len(_own(setting, _lines(text))) > 1:
        raise ReleaseAgeError(refusal)
    return held


def _filled(text: str, setting: Setting, hours: int) -> str:
    """The text with the setting's line as the hours give it, read again as its resolver does."""
    lines = _lines(text)
    value = setting.render(hours)
    rendered = [] if value is None else [setting.write(value)]
    own = _own(setting, lines)
    if own:
        lines[own[0] : own[0] + 1] = rendered
    elif setting.inserted:
        # Above the first table and its blank lines, where a key belongs to no table.
        end = _top_level(lines)
        while end and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = rendered
    filled = "".join(f"{line}\n" for line in lines)
    # A line can stand where its resolver does not read it; only the read value shows the age.
    if not setting.holds(_held(setting, filled), hours):
        raise ReleaseAgeError(f"release_age_setting_unreadable:{setting.path}")
    return filled


def _survey(root: Path) -> tuple[dict[str, str], tuple[str, ...]]:
    """Each file that does not hold the declared age, with its filled text; and each other rule."""
    hours = declared_hours(root)
    filled: dict[str, str] = {}
    others: list[str] = []
    for setting in SETTINGS:
        text = _text(root / setting.path, f"release_age_setting_unreadable:{setting.path}")
        held = _held(setting, text)
        others += [f"release_age_other_rule:{setting.path}:{key}" for key in held.other]
        # A file with another rule is refused as it stands; no age is filled around the rule.
        if not held.other and not setting.holds(held, hours):
            filled[setting.path] = _filled(text, setting, hours)
    return filled, tuple(others)


def gaps(root: Path = ROOT) -> tuple[str, ...]:
    """Return the files whose release age differs from the declared one, and each other rule."""
    filled, others = _survey(root)
    return (*filled, *others)


def fill(root: Path = ROOT) -> tuple[str, ...]:
    """Write the declared release age into every resolver's file; return those that changed."""
    # Every file is read, filled and read again before the first write, so a refusal writes
    # nothing.
    filled, others = _survey(root)
    if others:
        raise ReleaseAgeError("\n".join(others))
    for path, text in filled.items():
        (root / path).write_text(text, encoding="utf-8", newline="\n")
    return tuple(filled)


def _command(*, fix: Annotated[bool, Parameter(name="--fix", negative=False)] = False) -> None:
    """Check that every resolver holds the declared release age and no other rule, or fill it."""
    if fix:
        for path in fill(ROOT):
            sys.stdout.write(f"release age filled: {path}\n")
        return
    filled, others = _survey(ROOT)
    differing = [f"release_age_differs:{','.join(filled)}: run `mise run fix`"] if filled else []
    if differing or others:
        raise ReleaseAgeError("\n".join((*differing, *others)))


def main(argv: Iterable[str] = ()) -> None:
    """Run the release-age check or fill through the shared parser stack."""
    try:
        App(default_command=_command, help=__doc__, result_action="return_value")(tuple(argv))
    except ReleaseAgeError as error:
        sys.stderr.write(f"{error}\n")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main(sys.argv[1:])
