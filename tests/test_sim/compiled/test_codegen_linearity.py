"""Generated code grows linearly with expression nesting, for every construct in every context.

Value and mask formulas refer to their operands' text more than once (a `?:`
repeats its false branch three times; `!x`, `x[i]`, `m[i]`, `a >> x` read `x`'s
value and mask), so nested text grows exponentially unless each node is
emitted once and long text goes to a temporary (`_emit_expr`). That held for
only a few node types, and only where the emitting code had opened a
temporaries scope; each blow-up was found in a real design and fixed for that
construct in that context: an ibex CSR comparison chain, an 8-term `&&` into
`hit[i]` (334 MB of C), a 16-way select as a memory index (27 GB). A scan
of these constructs and contexts (2026-11-08) found 343 of 1,523 pairs
growing 5-26x per level, and 20 more exhausting 3 GB at depth 7.

Each case nests a construct 3, 5 and 7 deep and compares the growth of the
code size and of the number of emitter calls (an emission that is discarded,
as in a sensitivity walk, only shows in the second): linear growth gives a
ratio of 2, quadratic 2.7, a doubling per level 9. The suite runs with
`VERIFORGE_CODEGEN_STRICT=1`, so a construct emitted outside any scope fails
here as well (`_emit_unscoped`).
"""

from __future__ import annotations

import warnings

import pytest

from veriforge.sim.compiled.codegen import CythonCodegen
from veriforge.sim.elaborate import flatten_module
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

_DEPTHS = (3, 5, 7)
_MAX_RATIO = 3.0
_SMALL_GROWTH = 4000  # bytes from depth 3 to 7

