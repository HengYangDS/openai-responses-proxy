"""Qualify repository consumers of the original governance command reports."""

from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from tools.governance import observation


def test_native_report_binds_the_exact_consumer_and_root(tmp_path, mocker):
    report = {"command": "status", "verdict": "pass", "data": {"root": str(tmp_path)}}
    run = mocker.patch.object(
        observation.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 0, json.dumps(report), ""),
    )

    assert observation.read(tmp_path, "status") == report["data"]
    assert run.call_args.args == (("ethos", "status", "--root", str(tmp_path), "--json"),)
    assert run.call_args.kwargs["cwd"] == tmp_path
    assert run.call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert run.call_args.kwargs["timeout"] == 30


def test_native_report_checks_referenced_evidence_bytes(tmp_path, mocker):
    payload = json.dumps(
        {"command": "status", "verdict": "pass", "data": {"root": str(tmp_path)}}
    ).encode()
    artifact = tmp_path / "report.json"
    artifact.write_bytes(payload)
    report = {
        "command": "status",
        "verdict": "pass",
        "data": {
            "artifact_reference": {
                "path": str(artifact),
                "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        },
    }
    mocker.patch.object(
        observation.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 0, json.dumps(report), ""),
    )
    assert observation.read(tmp_path, "status") == {"root": str(tmp_path)}
    artifact.write_bytes(payload + b" ")
    with pytest.raises(observation.ObservationError, match="evidence"):
        observation.read(tmp_path, "status")


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess([], 2, "{}", ""),
        subprocess.CompletedProcess([], 0, "not json", ""),
        subprocess.CompletedProcess([], 0, '{"command":"other","data":{}}', ""),
        subprocess.CompletedProcess(
            [], 0, '{"command":"status","verdict":"pass","data":{"root":"/other"}}', ""
        ),
    ],
)
def test_native_report_refuses_unproved_or_wrong_subjects(tmp_path, mocker, result):
    mocker.patch.object(observation.subprocess, "run", return_value=result)
    with pytest.raises(observation.ObservationError):
        observation.read(tmp_path, "status")


@pytest.mark.parametrize(
    "failure", [FileNotFoundError("missing"), subprocess.TimeoutExpired([], 30)]
)
def test_native_report_has_no_replacement_or_retry(tmp_path, mocker, failure):
    run = mocker.patch.object(observation.subprocess, "run", side_effect=failure)
    with pytest.raises(observation.ObservationError):
        observation.read(tmp_path, "status")
    assert run.call_count == 1


@pytest.mark.parametrize("exit_code", [0, 1])
def test_native_report_preserves_a_block_without_claiming_acceptance(tmp_path, mocker, exit_code):
    report = {
        "command": "status",
        "verdict": "block",
        "data": {"root": str(tmp_path), "required_gaps": ["unrelated_landing_gap"]},
    }
    mocker.patch.object(
        observation.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], exit_code, json.dumps(report), ""),
    )
    assert observation.read(tmp_path, "status") == report["data"]
