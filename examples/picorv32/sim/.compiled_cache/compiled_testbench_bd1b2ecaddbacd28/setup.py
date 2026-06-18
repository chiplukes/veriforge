from setuptools import Extension, setup
from Cython.Build import cythonize

setup(
    ext_modules=cythonize(
        [Extension("compiled_testbench_bd1b2ecaddbacd28", ["compiled_testbench_bd1b2ecaddbacd28.pyx"])],
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
