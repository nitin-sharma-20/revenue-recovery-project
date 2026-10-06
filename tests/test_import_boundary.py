"""
tests/test_import_boundary.py — CI enforcement of the ground-truth import boundary.

DESIGN_DECISIONS.md item 8:
  data/ground_truth.py MUST have zero imports from any app/ module.
  This test enforces that boundary mechanically — it reads the source file and
  asserts no 'from app' or 'import app' line is present.

This test costs nothing to run and prevents the most common single-file architecture
violation without relying on anyone remembering the rule.
"""

from __future__ import annotations

from pathlib import Path
import ast
import sys


GROUND_TRUTH_PATH = Path(__file__).parent.parent / "data" / "ground_truth.py"


def test_ground_truth_has_no_app_imports():
    """
    Parses data/ground_truth.py with the AST and asserts no top-level or
    nested import references any 'app' package.

    Catches both:
      import app.something
      from app.something import anything
    """
    source = GROUND_TRUTH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(GROUND_TRUTH_PATH))

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app"):
                    violations.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("app"):
                violations.append(f"line {node.lineno}: from {module} import ...")

    assert not violations, (
        "ARCHITECTURE VIOLATION (DESIGN_DECISIONS.md item 8): "
        "data/ground_truth.py imports from app/:\n"
        + "\n".join(violations)
    )


def test_classify_outcome_reason_has_single_definition():
    """
    DESIGN_DECISIONS.md item 9: there must be exactly ONE definition of
    classify_outcome_reason in the codebase.

    Scans all .py files under app/ and eval/ and counts definitions.
    Fails if zero (not implemented) or more than one (duplicate implementations).
    """
    project_root = Path(__file__).parent.parent
    search_dirs = [project_root / "app", project_root / "eval"]

    definitions = []
    for search_dir in search_dirs:
        for py_file in search_dir.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == "classify_outcome_reason":
                        definitions.append(f"{py_file}:{node.lineno}")

    assert len(definitions) == 1, (
        "DESIGN_DECISIONS.md item 9 violation: expected exactly 1 definition of "
        f"classify_outcome_reason, found {len(definitions)}:\n"
        + "\n".join(definitions)
    )
