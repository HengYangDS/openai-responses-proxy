"""Native service-definition projections for supported platforms."""

from __future__ import annotations

from typing import TYPE_CHECKING
from xml.dom import minidom

from openai_responses_proxy.lifecycle.supervision import linux
from openai_responses_proxy.lifecycle.supervision import macos
from tests.lifecycle.fixtures import platform_context
from tests.lifecycle.supervision.fixtures import assert_fragments as _assert_fragments

if TYPE_CHECKING:
    import pytest

CONTEXT = platform_context()
EXECUTABLE = CONTEXT.executable

MACOS_CONTAINS = f"""<key>KeepAlive</key>
<true/>
{EXECUTABLE}
--internal-watchdog
openai-responses-proxy.watchdog
<string>/dev/null</string>""".splitlines()
LINUX_CONTAINS = f"""Restart=always
RestartSec=3
WantedBy=default.target
ExecStart="{EXECUTABLE}" --internal-watchdog""".splitlines()


class TestServiceDefinitions:
    def test_macos_plist(self) -> None:
        xml = macos.render_plist(platform_context())
        minidom.parseString(xml)
        _assert_fragments(
            xml,
            MACOS_CONTAINS,
            ("responses-proxy-watchdog.out.log", "responses-proxy-watchdog.err.log"),
        )

    def test_linux_unit(self) -> None:
        unit = linux.render_unit(platform_context())
        _assert_fragments(unit, LINUX_CONTAINS)
        assert "multi-user.target" not in unit

    def test_service_definitions_never_persist_python_or_source_paths(
        self, subtests: pytest.Subtests
    ) -> None:
        ctx = platform_context(port=8801)
        definitions = (
            macos.render_plist(ctx),
            linux.render_unit(ctx),
        )
        for definition in definitions:
            with subtests.test(definition=definition[:40]):
                assert "python" not in definition.lower()
                assert ".py" not in definition

    def test_service_definitions_contain_no_product_configuration(
        self, subtests: pytest.Subtests
    ) -> None:
        definitions = (
            macos.render_plist(CONTEXT),
            linux.render_unit(CONTEXT),
        )
        for definition in definitions:
            with subtests.test(definition=definition[:40]):
                assert "OPENAI_RESPONSES_PROXY_" not in definition
