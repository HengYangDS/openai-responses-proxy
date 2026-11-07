"""Construct deterministic environments for native product processes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from openai_responses_proxy import product_identity

if TYPE_CHECKING:
    from collections.abc import Mapping

_PRODUCT_NAMESPACE = f"{product_identity.ENVIRONMENT_PREFIX}_"
_PYTHON_RUNTIME_NAMESPACES = ("PYTHON", "PYINSTALLER_", "_PYI_")


def native_process_environment(
    *,
    install_root: str | os.PathLike[str],
    state_root: str | os.PathLike[str],
    command_search_path: str | os.PathLike[str] | None = None,
    user_home: str | os.PathLike[str] | None = None,
    restart_frozen_runtime: bool = False,
    inherited: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Preserve the host substrate while replacing product and Python state."""
    source = os.environ if inherited is None else inherited
    inherited_path = next(
        (value for name, value in source.items() if name.upper() == "PATH"),
        os.defpath,
    )
    environment = {
        name: value
        for name, value in source.items()
        if name.upper() != "PATH" and not _is_runtime_state(name)
    }
    environment.update(
        {
            product_identity.environment_name("HOME"): os.path.abspath(install_root),
            product_identity.environment_name("STATE_HOME"): os.path.abspath(state_root),
            "PATH": (
                inherited_path if command_search_path is None else os.fspath(command_search_path)
            ),
        }
    )
    if user_home is not None:
        user_root = Path(user_home).absolute()
        environment.update(
            {
                "APPDATA": os.fspath(user_root / "AppData" / "Roaming"),
                "HOME": os.fspath(user_root),
                "LOCALAPPDATA": os.fspath(user_root / "AppData" / "Local"),
                "USERPROFILE": os.fspath(user_root),
                "XDG_BIN_HOME": os.fspath(user_root / ".local" / "bin"),
                "XDG_CACHE_HOME": os.fspath(user_root / ".cache"),
                "XDG_CONFIG_HOME": os.fspath(user_root / ".config"),
                "XDG_DATA_HOME": os.fspath(user_root / ".local" / "share"),
                "XDG_STATE_HOME": os.fspath(user_root / ".local" / "state"),
            }
        )
    if restart_frozen_runtime:
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def _is_runtime_state(name: str) -> bool:
    """Return whether an inherited variable belongs to isolated runtime state."""
    normalized = name.upper()
    return normalized.startswith(_PRODUCT_NAMESPACE) or normalized.startswith(
        _PYTHON_RUNTIME_NAMESPACES
    )
