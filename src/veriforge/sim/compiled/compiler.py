"""Runtime .pyx → .so/.pyd compilation with caching.

Compiles Cython source strings into importable extension modules at runtime.
Compiled binaries are cached by a hash of the source, Python version, Cython
version, and platform tag so that subsequent runs skip compilation.

Cache location
--------------
By default, compiled extensions are stored in ``.cycache/`` under the current
working directory.  Override with the ``VERILOG_TOOLS_COMPILE_CACHE``
environment variable::

    VERILOG_TOOLS_COMPILE_CACHE=/tmp/my_cache uv run pytest ...

Cache invalidation
------------------
The cache key is a SHA-256 hash of:

* The generated .pyx source (which embeds module structure and signal layout).
* The installed Cython version.
* The Python platform tag (version, OS, architecture).
* ``_CACHE_VERSION`` — bumped manually whenever codegen changes break existing .pyd files.

The compiled_scheduler also hashes ``codegen.py`` and ``elaborate.py`` into
the elaboration cache key (``_codegen_infra_hash``), so changing those files
automatically invalidates all elab cache entries on the next run.

Disabling caching
-----------------
Set ``VERILOG_TOOLS_NO_COMPILE_CACHE=1`` to bypass the cache entirely.
Every call to ``compile_pyx`` will recompile from scratch.  Useful for
debugging codegen changes without manually clearing the cache.

Clearing the cache
------------------
Call ``CythonCompiler().clear_cache()`` to remove all cached extensions::

    from veriforge.sim.compiled.compiler import CythonCompiler
    removed = CythonCompiler().clear_cache()
    print(f"Removed {removed} cache entries")

If a cached extension fails to load, the stale entry is automatically removed
and the module is recompiled.

Usage
-----
    compiler = CythonCompiler()
    mod = compiler.compile_pyx(pyx_source, "my_module")
    result = mod.my_function(42)
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import platform
import shutil
import subprocess
import sys

try:
    from filelock import FileLock as _FileLock
except ImportError:  # pragma: no cover — filelock is a test/bench dep
    _FileLock = None  # type: ignore[assignment,misc]

log = logging.getLogger(__name__)

# Bump this whenever the codegen framework changes in a way that
# makes previously cached .pyd/.so files incompatible.
_CACHE_VERSION = "5"


class _nullctx:
    """No-op context manager — used when filelock is not available."""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _default_cache_dir() -> str:
    """Return the default cache directory for compiled extensions.

    Resolution order:
    1. ``VERILOG_TOOLS_COMPILE_CACHE`` environment variable.
    2. ``.cycache/`` under the current working directory.
    """
    env = os.environ.get("VERILOG_TOOLS_COMPILE_CACHE")
    if env:
        return env
    return os.path.join(os.getcwd(), ".cycache")


def _cython_version() -> str:
    """Return installed Cython version string, or '' if unavailable."""
    try:
        import Cython

        return Cython.__version__
    except ImportError:
        return ""


def _platform_tag() -> str:
    """Return a platform tag for cache keying."""
    return f"{sys.platform}-{platform.machine()}-py{sys.version_info.major}.{sys.version_info.minor}"


def _cache_key(source: str) -> str:
    """Compute a cache key from source + environment + framework version."""
    h = hashlib.sha256()
    h.update(source.encode("utf-8"))
    h.update(_cython_version().encode("utf-8"))
    h.update(_platform_tag().encode("utf-8"))
    h.update(_CACHE_VERSION.encode("utf-8"))
    return h.hexdigest()[:16]


def _module_name_key(module_name: str) -> str:
    """Return a compact stable token for *module_name*.

    The full module name can be very long for parametrized compiled tests,
    which pushes Windows linker artifact paths over the filesystem limit once
    setuptools adds nested build directories and ABI suffixes.
    """
    return hashlib.sha256(module_name.encode("utf-8")).hexdigest()[:8]


def _keyed_module_name(module_name: str, key: str) -> str:
    """Return the compact deterministic extension module name for a cache key."""
    return f"vtc_{_module_name_key(module_name)}_{key}"


def _find_extension(directory: str, module_name: str) -> str | None:
    """Find a compiled extension file (.pyd or .so) in directory."""
    if not os.path.isdir(directory):
        return None
    for fname in os.listdir(directory):
        # Match module_name.cpython-312-x86_64-linux-gnu.so or module_name.pyd
        if fname.startswith(module_name) and (fname.endswith(".pyd") or fname.endswith(".so")):
            return os.path.join(directory, fname)
    return None


class CythonCompiler:  # cm:3d7f4a
    """Compile .pyx source strings to importable Python extension modules.

    Args:
        cache_dir: Directory for compiled extension cache.
                   Defaults to ``.cycache/`` under the current working
                   directory.
                   Override via ``VERILOG_TOOLS_COMPILE_CACHE`` env var.
    """

    __slots__ = ("_cache_dir",)

    def __init__(self, cache_dir: str | None = None) -> None:
        self._cache_dir = cache_dir or _default_cache_dir()

    @property
    def cache_dir(self) -> str:
        return self._cache_dir

    def compile_pyx(self, source: str, module_name: str) -> object:
        """Compile a .pyx source string and return the imported module.

        If a cached build exists for this source + environment, it is
        loaded directly without recompilation.  If loading a cached
        extension fails, the stale entry is removed and recompiled.

        Set ``VERILOG_TOOLS_NO_COMPILE_CACHE=1`` to skip caching entirely.

        Args:
            source:      Complete .pyx file contents.
            module_name: Python module name for the extension.

        Returns:
            The imported extension module.

        Raises:
            RuntimeError: If Cython or a C compiler is not available,
                          or if compilation fails.
        """
        no_cache = os.environ.get("VERILOG_TOOLS_NO_COMPILE_CACHE", "") == "1"

        key = _cache_key(source)
        # Use key in the actual module name to allow multiple versions to coexist
        keyed_name = _keyed_module_name(module_name, key)
        build_dir = os.path.join(self._cache_dir, keyed_name)
        needs_compiled_sim = "CompiledSim" in source

        # Acquire a per-module file lock so that concurrent workers (e.g.
        # pytest-xdist) never compile the same module simultaneously.  A second
        # worker that arrives while the first is compiling will block here,
        # then find a warm cache entry after the lock is released.
        lock_path = build_dir + ".lock"
        os.makedirs(self._cache_dir, exist_ok=True)
        lock_ctx = _FileLock(lock_path) if _FileLock is not None else None

        with lock_ctx if lock_ctx is not None else _nullctx():
            # Check cache (unless caching is disabled)
            if not no_cache:
                cached = _find_extension(build_dir, keyed_name)
                if cached is not None:
                    try:
                        mod = self._import_extension(cached, keyed_name)
                        if needs_compiled_sim and not hasattr(mod, "CompiledSim"):
                            raise AttributeError(
                                f"Cached module {keyed_name} is missing CompiledSim "
                                f"— cache entry is corrupt, forcing recompile"
                            )
                        log.debug("Cache hit: %s", cached)
                        return mod
                    except Exception as e:
                        log.warning("Stale/corrupt cache entry %s — removing and recompiling: %s", keyed_name, e)
                        self._remove_build_dir(build_dir)

            # Cache miss (or no-cache mode) — compile
            log.info("Compiling %s (cache key %s)", module_name, key)
            self._remove_build_dir(build_dir)
            os.makedirs(build_dir, exist_ok=True)

            pyx_path = os.path.join(build_dir, f"{keyed_name}.pyx")
            with open(pyx_path, "w", encoding="utf-8") as f:
                f.write(source)

            # Delete any stale .c file so cythonize is forced to regenerate it.
            # Without this, cythonize may skip Cython if the .c has the same
            # modification timestamp as the just-written .pyx (Windows 1-second
            # timestamp granularity) and produce a .pyd without CompiledSim.
            c_path = os.path.join(build_dir, f"{keyed_name}.c")
            if os.path.exists(c_path):
                try:
                    os.remove(c_path)
                except OSError:
                    pass

            setup_source = self._generate_setup_py(keyed_name)
            setup_path = os.path.join(build_dir, "setup.py")
            with open(setup_path, "w", encoding="utf-8") as f:
                f.write(setup_source)

            self._run_build(build_dir)

            ext_path = _find_extension(build_dir, keyed_name)
            if ext_path is None:
                raise RuntimeError(
                    f"Compilation succeeded but no extension found in {build_dir}. "
                    f"Expected {keyed_name}.pyd or {keyed_name}*.so"
                )

            mod = self._import_extension(ext_path, keyed_name)
            if needs_compiled_sim and not hasattr(mod, "CompiledSim"):
                raise RuntimeError(
                    f"Compiled module {keyed_name} is missing CompiledSim. "
                    f"The Cython source may have been truncated during translation. "
                    f"Try deleting {build_dir!r} and rerunning."
                )
            return mod

    def _generate_setup_py(self, module_name: str) -> str:
        """Generate a minimal setup.py for building the extension."""
        return f"""\
