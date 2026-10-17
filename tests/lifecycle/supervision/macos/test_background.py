"""macOS user-domain and persistent launch-carrier admission."""

from __future__ import annotations

import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.supervision import macos
from tests.lifecycle.supervision.fixtures import completed as _completed
from tests.lifecycle.supervision.fixtures import set_file as _set_file
from tests.lifecycle.supervision.fixtures import temporary_context as _temporary_context
from tests.lifecycle.supervision.macos.fixtures import service as _service


class TestMacosBackgroundContract:
    @pytest.mark.parametrize(
        ("returncode", "stdout", "message"),
        [
            (1, "", "reachable launchd user domain"),
            (0, "gui/501 = {\n\ttype = login\n}\n", "user-domain identity is unproved"),
        ],
    )
    def test_unavailable_user_domain_preserves_installation(
        self, *, returncode, stdout, message, mocker
    ) -> None:
        mocker.patch.object(macos.os, "getuid", return_value=501, create=True)
        mocker.patch.object(macos, "_native_tool", side_effect=lambda name: name)
        invoked = mocker.patch.object(
            macos.subprocess,
            "run",
            return_value=_completed(returncode=returncode, stdout=stdout),
        )
        with (
            _temporary_context("log_dir") as ctx,
            pytest.raises(errors.InstallError, match=message),
        ):
            macos.install(ctx)
        assert not Path(macos._plist_path(ctx)).exists()
        assert invoked.call_count == 1

    @pytest.mark.parametrize(
        "executable_suffix",
        ["other/bin/openai-responses-proxy", "generations/invalid/bin/openai-responses-proxy"],
    )
    def test_carrier_requires_the_canonical_install_generation(
        self, *, executable_suffix, mocker
    ) -> None:
        with _temporary_context("log_dir") as ctx:
            foreign_context = SimpleNamespace(
                executable=f"{ctx.install_dir}/{executable_suffix}",
                install_dir=ctx.install_dir,
                service_id=ctx.service_id,
                user_home=ctx.user_home,
                log_dir=ctx.log_dir,
            )
            content = macos.render_plist(foreign_context)
            carrier = _set_file(macos._plist_path(ctx), content)
            invoked = mocker.patch.object(macos.subprocess, "run")
            with pytest.raises(errors.InstallError, match="executable ownership is unproved"):
                macos.install(ctx)
            assert carrier.read_text() == content
            invoked.assert_not_called()

    def test_carrier_changed_during_bootout_is_preserved(self, *, mocker) -> None:
        mocker.patch.object(macos, "_domains", return_value=("user/501",))
        mocker.patch.object(macos, "_native_tool", side_effect=lambda name: name)
        with _temporary_context("log_dir") as ctx:
            carrier = _set_file(macos._plist_path(ctx), macos.render_plist(ctx))
            changed = b"new external carrier content"
            reads = iter([_completed(stdout="registered"), _completed(returncode=113)])

            def native_operation(arguments, **_kwargs):
                if arguments[1] == "bootout":
                    carrier.write_bytes(changed)
                    return _completed()
                return next(reads)

            invoked = mocker.patch.object(macos.subprocess, "run", side_effect=native_operation)
            with pytest.raises(errors.InstallError, match="carrier is invalid"):
                macos.uninstall(ctx)
            assert carrier.read_bytes() == changed
            assert all(
                call.args[0][1] not in {"bootstrap", "kickstart"} for call in invoked.call_args_list
            )

    def test_carrier_admits_gui_login_and_background_sessions(self, *, mocker) -> None:
        mocker.patch.object(macos.os, "getuid", return_value=501, create=True)
        with _temporary_context("log_dir") as ctx:
            payload = plistlib.loads(macos.render_plist(ctx).encode())

        assert macos._user_domain() == "user/501"
        assert payload["LimitLoadToSessionType"] == ["Aqua", "Background"]

    @pytest.mark.parametrize("sessions", [None, "Background", ["Aqua", "Background"]])
    def test_carrier_accepts_each_released_session_declaration(self, *, sessions) -> None:
        with _temporary_context("log_dir") as ctx:
            payload = plistlib.loads(macos.render_plist(ctx).encode())
            if sessions is None:
                payload.pop("LimitLoadToSessionType")
            else:
                payload["LimitLoadToSessionType"] = sessions
            _set_file(macos._plist_path(ctx), plistlib.dumps(payload).decode())

            assert macos.configured_executable(ctx) == ctx.executable

    @pytest.mark.parametrize("sessions", [None, "Background"])
    def test_projection_is_current_only_for_this_release_rendering(self, *, sessions) -> None:
        with _temporary_context("log_dir") as ctx:
            plist = Path(macos._plist_path(ctx))
            assert macos.projection_is_current(ctx) is False
            _set_file(plist, macos.render_plist(ctx))
            assert macos.projection_is_current(ctx) is True
            payload = plistlib.loads(macos.render_plist(ctx).encode())
            if sessions is None:
                payload.pop("LimitLoadToSessionType")
            else:
                payload["LimitLoadToSessionType"] = sessions
            _set_file(plist, plistlib.dumps(payload).decode())

            assert macos.configured_executable(ctx) == ctx.executable
            assert macos.projection_is_current(ctx) is False

    @pytest.mark.parametrize(
        "sessions", ["Aqua", ["Background"], ["Aqua", "Background", "LoginWindow"]]
    )
    def test_carrier_rejects_an_unreleased_session_declaration(self, *, sessions) -> None:
        with _temporary_context("log_dir") as ctx:
            payload = plistlib.loads(macos.render_plist(ctx).encode())
            payload["LimitLoadToSessionType"] = sessions
            content = plistlib.dumps(payload).decode()
            carrier = _set_file(macos._plist_path(ctx), content)

            with pytest.raises(errors.InstallError, match="carrier ownership is unproved"):
                macos.configured_executable(ctx)
            assert carrier.read_text(encoding="utf-8") == content

    def test_install_registers_in_the_gui_login_domain_restored_by_launchd(self, *, mocker) -> None:
        mocker.patch.object(macos, "_native_tool", side_effect=lambda name: name)
        mocker.patch.object(macos, "_domains", return_value=("user/501", "gui/501"))
        with _temporary_context("log_dir") as ctx:
            plist = macos._plist_path(ctx)
            successor = macos.process.OwnedProcess(73, ctx.executable, 2.0)
            mocker.patch.object(macos.process, "wait_for_executable", return_value=successor)
            mocker.patch.object(macos.process, "owned_process_alive", return_value=True)
            invoked = mocker.patch.object(
                macos.subprocess,
                "run",
                side_effect=[
                    _completed(returncode=113),
                    _completed(returncode=113),
                    _completed(),
                    _completed(),
                    _completed(stdout="73\n"),
                    _completed(stdout=_service(73)),
                ],
            )

            macos.install(ctx)

            assert [call.args[0] for call in invoked.call_args_list] == [
                ["launchctl", "print", f"user/501/{ctx.service_id}"],
                ["launchctl", "print", f"gui/501/{ctx.service_id}"],
                ["plutil", "-lint", plist],
                ["launchctl", "bootstrap", "gui/501", plist],
                ["launchctl", "kickstart", "-p", f"gui/501/{ctx.service_id}"],
                ["launchctl", "print", f"gui/501/{ctx.service_id}"],
            ]

    def test_install_moves_a_background_only_watchdog_into_the_gui_login_domain(
        self, *, mocker
    ) -> None:
        mocker.patch.object(macos, "_native_tool", side_effect=lambda name: name)
        mocker.patch.object(macos, "_domains", return_value=("user/501", "gui/501"))
        with _temporary_context("log_dir") as ctx:
            carrier = Path(macos._plist_path(ctx))
            background_only = plistlib.loads(macos.render_plist(ctx).encode())
            background_only["LimitLoadToSessionType"] = "Background"
            carrier.parent.mkdir(parents=True)
            carrier.write_bytes(plistlib.dumps(background_only))
            predecessor = macos.process.OwnedProcess(41, ctx.executable, 1.0)
            successor = macos.process.OwnedProcess(73, ctx.executable, 2.0)
            mocker.patch.object(macos.process, "capture_executable", return_value=predecessor)
            wait_for_exit = mocker.patch.object(macos.process, "wait_for_exit", return_value=True)
            mocker.patch.object(macos.process, "wait_for_executable", return_value=successor)
            mocker.patch.object(macos.process, "owned_process_alive", return_value=True)
            invoked = mocker.patch.object(
                macos.subprocess,
                "run",
                side_effect=[
                    _completed(stdout=_service(41)),
                    _completed(returncode=113),
                    _completed(),
                    _completed(returncode=113),
                    _completed(),
                    _completed(),
                    _completed(stdout="73\n"),
                    _completed(stdout=_service(73)),
                ],
            )

            macos.install(ctx)

            commands = [call.args[0] for call in invoked.call_args_list]
            assert commands[2] == ["launchctl", "bootout", f"user/501/{ctx.service_id}"]
            assert commands[-3:] == [
                ["launchctl", "bootstrap", "gui/501", str(carrier)],
                ["launchctl", "kickstart", "-p", f"gui/501/{ctx.service_id}"],
                ["launchctl", "print", f"gui/501/{ctx.service_id}"],
            ]
            wait_for_exit.assert_called_once_with(predecessor)
            sessions = plistlib.loads(carrier.read_bytes())["LimitLoadToSessionType"]
            assert sessions == ["Aqua", "Background"]

    def test_status_observes_a_registered_watchdog_without_its_carrier(self, *, mocker) -> None:
        mocker.patch.object(macos.os, "getuid", return_value=501, create=True)
        mocker.patch.object(macos, "_native_tool", side_effect=lambda name: name)
        mocker.patch.object(
            macos.subprocess,
            "run",
            side_effect=lambda arguments, **_kwargs: (
                _completed(stdout="user/501 = {\n\ttype = user\n}\n")
                if arguments[-1] == "user/501"
                else _completed(stdout="\tpid = 73\n")
            ),
        )
        with _temporary_context("log_dir") as ctx:
            assert not Path(macos._plist_path(ctx)).exists()
            assert macos.status(ctx) == "running"

    def test_pristine_status_preserves_an_absent_home(self, tmp_path, *, mocker) -> None:
        home = tmp_path / "home"
        mocker.patch.object(macos, "_domains", return_value=("user/501",))
        mocker.patch.object(macos, "_native_tool", side_effect=lambda name: name)
        invoked = mocker.patch.object(
            macos.subprocess, "run", return_value=_completed(returncode=113)
        )
        with _temporary_context("log_dir") as ctx:
            ctx.user_home = str(home)
            assert macos.status(ctx) == "absent"
            assert macos.configured_executable(ctx) is None
            invoked.assert_called_once_with(
                ["launchctl", "print", f"user/501/{ctx.service_id}"],
                capture_output=True,
                check=False,
                text=True,
            )
        assert not home.exists()

    def test_install_preserves_a_foreign_launch_agent(self, *, mocker) -> None:
        with _temporary_context("log_dir") as ctx:
            payload = plistlib.loads(macos.render_plist(ctx).encode())
            payload["Label"] = "foreign.watchdog"
            content = plistlib.dumps(payload)
            carrier = Path(macos._plist_path(ctx))
            carrier.parent.mkdir(parents=True)
            carrier.write_bytes(content)
            invoked = mocker.patch.object(macos.subprocess, "run")

            with pytest.raises(errors.InstallError, match="carrier ownership is unproved"):
                macos.install(ctx)

            assert carrier.read_bytes() == content
            invoked.assert_not_called()

    @pytest.mark.parametrize("gui", [False, True])
    def test_domain_observation_accepts_a_user_session_without_gui(self, *, gui, mocker) -> None:
        mocker.patch.object(macos.os, "getuid", return_value=501, create=True)
        mocker.patch.object(macos, "_native_tool", side_effect=lambda name: name)
        associated = "\tgui asid = 100021\n" if gui else ""
        invoked = mocker.patch.object(
            macos.subprocess,
            "run",
            return_value=_completed(stdout=f"user/501 = {{\n\ttype = user\n{associated}}}\n"),
        )

        assert macos._domains() == (("user/501", "gui/501") if gui else ("user/501",))
        invoked.assert_called_once_with(
            ["launchctl", "print", "user/501"],
            capture_output=True,
            check=False,
            text=True,
        )

    def test_competing_service_registrations_are_not_removed(self, *, mocker) -> None:
        mocker.patch.object(macos, "_domains", return_value=("user/501", "gui/501"))
        mocker.patch.object(macos, "_service", return_value=macos._Service(True, 73))
        invoked = mocker.patch.object(macos.subprocess, "run")
        with (
            _temporary_context("log_dir") as ctx,
            pytest.raises(errors.InstallError, match="both user and GUI"),
        ):
            macos.uninstall(ctx)
        invoked.assert_not_called()

    @pytest.mark.parametrize("operation", [macos.install, macos.uninstall])
    def test_carrier_symlink_preserves_its_foreign_target(self, tmp_path, *, operation, mocker):
        foreign = tmp_path / "foreign.plist"
        content = b"foreign launch agent"
        foreign.write_bytes(content)
        with _temporary_context("log_dir") as ctx:
            carrier = Path(macos._plist_path(ctx))
            carrier.parent.mkdir(parents=True)
            carrier.symlink_to(foreign)
            invoked = mocker.patch.object(macos.subprocess, "run")
            with pytest.raises(errors.InstallError, match="symlink"):
                operation(ctx)
            assert carrier.is_symlink()
        assert foreign.read_bytes() == content
        invoked.assert_not_called()

    def test_install_replaces_the_owned_legacy_gui_watchdog_in_its_login_domain(
        self, *, mocker
    ) -> None:
        mocker.patch.object(macos.os, "getuid", return_value=501, create=True)
        mocker.patch.object(macos, "_native_tool", side_effect=lambda name: name)
        mocker.patch.object(macos, "_domains", return_value=("user/501", "gui/501"))
        with _temporary_context("log_dir") as ctx:
            carrier = Path(macos._plist_path(ctx))
            legacy = plistlib.loads(macos.render_plist(ctx).encode())
            legacy.pop("LimitLoadToSessionType")
            carrier.parent.mkdir(parents=True)
            carrier.write_bytes(plistlib.dumps(legacy))
            predecessor = macos.process.OwnedProcess(41, ctx.executable, 1.0)
            successor = macos.process.OwnedProcess(73, ctx.executable, 2.0)
            mocker.patch.object(macos.process, "capture_executable", return_value=predecessor)
            mocker.patch.object(macos.process, "wait_for_exit", return_value=True)
            mocker.patch.object(macos.process, "wait_for_executable", return_value=successor)
            mocker.patch.object(macos.process, "owned_process_alive", return_value=True)
            invoked = mocker.patch.object(
                macos.subprocess,
                "run",
                side_effect=[
                    _completed(returncode=113),
                    _completed(stdout=_service(41)),
                    _completed(),
                    _completed(returncode=113),
                    _completed(),
                    _completed(),
                    _completed(stdout="73\n"),
                    _completed(stdout=_service(73)),
                ],
            )

            macos.install(ctx)

            commands = [call.args[0] for call in invoked.call_args_list]
            assert ["launchctl", "bootout", f"gui/501/{ctx.service_id}"] in commands
            assert ["launchctl", "bootstrap", "gui/501", str(carrier)] in commands
            sessions = plistlib.loads(carrier.read_bytes())["LimitLoadToSessionType"]
            assert sessions == ["Aqua", "Background"]
