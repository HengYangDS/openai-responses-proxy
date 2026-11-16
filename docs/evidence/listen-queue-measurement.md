---
subject: openai-responses-proxy:listen-queue-measurement-2026-11-16
role: evidence
state: archived
relations:
  part_of: README.md
---

# Listen-Queue Measurement

Status: dated measurement (2026-11-16).

The handoff controller must know when a listener can no longer begin a handoff request that the controller has given up. That rests on what a listener's operating system does with a connection that arrives while the listener's queue of pending connections is full. This record holds what was measured, how to measure it again, and what was not measured.

- **Scope:** TCP connections to a listening loopback socket that accepts nothing, on two hosts. Private sockets of the scripts below; no product code and no installed service.
- **Verifier:** the two scripts in this record, run as given under "Scripts".
- **Limit:** two kernels on one day. Windows was not measured. The record says what these kernels did; it does not lower the authority of the tests, which the hosted graph runs on every platform.

## Hosts

- macOS 27.2 (Darwin 27.2.0, arm64), Python 3.14.10, `kern.ipc.somaxconn` 128.
- Linux 7.2.12 (arm64, a container with its own network namespace and no network), Python 3.12.15, `net.core.somaxconn` 4096, `net.ipv4.tcp_abort_on_overflow` 0.

## Results

### Connects that arrive one after another

`queue_full_sequential.py`: 300 clients connect one at a time to `listen(256)`, the product's backlog, while nothing is accepted.

| Host  | Queue holds                             | A connect after the queue is full                                                 | Accepted afterwards                 |
| ----- | --------------------------------------- | --------------------------------------------------------------------------------- | ----------------------------------- |
| macOS | 128, the smaller of 256 and `somaxconn` | 155 failed with `ECONNRESET`; 17 returned, and a send on each failed with `EPIPE` | the 128, in the order of connecting |
| Linux | 257                                     | 43 timed out after 0.5 seconds without an answer                                  | the 257, in the order of connecting |

On macOS none of the 17 clients whose connect returned was accepted in the 20 seconds that followed: the listener had reset them.

### A burst of connects on Linux

`listen_overflow.py`: 48 clients connect at the same moment to `listen(0)`. Where more of them finish their connect than the listener can accept at once, the listener's stack has dropped the last step of the others' handshakes: such a client holds a connection that the listener does not have.

| What the client outside the queue did                         | Runs | Clients | Accepted by the listener later                                     |
| ------------------------------------------------------------- | ---- | ------- | ------------------------------------------------------------------ |
| Sent a request and closed in the ordinary way                 | 8    | 10      | 9, between 0.61 and 0.74 seconds after the close, request readable |
| Sent a request and closed with a reset (`SO_LINGER`, zero)    | 8    | 8       | 0 in 9 seconds, with room in the queue from 0.4 seconds on         |
| Sent a request and stayed open while a later client connected | 3    | 4       | all, each after the later client                                   |

On macOS the same burst never left a connected client outside the queue (60 trials): `listen(0)` there admitted all 48.

### A connection that is in the queue when its client resets it

The product's handoff transport, given up at its timeout before the listener accepted: on both hosts the listener then accepted the connection and read the whole request, and after it a reset. With the transport of a health read, which closes in the ordinary way, the read ended as an ordinary end of stream. `tests/runtime/test_loopback_reset.py` holds the first on every platform of the hosted graph.

## What follows

- A listener accepts the connections that are in its queue in the order in which they entered it, on both hosts.
- On Linux a client can hold a finished connect that is not in the queue. The listener takes such a connection later, after connections that were made later, and reads a request that the client sent before it closed in the ordinary way.
- A reset withdraws such a connection: the listener never accepts it. A reset does not withdraw a connection that is already in the queue.
- On macOS a full queue resets the connect, or the connection right after it; nothing of it reaches the listener later.

## Not measured

- Windows.
- A reset that is lost. On loopback nothing is lost.
- Other kernels, and other values of `tcp_abort_on_overflow`.

## Scripts

Each runs with a bare interpreter: `python3 -I -u <script>`. `listen_overflow.py` takes the cases to run as arguments (`graceful`, `abortive`, `order`), and `TRIALS` bounds its search for a burst that overflows. The Linux results came from `graceful` and `abortive` eight times each and `order` three times.

### `queue_full_sequential.py`

