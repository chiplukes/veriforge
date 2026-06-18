"""Auto-discovery of SystemVerilog dependencies for testbench scaffolding.

When a top-level cell instantiates child modules defined in sibling files,
the generated bench scaffold needs to parse all of them into one Design so
the simulator can elaborate the hierarchy. :func:`discover_sv_dependencies`
walks the DUT instance graph against a search path and returns the ordered
list of file paths required.

The discovery is intentionally simple and conservative:

* Only ``.sv`` and ``.v`` files in the supplied search directories are
  considered candidates.
* Each candidate is parsed once; its module-name set is recorded.
* The DUT's instance graph is walked recursively; any module whose name
  is not already part of the DUT file is looked up in the candidate map.
* Unresolved instances (likely vendor IP, packages, or tech cells) are
  silently skipped — the user can always hand-edit the generated DEPS list.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from veriforge.analysis.resolver import link_instances
from veriforge.model.design import Design
from veriforge.project import parse_file

log = logging.getLogger(__name__)


def _candidate_files(search_dirs: Iterable[Path]) -> list[Path]:
    """Collect ``.sv`` / ``.v`` files from each search directory (non-recursive)."""
    files: list[Path] = []
    seen: set[Path] = set()
    for d in search_dirs:
        if not d.is_dir():
            continue
        for ext in ("*.sv", "*.v"):
            for p in sorted(d.glob(ext)):
                rp = p.resolve()
                if rp in seen:
                    continue
                seen.add(rp)
                files.append(p)
    return files


def _module_index(files: Iterable[Path]) -> dict[str, Path]:
    """Build a ``module_name -> file_path`` map by parsing each candidate file."""
    index: dict[str, Path] = {}
    for f in files:
        try:
            d = parse_file(f)
        except Exception:
            log.debug("Failed to index %s for dep discovery", f, exc_info=True)
            continue
        for name in (m.name for m in d.modules):
            index.setdefault(name, f)
    return index


def discover_sv_dependencies(  # noqa: PLR0912, PLR0915
    dut_path: str | Path,
    *,
    top_module: str | None = None,
    search_dirs: Iterable[str | Path] | None = None,
) -> tuple[list[Path], Design]:
    """Discover the SV files required to elaborate ``dut_path``.

    Args:
        dut_path: Path to the top-level DUT source file.
        top_module: Optional explicit top module name. When ``None``, the
            DUT file's sole top module is used.
        search_dirs: Directories to scan for child modules. Defaults to
            the DUT file's parent directory.

    Returns:
        Tuple of ``(deps, design)`` where ``deps`` is the ordered list of
        dependency files (excluding the DUT itself) and ``design`` is the
        merged :class:`Design` containing all parsed modules.
    """
    dut_path = Path(dut_path)
    if search_dirs is None:
        search_dirs_list = [dut_path.parent]
    else:
        search_dirs_list = [Path(d) for d in search_dirs]

    candidates = [p for p in _candidate_files(search_dirs_list) if p.resolve() != dut_path.resolve()]
    name_to_path = _module_index(candidates)

    dut_design = parse_file(dut_path)
    if top_module is None:
        tops = dut_design.get_top_modules()
        if len(tops) == 1:
            top_module = tops[0].name
        elif len(dut_design.modules) == 1:
            top_module = dut_design.modules[0].name
        else:
            msg = "top_module is required when the DUT file defines multiple modules"
            raise ValueError(msg)

    if dut_design.get_module(top_module) is None:
        msg = f"Top module {top_module!r} not found in {dut_path}"
        raise ValueError(msg)

    merged = Design()
    merged.merge(dut_design)

    deps: list[Path] = []
    deps_seen: set[Path] = set()
    pending = [top_module]
    visited: set[str] = set()

    while pending:
        modname = pending.pop()
        if modname in visited:
            continue
        visited.add(modname)
        module = merged.get_module(modname)
        if module is None:
            src = name_to_path.get(modname)
            if src is None:
                continue
            rp = src.resolve()
            if rp not in deps_seen:
                deps_seen.add(rp)
                deps.append(src)
            try:
                file_design = parse_file(src)
            except Exception:
                log.debug("Failed to parse dependency %s", src, exc_info=True)
                continue
            merged.merge(file_design)
            module = merged.get_module(modname)
            if module is None:
                continue
        for inst in module.instances:
            child = inst.module_name
            if child and child not in visited:
                pending.append(child)

    if merged.modules:
        link_instances(merged)

    return deps, merged
