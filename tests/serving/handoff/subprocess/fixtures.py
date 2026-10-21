"""Owned subprocess payload, generation capture and teardown mechanics."""

from __future__ import annotations

import tempfile
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from pathlib import Path

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle import context as runtime_context
from openai_responses_proxy.lifecycle.supervision import native_service
from openai_responses_proxy.lifecycle.supervision import process
from tests.serving.handoff.fixtures import write_installed_payload
from tools.release import verification

PACKAGED_SUCCESSOR_TIMEOUT_SECONDS = 60
PAYLOAD_UNLOCK_TIMEOUT_SECONDS = 5


class SubprocessFixture:
    """Release exact observed child generations before their temporary payload."""

    def setup_method(self) -> None:
        self._cleanups = ExitStack()

    def teardown_method(self) -> None:
        self._cleanups.close()

    def _installed_fixture(
        self, *, release: str, port: int, upstream_url: str
    ) -> tuple[Path, runtime_context.RuntimeContext, dict[int, process.OwnedProcess]]:
        temporary = verification.temporary_home(delete=False)
        root = Path(temporary.name)
        try:
            ctx = write_installed_payload(
                root, release=release, port=port, upstream_url=upstream_url
            )
        except BaseException:
            temporary.cleanup()
            raise
        owned_processes: dict[int, process.OwnedProcess] = {}
        self._cleanups.callback(self._cleanup_installed_fixture, temporary, ctx, owned_processes)
        return root, ctx, owned_processes

    def _cleanup_installed_fixture(
        self,
        temporary: tempfile.TemporaryDirectory[str],
        ctx: runtime_context.RuntimeContext,
        owned_processes: dict[int, process.OwnedProcess],
    ) -> None:
        proxy_script = ctx.executable
        failure: Exception | None = None
        try:
            service = native_service.adapter()
            service.uninstall(ctx)
            assert service.status(ctx) == "absent"
        except Exception as error:
            failure = error
        finally:
            for pid in process.pids_naming_executable(proxy_script):
                owned = process.capture_executable(pid, proxy_script)
                if owned is None:
                    failure = failure or errors.InstallError("test process identity is unproved")
                else:
                    owned_processes[pid] = owned
            for owned in owned_processes.values():
                try:
                    if not process.terminate_owned_process(owned):
                        raise errors.InstallError("test process exit is unproved")
                except errors.InstallError as error:
                    failure = failure or error
        if failure is not None:
            raise failure
        remaining = {
            owned.pid for owned in owned_processes.values() if process.owned_process_alive(owned)
        }
        assert not remaining, f"orphaned proxy children for {proxy_script}: {remaining}"
        deadline = time.monotonic() + PAYLOAD_UNLOCK_TIMEOUT_SECONDS
        while True:
            try:
                temporary.cleanup()
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def _post_responses(self, port, *, timeout=15):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/dmxapi/v1/responses",
            data=b'{"stream": false, "input": []}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return response.read()
