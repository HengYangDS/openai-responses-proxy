"""Source-level privacy and client-ownership boundary contracts."""

from __future__ import annotations

from pathlib import Path

from tests.quality.fixtures import ROOT


def test_lifecycle_never_reads_or_prescribes_client_state():
    text = "\n".join(
        Path(ROOT, relative).read_text(encoding="utf-8").lower()
        for relative in (
            "src/openai_responses_proxy/lifecycle/install.py",
            "src/openai_responses_proxy/lifecycle/uninstall.py",
        )
    )
    assert "fully " + "quit & reopen" not in text
    assert "start a " + "new codex thread" not in text
    assert "conversation" not in text
    assert "config.toml" not in text


def test_control_and_data_planes_keep_explicit_privacy_boundaries(subtests):
    cases = (
        (
            ("src/openai_responses_proxy/lifecycle/control.py",),
            ("def status",),
            ("AIGW", "ChatGPT", "JetBrains"),
        ),
        (
            ("src/openai_responses_proxy/serving/entrypoint.py",),
            (),
            (
                "OPENAI_RESPONSES_PROXY_DUMP_BODIES",
                "OPENAI_RESPONSES_PROXY_DUMP_HEADERS",
                "reject-",
            ),
        ),
        (
            ("src/openai_responses_proxy/runtime/config.py",),
            (
                'product_identity.environment_name("PROXY_LOG_MAX_BYTES")',
                'product_identity.environment_name("PROXY_LOG_BACKUP_COUNT")',
                'product_identity.environment_name("WATCHDOG_LOG_MAX_BYTES")',
                'product_identity.environment_name("WATCHDOG_LOG_BACKUP_COUNT")',
            ),
            ("OPENAI_RESPONSES_PROXY_PROXY_LOG_MAX_BYTES",),
        ),
    )
    for paths, required, forbidden in cases:
        source = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in paths)
        with subtests.test(paths=paths):
            for value in required:
                assert value in source
            for value in forbidden:
                assert value not in source
