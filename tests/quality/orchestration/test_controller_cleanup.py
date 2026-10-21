"""Controller failure cleanup preserves custody and stops independent children."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import ExitStack

import pytest

from openai_responses_proxy import errors
from openai_responses_proxy.lifecycle.supervision import process
from tests.release.lifecycle import test_cleanup


@pytest.mark.parametrize("failure", ["registration", "termination"])
def test_controller_cleanup_stops_independent_children_after_failure(
    tmp_path, monkeypatch, failure
):
    """A failed observation must not strand a separate known child generation."""
    root = tmp_path / "owned"
    root.mkdir()
    preserved = root / "payload"
    preserved.write_bytes(b"preserved while any owned exit is unproved")
    contexts = tmp_path / "contexts.jsonl"
    contexts.touch()
    registered = tmp_path / "registered"
    command = [sys.executable, "-c", "from threading import Event; Event().wait(60)"]
    original_terminate = process.terminate_owned_process
    error = errors.InstallError("first owned generation exit is unproved")
    with ExitStack() as cleanup:
        children = []
        owned = {}
        for _ in range(2):
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL)
            cleanup.callback(child.wait, timeout=10)
            cleanup.callback(lambda item=child: item.kill() if item.poll() is None else None)
            children.append(child)
            identity = process.capture_generation(child.pid, sys.executable)
            assert identity is not None
            owned[identity.pid] = identity
        expected_exit = children[1].pid
        if failure == "registration":
            registered.write_bytes(b'{"pid":')

            def observe_registration():
                try:
                    json.loads(registered.read_text())
                finally:
                    test_cleanup._stop_controller(children[0], owned, root, contexts)

            with pytest.raises(json.JSONDecodeError):
                observe_registration()
            assert not root.exists()
            assert registered.read_bytes() == b'{"pid":'
        else:

            def deny_first(identity, **kwargs):
                if identity.pid == children[0].pid:
                    raise error
                return original_terminate(identity, **kwargs)

            monkeypatch.setattr(process, "terminate_owned_process", deny_first)
            with pytest.raises(errors.InstallError) as raised:
                test_cleanup._stop_controller(children[0], owned, root, contexts)
            assert raised.value.__cause__ is error
            assert preserved.read_bytes() == b"preserved while any owned exit is unproved"
            assert contexts.is_file()
        assert not process.owned_process_alive(owned[expected_exit])
