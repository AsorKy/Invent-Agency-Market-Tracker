"""Unit tests for the workflow and secret guards (REQ-ORC-001, REQ-SEC-004)."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, ROOT / "ops" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


workflows = _load("check_workflows")
configuration = _load("check_configuration")


def _write(directory: Path, name: str, nodes: list[dict[str, object]]) -> None:
    (directory / name).write_text(json.dumps({"nodes": nodes}), encoding="utf-8")


def test_repository_workflows_pass() -> None:
    assert workflows.check_directory(workflows.WORKFLOW_DIR) == {}


def test_code_node_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", [{"name": "x", "type": "n8n-nodes-base.code"}])
    assert "a.json" in workflows.check_directory(tmp_path)


def test_business_logic_node_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", [{"name": "x", "type": "n8n-nodes-base.if"}])
    assert "a.json" in workflows.check_directory(tmp_path)


def test_literal_credential_is_rejected(tmp_path: Path) -> None:
    node = {
        "name": "call",
        "type": "n8n-nodes-base.httpRequest",
        "parameters": {"headers": [{"name": "Authorization", "value": "Bearer abc"}]},
    }
    _write(tmp_path, "a.json", [node])
    assert "a.json" in workflows.check_directory(tmp_path)


def test_env_credential_is_accepted(tmp_path: Path) -> None:
    node = {
        "name": "call",
        "type": "n8n-nodes-base.httpRequest",
        "parameters": {"headers": [{"name": "Authorization", "value": "=Bearer {{$env.T}}"}]},
    }
    _write(tmp_path, "a.json", [node])
    assert workflows.check_directory(tmp_path) == {}


def test_secret_scan_reports_counts_only(tmp_path: Path) -> None:
    fake = "gh" + "p_" + "A1b2C3d4" * 5
    leaky = tmp_path / "leaky.txt"
    leaky.write_text(f"token = {fake}\n", encoding="utf-8")
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing here\n", encoding="utf-8")
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\0" + fake.encode())
    findings = configuration.scan_for_secrets([leaky, clean, binary])
    assert findings == {leaky: 1}
    assert fake not in repr(findings)


def test_repository_has_no_secret_like_values() -> None:
    files = configuration.list_repository_files(ROOT)
    assert configuration.scan_for_secrets(files) == {}
