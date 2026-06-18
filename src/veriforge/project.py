"""Multi-file Verilog project support.

Parse directories of Verilog files into a unified Design and resolve
cross-module references.

Usage::

    from veriforge.project import parse_files, parse_directory

    # Parse specific files
    design = parse_files(["top.v", "alu.v", "regfile.v"])

    # Parse a directory (recursively)
    design = parse_directory("rtl/", extensions=[".v", ".sv"])

    # Simulate from project
    from veriforge.sim.testbench import Simulator
    top = design.get_top_modules()[0]
    sim = Simulator(top, design=design)

Testbench building and DSL export helpers live in :mod:`veriforge.scaffold`:

    from veriforge.scaffold import build_testbench, export_dsl_project

For backward compatibility these names are also re-exported from this module::

    from veriforge.project import build_testbench   # still works
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
from pathlib import Path

from .model.design import Design
from .transforms.comment_extractor import extract_comments
from .transforms.tree_to_model import tree_to_design
from .verilog_parser import verilog_parser

log = logging.getLogger(__name__)

# Default file extensions to scan
DEFAULT_EXTENSIONS: tuple[str, ...] = (".v", ".sv", ".vh", ".svh")

# ── Parse cache infrastructure ───────────────────────────────────

# Files whose content affects parse output.  If any of these change,
# all cached results are invalidated automatically.
_PARSER_INFRA_GLOBS = [
    "lark_file/verilog.lark",
    "verilog_parser.py",
    "preprocessor.py",
    "transforms/tree_to_model.py",
    "model/*.py",
]

_parser_hash_cache: str | None = None


def _compute_parser_hash() -> str:
    """SHA-256 over all parser infrastructure files."""
    global _parser_hash_cache  # noqa: PLW0603
    if _parser_hash_cache is not None:
        return _parser_hash_cache
    pkg_dir = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for glob in _PARSER_INFRA_GLOBS:
        for p in sorted(pkg_dir.glob(glob)):
            h.update(p.read_bytes())
    _parser_hash_cache = h.hexdigest()
    return _parser_hash_cache


def _strip_parent_refs(design: Design) -> None:
    """Set all .parent refs to None for pickle-safe serialization."""
    for node in design.walk():
        node.parent = None
        node._parse_tree = None


def _rebuild_parent_refs(design: Design) -> None:
    """Rebuild .parent back-references after loading from cache."""
    for container in (*design.modules, *design.interfaces, *design.packages):
        for child in container._child_nodes():
            child.parent = container


def _cache_load(cache_path: Path) -> Design | None:
    """Load a cached Design, returning None on any failure."""
    try:
        with cache_path.open("rb") as f:
            design = pickle.load(f)  # noqa: S301
        _rebuild_parent_refs(design)
        return design
    except Exception:
        log.debug("Cache load failed: %s", cache_path, exc_info=True)
        return None


def _cache_save(cache_path: Path, design: Design) -> None:
    """Save a Design to the cache (parent refs stripped temporarily)."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        _strip_parent_refs(design)
        with cache_path.open("wb") as f:
            pickle.dump(design, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        log.debug("Cache save failed: %s", cache_path, exc_info=True)
    finally:
        _rebuild_parent_refs(design)


def _cache_cleanup(cache_dir: Path, parser_hash_prefix: str) -> None:
    """Remove stale cache files from previous parser versions."""
    try:
        for p in cache_dir.glob("*.pickle"):
            if not p.name.startswith(parser_hash_prefix):
                p.unlink(missing_ok=True)
    except Exception:
        log.debug("Cache cleanup failed for %s", cache_dir, exc_info=True)


def _path_hash(path: Path) -> str:
    """Return a stable hash for the normalized absolute file path."""
    normalized = os.path.normcase(str(path.resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def parse_file(  # cm:e5b6f8
    path: str | Path,
    *,
    comments: bool = True,
    preprocess: bool = False,
    defines: dict[str, str] | None = None,
    include_paths: list[str | Path] | None = None,
    _parser: "verilog_parser | None" = None,
) -> Design:
    """Parse a single Verilog file into a Design.

    Args:
        path: Path to a Verilog source file.
        comments: Whether to extract and attach comments.
        preprocess: Whether to run the Verilog preprocessor first.
        defines: Initial macro definitions for the preprocessor.
        include_paths: Directories to search for `include files.

    Returns:
        Design containing all modules/interfaces/packages from the file.
    """
    path = Path(path)
    source_text = path.read_text(encoding="utf-8")
    source_file = str(path)

    parse_text = source_text
    if preprocess:
        from .preprocessor import preprocess as run_preprocess

        parse_text = run_preprocess(  # type: ignore[assignment]
            parse_text,
            defines=defines,
            include_paths=include_paths,
            source_file=source_file,
        )

    comment_list = None
    if comments:
        parse_text, comment_list = extract_comments(parse_text, source_file)

    parser = _parser if _parser is not None else verilog_parser(start="verilog")
    tree = parser.build_tree(parse_text)

    return tree_to_design(
        tree,
        source_file=source_file,
        comments=comment_list,
        source_text=source_text,
    )


def parse_files(  # noqa: PLR0913  # cm:2d1a3c
    paths: list[str | Path],
    *,
    comments: bool = True,
    analyze: bool = True,
    preprocess: bool = False,
    defines: dict[str, str] | None = None,
    include_paths: list[str | Path] | None = None,
    cache_dir: str | Path | None = None,
) -> Design:
    """Parse multiple Verilog files into a single unified Design.

    Files are parsed individually and merged. Duplicate module/interface/
    package names are silently deduplicated (first definition wins).

    Args:
        paths: List of file paths to parse.
        comments: Whether to extract and attach comments.
        analyze: Whether to run instance linking after merging.
        preprocess: Whether to run the Verilog preprocessor on each file.
        defines: Initial macro definitions for the preprocessor.
        include_paths: Directories to search for `include files.
        cache_dir: Directory for per-file parse cache (None to disable).

    Returns:
        Unified Design containing all parsed definitions.
    """
    if not paths:
        return Design()

    # Set up cache if requested
    use_cache = cache_dir is not None
    cache_path_dir: Path | None = None
    parser_hash_prefix = ""
    if use_cache:
        assert cache_dir is not None
        cache_path_dir = Path(cache_dir)
        parser_hash = _compute_parser_hash()
        parser_hash_prefix = parser_hash[:16]
        _cache_cleanup(cache_path_dir, parser_hash_prefix)

    merged = Design()
    cache_hits = 0
    for raw_path in paths:
        p = Path(raw_path)
        if not p.is_file():
            log.warning("Skipping non-existent file: %s", p)
            continue

        # Try cache lookup
        if use_cache:
            assert cache_path_dir is not None
            source_bytes = p.read_bytes()
            source_hash = hashlib.sha256(source_bytes).hexdigest()[:16]
            cache_file = cache_path_dir / f"{parser_hash_prefix}_{_path_hash(p)}_{source_hash}_{p.stem}.pickle"
            cached = _cache_load(cache_file)
            if cached is not None:
                merged.merge(cached)
                cache_hits += 1
                continue

        try:
            file_design = parse_file(
                p,
                comments=comments,
                preprocess=preprocess,
                defines=defines,
                include_paths=include_paths,
            )
            if use_cache:
                _cache_save(cache_file, file_design)
            merged.merge(file_design)
        except Exception:
            log.exception("Failed to parse %s", p)
            raise

    if analyze and merged.modules:
        from .analysis.resolver import link_instances

        link_instances(merged)

    log.info(
        "Parsed %d files (%d cached): %d modules, %d interfaces, %d packages",
        len(merged.source_files),
        cache_hits,
        len(merged.modules),
        len(merged.interfaces),
        len(merged.packages),
    )
    return merged


def parse_directory(  # noqa: PLR0913  # cm:7b9e5f
    directory: str | Path,
    *,
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
    recursive: bool = True,
    comments: bool = True,
    analyze: bool = True,
    exclude: list[str] | None = None,
    preprocess: bool = False,
    defines: dict[str, str] | None = None,
    include_paths: list[str | Path] | None = None,
    cache_dir: str | Path | None = None,
) -> Design:
    """Parse all Verilog files in a directory into a unified Design.

    Args:
        directory: Root directory to scan.
        extensions: File extensions to include (case-insensitive).
        recursive: Whether to scan subdirectories.
        comments: Whether to extract and attach comments.
        analyze: Whether to run instance linking after merging.
        exclude: List of glob patterns to exclude (e.g. ["*_tb.v", "testbench/"]).
        preprocess: Whether to run the Verilog preprocessor on each file.
        defines: Initial macro definitions for the preprocessor.
        include_paths: Directories to search for `include files.

    Returns:
        Unified Design containing all parsed definitions.
    """
    directory = Path(directory)
    if not directory.is_dir():
        msg = f"Not a directory: {directory}"
        raise FileNotFoundError(msg)

    ext_lower = {e.lower() for e in extensions}
    glob_fn = directory.rglob if recursive else directory.glob

    paths: list[str | Path] = sorted(p for p in glob_fn("*") if p.is_file() and p.suffix.lower() in ext_lower)

    if exclude:
        from fnmatch import fnmatch

        def _excluded(p: str | Path) -> bool:
            rel = str(Path(p).relative_to(directory))
            return any(fnmatch(rel, pat) or fnmatch(Path(p).name, pat) for pat in exclude)

        paths = [p for p in paths if not _excluded(p)]

    if not paths:
        log.warning("No Verilog files found in %s", directory)
        return Design()

    log.info("Found %d Verilog files in %s", len(paths), directory)
    return parse_files(
        paths,
        comments=comments,
        analyze=analyze,
        preprocess=preprocess,
        defines=defines,
        include_paths=include_paths,
        cache_dir=cache_dir,
    )


# ── Backward-compatible re-exports ───────────────────────────────────────────
# The functions below now live in veriforge.scaffold.  They are re-exported
# here so that existing code using ``from veriforge.project import …``
# continues to work without modification.

from .scaffold import (  # noqa: E402
    build_testbench,
    build_testbench_plan,
    export_dsl_project,
    generate_python_testbench_skeleton,
)

__all__ = [
    "DEFAULT_EXTENSIONS",
    "build_testbench",
    "build_testbench_plan",
    "export_dsl_project",
    "generate_python_testbench_skeleton",
    "parse_directory",
    "parse_file",
    "parse_files",
]
