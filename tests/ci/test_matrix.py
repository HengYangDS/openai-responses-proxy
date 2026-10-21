"""CLI and metadata contracts for the CUE-owned Python runtime matrix."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tests.quality.fixtures import ROOT
from tools.quality import python_matrix


def test_python_matrix_output_comes_from_the_repository_ssot(tmp_path: Path) -> None:
    module = importlib.import_module("tools.quality.python_matrix")
    versions = tmp_path / "versions"
    image = tmp_path / "image"
    output = tmp_path / "github-output"
    versions.write_text("3.12\n3.13\n3.14\n", encoding="ascii")
    release = tmp_path / "release"
    release.write_text("3.14.7\n", encoding="ascii")
    image.write_text(f"python:3.14.7-bookworm@sha256:{'a' * 64}\n", encoding="ascii")

    module.write(versions=versions, release=release, image=image, output=output)

    assert output.read_text(encoding="utf-8") == (
        'value=["3.12", "3.13", "3.14"]\nfloor=3.12\nlatest=3.14\nrelease=3.14.7\n'
        f"linux-release-image=python:3.14.7-bookworm@sha256:{'a' * 64}\n"
    )


@pytest.mark.parametrize(
    ("release_value", "image_version", "message"),
    [
        ("3.14", "3.14.7", "native release Python is unavailable or invalid"),
        (
            "not.a.version",
            "not.a.version",
            "native release Python is unavailable or invalid",
        ),
        ("3.14.7", "3.14.6", "Linux release runtime is unavailable or mutable"),
    ],
)
def test_python_matrix_rejects_invalid_or_mismatched_release_runtime(
    tmp_path: Path, release_value: str, image_version: str, message: str
) -> None:
    module = importlib.import_module("tools.quality.python_matrix")
    versions = tmp_path / "versions"
    release = tmp_path / "release"
    image = tmp_path / "image"
    versions.write_text("3.12\n3.13\n3.14\n", encoding="ascii")
    release.write_text(f"{release_value}\n", encoding="ascii")
    image.write_text(f"python:{image_version}-bookworm@sha256:{'a' * 64}\n", encoding="ascii")

    with pytest.raises(ValueError, match=message):
        module.write(
            versions=versions,
            release=release,
            image=image,
            output=tmp_path / "github-output",
        )


@pytest.mark.parametrize("explicit", [False, True])
def test_matrix_cli_projects_to_exact_selected_output(explicit, tmp_path, monkeypatch):
    output = tmp_path / "output"
    args = (
        "--versions",
        str(ROOT / ".config/ci/python-versions"),
        "--release",
        str(ROOT / ".config/mise/python-release"),
        "--image",
        str(ROOT / ".config/ci/linux-release-image"),
    )
    if explicit:
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
        python_matrix.main((*args, "--output", str(output)))
    else:
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        python_matrix.main(args)
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert (
        json.loads(values["value"])
        == (ROOT / ".config/ci/python-versions").read_text().splitlines()
    )
    assert values["release"] == (ROOT / ".config/mise/python-release").read_text().strip()


def test_matrix_cli_requires_an_output_and_nonempty_unique_versions(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    with pytest.raises(SystemExit, match="output path is unavailable"):
        python_matrix.main(())
    versions = tmp_path / "versions"
    versions.write_text("")
    with pytest.raises(ValueError, match="matrix is unavailable"):
        python_matrix.write(
            versions=versions,
            release=ROOT / ".config/mise/python-release",
            image=ROOT / ".config/ci/linux-release-image",
            output=tmp_path / "output",
        )


@pytest.mark.parametrize(
    ("versions", "requirement"),
    [("3.12\n3.13\n3.14\n", ">=3.12,<3.15"), ("3.13\n3.14\n", ">=3.13,<3.15")],
)
def test_package_python_bounds_derive_from_the_selected_matrix(tmp_path, versions, requirement):
    path = tmp_path / "versions"
    path.write_text(versions)
    assert python_matrix.supported_requirement(path) == requirement


@pytest.mark.parametrize("versions", ["3.12\n3.12\n", "3.12.7\n", "3.12\n3.14\n"])
def test_package_python_bounds_require_an_ordered_contiguous_matrix(tmp_path, versions):
    path = tmp_path / "versions"
    path.write_text(versions)
    with pytest.raises(ValueError, match="matrix is unavailable or invalid"):
        python_matrix.supported_requirement(path)


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "python:3.14.7-bookworm",
        "python:3.14.7-bookworm@sha256:a",
        "prefix-python:3.14.7-bookworm@sha256:" + "a" * 64,
    ],
)
def test_release_runtime_refuses_invalid_images_before_output_mutation(tmp_path, invalid):
    versions = tmp_path / "versions"
    release = tmp_path / "release"
    image = tmp_path / "image"
    output = tmp_path / "output"
    versions.write_text("3.12\n3.13\n3.14\n")
    release.write_text("3.14.7\n")
    image.write_text(invalid + "\n")
    output.write_bytes(b"preserve existing output\n")
    with pytest.raises(ValueError, match="Linux release runtime is unavailable or mutable"):
        python_matrix.write(versions=versions, release=release, image=image, output=output)
    assert output.read_bytes() == b"preserve existing output\n"
