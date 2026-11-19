"""Enforce the layering: imports point downwards only (see README)."""

from __future__ import annotations

import ast
from pathlib import Path

import mortal

ROOT = Path(mortal.__file__).parent
ALLOWED = {
    "errors": set(),
    "config": {"errors"},
    "core": {"errors"},
    "llm": {"errors", "config"},
    "engine": {"core", "llm", "errors", "config"},
    "api": {"engine", "core", "llm", "errors", "config"},
    "cli": {"api", "engine", "core", "llm", "errors", "config"},
}


def _layer(path: Path) -> str:
    rel = path.relative_to(ROOT)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def _targets(tree: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            parts = node.module.split(".")
            if parts[0] == "mortal" and len(parts) > 1:
                out.append(parts[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "mortal" and len(parts) > 1:
                    out.append(parts[1])
    return out


def test_imports_only_point_downwards():
    violations = []
    for path in ROOT.rglob("*.py"):
        layer = _layer(path)
        if layer in {"__init__", "web"}:
            continue
        assert layer in ALLOWED, f"new top-level module {layer!r} must be assigned to a layer"
        for target in _targets(ast.parse(path.read_text(encoding="utf-8"))):
            if target != layer and target not in ALLOWED[layer]:
                violations.append(f"{path.relative_to(ROOT)} imports mortal.{target}")
    assert not violations, "\n".join(violations)


def test_relative_imports_are_not_used():
    for path in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"{path.relative_to(ROOT)} uses a relative import"