# Each wraps X, a 4-bit expression, into a 4-bit expression using X once.
_NARROW = {
    "tern": "((a == 4'd{k}) ? p{k} : ({X}))",
    "tern_cond": "((({X}) != 4'd0) ? p{k} : a)",
    "land": "{{3'b0, (({X}) != 4'd0) && p{k}[0]}}",
    "lor": "{{3'b0, (({X}) == 4'd0) || p{k}[1]}}",
    "eq": "{{3'b0, ({X}) == p{k}}}",
    "ceq": "{{3'b0, ({X}) === p{k}}}",
    "lt": "{{3'b0, ({X}) < p{k}}}",
    "add": "(({X}) + p{k})",
    "mul": "(({X}) * p{k})",
    "div": "(({X}) / p{k})",
    "mod": "(({X}) % p{k})",
    "pow": "(({X}) ** p{k}[1:0])",
    "shl": "(({X}) << p{k}[1:0])",
    "shr_by": "(p{k} >> ({X}))",
    "ashr": "$unsigned($signed({X}) >>> p{k}[1:0])",
    "and": "(({X}) & p{k})",
    "xor": "(({X}) ^ p{k})",
    "not": "(~({X}) ^ p{k})",
    "neg": "(-({X}) + p{k})",
    "lnot": "{{3'b0, !({X})}}",
    "redxor": "{{3'b0, ^({X})}}",
    "redand": "{{3'b0, &({X})}}",
    "mem": "(lut[{X}] ^ p{k})",
    "bsel": "{{3'b0, w16[{X}]}}",
    "psel": "w64[({X}) * 4 +: 4]",
    "func": "f(({X}), p{k})",
    "signed_add": "$unsigned($signed({X}) + $signed(p{k}))",
}
# The same with a 100-bit X.
_WIDE = {
    "w_tern": "((a == 4'd{k}) ? q{k} : ({X}))",
    "w_tern_fill": "((a == 4'd{k}) ? '0 : ({X}))",
    "w_tern_fill1": "((a == 4'd{k}) ? ({X}) : '1)",
    "w_tern_cond": "((({X}) != 100'd0) ? q{k} : '0)",
    "w_add": "(({X}) + q{k})",
    "w_xor": "(({X}) ^ q{k})",
    "w_eq": "{{99'b0, ({X}) == q{k}}}",
    "w_land": "{{99'b0, (({X}) != 100'd0) && q{k}[0]}}",
    "w_shl": "(({X}) << p{k}[1:0])",
    "w_mul": "(({X}) * q{k})",
    "w_not": "(~({X}) ^ q{k})",
    "w_redor": "{{99'b0, |({X})}}",
    "w_func": "wf(({X}), q{k})",
}
# Where E (the nested expression, W bits) is used.
_CONTEXTS = {
    "cont": "assign y = {E};",
    "cont_range": "assign z[11:4] = {E};",
    "cont_bit": "assign z[1] = ({E}) != 0;",
    "cont_concat": "assign {{y1, y2}} = {E};",
    "comb": "always_comb y = {E};",
    "comb_dyn_bit": "always_comb begin z = 0; z[a] = ({E}) != 0; end",
    "comb_dyn_psel": "always_comb begin z = 0; z[a +: 4] = {E}; end",
    "comb_concat": "always_comb {{y1, y2}} = {E};",
    "ff": "always_ff @(posedge clk) r <= {E};",
    "ff_mem_data": "always_ff @(posedge clk) wm[a] <= {E};",
    "if": "always_comb if (({E}) != 0) y = 1; else y = 2;",
    "case_sel": "always_comb case ({E}) 1: y = 1; 2: y = 3; default: y = 2; endcase",
    "case_item": "always_comb case (a) {E}: y = 1; default: y = 2; endcase",
    "for": "always_comb begin y = 0; for (int i = 0; i < 4 && i < ({E}); i++) y = y + 1; end",
    "while": "always_comb begin y = 0; while (y < 4 && y < ({E})) y = y + 1; end",
    "display": 'always @(posedge clk) $display("%d", {E});',
    "func_arg": "assign y = f({E}, a);",
    "task_arg": "always_comb begin tk({E}, y); end",
    "func_body": "function automatic [W-1:0] g(input [3:0] aa); g = {E}; endfunction assign y = g(a);",
    "generate": "for (genvar gi = 0; gi < 2; gi++) begin : G assign zg[gi] = ({E}) != 0; end",
    "nba_if": "always_ff @(posedge clk) if (({E}) != 0) r <= 1;",
    "struct_field": "assign sv.f = {E};",
    "wide_concat_part": "assign yw = {{q0[99:W], {E}}};",
    "wide_concat_part_comb": "always_comb yw = {{q0[99:W], {E}}};",
}
# Contexts that need a 4-bit E (an index).
_INDEX_CONTEXTS = {
    "mem_index_cont": "assign y = lut[{E}];",
    "mem_index_comb": "always_comb y = lut[{E}];",
    "mem_index_ff": "always_ff @(posedge clk) r <= lut[{E}];",
    "mem_write_index": "always_ff @(posedge clk) wm2[{E}] <= a;",
    "wide_mem_index_cont": "assign yw = wlut[{E}];",
    "wide_mem_index_comb": "always_comb yw = wlut[{E}];",
    "bsel_index": "assign z[0] = w16[{E}];",
    "psel_index": "assign y = w64[({E}) * 4 +: 4];",
    "wide_psel_index": "assign yw = wide_src[({E}) * 4 +: 100];",
    "repeat": "always_comb begin y = 0; repeat ({E}) y = y + 1; end",
    "lhs_bit_idx": "always_comb begin z = 0; z[{E}] = 1; end",
    "cont_lhs_bit_idx": "assign z[{E}] = a[0];",
    "cont_lhs_psel_idx": "assign z[{E} +: 2] = a[1:0];",
    "concat_lhs_mem_idx": "assign {{wm2[{E}][3:2], z[1:0]}} = a;",
    "concat_lhs_bit_idx": "assign {{z[{E}], y1[1:0]}} = a[2:0];",
    "mem_lhs_range": "always_ff @(posedge clk) wm2[{E}][2:1] <= a[1:0];",
}
# The compiled engine has no >64-bit support for these yet (it says so).
_UNSUPPORTED = {
    ("w_tern_fill", "ff_mem_data"),
    ("w_tern_fill1", "ff_mem_data"),
    ("w_tern_cond", "ff_mem_data"),
    ("w_tern_fill", "display"),
    ("w_tern_fill1", "display"),
    ("w_tern_cond", "display"),
    ("w_func", "case_sel"),
    ("w_func", "case_item"),
    ("w_func", "while"),
}
# Run by default; the rest of the cross product with --run-slow.
_FAST_CONTEXTS = {"cont", "comb", "if", "mem_index_cont", "wide_concat_part", "concat_lhs_bit_idx"}
_FAST_CONSTRUCTS = {"tern", "land", "eq", "mem", "lnot", "psel", "func", "w_tern", "w_tern_fill", "w_land"}


