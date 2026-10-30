"""Conservative dependency selection for generated Cython helper functions."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile

from veriforge._env import get_env

HELPERS_BEGIN = "# vf:helpers-begin\n"
HELPERS_END = "# vf:helpers-end\n"
_DEFINITION = re.compile(r"^cdef\s+(?:inline\s+)?(?:[\w*]+\s+)+([A-Za-z_]\w*)\([^\n]*\)[^\n]*:\s*$")
_WORD = re.compile(r"\b[A-Za-z_]\w*\b")
_BEGIN = re.compile(r"^# vf:helpers-begin\r?\n", re.M)
_END = re.compile(r"^# vf:helpers-end\r?\n", re.M)


def helper_mode() -> str:
    mode = (get_env("COMPILE_HELPERS", "needed") or "needed").lower().strip()
    if mode not in {"all", "needed"}:
        raise ValueError("VERIFORGE_COMPILE_HELPERS must be 'all' or 'needed'")
    return mode


def _definitions(region: str) -> tuple[list[str], dict[str, tuple[int, int]]]:
    lines = region.splitlines(keepends=True)
    definitions = {}
    for start, line in enumerate(lines):
        match = _DEFINITION.match(line)
        if match is None:
            continue
        previous = start - 1
        while previous >= 0 and not lines[previous].strip():
            previous -= 1
        if previous >= 0 and lines[previous].startswith(("@", ")")):
            continue  # Keep decorated/unrecognized declarations conservatively.
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0].isspace() or lines[end].startswith("#")):
            end += 1
        definitions[match[1]] = (start, end)
    return lines, definitions


def _select(region: str, roots: set[str]) -> str:
    lines, definitions = _definitions(region)
    names = definitions.keys()
    occupied = {i for start, end in definitions.values() for i in range(start, end)}
    # Unrecognized definitions/imports/extern declarations stay intact. Their
    # references are roots too, so a new declaration shape cannot lose a callee.
    residual = "".join(line for i, line in enumerate(lines) if i not in occupied)
    live = (roots | set(_WORD.findall(residual))) & names
    pending = list(live)
    while pending:
        start, end = definitions[pending.pop()]
        dependencies = set(_WORD.findall("".join(lines[start:end]))) & names
        for dependency in dependencies - live:
            live.add(dependency)
            pending.append(dependency)
    dropped = {i for name, (start, end) in definitions.items() if name not in live for i in range(start, end)}
    return "".join(line for i, line in enumerate(lines) if i not in dropped)


# cm:a72e10
def select_helpers(source: str) -> str:
    """Keep the helper dependency closure, including address-taken callbacks."""
    begin = _BEGIN.search(source)
    if helper_mode() == "all" or begin is None:
        return source
    start = begin.end()
    ending = _END.search(source, start)
    if ending is None:
        raise ValueError("Unterminated generated helper region")
    end = ending.start()
    region = source[start:end]
    roots = set(_WORD.findall(source[:start])) | set(_WORD.findall(source[end:]))
    return source[:start] + _select(region, roots) + source[end:]


def select_helpers_file(path: str) -> None:
    """Rewrite only the helper region without holding a large design in RAM."""
    if helper_mode() == "all":
        return
    region_lines = []
    start = end = None
    with open(path, encoding="utf-8", newline="") as fh:
        while line := fh.readline():
            if _BEGIN.fullmatch(line):
                start = fh.tell()
                break
        if start is None:
            return
        while True:
            offset = fh.tell()
            line = fh.readline()
            if not line:
                raise ValueError("Unterminated generated helper region")
            if _END.fullmatch(line):
                end = offset
                break
            region_lines.append(line)
    region = "".join(region_lines)
    _, definitions = _definitions(region)
    roots: set[str] = set()
    with open(path, encoding="utf-8", newline="") as fh:
        in_helpers = False
        for line in fh:
            if _BEGIN.fullmatch(line):
                in_helpers = True
            elif _END.fullmatch(line):
                in_helpers = False
            elif not in_helpers:
                roots.update(set(_WORD.findall(line)) & definitions.keys())
    selected = _select(region, roots)
    if selected == region:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=Path(path).parent, delete=False) as out, open(path, "rb") as original:
            temporary = out.name
            remaining = start
            while remaining:
                chunk = original.read(min(remaining, 65536))
                out.write(chunk)
                remaining -= len(chunk)
            out.write(selected.encode("utf-8"))
            original.seek(end)
            shutil.copyfileobj(original, out)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def finalize_sources(paths: list[str]) -> str:
    """Select helpers per translation unit and hash the final build inputs."""
    digest = hashlib.sha256()
    for path in paths:
        if path.endswith(".pyx"):
            select_helpers_file(path)
        with open(path, "rb") as fh:
            while chunk := fh.read(65536):
                digest.update(chunk)
    return digest.hexdigest()
