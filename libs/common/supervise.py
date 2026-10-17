"""Define the minimum contract for a process supervisor.

``ProcessSpec`` describes a name, command, and heartbeat without starting a
process. It relates to ``services/*/procs.toml`` and the commands in
``docker-compose.yml``; in F0 it documents the interface while the supervisor
implementation remains a deployment concern. It gives every API and worker a
uniform operational description.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    name: str
    command: tuple[str, ...]
    heartbeat: str | None = None
