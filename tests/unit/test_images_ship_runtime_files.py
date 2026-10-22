"""Check the Python image ships the files that code reads by repository path (REQ-DEP-001).

Parses ``deploy/Dockerfile.python`` and ``.dockerignore`` without Docker. The funnel
checklists and the validation thresholds are not package data, so the image must copy
them and import ``libs`` from ``/app`` (not from site-packages) for the paths to resolve.
"""

import fnmatch
import re
from pathlib import Path, PurePosixPath

from libs.domain import funnel, validation

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "Dockerfile.python"
DOCKERIGNORE = ROOT / ".dockerignore"


def _workdir_and_copies() -> tuple[str, list[str], str]:
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    workdir = "/"
    copies: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "WORKDIR":
            workdir = parts[1]
        elif parts[0] == "COPY" and not any(p.startswith("--from") for p in parts):
            copies.extend(p for p in parts[1:-1] if not p.startswith("--"))
    match = re.search(r"PYTHONPATH=(\S+)", text)
    return (
        workdir,
        [c.strip("./").rstrip("/") or "." for c in copies],
        match.group(1) if match else "",
    )


def _ignored(rel: str) -> bool:
    for raw in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        pattern = raw.strip().strip("/")
        if not pattern or pattern.startswith("#") or pattern.startswith("!"):
            continue
        parts = PurePosixPath(rel).parts
        for i in range(1, len(parts) + 1):
            if fnmatch.fnmatch("/".join(parts[:i]), pattern) or fnmatch.fnmatch(
                parts[i - 1], pattern
            ):
                return True
    return False


def _runtime_files() -> list[str]:
    files = [str(validation.THRESHOLDS_PATH.relative_to(ROOT))]
    files += [str(p.relative_to(ROOT)) for p in sorted(funnel.CHECKLIST_DIR.glob("*.yaml"))]
    return files


def test_runtime_files_exist_in_repo() -> None:
    files = _runtime_files()
    assert "libs/domain/validation_thresholds.yaml" in files
    assert len([f for f in files if f.startswith("config/funnel/")]) >= 7


def test_runtime_files_are_copied_and_not_ignored() -> None:
    _, copies, _ = _workdir_and_copies()
    for rel in _runtime_files():
        covered = any(rel == c or rel.startswith(c + "/") for c in copies)
        assert covered, f"{rel} is read at runtime by path but no COPY in the Dockerfile covers it"
        assert not _ignored(rel), f"{rel} is read at runtime by path but .dockerignore excludes it"


def test_code_resolves_to_app_not_site_packages() -> None:
    workdir, copies, pythonpath = _workdir_and_copies()
    assert "libs" in copies
    # funnel.CHECKLIST_DIR is parents[2] of libs/domain/funnel.py, i.e. the import root.
    assert pythonpath.split(":")[0] == workdir, (
        "set PYTHONPATH to the WORKDIR so libs is imported from there and the repository "
        "relative config path exists; a pip-installed copy in site-packages has no config/"
    )
