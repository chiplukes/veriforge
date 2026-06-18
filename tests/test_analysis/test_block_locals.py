"""Tests for procedural block-local declarations."""

from __future__ import annotations

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.model.statements import ParBlock, SeqBlock
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


def _parse_module(verilog: str):
    parser = verilog_parser(start="source_text")
    tree = parser.build_tree(verilog)
    design = tree_to_design(tree)
    assert len(design.modules) == 1
    return design.modules[0]


def test_seq_block_locals_roundtrip():
    mod = _parse_module("""
module t(input logic a, output logic y);
    always_comb begin
        logic tmp;
        tmp = ~a;
        y = tmp;
    end
endmodule
""")
    body = mod.always_blocks[0].body
    assert isinstance(body, SeqBlock)
    assert [var.name for var in body.local_vars] == ["tmp"]
    emitted = emit_module(mod)
    assert "logic tmp;" in emitted
    mod2 = _parse_module(emitted)
    body2 = mod2.always_blocks[0].body
    assert isinstance(body2, SeqBlock)
    assert [var.name for var in body2.local_vars] == ["tmp"]


def test_par_block_locals_roundtrip():
    mod = _parse_module("""
module t;
    initial fork
        logic tmp;
        tmp = 1'b1;
    join
endmodule
""")
    body = mod.initial_blocks[0].body
    assert isinstance(body, ParBlock)
    assert [var.name for var in body.local_vars] == ["tmp"]
    emitted = emit_module(mod)
    assert "fork" in emitted
    assert "logic tmp;" in emitted
    mod2 = _parse_module(emitted)
    body2 = mod2.initial_blocks[0].body
    assert isinstance(body2, ParBlock)
    assert [var.name for var in body2.local_vars] == ["tmp"]
