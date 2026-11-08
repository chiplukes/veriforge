"""Memory-bounded source-object parallelism and MSVC settings for generated extensions.

The generated ``setup.py`` loads this file by path, not through the
``veriforge`` package: it imports nothing else from veriforge, so the C
compiler step neither pays for importing the whole package nor fails on an
unrelated broken module in it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys

from setuptools._distutils.ccompiler import CCompiler
from setuptools._distutils.errors import DistutilsPlatformError
from setuptools.command.build_ext import build_ext

_MIB = 1024 * 1024


def get_env(suffix: str, default: str | None = None) -> str | None:
    """``VERIFORGE_<suffix>``, else the legacy ``VERILOG_TOOLS_<suffix>``.

    (`veriforge._env.get_env` without its deprecation warning: the parent
    process already read these options, and warned, for the cache key.)
    """
    for prefix in ("VERIFORGE_", "VERILOG_TOOLS_"):
        value = os.environ.get(prefix + suffix)
        if value is not None:
            return value
    return default


def _available_memory() -> int | None:
    try:
        available = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        available = None
    # Account for container limits rather than assuming host RAM is usable.
    try:
        limit = int(Path("/sys/fs/cgroup/memory.max").read_text())
        used = int(Path("/sys/fs/cgroup/memory.current").read_text())
        remaining = max(0, limit - used)
        available = min(available, remaining) if available is not None else remaining
    except (OSError, ValueError):
        pass
    return available


def _positive_option(name: str, default: int) -> int:
    value = int(get_env(name, str(default)) or str(default))
    if value < 1:
        raise ValueError(f"VERIFORGE_{name} must be positive")
    return value


def build_worker_limit(n_sources: int) -> int:
    cpus = os.cpu_count() or 1
    try:
        cpus = min(cpus, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        pass
    cap = _positive_option("COMPILE_JOBS", 4)
    per_worker = _positive_option("COMPILE_WORKER_MEMORY_MB", 1024) * _MIB
    available = _available_memory()
    explicit_budget = get_env("COMPILE_MEMORY_MB")
    budget = _positive_option("COMPILE_MEMORY_MB", 1) * _MIB if explicit_budget else (available or 0) // 2
    if available is not None:
        budget = min(budget, available)
    return max(1, min(n_sources, cpus, cap, max(1, budget // per_worker)))


# cm:c519a2
def compile_objects(compiler: CCompiler, sources, *args, **kwargs):
    """Parallelize only the independent object jobs of the base compile loop.

    Setup/dependency discovery stays serial. Compiler overrides such as MSVC's
    custom compile method retain their existing serial behavior.
    """
    original = compiler.compile
    workers = build_worker_limit(len(sources))
    # setuptools loads the same code under two module aliases. Compare code
    # objects, not function identity, while leaving custom compile loops alone.
    implementation = getattr(getattr(original, "__func__", None), "__code__", None)
    if workers <= 1 or implementation != CCompiler.compile.__code__ or compiler.compiler_type != "unix":
        return original(sources, *args, **kwargs)
    compile_one = compiler._compile
    futures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:

        def submit(*job_args, **job_kwargs):
            futures.append(pool.submit(compile_one, *job_args, **job_kwargs))

        compiler._compile = submit
        try:
            objects = original(sources, *args, **kwargs)
        finally:
            compiler._compile = compile_one
        for future in futures:
            future.result()
    return objects


# Whole-program optimization flags setuptools gives MSVC. A generated
# extension is one translation unit, so cross-module inlining has nothing to
# work with; ``/GL`` only defers all code generation to a ``/LTCG`` link step
# that holds the whole program's intermediate code in memory at once.
_MSVC_WHOLE_PROGRAM_FLAGS = frozenset({"/GL", "/LTCG"})


def _initialize_msvc(compiler: CCompiler, plat_name: str | None) -> None:
    """``compiler.initialize()``, with the 64-bit-hosted toolset when there is one.

    setuptools always sets up the 32-bit-hosted cross compiler
    (``vcvarsall x86_amd64``) for a 64-bit target, so it works with Visual
    Studio installs that lack the 64-bit-hosted tools. That cl.exe has a
    32-bit address space and ran out of heap (C1060) on a large generated
    design. Ask for the native ``amd64`` toolset on a 64-bit host, and fall
    back to setuptools' choice when vcvarsall cannot provide it.
    """
    module = sys.modules.get(type(compiler).__module__)
    get_spec = getattr(module, "_get_vcvars_spec", None)
    if get_spec is None:  # (another setuptools layout: keep its behavior)
        compiler.initialize(plat_name)
        return

    def native_spec(host_platform: str, platform: str) -> str:
        if host_platform == "win-amd64" and platform == "win-amd64":
            return "amd64"
        return get_spec(host_platform, platform)

    module._get_vcvars_spec = native_spec  # type: ignore[union-attr]
    try:
        compiler.initialize(plat_name)
    except DistutilsPlatformError:
        module._get_vcvars_spec = get_spec  # type: ignore[union-attr]
        compiler.initialize(plat_name)
    finally:
        module._get_vcvars_spec = get_spec  # type: ignore[union-attr]


# cm:c519a3
def configure_msvc(compiler: CCompiler, plat_name: str | None = None) -> None:
    """MSVC settings for a generated extension; other compilers are left alone.

    - the 64-bit-hosted compiler when available (see `_initialize_msvc`);
    - ``/bigobj``: a large generated file has more sections than the
      default object format holds (C1128);
    - no ``/GL`` or ``/LTCG`` (see `_MSVC_WHOLE_PROGRAM_FLAGS`).

    MSVC sets its flag lists in ``initialize()``, which otherwise runs
    lazily on the first compile; run it now so there is something to edit.
    """
    if compiler.compiler_type != "msvc":
        return
    if not getattr(compiler, "initialized", True):
        _initialize_msvc(compiler, plat_name)
    for name in ("compile_options", "compile_options_debug"):
        flags = getattr(compiler, name, None)
        if isinstance(flags, list) and "/bigobj" not in flags:
            flags.append("/bigobj")
    ldflags = getattr(compiler, "_ldflags", {})
    flag_lists = [getattr(compiler, "compile_options", None), getattr(compiler, "compile_options_debug", None)]
    for flags in [*flag_lists, *ldflags.values()]:
        if isinstance(flags, list):
            flags[:] = [flag for flag in flags if flag not in _MSVC_WHOLE_PROGRAM_FLAGS]


class ParallelBuildExt(build_ext):
    def build_extensions(self):
        configure_msvc(self.compiler, self.plat_name)
        original = self.compiler.compile

        def compile_sources(sources, *args, **kwargs):
            # compile_objects must see the real bound method, not this wrapper.
            self.compiler.compile = original
            try:
                return compile_objects(self.compiler, sources, *args, **kwargs)
            finally:
                self.compiler.compile = compile_sources

        self.compiler.compile = compile_sources
        self.parallel = None
        try:
            super().build_extensions()
        finally:
            self.compiler.compile = original
