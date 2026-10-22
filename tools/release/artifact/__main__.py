"""Build and verify immutable native release artifacts."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated

from cyclopts import App
from cyclopts import Parameter

from tools.release.artifact import assembly
from tools.release.artifact import bundle
from tools.release.artifact import format as assets
from tools.release.artifact import signing


def _assemble(
    *,
    inputs: Annotated[tuple[Path, ...], Parameter(name="--input")],
    output: Path,
    sign: bool = False,
) -> None:
    """Assemble one complete release, optionally signing with explicit inputs."""
    if sign:
        release = assembly.assemble_sign_verify(
            inputs=inputs,
            output=output,
            key=Path(os.environ.get("RELEASE_ASSET_SIGNING_KEY_PATH", "")),
            trust=os.environ.get("RELEASE_ASSET_TRUST", ""),
        )
    else:
        release = assembly.assemble(inputs, output)
    print(f"assembled release assets: {len(release)} files")


def _verify(*, assets: Path) -> None:
    """Authenticate the complete release using the explicit release trust input."""
    digests = assembly.verify(assets, trust=os.environ.get("RELEASE_ASSET_TRUST", ""))
    print(f"verified release assets: {len(digests)} files")


def main(argv: tuple[str, ...] | None = None) -> None:
    """Run artifact operations through one explicit command tree."""
    app = App(name=f"python -m {__package__}", help=__doc__, result_action="return_value")
    app.command(bundle.normalize, name="normalize")
    app.command(bundle.pack, name="pack")
    app.command(bundle.materialize, name="materialize")
    app.command(_assemble, name="assemble")
    app.command(_verify, name="verify")
    try:
        app(tuple(sys.argv[1:] if argv is None else argv))
    except (assets.AssetError, signing.SignatureError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
