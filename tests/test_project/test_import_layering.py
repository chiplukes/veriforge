"""Enforce the package-dependency DAG for `src/veriforge` (architecture review item 6).

Walks every ``.py`` file under ``src/veriforge`` with :mod:`ast`, extracts
*module-level* imports only (imports nested inside a function or method body
are the sanctioned lazy-import pattern used throughout this codebase to break
otherwise-unavoidable cycles -- see e.g. ``sim/cosim.py``'s deferred
``from ..project import parse_files`` -- and are intentionally not checked
here), and asserts every cross-subpackage edge is one this test explicitly
allows.

If this test fails, the failure message names the offending file, line, and
import -- either the import is architecturally wrong (fix the code) or it's
a legitimate new dependency (add it to ``ALLOWED_EDGES`` below, or to
``ALLOWED_FILE_EDGES`` if it needs file-level precision like the sim/dsl
bridge does).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "veriforge"

# Top-level entry points allowed to import anything -- not subject to the DAG.
ENTRY_POINTS = {"__init__", "__main__"}

# Small leaf utility modules with no dependents-of-concern; any package may
# import them, and they aren't part of the DAG themselves.
UTILITY_MODULES = {"_env", "_version"}

# Coarse top-level-subpackage DAG. Keys are source subpackages; values are
# the set of subpackages they may import from at module level. A subpackage
# not listed here (e.g. `model`, `preprocessor`, `lark_file`) has no allowed
# cross-package imports (an empty ceiling) unless added below.
#
# This was derived by running this test's own edge-extraction logic against
# the tree and encoding what legitimately remained after work plan item 4.1
# broke the project<->scaffold and sim<->dsl cycles -- it is a ceiling (what
# is architecturally sanctioned), not a requirement (a package need not use
# everything it's allowed to).
ALLOWED_EDGES: dict[str, set[str]] = {
    "analysis": {"model", "semantics"},
    "codegen": {"model"},
    "convert": {"model"},
    "transforms": {"model"},
    "lark_file": {"model", "preprocessor"},
    "verilog_parser": {"preprocessor"},
    "project": {"model", "transforms", "verilog_parser", "preprocessor", "analysis"},
    "dsl": {"model", "analysis", "project"},  # `sim` is handled by ALLOWED_FILE_EDGES below
    "sim": {"model", "analysis", "semantics"},  # `dsl` is handled by ALLOWED_FILE_EDGES below
    "refactor": {"model", "analysis", "codegen"},
    "semantics": {"model"},
    "scaffold": {"model", "analysis", "transforms", "verilog_parser", "preprocessor", "project", "sim", "dsl"},
}

# File-level exceptions for the sim <-> dsl boundary, which the coarse
# per-package DAG above is deliberately too blunt to express precisely:
#   - `sim.bench.*` legitimately depends on `dsl` (its job is bridging
#     DSL-built designs into the simulator: `lowering.py` builds DSL Module
#     objects for native lowering, `skeleton.py` generates testbench
#     wrappers using DSL expression helpers).
#   - `dsl/testbench.py` is a backward-compatible re-export shim with no
#     real logic (see notes/architecture.md, "sim ↔ dsl: now acyclic") --
#     it is the one legitimate `dsl -> sim` edge, restricted to exactly the
#     module it shims.
# Anything else (e.g. `sim/evaluator.py` importing `dsl`, or some other
# `dsl/*.py` importing `sim.evaluator`) must fail: those are exactly the
# kinds of edges that recreate the cycle item 4.1 broke.
ALLOWED_FILE_EDGES: dict[str, set[str]] = {
    "dsl/testbench.py": {"sim.bench.skeleton", "sim"},
}


def _allowed_file_edge(rel_path: str, resolved: str) -> bool:
    if rel_path.startswith("sim/bench/") and resolved.split(".")[0:2] == ["veriforge", "dsl"]:
        return True
    if rel_path.startswith("sim/bench/") and resolved == "veriforge.dsl":
        return True
    allowed_targets = ALLOWED_FILE_EDGES.get(rel_path)
    if allowed_targets is None:
        return False
    target = resolved.removeprefix("veriforge.")
    return any(target == t or target.startswith(t + ".") for t in allowed_targets)


def _top_package(rel_path: str) -> str:
    """Map a path relative to src/veriforge to its top-level subpackage name."""
    parts = rel_path.split("/")
    if parts[0].endswith(".py"):
        return parts[0][:-3]
    return parts[0]


def _resolve_import(module: str, level: int, rel_path: str) -> str:
    """Resolve a (possibly relative) import target to an absolute dotted path."""
    if level == 0:
        return module
    # `rel_path` is relative to src/veriforge, e.g. "sim/bench/lowering.py".
    containing_dir_parts = rel_path.split("/")[:-1]
    up = level - 1
    if up > 0:
        containing_dir_parts = containing_dir_parts[:-up] if up <= len(containing_dir_parts) else []
    base = "veriforge." + ".".join(containing_dir_parts) if containing_dir_parts else "veriforge"
    return f"{base}.{module}" if module else base


def _module_level_imports(path: Path) -> list[tuple[str, int, int]]:
    """Return (resolved_module, lineno, level) for each module-level import.

    Only walks `tree.body` (top-level statements) -- imports nested inside a
    function or class body (the sanctioned lazy-import pattern) are not
    visited, matching a plain `ast.walk` would over-collect.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    results = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((alias.name, node.lineno, 0))
        elif isinstance(node, ast.ImportFrom):
            results.append((node.module or "", node.lineno, node.level))
    return results


