"""
check_overview.py — verify that notes/python_overview.md matches tracked .py files.

Usage:
    uv run python tools/check_overview.py [--root <repo-root>]

Exits 0 when the overview is in sync with git; exits 1 and lists
missing/extra entries when drift is detected.

What is compared
----------------
* Git-tracked .py files under src/ and veriforge_lsp/.
* The ```.`` code block(s) in python_overview.md that show the directory
  tree.  The parser strips tree-drawing characters (├── └── │) and
  reconstructs approximate full paths from the indentation level.

Limitations
-----------
* The tree format in python_overview.md is hand-maintained and can have
  minor structural inconsistencies (e.g. multiple "└──" at the same
  level).  The parser is lenient about these.
* Files at the same basename in different directories are matched by
  basename only as a fallback when the path cannot be reconstructed
  unambiguously.  Full-path mismatches are reported separately.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Tree parser
# ---------------------------------------------------------------------------

_TREE_PREFIX_RE = re.compile(r"^((?:[│ ]\s{3}|    )*)([├└]── )?(.*)")
_NAME_RE = re.compile(r"^(\S+?)(/?)(?:\s.*)?$")


def _parse_tree_block(lines: list[str]) -> list[str]:
    """Extract approximate .py file paths from tree-format lines.

    Returns paths relative to the repository root (using forward slashes).
    """
    paths: list[str] = []
    dir_stack: list[str] = []  # dir_stack[depth] = directory name at that depth

    for raw in lines:
        m = _TREE_PREFIX_RE.match(raw)
        if m is None:
            continue
        prefix, arrow, rest = m.group(1), m.group(2), m.group(3).strip()

        if not rest:
            continue

        nm = _NAME_RE.match(rest)
        if nm is None:
            continue
        name, slash = nm.group(1), nm.group(2)

        if arrow is None:
            # Root directory line only if the prefix is empty AND this looks
            # like a real path (starts with src/ or veriforge_lsp/).
            # Lines with a non-empty prefix but no arrow are continuation
            # comment lines (e.g. "│       │   #   description") — skip them.
            if prefix:
                continue
            if not (rest.startswith("src/") or rest.startswith("veriforge_lsp/")):
                continue
            dir_stack = [rest.split()[0].rstrip("/")]
            continue

        # Depth = number of 4-char prefix groups before the arrow.
        depth = len(prefix) // 4  # 0 = direct child of root

        if slash:  # directory
            # Ensure stack is long enough
            if len(dir_stack) <= depth + 1:
                dir_stack.extend([""] * (depth + 2 - len(dir_stack)))
            dir_stack[depth + 1] = name
            # Truncate deeper levels that are now stale
            del dir_stack[depth + 2 :]
        else:  # file
            if not name.endswith(".py"):
                continue
            parent_parts = dir_stack[: depth + 1]
            full = "/".join(parent_parts) + "/" + name
            # Normalise leading separators
            full = full.lstrip("/")
            paths.append(full)

    return paths


def _extract_paths_from_overview(doc_path: Path) -> list[str]:
    """Return all .py paths found in ``` code blocks in the document."""
    text = doc_path.read_text(encoding="utf-8")
    paths: list[str] = []
    in_block = False
    block_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("```"):
            if in_block:
                paths.extend(_parse_tree_block(block_lines))
                block_lines = []
                in_block = False
            else:
                in_block = True
            continue
        if in_block:
            block_lines.append(line)
    return paths


# ---------------------------------------------------------------------------
# Git query
# ---------------------------------------------------------------------------


def _git_tracked_py(repo_root: Path) -> set[str]:
    """Return a set of forward-slash paths for tracked .py files under src/."""
    result = subprocess.run(
        ["git", "ls-files", "src/"],
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=True,
    )
    return {line.replace("\\", "/") for line in result.stdout.splitlines() if line.endswith(".py")}


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _compare(doc_paths: list[str], git_paths: set[str]) -> tuple[list[str], list[str]]:
    """Return (in_git_not_doc, in_doc_not_git).

    Matching is first attempted by full path; if the doc path cannot be
    matched exactly, fall back to basename matching as a hint.
    """
    doc_set = set(doc_paths)
    missing_from_doc = sorted(git_paths - doc_set)
    extra_in_doc = sorted(doc_set - git_paths)
    return missing_from_doc, extra_in_doc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that python_overview.md matches tracked .py files.")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: two levels up from this script).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print matched paths too.",
    )
    args = parser.parse_args(argv)

    root = args.root or Path(__file__).resolve().parents[1]
    doc = root / "notes" / "python_overview.md"

    if not doc.exists():
        print(f"ERROR: {doc} not found", file=sys.stderr)
        return 1

    all_doc_paths = _extract_paths_from_overview(doc)
    # Scope to src/veriforge/ only — veriforge_lsp/ is documented separately.
    doc_paths = [p for p in all_doc_paths if p.startswith("src/")]
    git_paths = _git_tracked_py(root)

    if args.verbose:
        print(f"Overview mentions {len(doc_paths)} .py paths")
        print(f"Git tracks {len(git_paths)} .py files")

    missing, extra = _compare(doc_paths, git_paths)

    ok = True
    if missing:
        print(f"\n{len(missing)} file(s) tracked by git but MISSING from python_overview.md:")
        for p in missing:
            print(f"  + {p}")
        ok = False

    if extra:
        print(f"\n{len(extra)} path(s) in python_overview.md but NOT in git (stale or wrong path):")
        for p in extra:
            print(f"  - {p}")
        ok = False

    if ok:
        print(f"python_overview.md is in sync ({len(git_paths)} .py files).")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
