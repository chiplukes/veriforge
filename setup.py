"""Build script for veriforge.

Most metadata lives in pyproject.toml; this file exists to compile the optional
Cython extension `veriforge.sim.vm._interp_fast` at install time when
Cython is available.  If Cython is not present, the package still installs as
pure Python — the VM scheduler transparently falls back to the Python
interpreter (`vm_scheduler._HAS_CYTHON = False`).

The Cython VM's drift from the pure-Python interpreter (memory
read-after-write divergence, plus a batch of narrow-path signed/unsigned
opcode bugs) was fixed in work plan item 3.3 (July 2026) and is now gated in
CI by running the VM test selection twice — once with the extension built,
once with `VERIFORGE_DISABLE_CYTHON_VM=1` — and requiring both green. Any
change to `sim/vm/interpreter.py` or `sim/vm/opcodes.py` must land with the
matching `_interp_fast.pyx` change in the same commit; see
`notes/developer_guide.md` §5 and `notes/simulation/simulator_engines.md`.
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