```python
"""What a listener's full queue does to connects that arrive one after another.

Private loopback sockets only. A listener with the product's backlog of 256 accepts nothing
while clients connect one at a time; then it accepts everything and the order is compared.
"""

from __future__ import annotations

import collections
import errno
import platform
import select
import socket
import time

CLIENTS = 300

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
sock.listen(256)
address = sock.getsockname()
results = []
clients = []
for index in range(CLIENTS):
    client = socket.socket()
    client.settimeout(0.5)
    try:
        client.connect(address)
        results.append("connected")
        clients.append((index, client))
    except TimeoutError:
        results.append("timed out")
        client.close()
    except OSError as error:
        results.append(errno.errorcode.get(error.errno, str(error.errno)))
        client.close()
counts = collections.Counter(results)
first_failure = next((i for i, r in enumerate(results) if r != "connected"), None)
print(
    f"{platform.system()} {platform.release()}: {CLIENTS} connects, one after another, to listen(256) with no accept"
)
print(f"  results {dict(counts)}; first that did not connect: number {first_failure}")
ports = [client.getsockname()[1] for _, client in clients]
sock.setblocking(False)
accepted = []
deadline = time.monotonic() + 12.0
while time.monotonic() < deadline:
    readable, _, _ = select.select([sock], [], [], 0.2)
    if not readable:
        continue
    conn, peer = sock.accept()
    accepted.append(peer[1])
    conn.close()
held = set(accepted)
known = [port for port in accepted if port in set(ports)]
in_order = known == [port for port in ports if port in set(known)]
print(
    f"  accepted in the 12 s afterwards {len(accepted)} of {len(ports)} connected; in the order of connecting: {in_order}"
)
outside = [(index, client) for index, client in clients if client.getsockname()[1] not in held]
sends = collections.Counter()
for _, client in outside:
    try:
        client.sendall(b"REQUEST")
        sends["sent"] += 1
    except OSError as error:
        sends[errno.errorcode.get(error.errno, str(error.errno))] += 1
if outside:
    print(
        f"  {len(outside)} clients whose connect returned were never accepted (numbers {outside[0][0]} to {outside[-1][0]}); a send on them: {dict(sends)}"
    )
    later = 0
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        readable, _, _ = select.select([sock], [], [], 0.2)
        if readable:
            conn, _ = sock.accept()
            later += 1
            conn.close()
    print(f"  accepted in 8 s after those sends: {later}")
for _, client in clients:
    client.close()
sock.close()
```

### `listen_overflow.py`

