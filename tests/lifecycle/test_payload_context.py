"""The control root's context names no payload, and one generation's context names its own."""

from __future__ import annotations

from dataclasses import fields
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle import generation
from openai_responses_proxy.serving import inventory
from tests.lifecycle.fixtures import FIXTURE_GENERATION
from tests.lifecycle.fixtures import control_root
from tests.lifecycle.fixtures import install_context
from tests.lifecycle.fixtures import install_payload

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

OTHER_GENERATION = "a" * 32


def test_a_control_root_context_names_no_payload(tmp_path: Path) -> None:
    ctx = control_root(tmp_path)

    assert type(ctx) is runtime_context.InstallationContext
    assert {"executable", "payload_dir"}.isdisjoint(item.name for item in fields(ctx))
    for name in ("executable", "payload_dir"):
        with pytest.raises(AttributeError):
            getattr(ctx, name)


def test_a_payload_context_is_the_control_root_with_one_executable(tmp_path: Path) -> None:
    control = replace(control_root(tmp_path), port=8808)
    executable = inventory.installed_executable(str(generation.path(control, OTHER_GENERATION)))

    payload = control.for_executable(executable)

    assert type(payload) is runtime_context.RuntimeContext
    assert isinstance(payload, runtime_context.InstallationContext)
    assert payload.executable == executable
    assert Path(payload.payload_dir) == generation.path(control, OTHER_GENERATION)
    assert payload.service_id == control.service_id
    for item in fields(control):
        assert getattr(payload, item.name) == getattr(control, item.name)
    # A payload context gives another generation the control root's fields, not its own payload.
    other = generation.context(payload, FIXTURE_GENERATION)
    assert other.executable == inventory.installed_executable(
        str(generation.path(control, FIXTURE_GENERATION))
    )
    assert other == control.for_executable(other.executable)


def test_every_generation_context_names_that_generation_executable(tmp_path: Path) -> None:
    ctx = control_root(tmp_path)
    for name in (FIXTURE_GENERATION, OTHER_GENERATION):
        generation.path(ctx, name).mkdir(parents=True)

    assert [owned.executable for owned in generation.owned_contexts(ctx)] == [
        inventory.installed_executable(str(generation.path(ctx, name)))
        for name in sorted((FIXTURE_GENERATION, OTHER_GENERATION))
    ]
    assert generation.context(ctx, OTHER_GENERATION).executable == (
        inventory.installed_executable(str(generation.path(ctx, OTHER_GENERATION)))
    )


def test_no_producer_returns_a_payload_context_without_a_selection(tmp_path: Path) -> None:
    ctx = control_root(tmp_path)
    Path(ctx.install_dir).mkdir(parents=True)

    assert generation.selected_context(ctx) is None
    assert generation.control_context(ctx) is None
    assert generation.owned_contexts(ctx) == ()
    with pytest.raises(errors.InstallError, match="no payload generation is selected"):
        generation.require_control_context(ctx)


def test_the_producers_return_the_selected_generation_of_an_installation(
    tmp_path: Path, *, mocker: MockerFixture
) -> None:
    installed = install_context(tmp_path)
    install_payload(installed, mocker=mocker)
    ctx = control_root(tmp_path)

    assert generation.selected_context(ctx) == installed
    assert generation.control_context(ctx) == installed
    assert generation.require_control_context(ctx) == installed
    assert generation.owned_contexts(ctx) == (installed,)
