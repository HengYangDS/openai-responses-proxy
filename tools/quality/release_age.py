"""Fill every resolver's release age from the one that the Mise configuration declares."""

from __future__ import annotations

import math
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Annotated

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


class ReleaseAgeError(RuntimeError):
    """The release age is not declared, or a resolver's file cannot hold it."""


@dataclass(frozen=True)
class Setting:
    """One resolver's own release-age line, and the line that a number of hours gives it."""

    path: str
    line: re.Pattern[str]
    render: Callable[[int], str | None]
    # A line that the file may lack is appended; one that it always holds is only replaced.
    appended: bool


SETTINGS = (
    # Renovate's root value. The security-fix rule's own age is nested deeper and stays zero.
    Setting(
        ".config/checks/renovate/renovate.json5",
        re.compile(r'  "minimumReleaseAge": "[^"]*",'),
        lambda hours: f'  "minimumReleaseAge": "{hours} hours",',
        appended=False,
    ),
    # uv holds a release for a span; an age of zero leaves the line out.
    Setting(
        ".config/mise/uv.toml",
        re.compile(r"exclude-newer = .*"),
        lambda hours: f'exclude-newer = "{hours} hours"' if hours else None,
        appended=True,
    ),
    # npm counts whole days, so part of a day holds a release for the whole day.
    Setting(
        ".config/npm/npmrc",
        re.compile(r"min-release-age=.*"),
        lambda hours: f"min-release-age={math.ceil(hours / _HOURS_PER_DAY)}" if hours else None,
        appended=True,
    ),
)


def declared_hours(root: Path = ROOT) -> int:
    """Return the repository's release age in hours, as its Mise configuration declares it."""
    settings = tomllib.loads((root / DECLARATION).read_text(encoding="utf-8")).get("settings")
    value = settings.get("minimum_release_age") if isinstance(settings, dict) else None
    declared = _DECLARED.fullmatch(value) if isinstance(value, str) else None
    if declared is None:
        raise ReleaseAgeError(
            f"release_age_undeclared:{DECLARATION}: "
            '[settings] minimum_release_age takes whole hours, such as "24h"'
        )
    return int(declared[1])


def _filled(text: str, setting: Setting, hours: int) -> str:
    lines = text.splitlines()
    held = [index for index, line in enumerate(lines) if setting.line.fullmatch(line)]
    if len(held) > 1 or (not held and not setting.appended):
        raise ReleaseAgeError(f"release_age_setting_unreadable:{setting.path}")
    rendered = setting.render(hours)
    if held:
        lines[held[0] : held[0] + 1] = [] if rendered is None else [rendered]
    elif rendered is not None:
        lines.append(rendered)
    return "\n".join(lines) + "\n"


def _differing(root: Path) -> dict[str, str]:
    """Each resolver's file that differs from what the declared age fills, with that text."""
    hours = declared_hours(root)
    filled = {
        setting.path: _filled((root / setting.path).read_text(encoding="utf-8"), setting, hours)
        for setting in SETTINGS
    }
    return {
        path: text
        for path, text in filled.items()
        if (root / path).read_text(encoding="utf-8") != text
    }


def gaps(root: Path = ROOT) -> tuple[str, ...]:
    """Return the files whose release age differs from the declared one."""
    return tuple(_differing(root))


def fill(root: Path = ROOT) -> tuple[str, ...]:
    """Write the declared release age into every resolver's file; return those that changed."""
    # Every file is read and filled before the first write, so a refusal writes nothing.
    differing = _differing(root)
    for path, text in differing.items():
        (root / path).write_text(text, encoding="utf-8")
    return tuple(differing)


def _command(*, fix: Annotated[bool, Parameter(name="--fix", negative=False)] = False) -> None:
    """Check that every resolver holds the declared release age, or fill it in."""
    if fix:
        for path in fill(ROOT):
            sys.stdout.write(f"release age filled: {path}\n")
        return
    differing = gaps(ROOT)
    if differing:
        raise ReleaseAgeError(f"release_age_differs:{','.join(differing)}: run `mise run fix`")


def main(argv: Iterable[str] = ()) -> None:
    """Run the release-age check or fill through the shared parser stack."""
    try:
        App(default_command=_command, help=__doc__, result_action="return_value")(tuple(argv))
    except ReleaseAgeError as error:
        sys.stderr.write(f"{error}\n")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main(sys.argv[1:])
