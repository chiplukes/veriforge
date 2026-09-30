"""Large-design regression for the compiled batch runner's falling-clock check."""

import shutil
import subprocess

import pytest

from veriforge.sim.compiled.codegen import CythonCodegen
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


@pytest.mark.slow
def test_many_independent_inputs_translate_through_cython(tmp_path):
    """512 distinct assign sensitivities must not form one recursive OR tree."""
    count = 512
    ports = ["input wire clk"]
    ports.extend(f"input wire a{i}" for i in range(count))
    ports.extend(f"output wire y{i}" for i in range(count))
    assigns = [f"assign y{i} = a{i};" for i in range(count)]
    source = f"module many({', '.join(ports)});\n" + "\n".join(assigns) + "\nendmodule\n"
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))

    pyx = tmp_path / "many.pyx"
    CythonCodegen().generate_to_file(design.modules[0], str(pyx))
    cython = shutil.which("cython")
    assert cython is not None
    result = subprocess.run(
        [cython, "-3", str(pyx), "-o", str(tmp_path / "many.c")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]
