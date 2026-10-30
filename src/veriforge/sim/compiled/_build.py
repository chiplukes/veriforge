"""Memory-bounded source-object parallelism for generated extensions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path

from setuptools._distutils.ccompiler import CCompiler
from setuptools.command.build_ext import build_ext

from veriforge._env import get_env

_MIB = 1024 * 1024


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


class ParallelBuildExt(build_ext):
    def build_extensions(self):
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
