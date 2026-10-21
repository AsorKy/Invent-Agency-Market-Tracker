"""Check the Redis ACL rules against the commands and keys the code really uses.

The rules in ``deploy/redis/acl.rules`` are rendered by ``deploy/redis/entrypoint.sh``
(REQ-SEC-005). No Redis server runs here, so a small evaluator applies the ACL rule
syntax used by the file to commands and keys extracted from ``libs`` and ``services``;
a command or key the code uses but an ACL denies fails the test.
"""

import fnmatch
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "deploy/redis/acl.rules"
ENTRYPOINT = ROOT / "deploy/redis/entrypoint.sh"
METHOD_TO_COMMAND = {"xgroup_create": "xgroup|create"}
REDIS_METHODS = (
    r"xadd|xreadgroup|xack|xgroup_create|xautoclaim|xclaim|xpending|xrange|xlen|xdel|xtrim|"
    r"eval|evalsha|ping|set|get|delete|exists|incr|expire|hset|hget|zadd|sadd|publish|keys|scan"
)
CALL = re.compile(rf"\b_?(?:self\.)?_?redis\.({REDIS_METHODS})\(")
CLIENT_PING = re.compile(r"\bclient\.(ping)\(")
LUA_CALL = re.compile(r"redis\.call\(\s*['\"](\w+)['\"]")
KEY_LITERAL = re.compile(r"f?\"((?:invent|brief|lock|republish)[.:][^\"]*)\"")
EDGE_FILES = ["libs/common/rate_limiter.py", "libs/common/redis_client.py"]


class Acl:
    """Evaluate ``~key`` patterns and ``-@all +cmd`` rules of one rendered user."""

    def __init__(self, rules: str) -> None:
        tokens = rules.split()
        self.keys = [t[1:] for t in tokens if t.startswith("~")]
        self.commands = {t[1:].lower() for t in tokens if t.startswith("+")}
        assert "-@all" in tokens and "+@all" not in tokens and "allkeys" not in tokens
        assert "~*" not in tokens and "allcommands" not in tokens

    def allows(self, command: str) -> bool:
        return command.lower() in self.commands

    def allows_key(self, key: str) -> bool:
        return any(fnmatch.fnmatchcase(key, pattern) for pattern in self.keys)


def _load_rules() -> dict[str, tuple[str, Acl]]:
    users: dict[str, tuple[str, Acl]] = {}
    for line in RULES.read_text(encoding="ascii").splitlines():
        if line.strip() and not line.startswith("#"):
            user, variable, rest = line.split(maxsplit=2)
            users[user] = (variable, Acl(rest))
    return users


def _python_files(paths: list[str] | None = None) -> list[Path]:
    if paths is not None:
        return [ROOT / p for p in paths]
    files = [*(ROOT / "libs").rglob("*.py"), *(ROOT / "services").rglob("*.py")]
    return [f for f in files if "tests" not in f.parts and "__pycache__" not in f.parts]


def _commands_used(files: list[Path]) -> set[str]:
    used: set[str] = set()
    for path in files:
        text = path.read_text(encoding="utf-8")
        for method in [*CALL.findall(text), *CLIENT_PING.findall(text)]:
            used.add(METHOD_TO_COMMAND.get(method, method))
        used.update(name.lower() for name in LUA_CALL.findall(text))
        used.update(name.lower() for name in re.findall(r"redis\.call\('(\w+)'", text))
    return used


def _lua_commands(path: Path) -> set[str]:
    return {name.lower() for name in re.findall(r"redis\.call\('(\w+)'", path.read_text())}


def _key_globs(files: list[Path]) -> set[str]:
    keys: set[str] = set()
    for path in files:
        for literal in KEY_LITERAL.findall(path.read_text(encoding="utf-8")):
            keys.add(re.sub(r"\{[^}]*\}", "x", literal))
    return keys


USERS = _load_rules()


def test_rules_define_exactly_edge_and_data() -> None:
    assert set(USERS) == {"edge", "data"}
    assert USERS["edge"][0] == "REDIS_EDGE_PASSWORD"
    assert USERS["data"][0] == "REDIS_DATA_PASSWORD"


def test_edge_covers_gateway_commands_and_only_rate_keys() -> None:
    edge = USERS["edge"][1]
    used = _commands_used([ROOT / p for p in EDGE_FILES])
    assert {"eval", "ping", "time", "zadd", "zcard"} <= used
    assert {c for c in used if not edge.allows(c)} == set()
    keys = _key_globs([ROOT / "libs/common/rate_limiter.py"])
    assert keys == {"invent:rate:x"}
    assert all(edge.allows_key(k) for k in keys)
    assert edge.keys == ["invent:rate:*"]


def test_edge_cannot_touch_streams_or_data_keys() -> None:
    edge = USERS["edge"][1]
    for command in (
        "xadd",
        "xreadgroup",
        "xack",
        "set",
        "get",
        "del",
        "flushall",
        "keys",
        "config",
    ):
        assert not edge.allows(command)
    for key in ("invent.events", "invent.ingest", "brief:lock:1", "lock:score:1"):
        assert not edge.allows_key(key)


