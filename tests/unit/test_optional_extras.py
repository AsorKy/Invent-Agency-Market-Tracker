"""Every lazy import of a heavy library maps to exactly one optional extra."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("services/ml/app", "libs/domain")

# Top-level module name -> distribution name.
HEAVY = {
    "kmodes": "kmodes",
    "igraph": "igraph",
    "leidenalg": "leidenalg",
    "yake": "yake",
    "gensim": "gensim",
    "shap": "shap",
    "hdbscan": "hdbscan",
    "umap": "umap-learn",
    "sklearn": "scikit-learn",
    "numpy": "numpy",
}
# numpy is a transitive dependency of the ml extra and is not pinned on its own.
TRANSITIVE = {"numpy": "scikit-learn"}
HEAVY_FUNCS = {"import_module", "_require"}


def _extras() -> dict[str, set[str]]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="ascii"))
    result: dict[str, set[str]] = {}
    for name, reqs in data["project"]["optional-dependencies"].items():
        result[name] = {re.split(r"[\[<>=!~ ]", r, maxsplit=1)[0].lower() for r in reqs}
    return result


def _heavy_modules(path: Path) -> set[str]:
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            arg = node.args[0]
            if fname in HEAVY_FUNCS and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names = [arg.value]
        for name in names:
            top = name.split(".")[0]
            if top in HEAVY:
                found.add(top)
    return found


def _source_files() -> list[Path]:
    files: list[Path] = []
    for d in SCAN_DIRS:
        files.extend(sorted((ROOT / d).rglob("*.py")))
    return files


def test_every_heavy_import_is_covered_by_exactly_one_extra() -> None:
    extras = _extras()
    used: set[str] = set()
    for path in _source_files():
        used |= _heavy_modules(path)
    assert used, "scan found no heavy imports; the scanner is broken"
    for module in sorted(used):
        dist = TRANSITIVE.get(module, HEAVY[module])
        owners = [name for name, dists in extras.items() if dist in dists]
        assert len(owners) == 1, f"{module} ({dist}) is in extras {owners}, expected exactly one"


def test_new_extras_exist_and_are_upper_bounded() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="ascii"))
    opt = data["project"]["optional-dependencies"]
    for extra in ("kmodes", "graph", "topics"):
        assert extra in opt
        for req in opt[extra]:
            assert "<" in req, f"{req} in extra {extra} has no upper bound"
    assert {"igraph", "leidenalg"} <= _extras()["graph"]
    assert {"yake", "gensim"} <= _extras()["topics"]


def test_modules_import_without_heavy_libs() -> None:
    modules = []
    for path in _source_files():
        rel = path.relative_to(ROOT).with_suffix("")
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        modules.append(".".join(parts))
    code = (
        "import importlib, sys\n"
        f"heavy = {sorted(HEAVY)!r}\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in heavy:\n"
        "            raise ImportError('blocked ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "for m in " + repr(modules) + ":\n"
        "    importlib.import_module(m)\n"
        "assert not [m for m in sys.modules if m.split('.')[0] in heavy]\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=90
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
