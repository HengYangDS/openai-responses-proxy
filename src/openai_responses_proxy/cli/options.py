"""Option grammar shared by the public commands."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING
from typing import Annotated
from typing import Protocol

from cyclopts import Parameter
from cyclopts.validators import Number

if TYPE_CHECKING:
    from collections.abc import Sequence

JSON = Annotated[
    bool,
    Parameter(
        name="--json",
        negative=False,
        help="Emit stable JSON for automation.",
        show_default=False,
    ),
]


class _Word(Protocol):
    """The one field this module reads of a command-line token."""

    value: str


def _decimal_port(_kind: object, words: Sequence[_Word]) -> int:
    """Read a port written in ASCII decimal digits.

    The default conversion also took a fraction, a base prefix, a digit separator and
    non-ASCII digits, and ran the command on another integer: 8.5 as 8, 0x50 as 80.
    """
    text = words[0].value
    if not (text.isascii() and text.isdecimal()):
        message = "a port is a whole number written in decimal digits"
        raise ValueError(message)
    return int(text)


def _finite_deadline(_kind: object, seconds: float) -> None:
    """Refuse a deadline that never ends: infinity, an overflowing decimal, or no number."""
    if not (math.isfinite(seconds) and seconds > 0):
        message = "A deadline is a finite number of seconds above zero."
        raise ValueError(message)


PORT = Annotated[
    int,
    Parameter(
        help="Loopback listener port.",
        converter=_decimal_port,
        validator=Number(gte=1, lte=65535),
    ),
]
TIMEOUT = Annotated[
    float,
    Parameter(
        name="--timeout-seconds",
        help="Native lifecycle deadline in seconds.",
        validator=_finite_deadline,
    ),
]
PURGE = Annotated[
    bool,
    Parameter(
        name="--purge",
        negative=False,
        help="Also remove the verified installed payload.",
        show_default=False,
    ),
]
