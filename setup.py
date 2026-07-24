"""Build script for veriforge.

Most metadata lives in pyproject.toml; this file exists to compile the optional
Cython extension `veriforge.sim.vm._interp_fast` at install time when
Cython is available.  If Cython is not present, the package still installs as
pure Python — the VM scheduler transparently falls back to the Python
interpreter (`vm_scheduler._HAS_CYTHON = False`).

NOTE (known issue): the Cython VM has drifted from the pure-Python interpreter
and currently fails ~18 tests under `tests/test_sim/test_bench_native.py`
(memory read-after-write divergence).  Users who hit those failures can set
the environment variable `VERIFORGE_DISABLE_CYTHON_VM=1` or delete the
built `_interp_fast.*.pyd`/`.so` to force the pure-Python path.  See
`notes/simulation/simulator_engines.md` and `notes/known_issues.md` for status.
"""

from __future__ import annotations

from setuptools import Extension, setup

ext_modules: list[Extension] = []
try:
    from Cython.Build import cythonize

    ext_modules = cythonize(
        [
            Extension(
                "veriforge.sim.vm._interp_fast",
                ["src/veriforge/sim/vm/_interp_fast.pyx"],
            ),
        ],
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
            "initializedcheck": False,
            "nonecheck": False,
        },
    )
except ImportError:
    # Cython not installed — fall back to pure-Python interpreter at runtime.
    ext_modules = []

setup(ext_modules=ext_modules)
