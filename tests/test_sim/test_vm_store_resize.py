"""Direct stores apply narrow destination widths without a separate resize."""

from copy import deepcopy
from functools import lru_cache

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.vm.compiler import Compiler
from veriforge.sim.vm.opcodes import Op
from veriforge.sim.vm.vm_scheduler import _HAS_CYTHON
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


@lru_cache(maxsize=4)
def _module(source):
    return tree_to_design(verilog_parser(start="source_text").build_tree(source)).modules[0]


def _run(source, engine):
    sim = Simulator(deepcopy(_module(source)), engine=engine)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=25)
    return sim


@pytest.mark.skipif(not _HAS_CYTHON, reason="requires native VM extension")
def test_wide_rhs_to_narrow_direct_stores_preserve_values_and_unknown_masks():
    source = """module narrow_stores(input clk,
        output reg [7:0] blocking_out, nba_out,
        output reg [15:0] signed_out,
        output reg [127:0] wide_out);
        reg signed [7:0] signed_byte;
        initial begin signed_byte=-1; blocking_out=0; nba_out=0; signed_out=0; wide_out=0; end
        always @(posedge clk) begin
            blocking_out = {64'h0123456789abcdef, 64'hfedcba98765432a5};
            nba_out <= 128'h123456789abcdef0123456789abcdeX3;
            signed_out <= signed_byte;
            wide_out <= 8'hff;
        end
    endmodule"""
    native = _run(source, "vm-fast")
    names = ("blocking_out", "nba_out", "signed_out", "wide_out")
    state = tuple((native.read(name).val, native.read(name).mask) for name in names)
    for engine in ("vm", "reference"):
        other = _run(source, engine)
        assert state == tuple((other.read(name).val, other.read(name).mask) for name in names), engine
    assert native.read("blocking_out").val == 0xA5
    assert native.read("nba_out").mask == 0xF0
    assert native.read("signed_out").val == 0xFFFF
    assert native.read("wide_out").val == 0xFF


def test_compiler_keeps_resize_for_wide_destinations_only():
    source = """module store_widths(output reg [7:0] narrow, output reg [127:0] wide);
        initial begin narrow=8'h12; wide=8'h34; end
    endmodule"""
    compiler = Compiler()
    compiler.compile_module(deepcopy(_module(source)))
    instructions = [instruction for proc in compiler.processes for instruction in proc.program]
    narrow_sid = compiler.signal_map["narrow"]
    wide_sid = compiler.signal_map["wide"]
    narrow_store = next(i for i, (op, arg, _) in enumerate(instructions) if op == Op.STORE_SIG and arg == narrow_sid)
    wide_store = next(i for i, (op, arg, _) in enumerate(instructions) if op == Op.STORE_SIG and arg == wide_sid)
    assert instructions[narrow_store - 1] != (Op.RESIZE, 8, 0)
    assert instructions[wide_store - 1] == (Op.RESIZE, 128, 0)
