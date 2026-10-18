"""Run several child processes as one unit and fail fast when any of them dies.

``ProcessSpec`` describes a name, command, and optional heartbeat file;
``load_specs`` reads ``services/*/procs.toml``. ``Supervisor`` starts every
child, polls them, and when one exits or stops touching its heartbeat file for
90 seconds it stops the others and returns exit code 1 so the container
restarts as a whole (REQ-EVT-006, ADR-IMT-0001 section 6.3). Clock, sleep, and
process factory are injectable so the logic is testable without waiting.
"""

import os
import signal
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

HEARTBEAT_TIMEOUT_SECONDS = 90.0
POLL_INTERVAL_SECONDS = 1.0
STOP_GRACE_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    name: str
    command: tuple[str, ...]
    heartbeat: str | None = None
    probe: str | None = None


class Child(Protocol):
    """Subset of ``subprocess.Popen`` used by the supervisor."""

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


def load_specs(path: Path) -> list[ProcessSpec]:
    """Parse a ``procs.toml`` file into process specs."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [
        ProcessSpec(
            name=str(item["name"]),
            command=tuple(str(part) for part in item["cmd"]),
            heartbeat=item.get("heartbeat"),
            probe=item.get("probe"),
        )
        for item in data.get("proc", [])
    ]


def _spawn(spec: ProcessSpec) -> Child:
    return subprocess.Popen(list(spec.command))


class Supervisor:
    """Start children, watch them, and stop everything when one fails."""

    def __init__(
        self,
        specs: Sequence[ProcessSpec],
        *,
        spawn: Callable[[ProcessSpec], Child] = _spawn,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        heartbeat_timeout: float = HEARTBEAT_TIMEOUT_SECONDS,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        stop_grace: float = STOP_GRACE_SECONDS,
    ) -> None:
        self._specs = list(specs)
        self._spawn = spawn
        self._clock = clock
        self._sleep = sleep
        self._heartbeat_timeout = heartbeat_timeout
        self._poll_interval = poll_interval
        self._stop_grace = stop_grace
        self._children: dict[str, Child] = {}
        self._started_at = 0.0
        self._stop_requested = False

    def request_stop(self) -> None:
        """Ask the run loop to stop all children and return 0."""
        self._stop_requested = True

    def _heartbeat_stale(self, spec: ProcessSpec) -> bool:
        if spec.heartbeat is None:
            return False
        try:
            last = os.stat(spec.heartbeat).st_mtime
        except OSError:
            last = self._started_at
        return self._clock() - max(last, self._started_at) > self._heartbeat_timeout

    def check(self) -> str | None:
        """Return the name of the first dead child, or ``None`` when all are healthy."""
        for spec in self._specs:
            child = self._children[spec.name]
            if child.poll() is not None or self._heartbeat_stale(spec):
                return spec.name
        return None

    def stop_all(self) -> None:
        """Terminate every child, escalating to kill after the grace period."""
        for child in self._children.values():
            if child.poll() is None:
                child.terminate()
        deadline = self._clock() + self._stop_grace
        while self._clock() < deadline and any(
            child.poll() is None for child in self._children.values()
        ):
            self._sleep(0.05)
        for child in self._children.values():
            if child.poll() is None:
                child.kill()

    def run(self) -> int:
        """Run until a child dies (exit 1) or a stop is requested (exit 0)."""
        self._started_at = self._clock()
        try:
            for spec in self._specs:
                self._children[spec.name] = self._spawn(spec)
            while not self._stop_requested:
                if self.check() is not None:
                    return 1
                self._sleep(self._poll_interval)
            return 0
        finally:
            self.stop_all()


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: ``python -m libs.common.supervise services/core/procs.toml``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write("usage: python -m libs.common.supervise <procs.toml>\n")
        return 2
    supervisor = Supervisor(load_specs(Path(args[0])))
    signal.signal(signal.SIGTERM, lambda *_: supervisor.request_stop())
    signal.signal(signal.SIGINT, lambda *_: supervisor.request_stop())
    return supervisor.run()


if __name__ == "__main__":
    sys.exit(main())
