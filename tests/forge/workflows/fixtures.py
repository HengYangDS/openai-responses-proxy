"""Typed workflow parsing and scalar admission shared by Forge contracts."""

from collections.abc import Mapping
from pathlib import Path

import yaml


def _load_yaml(path: Path) -> dict[str, object]:
    """Load one workflow while preserving GitHub's literal ``on`` key."""

    class WorkflowLoader(yaml.SafeLoader):
        pass

    for key, resolvers in tuple(WorkflowLoader.yaml_implicit_resolvers.items()):
        WorkflowLoader.yaml_implicit_resolvers[key] = [
            resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:bool"
        ]
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=WorkflowLoader)
    assert isinstance(data, dict)
    assert all(isinstance(key, str) for key in data)
    return {str(key): value for key, value in data.items()}


def _mapping(value: object) -> Mapping[str, object]:
    """Narrow one parsed workflow mapping for typed contract assertions."""
    assert isinstance(value, Mapping)
    assert all(isinstance(key, str) for key in value)
    return {str(key): item for key, item in value.items()}


def _sequence(value: object) -> list[object]:
    """Narrow one parsed workflow sequence."""
    assert isinstance(value, list)
    return list(value)


def _strings(value: object) -> list[str]:
    """Narrow one parsed workflow string sequence."""
    values = _sequence(value)
    assert all(isinstance(item, str) for item in values)
    return [item for item in values if isinstance(item, str)]


def _string(value: object) -> str:
    """Narrow one parsed workflow scalar to a string."""
    assert isinstance(value, str)
    return value
