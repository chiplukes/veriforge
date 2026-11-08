"""Long `&&`/`||`/comparison chains generate code linear in their length.

The value and mask formulas of `&&`, `||` and comparisons repeat their
operands' value and mask text. Expression temporaries (`_et{n}_v`/`_m`)
keep that linear, but only whole-signal assignments and `if` conditions
opened a temporaries scope. A chain written to a bit, range, part-select,
memory or concatenation target, used as a `case` selector or item, a loop
condition, a `$display` argument or an index grew 4-5x per term: an
axis_pix_correction2 loop assigning a long `&&` chain to `hit[i]` produced a
334 MB C file that MSVC ran out of heap compiling (2026-11-07).
"""

from __future__ import annotations

import pytest

from veriforge.sim.compiled.codegen import CythonCodegen
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from ..engines import ENGINES

_TERMS = 9


def _chain(op: str, n: int = _TERMS, value: str = "8'd5") -> str:
    return f" {op} ".join(f"(a{i} == {value})" for i in range(n))


_CONTEXTS = {
    "bit_select": "always_comb hit[3] = {and_};",
    "dynamic_bit_in_loop": "always_comb for (int i = 0; i < 4; i++) hit[i] = {and_i};",
    "continuous_bit": "assign hit[3] = {and_};",
    "range": "always_comb hit[7:4] = {or_};",
    "part_select": "always_comb hit[k +: 2] = {and_};",
    "nonblocking_memory": "always_ff @(posedge clk) m[k] <= {and_};",
    "memory_bit": "always_comb m[k][2] = {and_};",
    "concatenation": "assign {{p, q}} = {and_};",
    "wide_bit": "always_comb wd[k] = {and_};",
    "packed_element": "always_comb pk[1] = {and_};",
    "case_selector": "always_comb begin h = 0; case ({and_}) 1'b1: h = 1; default: h = 0; endcase end",
    "case_item": "always_comb begin h = 0; case (1'b1) ({and_}): h = 1; default: h = 0; endcase end",
    "while_condition": "always_comb begin h = 0; while (({and_}) && !h) h = 1; end",
    "display": 'always @(posedge clk) $display("%b", {and_});',
    "index": "always_comb begin hit = 0; hit[{and_}] = 1'b1; end",
    "equality_chain": "always_comb hit[3] = {eq};",
}


@pytest.mark.parametrize("context", list(_CONTEXTS))
def test_chain_codegen_is_linear(parser, context):
    ports = ", ".join(f"input logic [7:0] a{i}" for i in range(_TERMS))
    stmt = _CONTEXTS[context].format(
        and_=_chain("&&"),
        and_i=_chain("&&", value="i"),
        or_=_chain("||"),
        eq=" == ".join(f"(a{i} == 5)" for i in range(_TERMS)),
    )
    src = (
        f"module t(input logic clk, input logic [2:0] k, {ports});\n"
        "logic [23:0] hit; logic h, p, q; logic [7:0] m [0:7]; logic [99:0] wd; logic [3:0][1:0] pk;\n"
        f"{stmt}\nendmodule\n"
    )
    pyx = CythonCodegen().generate(tree_to_design(parser.build_tree(src)).modules[0])
    # About 50-100 KB now, most of it the fixed runtime; before, 9 terms
    # generated about 130 MB (`&&`) or 15 MB (`==`).
    assert len(pyx) < 400_000


_N = 6
_PORTS = ", ".join(f"input logic [7:0] a{i}" for i in range(_N))
_AND = _chain("&&", _N)
_OR = _chain("||", _N, "8'd9")
_SRC = f"""module t(input logic clk, input logic [2:0] k, {_PORTS},
         output logic [7:0] hit, output logic [1:0] pq, output logic h_case, output logic h_item,
         output logic [1:0] hix, output logic [7:0] m_out, output logic [69:0] wd);
  logic [7:0] m [0:7];
  always_comb for (int i = 0; i < 2; i++) hit[i] = (a0 != i) && {_AND};
  assign hit[2] = {_AND};
  always_comb hit[5:3] = {{2'b0, {_OR}}};
  always_comb hit[7:6] = {{({_AND}), ({_OR})}};
  assign pq = {{{_OR}, {_AND}}};
  always_comb begin
    h_case = 0;
    case ({_AND}) 1'b1: h_case = 1; 1'b0: h_case = 0; default: h_case = 1'bx; endcase
  end
  always_comb begin h_item = 0; case (1'b1) ({_OR}): h_item = 1; default: h_item = 0; endcase end
  always_comb begin hix = 0; hix[{_AND}] = 1'b1; end
  always_ff @(posedge clk) m[k] <= {{7'd0, {_AND}}} | {{6'd0, {_OR}, 1'b0}};
  assign m_out = m[k];
  always_comb begin wd = 0; wd[64 + k] = {_AND}; wd[1:0] = {{{_OR}, {_AND}}}; end
endmodule
"""

_X = Value(0, width=8, mask=0xFF)
# (a0..a5): every && term true; one false; one x with the rest true (&& is
# x); one x and one false (&& is a definite 0); an || term true beside an x.
_VECTORS = [
    [5, 5, 5, 5, 5, 5],
    [5, 5, 1, 5, 5, 5],
    [5, 5, _X, 5, 5, 5],
    [5, _X, 5, 1, 5, 5],
    [9, 5, _X, 5, 5, 5],
    [_X, 0, 0, 0, 0, 0],
]
_OUTPUTS = ("hit", "pq", "h_case", "h_item", "hix", "m_out", "wd")


def _run(parser, engine):
    sim = Simulator(tree_to_design(parser.build_tree(_SRC)).modules[0], engine=engine)
    sim.drive("clk", 0)
    results = []
    for step, vector in enumerate(_VECTORS):
        sim.drive("k", step % 8)
        for i, value in enumerate(vector):
            sim.drive(f"a{i}", value if isinstance(value, Value) else Value(value, width=8))
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        sim.drive("clk", 0)
        sim.settle()
        results.append({name: sim.read(name) for name in _OUTPUTS})
    return results


@pytest.mark.parametrize("engine", [e for e in ENGINES if e != "reference"])
def test_chain_targets_match_reference(parser, engine):
    want = _run(parser, "reference")
    got = _run(parser, engine)
    for step, (w, g) in enumerate(zip(want, got, strict=True)):
        for name in _OUTPUTS:
            assert g[name] == w[name], f"{engine} vector {step} {name}: {g[name]!r} != {w[name]!r}"


def test_reference_chain_results(parser):
    """Spot-check the reference itself, so the comparison above is not vacuous."""
    results = _run(parser, "reference")
    assert results[0]["pq"] == Value(0b01, width=2)  # && true, || false
    assert results[1]["pq"] == Value(0b00, width=2)
    assert results[2]["pq"] == Value(0, width=2, mask=0b11)  # x in both chains
    assert results[3]["pq"] == Value(0b00, width=2, mask=0b10)  # && definitely 0, || x
    assert results[4]["pq"] == Value(0b10, width=2)  # || true despite the x