from setuptools import Extension, setup
from Cython.Build import cythonize

setup(
    ext_modules=cythonize(
        [Extension("{module_name}", ["{module_name}.pyx"])],
        compiler_directives={{
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
            "initializedcheck": False,
            "nonecheck": False,
        }},
    ),
)
"""

    def _run_build(self, build_dir: str) -> None:
        """Run the build command in the given directory."""
        try:
            import Cython  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "Cython is required for the compiled engine. Install it with: pip install cython"
            ) from None

        cmd = [sys.executable, "setup.py", "build_ext", "--inplace", "--build-temp", "t", "--build-lib", "l"]
        log.debug("Running: %s in %s", cmd, build_dir)

        # On Windows, MSVC (cl.exe) is a child of setup.py and inherits the
        # stdout/stderr pipes.  When subprocess.run's timeout fires it kills
        # setup.py but cl.exe may still hold the pipe handles, causing
        # communicate() to block indefinitely.  We use Popen directly and
        # kill the whole process tree (via taskkill /T on Windows) to ensure
        # all pipe handles are closed before we read the output.
        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                cwd=build_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            raise RuntimeError("Failed to run build command. Ensure Python is accessible.") from None

        try:
            stdout, stderr = proc.communicate(timeout=600)  # cm:3d7f4b
        except subprocess.TimeoutExpired:
            # Kill the full process tree so all pipe handles are closed.
            if platform.system() == "Windows":
                subprocess.run(  # noqa: S603
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    check=False,
                )
            proc.kill()
            proc.wait()
            raise RuntimeError(
                f"Cython compilation timed out after 300 s in {build_dir}. "
                "The generated module may be too large for the C compiler. "
                "Use engine='vm' for complex designs."
            ) from None

        if proc.returncode != 0:
            # Extract the most useful error info
            stderr = stderr or ""
            stdout = stdout or ""
            # Check for missing C compiler
            if "error: Microsoft Visual C++" in stderr or "Unable to find vcvarsall" in stderr:
                raise RuntimeError(
                    "No C compiler found. On Windows, install Visual Studio Build Tools.\n"
                    "Alternatively, use engine='vm' which does not require a C compiler."
                )
            if "gcc" in stderr and ("not found" in stderr or "No such file" in stderr):
                raise RuntimeError("No C compiler (gcc) found. Install gcc or use engine='vm'.")
            raise RuntimeError(
                f"Cython compilation failed (exit code {proc.returncode}).\n"
                f"stderr: {stderr[-2000:]}\nstdout: {stdout[-2000:]}"
            )

    def _import_extension(self, ext_path: str, module_name: str) -> object:
        """Import a compiled extension from a file path."""
        # module_name already includes the cache key, so it's unique per source.
        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, ext_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load extension from {ext_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def load_cached(self, keyed_name: str) -> object | None:
        """Load a previously compiled extension by its keyed name.

        Returns the imported module, or None if not found or load fails.
        """
        build_dir = os.path.join(self._cache_dir, keyed_name)
        ext_path = _find_extension(build_dir, keyed_name)
        if ext_path is None:
            return None
        try:
            return self._import_extension(ext_path, keyed_name)
        except Exception:
            log.debug("Failed to load cached extension %s", keyed_name)
            return None

    @staticmethod
    def _remove_build_dir(build_dir: str) -> None:
        """Remove a build directory, ignoring errors (e.g. locked .pyd on Windows)."""
        if os.path.isdir(build_dir):
            try:
                shutil.rmtree(build_dir)
            except OSError:
                log.debug("Cannot remove %s (files may be in use)", build_dir)

    def clear_cache(self) -> int:
        """Remove all cached compiled extensions.

        On Windows, directories containing loaded .pyd files cannot be
        removed while the process holds them. Those entries are skipped.

        Returns:
            Number of cache entries removed.
        """
        if not os.path.isdir(self._cache_dir):
            return 0
        entries = os.listdir(self._cache_dir)
        removed = 0
        for entry in entries:
            path = os.path.join(self._cache_dir, entry)
            if os.path.isdir(path):
                try:
                    shutil.rmtree(path)
                    removed += 1
                except OSError:
                    log.debug("Cannot remove %s (files may be in use)", path)
        return removed
