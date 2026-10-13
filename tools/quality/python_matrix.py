"""Project the supported Python lines into GitHub Actions outputs."""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path

from cyclopts import App


def supported_versions(versions: Path) -> list[str]:
    """Read one nonempty ordered range of supported Python minor lines."""
    values = versions.read_text(encoding="utf-8").splitlines()
    if not values or any(re.fullmatch(r"\d+\.\d+", value) is None for value in values):
        raise ValueError("supported Python matrix is unavailable or invalid")
    lines = [tuple(map(int, value.split("."))) for value in values]
    if lines != [(lines[0][0], lines[0][1] + offset) for offset in range(len(lines))]:
        raise ValueError("supported Python matrix is unavailable or invalid")
    return values


def supported_requirement(versions: Path) -> str:
    """Project the package bounds from the supported Python minor lines."""
    values = supported_versions(versions)
    major, minor = map(int, values[-1].split("."))
    return f">={values[0]},<{major}.{minor + 1}"


def write(*, versions: Path, release: Path, metadata: Path, output: Path) -> None:
    """Write Python and native-runtime outputs from repository SSOTs."""
    values = supported_versions(versions)
    if output.is_symlink():
        raise ValueError("supported Python matrix is unavailable or invalid")
    release_version = release.read_text(encoding="utf-8").strip()
    if re.fullmatch(r"\d+\.\d+\.\d+", release_version) is None:
        raise ValueError("native release Python is unavailable or invalid")
    project = tomllib.loads(metadata.read_text(encoding="utf-8"))
    project_name = project["project"]["name"]
    image = project["tool"][project_name]["linux-release-image"]
    if (
        not isinstance(image, str)
        or f"python:{release_version}-" not in image
        or "@sha256:" not in image
    ):
        raise ValueError("Linux release runtime is unavailable or mutable")
    with output.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"value={json.dumps(values)}\n")
        stream.write(f"floor={values[0]}\n")
        stream.write(f"latest={values[-1]}\n")
        stream.write(f"release={release_version}\n")
        stream.write(f"linux-release-image={image}\n")


def _command(
    *,
    versions: Path = Path(".python-versions"),
    release: Path = Path(".python-release"),
    metadata: Path = Path("pyproject.toml"),
    output: Path | None = None,
) -> None:
    """Project the matrix to an explicit or GitHub-provided output path."""
    configured = os.environ.get("GITHUB_OUTPUT")
    if output is None and not configured:
        raise SystemExit("GitHub output path is unavailable")
    target = output or Path(configured or "")
    write(versions=versions, release=release, metadata=metadata, output=target)


def main(argv: tuple[str, ...] | None = None) -> None:
    """Run matrix projection through the repository parser stack."""
    App(default_command=_command, help=__doc__, result_action="return_value")(
        tuple(sys.argv[1:] if argv is None else argv)
    )


if __name__ == "__main__":
    main()
