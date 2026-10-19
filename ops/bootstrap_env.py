"""Create ``.env`` for the local stack and wait for the gateway (REQ-RUN-001).

Without options the script copies ``.env.example`` to ``.env`` only when ``.env``
is missing, and fills the local-only secrets (service token, edge secret,
database passwords, n8n key, development user passwords) with values from
``secrets``. Generated values are never printed. An existing ``.env`` is left
untouched, so running it again is safe. With ``--wait`` it polls the gateway
``/ready`` endpoint until it is green and prints the service URLs. It relates to
``.env.example``, ``docker-compose.yml``, and the ``invent-tracker`` make target.
"""

import argparse
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".env.example"
TARGET = ROOT / ".env"

# Variables that receive a generated value when blank or still a placeholder.
SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "INTERNAL_SERVICE_TOKEN",
    "EDGE_SECRET",
    "N8N_ENCRYPTION_KEY",
    "BOOTSTRAP_PASSWORD_INGEST",
    "BOOTSTRAP_PASSWORD_CORE",
    "BOOTSTRAP_PASSWORD_PULSE",
    "BOOTSTRAP_PASSWORD_BRIEF",
    "BOOTSTRAP_PASSWORD_ML",
    "DEV_USER_PASSWORD_ADMIN",
    "DEV_USER_PASSWORD_STRATEGIST",
    "DEV_USER_PASSWORD_INTAKE",
    "DEV_USER_PASSWORD_BD_REP",
    "DEV_USER_PASSWORD_ANALYST",
)
PLACEHOLDERS = frozenset({"", "postgres", "local-development-token", "replace-me-for-local-use"})
DATABASE_URL_PASSWORD = re.compile(r"^(DATABASE_URL=[^:]+://[^:@/]+:)[^@]*(@)", re.MULTILINE)


def new_secret() -> str:
    """Return a URL-safe random value for local use."""
    return secrets.token_hex(16)


def fill_secrets(template: str, generate: Callable[[], str] = new_secret) -> str:
    """Return the template text with blank or placeholder secrets replaced."""
    out: list[str] = []
    database_password = ""
    for line in template.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in SECRET_KEYS and value.strip() in PLACEHOLDERS:
            value = generate()
            if key == "POSTGRES_PASSWORD":
                database_password = value
            line = f"{key}={value}"
        out.append(line)
    text = "\n".join(out) + "\n"
    if database_password:
        text = DATABASE_URL_PASSWORD.sub(lambda m: m[1] + database_password + m[2], text)
    return text


def ensure_env(
    template: Path = TEMPLATE,
    target: Path = TARGET,
    generate: Callable[[], str] = new_secret,
) -> bool:
    """Create ``target`` from ``template``; return False when it already exists."""
    if target.exists():
        return False
    target.write_text(
        fill_secrets(template.read_text(encoding="utf-8"), generate), encoding="utf-8"
    )
    target.chmod(0o600)
    return True


def read_values(path: Path) -> dict[str, str]:
    """Parse simple ``KEY=value`` lines from an env file."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip()
    return values


def service_urls(values: Mapping[str, str]) -> dict[str, str]:
    """Return the host URLs printed after start."""
    gateway = values.get("GATEWAY_PORT") or "8000"
    streamlit = values.get("STREAMLIT_PORT") or "8501"
    return {
        "Gateway": f"http://localhost:{gateway}",
        "Gateway readiness": f"http://localhost:{gateway}/ready",
        "Streamlit": f"http://localhost:{streamlit}",
        "n8n": "http://localhost:5678",
    }


def probe(url: str, timeout: float = 3.0) -> bool:
    """Return True when ``url`` answers with HTTP 200."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return bool(response.status == 200)
    except (urllib.error.URLError, OSError):
        return False


def wait_ready(
    url: str,
    timeout: float,
    interval: float = 3.0,
    check: Callable[[str], bool] = probe,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Poll ``url`` until ``check`` passes or ``timeout`` seconds elapse."""
    deadline = clock() + timeout
    while True:
        if check(url):
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def main(argv: Sequence[str] | None = None) -> int:
    """Create ``.env`` or, with ``--wait``, wait for readiness and print URLs."""
    parser = argparse.ArgumentParser(description="Prepare .env or wait for the gateway.")
    parser.add_argument("--wait", action="store_true", help="wait for the gateway /ready")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    if not args.wait:
        created = ensure_env()
        print(".env created from .env.example" if created else ".env already exists, left as is")
        return 0
    urls = service_urls(read_values(TARGET) if TARGET.exists() else {})
    if not wait_ready(urls["Gateway readiness"], args.timeout):
        print("The gateway did not become ready in time; run: docker compose logs", file=sys.stderr)
        return 1
    print("Invent Market Tracker is ready.")
    for name, url in urls.items():
        print(f"  {name}: {url}")
    print("Next steps: sign in with the development users (passwords are in .env),")
    print("see docs/runbooks, stop with 'make invent-tracker-down'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
