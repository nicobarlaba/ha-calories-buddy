"""Architectural boundary enforcement tests for the core domain."""

from __future__ import annotations

import ast
from pathlib import Path

CORE_DIR = (
    Path(__file__).resolve().parents[2]
    / "custom_components"
    / "ha_calories_buddy"
    / "core"
)


def test_core_has_zero_homeassistant_imports() -> None:
    """Verify no module inside core/ imports homeassistant."""
    py_files = sorted(CORE_DIR.rglob("*.py"))
    assert py_files, "Expected core Python files to exist"

    violations: list[str] = []
    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "homeassistant" or alias.name.startswith("homeassistant."):
                        violations.append(f"{py_file}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and (
                    node.module == "homeassistant" or node.module.startswith("homeassistant.")
                ):
                    violations.append(f"{py_file}: from {node.module} import ...")

    assert not violations, f"Found forbidden Home Assistant imports in core: {violations}"
