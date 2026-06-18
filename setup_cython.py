"""Build script for the Cython fast interpreter extension.

Usage:
    uv run python setup_cython.py build_ext --inplace

The compiled extension lands next to the .pyx source so the VM
scheduler can import it with a simple ``from ._interp_fast import ...``.
"""

from setuptools import Extension, setup

from Cython.Build import cythonize

extensions = [
    Extension(
        "veriforge.sim.vm._interp_fast",
        sources=["src/veriforge/sim/vm/_interp_fast.pyx"],
    ),
]

setup(
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
            "initializedcheck": False,
            "nonecheck": False,
        },
    ),
)
