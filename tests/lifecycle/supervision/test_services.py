"""Native service-definition projections for supported platforms."""

from __future__ import annotations

from typing import TYPE_CHECKING
from xml.dom import minidom

from openai_responses_proxy.lifecycle.supervision import linux
from openai_responses_proxy.lifecycle.supervision import macos
from openai_responses_proxy.lifecycle.supervision import windows
from tests.lifecycle.fixtures import platform_context
from tests.lifecycle.supervision.fixtures import assert_fragments as _assert_fragments

if TYPE_CHECKING:
    import pytest

POSIX_CONTEXT = platform_context()
WINDOWS_CONTEXT = platform_context(windows=True)
EXECUTABLE = POSIX_CONTEXT.executable

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
WINDOWS_TASK_CONTAINS = f"""<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
<LogonTrigger>
<RestartOnFailure>
<LogonType>InteractiveToken</LogonType>
<RunLevel>LeastPrivilege</RunLevel>
<TimeTrigger>
<StartBoundary>{windows._SELF_HEAL_START_BOUNDARY}</StartBoundary>
<Repetition>
<Interval>PT1M</Interval>
<StopAtDurationEnd>false</StopAtDurationEnd>
<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
<Command>{WINDOWS_CONTEXT.executable}</Command>
<Arguments>--internal-watchdog</Arguments>""".splitlines()


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

    def test_windows_task(self) -> None:
        rendered = windows.render_task_xml(WINDOWS_CONTEXT)
        minidom.parseString(rendered)
        xml = rendered.decode("utf-16")
        _assert_fragments(xml, WINDOWS_TASK_CONTAINS)
        _assert_fragments(xml.lower(), exclude=("cmd.exe", "comspec", "run-watchdog.cmd"))

    def test_service_definitions_never_persist_python_or_source_paths(
        self, subtests: pytest.Subtests
    ) -> None:
        ctx = platform_context(port=8801)
        definitions = (
            macos.render_plist(ctx),
            linux.render_unit(ctx),
            windows.render_task_xml(ctx).decode("utf-16"),
        )
        for definition in definitions:
            with subtests.test(definition=definition[:40]):
                assert "python" not in definition.lower()
                assert ".py" not in definition

    def test_service_definitions_contain_no_product_configuration(
        self, subtests: pytest.Subtests
    ) -> None:
        definitions = (
            macos.render_plist(POSIX_CONTEXT),
            linux.render_unit(POSIX_CONTEXT),
            windows.render_task_xml(WINDOWS_CONTEXT).decode("utf-16"),
        )
        for definition in definitions:
            with subtests.test(definition=definition[:40]):
                assert "OPENAI_RESPONSES_PROXY_" not in definition
