"""Compiled codegen of a memory read whose index is a nested ternary chain.

A DSL `select()` with N entries is an N-deep right-nested ternary. The
compiled emitter hoists each node into a `_et` temporary while `_et_pending`
is set; emitted without it, a node emits its false branch's value and mask
three times, so the string is 3^N long (2^N emitter calls). The sensitivity
walk and the wide-word memory copy check emitted the memory index that way,
and a 16-way chain in an index used 27 GB in codegen."""

import pytest

from veriforge.model.design import Module
from veriforge.sim.compiled.codegen import CythonCodegen
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser

DEPTH = 12


def _chain_module(data_width: int) -> Module:
    regs = " ".join(f"reg [3:0] p{c};" for c in range(DEPTH))
    chain = " : ".join(f"a == 4'd{c} ? p{c}" for c in range(DEPTH)) + " : 4'd0"
    source = (
        f"module chain(input wire [3:0] a, output wire [{data_width - 1}:0] y); {regs} "
        f"reg [{data_width - 1}:0] mem [0:15]; assign y = mem[{chain}]; endmodule"
    )
    return tree_to_design(verilog_parser(start="source_text").build_tree(source)).modules[0]


@pytest.mark.parametrize("data_width", [8, 80])
def test_ternary_chain_in_memory_index_emits_linearly(data_width, tmp_path, monkeypatch):
    """Each ternary node is emitted a bounded number of times (8 bits: the
    narrow path; 80 bits: the `_whole_assign_mem_elem` copy)."""
    calls = 0
    original = CythonCodegen._emit_ternary_value_mask_exprs

    def counting(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CythonCodegen, "_emit_ternary_value_mask_exprs", counting)
    CythonCodegen().generate_to_file(_chain_module(data_width), str(tmp_path / "chain.pyx"))
    assert calls <= 4 * DEPTH, f"{calls} ternary emissions for a {DEPTH}-deep chain"