def _iter_source_files():
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_import_layering():
    violations: list[str] = []
    for path in _iter_source_files():
        rel_path = path.relative_to(SRC_ROOT).as_posix()
        src_top = _top_package(rel_path)
        if src_top in ENTRY_POINTS or src_top in UTILITY_MODULES:
            continue
        for module, lineno, level in _module_level_imports(path):
            resolved = _resolve_import(module, level, rel_path)
            if resolved == "veriforge" or not resolved.startswith("veriforge"):
                continue  # package-root or external import
            target_parts = resolved.split(".")
            target_top = target_parts[1] if len(target_parts) > 1 else target_parts[0].removeprefix("veriforge")
            if target_top == src_top or target_top in UTILITY_MODULES:
                continue  # intra-package or leaf utility
            allowed = target_top in ALLOWED_EDGES.get(src_top, set())
            if not allowed:
                allowed = _allowed_file_edge(rel_path, resolved)
            if not allowed:
                violations.append(
                    f"{rel_path}:{lineno}: `{src_top}` imports `{resolved}` "
                    f"(top-level `{target_top}`) -- not in ALLOWED_EDGES['{src_top}'] "
                    f"or ALLOWED_FILE_EDGES; add it there if legitimate, or fix the import."
                )

    assert not violations, "Forbidden module-level import(s) found:\n" + "\n".join(violations)


# Names that must only ever be *defined* in semantics.py (work plan item 4.2,
# Phase G) -- everywhere else should import from there instead of keeping its
# own copy. An `import ... as _const_int` (etc.) local name binding is fine;
# this only flags a `def`/nested `def`.
_SEMANTICS_OWNED_NAMES = {"_const_int", "_range_width", "_var_width"}


def _defined_function_names(path: Path) -> list[tuple[str, int]]:
    """Return (name, lineno) for every function def (top-level or nested) in *path*."""
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        (node.name, node.lineno) for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def test_no_duplicate_semantics_helpers_outside_semantics_module():
    """Work plan item 4.2, Phase G: `_const_int`/`_range_width`/`_var_width`
    must only be defined in `semantics.py` -- every other engine/analysis
    consumer delegates via `from ...semantics import const_int as _const_int`
    (etc.), not its own copy. See notes/plans/work_plan_2026-07.md item 4.2."""
    violations: list[str] = []
    for path in _iter_source_files():
        rel_path = path.relative_to(SRC_ROOT).as_posix()
        if rel_path == "semantics.py":
            continue
        for name, lineno in _defined_function_names(path):
            if name in _SEMANTICS_OWNED_NAMES:
                violations.append(
                    f"{rel_path}:{lineno}: defines `{name}` -- delegate to `semantics.{name.lstrip('_')}` instead"
                )

    assert not violations, "Duplicate semantics helper definition(s) found:\n" + "\n".join(violations)