def test_gateway_uses_no_other_redis_commands() -> None:
    gateway = [f for f in (ROOT / "services/gateway").rglob("*.py") if "tests" not in f.parts]
    assert _commands_used(gateway) <= {"ping"}


def test_data_covers_every_command_the_services_use() -> None:
    data = USERS["data"][1]
    services = [
        f
        for f in _python_files()
        if "services/gateway" not in f.as_posix()
        and f.as_posix() != (ROOT / "libs/common/rate_limiter.py").as_posix()
    ]
    used = _commands_used(services)
    assert {"xadd", "xreadgroup", "xack", "xgroup|create", "set", "delete", "eval"} <= used
    denied = {c for c in used if not data.allows(c.replace("delete", "del"))}
    assert denied == set()


def test_data_covers_lua_scripts_of_services() -> None:
    data = USERS["data"][1]
    for rel in ("services/brief/app/single_flight.py", "services/ml/app/scoring_service.py"):
        assert {c for c in _lua_commands(ROOT / rel) if not data.allows(c)} == set()


def test_data_covers_every_key_the_services_use() -> None:
    data = USERS["data"][1]
    services = [f for f in _python_files() if f.name != "rate_limiter.py"]
    keys = _key_globs(services)
    assert {"invent.events", "invent.events.dead", "invent.ingest", "lock:score:x"} <= keys
    assert {k for k in keys if not data.allows_key(k)} == set()


def test_data_cannot_reach_rate_keys_or_admin_commands() -> None:
    data = USERS["data"][1]
    assert not data.allows_key("invent:rate:abc")
    for command in ("flushall", "flushdb", "config", "keys", "acl", "shutdown", "debug", "zadd"):
        assert not data.allows(command)


def _render(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    fake = stub / "redis-server"
    fake.write_text('#!/bin/sh\necho "$@" > "$STUB_ARGS"\n', encoding="ascii")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    full = {
        "PATH": f"{stub}:{os.environ['PATH']}",
        "REDIS_ACL_FILE": str(tmp_path / "users.acl"),
        "STUB_ARGS": str(tmp_path / "args"),
        **env,
    }
    return subprocess.run(
        ["sh", str(ENTRYPOINT)],
        env=full,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_entrypoint_renders_loadable_acl_file(tmp_path: Path) -> None:
    done = _render(tmp_path, {"REDIS_EDGE_PASSWORD": "e-pass/&1", "REDIS_DATA_PASSWORD": "d-pass"})
    assert done.returncode == 0, done.stderr
    lines = (tmp_path / "users.acl").read_text(encoding="ascii").splitlines()
    assert lines[0] == "user default off"
    by_user = {line.split()[1]: line for line in lines[1:]}
    assert set(by_user) == {"edge", "data"}
    assert by_user["edge"].startswith("user edge on >e-pass/&1 ~invent:rate:*")
    assert by_user["data"].startswith("user data on >d-pass ~invent.events")
    assert (tmp_path / "args").read_text().split() == ["--aclfile", str(tmp_path / "users.acl")]
    assert (tmp_path / "users.acl").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("bad", ["", "has space"])
def test_entrypoint_refuses_missing_or_unusable_password(tmp_path: Path, bad: str) -> None:
    done = _render(tmp_path, {"REDIS_EDGE_PASSWORD": bad, "REDIS_DATA_PASSWORD": "d-pass"})
    assert done.returncode != 0
    assert "REDIS_EDGE_PASSWORD" in done.stderr
    assert bad.strip() == "" or bad not in done.stderr
    assert not (tmp_path / "args").exists()


def test_rendered_users_match_rules_file_lines(tmp_path: Path) -> None:
    done = _render(tmp_path, {"REDIS_EDGE_PASSWORD": "a", "REDIS_DATA_PASSWORD": "b"})
    assert done.returncode == 0
    rendered = {
        line.split()[1]: Acl(line.split(maxsplit=4)[4])
        for line in (tmp_path / "users.acl").read_text().splitlines()[1:]
    }
    for user, (_var, acl) in USERS.items():
        assert rendered[user].commands == acl.commands
        assert rendered[user].keys == acl.keys


def test_production_settings_require_a_redis_acl_user() -> None:
    from libs.common.settings import Settings

    with pytest.raises(ValueError, match="ACL user"):
        Settings(app_profile="production", redis_url="redis://redis:6379/0", _env_file=None)  # type: ignore[call-arg]
    ok = Settings(
        app_profile="production",
        redis_url="redis://data:pw@redis:6379/0",
        _env_file=None,  # type: ignore[call-arg]
    )
    assert ok.redis_url.startswith("redis://data:")
    local = Settings(redis_url="redis://localhost:6379/0", _env_file=None)  # type: ignore[call-arg]
    assert local.app_profile == "local"
