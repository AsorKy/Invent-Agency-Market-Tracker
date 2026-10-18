"""Test the process supervisor with real short-lived subprocesses and fake clocks."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from libs.common.supervise import ProcessSpec, Supervisor, load_specs

ROOT = Path(__file__).resolve().parents[2]
SLEEPER = (sys.executable, "-c", "import time; time.sleep(60)")
QUICK_EXIT = (sys.executable, "-c", "raise SystemExit(3)")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _spawn_real(spec: ProcessSpec) -> subprocess.Popen[bytes]:
    return subprocess.Popen(list(spec.command))


def test_child_death_stops_others_and_exits_one() -> None:
    spawned: list[subprocess.Popen[bytes]] = []

    def spawn(spec: ProcessSpec) -> subprocess.Popen[bytes]:
        proc = _spawn_real(spec)
        spawned.append(proc)
        return proc

    specs = [ProcessSpec("long", SLEEPER), ProcessSpec("short", QUICK_EXIT)]
    code = Supervisor(specs, spawn=spawn, poll_interval=0.05, stop_grace=5.0).run()
    assert code == 1
    assert all(proc.poll() is not None for proc in spawned)
    assert spawned[1].returncode == 3


def test_missing_heartbeat_is_dead_after_90_seconds(tmp_path: Path) -> None:
    clock = FakeClock()
    hb = tmp_path / "hb"
    specs = [ProcessSpec("worker", SLEEPER, heartbeat=str(hb))]
    supervisor = Supervisor(specs, spawn=_spawn_real, clock=clock, sleep=clock.sleep)
    supervisor._started_at = clock.now
    supervisor._children["worker"] = _spawn_real(specs[0])
    try:
        clock.now += 89
        assert supervisor.check() is None
        clock.now += 2
        assert supervisor.check() == "worker"
    finally:
        supervisor.stop_all()


def test_touched_heartbeat_keeps_child_alive(tmp_path: Path) -> None:
    clock = FakeClock()
    hb = tmp_path / "hb"
    hb.write_text("")
    specs = [ProcessSpec("worker", SLEEPER, heartbeat=str(hb))]
    supervisor = Supervisor(specs, spawn=_spawn_real, clock=clock, sleep=clock.sleep)
    supervisor._started_at = clock.now
    supervisor._children["worker"] = _spawn_real(specs[0])
    try:
        clock.now += 200
        os.utime(hb, (clock.now - 10, clock.now - 10))
        assert supervisor.check() is None
        clock.now += 81
        assert supervisor.check() == "worker"
    finally:
        supervisor.stop_all()


def test_run_exits_one_on_stale_heartbeat_and_kills_children(tmp_path: Path) -> None:
    clock = FakeClock()
    spawned: list[subprocess.Popen[bytes]] = []

    def spawn(spec: ProcessSpec) -> subprocess.Popen[bytes]:
        proc = _spawn_real(spec)
        spawned.append(proc)
        return proc

    specs = [
        ProcessSpec("api", SLEEPER),
        ProcessSpec("worker", SLEEPER, heartbeat=str(tmp_path / "never")),
    ]
    supervisor = Supervisor(specs, spawn=spawn, clock=clock, sleep=clock.sleep, stop_grace=0.0)
    assert supervisor.run() == 1
    for proc in spawned:
        proc.wait(timeout=10)
    assert all(proc.poll() is not None for proc in spawned)


def test_request_stop_returns_zero_and_stops_children() -> None:
    spawned: list[subprocess.Popen[bytes]] = []

    def spawn(spec: ProcessSpec) -> subprocess.Popen[bytes]:
        proc = _spawn_real(spec)
        spawned.append(proc)
        return proc

    holder: list[Supervisor] = []

    def sleep(_: float) -> None:
        holder[0].request_stop()

    supervisor = Supervisor([ProcessSpec("a", SLEEPER)], spawn=spawn, sleep=sleep)
    holder.append(supervisor)
    assert supervisor.run() == 0
    assert spawned[0].poll() is not None


@pytest.mark.parametrize("path", sorted((ROOT / "services").glob("*/procs.toml")))
def test_procs_toml_files_parse(path: Path) -> None:
    specs = load_specs(path)
    assert specs
    assert all(spec.command for spec in specs)
    assert len({spec.name for spec in specs}) == len(specs)
