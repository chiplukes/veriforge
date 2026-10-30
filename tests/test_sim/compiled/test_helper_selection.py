"""Dependency selection preserves callback, wide, and streaming behavior."""

import hashlib

import pytest

from veriforge.model.design import Module
from veriforge.sim.compiled._helpers import HELPERS_BEGIN, HELPERS_END, select_helpers, select_helpers_file
from veriforge.sim.compiled.codegen import CythonCodegen
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design


def test_transitive_callback_and_unrecognized_definition_are_retained(tmp_path):
    helpers = """from libc.math cimport sin
cdef inline int leaf(int x) noexcept nogil:
    return x + 1

cdef inline int callback(int x) noexcept nogil:
    return leaf(x)

cdef inline int unused(int x) noexcept nogil:
    return x - 1

cdef int multiline(
    int x,
) noexcept nogil:
    return leaf(x)

"""
    source = HELPERS_BEGIN + helpers + HELPERS_END + "cdef int (*fn)(int) noexcept nogil\nfn = callback\n"
    selected = select_helpers(source)
    assert "cdef inline int unused" not in selected
    assert "cdef inline int callback" in selected
    assert "cdef inline int leaf" in selected
    assert "cdef int multiline" in selected
    assert "from libc.math cimport sin" in selected
    path = tmp_path / "generated.pyx"
    path.write_text(source)
    select_helpers_file(str(path))
    assert path.read_text() == selected


def test_decorated_helpers_and_crlf_sources_are_conservative(tmp_path):
    source = HELPERS_BEGIN + "@decorator\ncdef int retained(int x):\n    return x\n\n" + HELPERS_END
    assert select_helpers(source) == source
    source = source.replace("\n", "\r\n")
    path = tmp_path / "crlf.pyx"
    path.write_bytes(source.encode())
    select_helpers_file(str(path))
    assert path.read_bytes().decode() == select_helpers(source) == source


def test_streamed_generation_matches_string_and_final_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("VERIFORGE_COMPILE_HELPERS", "needed")
    module = Module("empty")
    expected = CythonCodegen().generate(module)
    path = tmp_path / "empty.pyx"
    digest = CythonCodegen().generate_to_file(module, str(path))
    assert path.read_text() == expected
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    files, digest = CythonCodegen().generate_to_files(module, str(tmp_path))
    assert digest == hashlib.sha256(b"".join(open(p, "rb").read() for p in files)).hexdigest()
    assert tmp_path.joinpath("main.pyx").read_text() == expected


@pytest.mark.parametrize("width", [16, 128])
@pytest.mark.parametrize("mode", ["all", "needed"])
def test_selected_helpers_preserve_wide_memory_and_unknown_values(parser, monkeypatch, width, mode):
    monkeypatch.setenv("VERIFORGE_COMPILE_HELPERS", mode)
    source = f"""module top(input clk, wr, input [1:0] addr,
  input [{width - 1}:0] d, output [{width - 1}:0] y);
  reg [{width - 1}:0] mem [0:3];
  always @(posedge clk) if (wr) mem[addr] <= d + {width}'d1;
  assign y = mem[addr];
endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine="compiled")
    sim.drive("clk", 0)
    sim.drive("wr", 1)
    sim.drive("addr", 2)
    data = (1 << (width - 1)) + 7
    sim.drive("d", Value(data, width=width))
    sim.settle()
    sim.drive("clk", 1)
    sim.settle()
    assert int(sim.read("y")) == data + 1
    sim.drive("clk", 0)
    sim.settle()
    sim.drive("d", Value(0, mask=1 << (width - 1), width=width))
    sim.drive("clk", 1)
    sim.settle()
    assert not sim.read("y").is_defined
