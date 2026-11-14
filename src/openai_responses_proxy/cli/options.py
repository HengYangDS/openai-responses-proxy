"""Option grammar shared by the public commands."""

from __future__ import annotations

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
    # A port has at most five digits; the length bound also keeps a long digit string
    # away from the interpreter's own integer-conversion limit and its message.
    if not (text.isascii() and text.isdecimal() and len(text.lstrip("0")) <= _PORT_DIGITS):
        message = "a port is a whole number written in decimal digits"
        raise ValueError(message)
    return int(text)


_PORT_DIGITS = 5
# No native lifecycle step waits longer than an hour. Without a bound, `inf` and any
# huge finite value were deadlines that never end.
MAXIMUM_DEADLINE_SECONDS = 3600


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
        help=f"Native lifecycle deadline in seconds, at most {MAXIMUM_DEADLINE_SECONDS}.",
        validator=Number(gt=0, lte=MAXIMUM_DEADLINE_SECONDS),
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