```python
"""Measure what a full listen queue does to a connection whose handshake the client finished.

Private loopback sockets only; no product code. A listener with a queue for one connection
accepts nothing while many clients connect at once. Where more clients report a finished
connect than the queue holds, the listener's stack has dropped the last step of the others'
handshakes: such a client holds a connection that the listener does not have yet.

For each such client the script sends a request while the queue is full, closes the client
in one of two ways, then makes room and reports whether the listener ever accepts the
connection and what it reads from it:

  graceful   an ordinary close
  abortive   SO_LINGER with zero seconds, then close: a reset

It also reports the order in which the listener accepts such a connection and a connection
that is made after room exists.
"""

from __future__ import annotations

import os
import platform
import select
import socket
import struct
import sys
import threading
import time

CLIENTS = 48
TRIALS = int(os.environ.get("TRIALS", "400"))


def listener() -> socket.socket:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(0)
    return sock


def connect_all(address: tuple[str, int]) -> list[socket.socket]:
    """Connect CLIENTS sockets at once; return those whose connect finished within 0.3 s."""
    barrier = threading.Barrier(CLIENTS)
    socks = [socket.socket() for _ in range(CLIENTS)]
    for sock in socks:
        sock.setblocking(False)

    def go(sock: socket.socket) -> None:
        barrier.wait()
        sock.connect_ex(address)

    threads = [threading.Thread(target=go, args=(sock,)) for sock in socks]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    time.sleep(0.3)
    done = []
    for sock in socks:
        _, writable, _ = select.select([], [sock], [], 0)
        if writable and sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
            done.append(sock)
        else:
            sock.close()
    return done


def accept_within(sock: socket.socket, seconds: float) -> socket.socket | None:
    sock.settimeout(seconds)
    try:
        conn, _ = sock.accept()
    except TimeoutError:
        return None
    return conn


def read_all(conn: socket.socket) -> str:
    conn.settimeout(2.0)
    data = b""
    try:
        while chunk := conn.recv(4096):
            data += chunk
    except TimeoutError:
        return f"{data!r} then no end within 2 s"
    except OSError as error:
        return f"{data!r} then {type(error).__name__}"
    return f"{data!r} then end of stream"


def overflowed() -> tuple[socket.socket, socket.socket, list[socket.socket]] | None:
    """Return a listener with a full queue and the clients whose handshake it dropped.

    Right after the burst, before any retransmission, the listener accepts all that its queue
    holds. A client whose connect finished and which is not among them holds a connection that
    the listener does not have. One fresh client then fills the queue again and is returned,
    so that the dropped clients stay outside until it is accepted.
    """
    for _ in range(TRIALS):
        sock = listener()
        done = connect_all(sock.getsockname())
        sock.setblocking(False)
        held = set()
        while True:
            try:
                conn, peer = sock.accept()
            except BlockingIOError:
                break
            held.add(peer[1])
            conn.close()
        dropped = [c for c in done if c.getsockname()[1] not in held]
        for c in done:
            if c.getsockname()[1] in held:
                c.close()
        if dropped:
            try:
                filler = socket.create_connection(sock.getsockname(), timeout=0.2)
            except OSError:
                filler = None
            if filler is not None:
                return sock, filler, dropped
        for c in dropped:
            c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            c.close()
        sock.close()
    return None


def take(sock: socket.socket, filler: socket.socket) -> None:
    """Accept the filler, which makes room in the queue."""
    conn = accept_within(sock, 1.0)
    assert conn is not None and conn.getpeername()[1] == filler.getsockname()[1]
    conn.close()
    filler.close()


def close_and_watch(mode: str) -> None:
    found = overflowed()
    if found is None:
        print(f"{mode}: none of {TRIALS} trials left a connected client outside the queue")
        return
    sock, filler, dropped = found
    for client in dropped:
        client.setblocking(True)
        client.sendall(b"REQUEST")
        if mode == "abortive":
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        client.close()
    closed = time.monotonic()
    time.sleep(0.4)
    take(sock, filler)
    arrivals = []
    until = closed + 9.0
    while (left := until - time.monotonic()) > 0:
        conn = accept_within(sock, left)
        if conn is None:
            break
        arrivals.append(f"at {time.monotonic() - closed:.2f} s read {read_all(conn)}")
        conn.close()
    print(
        f"{mode}: {len(dropped)} clients outside a full queue sent a request and closed; room "
        f"was made 0.4 s later. Accepted in the 9 s after the close: {len(arrivals)}"
    )
    for line in arrivals:
        print(f"  {line}")
    sock.close()


def order() -> None:
    found = overflowed()
    if found is None:
        print(f"order: none of {TRIALS} trials left a connected client outside the queue")
        return
    sock, filler, dropped = found
    for client in dropped:
        client.setblocking(True)
        client.sendall(b"EARLY")
    time.sleep(0.25)
    take(sock, filler)
    try:
        late = socket.create_connection(sock.getsockname(), timeout=5.0)
    except OSError:
        print("order: the late client could not connect; an early one took the room first")
        sock.close()
        return
    late.sendall(b"LATE")
    late_port = late.getsockname()[1]
    seen = []
    for _ in range(len(dropped) + 1):
        conn = accept_within(sock, 12.0)
        if conn is None:
            seen.append("nothing")
            break
        seen.append("late" if conn.getpeername()[1] == late_port else "early")
        conn.close()
    print(
        f"order: {len(dropped)} early clients outside a full queue had sent; room was made and "
        f"one late client connected and sent. Accepted: {', '.join(seen)}"
    )
    for c in (*dropped, late):
        c.close()
    sock.close()


if __name__ == "__main__":
    print(f"{platform.system()} {platform.release()} Python {platform.python_version()}")
    try:
        with open("/proc/sys/net/ipv4/tcp_abort_on_overflow") as handle:
            print("tcp_abort_on_overflow", handle.read().strip())
    except OSError:
        pass
    for mode in sys.argv[1:] or ["graceful", "abortive", "order"]:
        if mode == "order":
            order()
        else:
            close_and_watch(mode)
```
