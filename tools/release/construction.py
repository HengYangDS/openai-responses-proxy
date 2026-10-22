"""Freeze and package the native candidate through the release-owned executors."""

from __future__ import annotations

import os
import platform
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from openai_responses_proxy import product_identity

if TYPE_CHECKING:
    import nox


def freeze(
    session: nox.Session, work: Path, *, root: Path, environment: Mapping[str, str]
) -> tuple[Path, Path]:
    """Build one native directory bundle with only release-owned data."""
    name = product_identity.executable_name(windows=os.name == "nt")
    dist = work / "dist"
    command = (
        "pyinstaller",
        "--clean",
        "--noconfirm",
        "--log-level",
        "ERROR",
        "--onedir",
        "--name",
        product_identity.PRODUCT_SLUG,
        "--distpath",
        str(dist),
        "--workpath",
        str(work / "build"),
        "--specpath",
        str(work),
        "--additional-hooks-dir",
        str(root / "tools/release/hooks"),
        "--add-data",
        f"{root / 'VERSION'}{os.pathsep}.",
        "--add-data",
        (
            f"{root / 'src/openai_responses_proxy/providers/manifest.toml'}"
            f"{os.pathsep}openai_responses_proxy/providers"
        ),
        "--collect-submodules",
        "openai_responses_proxy.providers.policies",
        str(root / "src/openai_responses_proxy/cli/__main__.py"),
    )
    session.run(*command, env=environment)
    bundle = dist / product_identity.PRODUCT_SLUG
    executable = bundle / name
    if not executable.is_file():
        session.error(f"native executable was not produced: {executable}")
    return bundle, executable


def pack(session: nox.Session, bundle: Path, work: Path, *, environment: Mapping[str, str]) -> None:
    """Export one manifest-bound native asset set after black-box acceptance."""
    output = Path(session.posargs[0]).resolve() if session.posargs else work / "release-assets"
    try:
        platform_id = product_identity.native_release_platform(
            platform.system(), platform.machine()
        )
    except ValueError as error:
        session.error(str(error))
    session.run(
        "python",
        "-m",
        "tools.release.artifact",
        "pack",
        "--bundle",
        str(bundle),
        "--platform",
        platform_id,
        "--output",
        str(output),
        env=environment,
    )
