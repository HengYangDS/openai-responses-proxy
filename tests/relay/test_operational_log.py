"""Unit contracts for bounded secret-safe operational logging."""

from __future__ import annotations

import tempfile
import urllib.error
from pathlib import Path

import pytest

from openai_responses_proxy.relay import operational_log
from openai_responses_proxy.relay import sse
from tests.lifecycle.fixtures import assert_private_log_mode
from tests.relay.fixtures import DirectResponse
from tests.relay.fixtures import MemoryHandler

ROOT = Path(__file__).resolve().parents[2]


class RuntimeLoggingTests:
    @pytest.mark.parametrize(
        ("raw", "event", "detail"),
        [
            (
                b'data: {"type":"response.completed","response":{"private":"prompt-secret"}}\n\n',
                "sse_terminal",
                "terminal=response.completed",
            ),
            (
                OSError("private-transport-secret"),
                "sse_end_without_terminal",
                "exception=OSError",
            ),
        ],
    )
    def test_stream_events_correlate_outcomes_without_payloads(
        self, raw, event, detail, tmp_path, *, mocker
    ) -> None:
        log_path = tmp_path / "proxy.log"
        mocker.patch.object(operational_log, "LOG_PATH", str(log_path))
        mocker.patch.object(operational_log.sys.stderr, "write")

        sse.relay(
            MemoryHandler(),
            DirectResponse(raw),
            "/upstream/v1/responses?token=private-query-secret",
            71,
        )

        text = log_path.read_text(encoding="utf-8")
        fields = [
            dict(field.split("=", 1) for field in line.split()[1:]) for line in text.splitlines()
        ]
        selected = next(item for item in fields if item["event"] == event)
        key, value = detail.split("=", 1)
        assert selected[key] == value
        assert selected["req"] == "71"
        assert selected["path"] == "/upstream/v1/responses"
        assert "events" in selected
        assert "prompt-secret" not in text
        assert "private-transport-secret" not in text
        assert "private-query-secret" not in text

    def test_labels_and_paths_never_expose_caller_controlled_values(self, *, mocker) -> None:
        assert (
            operational_log.safe_request_path("/v1/private path?secret=value") == "/v1/private_path"
        )
        assert operational_log.safe_request_path("relative?secret=value") == "/invalid-path"
        mocker.patch.object(operational_log.urllib.parse, "urlsplit", side_effect=ValueError)
        assert operational_log.safe_request_path("private") == "/invalid-path"
        assert (
            operational_log.safe_exception_label(ValueError("private")),
            operational_log.safe_exception_label(None),
        ) == ("ValueError", "UnknownError")

    def test_exception_context_exposes_only_safe_transport_diagnostics(self) -> None:
        class TlsError(Exception):
            errno = 1
            verify_code = 20

        class UnsafeMetadataError(Exception):
            errno = True
            verify_code = "private-verify-message"

        error = urllib.error.URLError(OSError(8, "private-host.example"))

        context = operational_log.safe_exception_context(error)

        assert context == "exception=URLError reason_exception=OSError reason_errno=8"
        assert "private-host.example" not in context
        assert operational_log.safe_exception_context(TlsError("private")) == (
            "exception=TlsError errno=1 verify_code=20"
        )
        assert operational_log.safe_exception_context(UnsafeMetadataError("private")) == (
            "exception=UnsafeMetadataError"
        )
        assert operational_log.safe_exception_context(None) == "exception=UnknownError"

    def test_logging_redacts_and_bounds_retention_without_becoming_fatal(self, *, mocker) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxy.log"
            mocker.patch.object(operational_log, "LOG_PATH", str(path))
            mocker.patch.object(operational_log, "LOG_MAX_BYTES", 16)
            mocker.patch.object(operational_log, "LOG_BACKUP_COUNT", 2)
            mocker.patch.object(operational_log.sys.stderr, "write")
            operational_log.log(
                "authorization: Bearer private-token encrypted=gAAAA-secret" + "x" * 2048
            )
            text = path.read_text()
            assert "private-token" not in text
            assert "gAAAA-secret" not in text
            path.write_text("x" * 17)
            operational_log.log("rotation")
            assert "discarded_oversized_bytes=17" in path.read_text()
            path.write_text("x" * 12)
            (path.parent / "proxy.log.1").write_text("y" * 17)
            operational_log.log("backup rotation")
            assert (path.parent / "proxy.log.1").exists()
            no_backups = mocker.patch.object(operational_log, "LOG_BACKUP_COUNT", 0)
            path.write_text("x" * 12)
            operational_log.log("no backups")
            mocker.stop(no_backups)
            no_prior_backup = mocker.patch.object(Path, "exists", return_value=False)
            path.write_text("x" * 12)
            operational_log.log("no prior backup")
            mocker.stop(no_prior_backup)
            (path.parent / "proxy.log.1").write_text("y" * 12)
            path.write_text("x" * 12)
            operational_log.log("retained backup")
            path.unlink()
            path.mkdir()
            operational_log.log("ignored errors")
            mocker.patch.object(operational_log.sys.stderr, "write", side_effect=OSError)
            operational_log.log("stderr error")


class OperationalLogPrivacyContracts:
    def test_log_redacts_secrets_limits_line_length_and_removes_query_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "proxy.log"
            old_log_path = operational_log.LOG_PATH
            operational_log.LOG_PATH = str(log_path)
            try:
                operational_log.log(
                    "authorization: Bearer super-secret-token encrypted=gAAAA_replay_secret x"
                    * 2048
                )
                operational_log.log(
                    f"path={operational_log.safe_request_path('/v1/responses?prompt=private')}"
                )
            finally:
                operational_log.LOG_PATH = old_log_path
            text = log_path.read_text(encoding="utf-8")
            mode = log_path.stat().st_mode & 511
        assert "super-secret-token" not in text
        assert "gAAAA_replay_secret" not in text
        assert "prompt=private" not in text
        assert "[redacted]" in text
        assert "path=/v1/responses" in text
        assert_private_log_mode(mode)
        assert (
            max(len(line.encode("utf-8")) for line in text.splitlines())
            <= operational_log.LOG_LINE_MAX_BYTES + 96
        )

    def test_log_rotation_discards_an_oversized_segment_without_reading_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "proxy.log"
            log_path.write_bytes(b"x" * 8192)
            old_log_path = operational_log.LOG_PATH
            old_max = operational_log.LOG_MAX_BYTES
            old_backups = operational_log.LOG_BACKUP_COUNT
            operational_log.LOG_PATH = str(log_path)
            operational_log.LOG_MAX_BYTES = 4096
            operational_log.LOG_BACKUP_COUNT = 1
            try:
                operational_log.log("event=rotation_probe")
            finally:
                operational_log.LOG_PATH = old_log_path
                operational_log.LOG_MAX_BYTES = old_max
                operational_log.LOG_BACKUP_COUNT = old_backups
            assert log_path.exists()
            assert log_path.stat().st_size <= 4096
            assert not (Path(tmp) / "proxy.log.1").exists()
            assert "log_retention_discarded_oversized_bytes=8192" in log_path.read_text(
                encoding="utf-8"
            )