def _cases():
    for context in (*_CONTEXTS, *_INDEX_CONTEXTS):
        constructs = [*_NARROW, *(_WIDE if context in _CONTEXTS else ())]
        for construct in constructs:
            if (construct, context) in _UNSUPPORTED:
                continue
            fast = context in _FAST_CONTEXTS or construct in _FAST_CONSTRUCTS
            marks = () if fast else (pytest.mark.slow,)
            yield pytest.param(construct, context, id=f"{construct}-{context}", marks=marks)


def _source(construct: str, context: str, depth: int) -> str:
    wide = construct in _WIDE
    template = _WIDE[construct] if wide else _NARROW[construct]
    expr = "q0" if wide else "a"
    for k in range(depth):
        expr = template.format(X=expr, k=k % 8)
    w = 100 if wide else 4
    body = {**_CONTEXTS, **_INDEX_CONTEXTS}[context]
    body = body.replace("[W-1:0]", f"[{w - 1}:0]").replace("99:W]", f"99:{min(w, 99)}]").format(E=expr)
    ports = ", ".join(f"input [3:0] p{k}" for k in range(8)) + ", " + ", ".join(f"input [99:0] q{k}" for k in range(8))
    return f"""
module t(input clk, input [3:0] a, {ports}, input [15:0] w16, input [63:0] w64, input [499:0] wide_src,
         output logic [{w - 1}:0] y, output logic [{w - 1}:0] y1, output logic [{w - 1}:0] y2,
         output logic [15:0] z, output logic [99:0] yw, output logic [1:0] zg);
  typedef struct packed {{ logic [{w - 1}:0] f; logic [3:0] g; }} s_t;
  reg [3:0] lut [0:15];
  reg [99:0] wlut [0:15];
  reg [{w - 1}:0] wm [0:15];
  reg [3:0] wm2 [0:15];
  reg [{w - 1}:0] r;
  s_t sv;
  function automatic [3:0] f(input [3:0] x, input [3:0] v); f = x ^ v; endfunction
  function automatic [99:0] wf(input [99:0] x, input [99:0] v); wf = x ^ v; endfunction
  task automatic tk(input [{w - 1}:0] x, output [{w - 1}:0] o); o = x; endtask
  {body}
endmodule
"""


@pytest.fixture(scope="module")
def source_parser():
    return verilog_parser(start="source_text")


def _measure(source_parser, monkeypatch, source: str) -> tuple[int, int]:
    calls = 0

    def counting(original):
        def wrapper(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            return original(self, *args, **kwargs)

        return wrapper

    with monkeypatch.context() as patch:
        for name in ("_emit_expr_node", "_emit_mask_expr_node", "_emit_py_expr_node", "_emit_py_mask_expr_node"):
            patch.setattr(CythonCodegen, name, counting(getattr(CythonCodegen, name)))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            design = tree_to_design(source_parser.build_tree(source))
            flat = flatten_module(design.get_module("t"), design=design)
            text = CythonCodegen().generate(flat)
    return len(text), calls


def _growth(values: list[int]) -> float:
    first, second = values[1] - values[0], values[2] - values[0]
    return second / first if first > 0 else (1.0 if second == 0 else float("inf"))


@pytest.mark.parametrize(("construct", "context"), list(_cases()))
def test_codegen_grows_linearly_with_nesting(source_parser, monkeypatch, construct, context):
    sizes, calls = zip(*(_measure(source_parser, monkeypatch, _source(construct, context, d)) for d in _DEPTHS))
    # (A few hundred bytes per level can step past the length at which text
    # goes to a temporary: no ratio for growth that small.)
    assert sizes[2] - sizes[0] < _SMALL_GROWTH or _growth(list(sizes)) < _MAX_RATIO, (
        f"code size {sizes} at depths {_DEPTHS}"
    )
    assert _growth(list(calls)) < _MAX_RATIO, f"emitter calls {calls} at depths {_DEPTHS}"
