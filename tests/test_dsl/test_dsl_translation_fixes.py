"""Regressions for DSL translation bugs found by fuzzing the DSL paths
(builder -> emitter, Verilog -> to_dsl -> DSL). See test_dsl_fuzz.py."""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.codegen.verilog_formatter import format_module
from veriforge.convert.to_dsl import _expr_to_python, module_to_dsl
from veriforge.dsl import Module, cast, cat, lit, reduce_nand, reduce_nor, reduce_xnor, unsupported
from veriforge.model.expressions import Literal
from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


def _parse(text: str):
    return tree_to_design(verilog_parser(start="source_text").build_tree(text))


def _sim(text: str):
    design = _parse(text)
    return Simulator(design.modules[0], engine="reference", design=design)


def _round_trip(text: str, module_var: str = "m") -> str:
    """Verilog -> DSL code -> executed -> emitted Verilog."""
    code = module_to_dsl(_parse(text).modules[0], module_var=module_var)
    namespace: dict = {}
    exec(code, namespace)  # noqa: S102
    return emit_module(namespace["module"])


def _outputs(text: str, drives: list[dict[str, int]], outs: list[str]) -> list[list[str]]:
    sim = _sim(text)
    result = []
    for d in drives:
        for k, v in d.items():
            sim.drive(k, v)
        sim.settle()
        result.append([str(sim.read(o)) for o in outs])
    return result


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decl", ["int", "int unsigned"])
@pytest.mark.parametrize("emit", [emit_module, format_module])
def test_for_loop_keeps_its_own_variable_declaration(decl, emit):
    # `for (int i ...)` was emitted as `for (i = ...)`, so the loop used an
    # outer `i` (or an undeclared one).
    text = f"""module t(output reg [7:0] y);
  integer i;
  always @* begin
    i = 100;
    y = 0;
    for ({decl} i = 0; i < 3; i = i + 1) y = y + 1;
    y = y + i;
  end
endmodule
"""
    out = emit(_parse(text).modules[0])
    assert f"for ({decl} i = 0;" in out
    assert _outputs(out, [{}], ["y"]) == _outputs(text, [{}], ["y"]) == [["8'b01100111"]]


# ---------------------------------------------------------------------------
# DSL builder
# ---------------------------------------------------------------------------


def test_nested_case_reusing_the_as_name_raises():
    # The outer case's later items used to join the inner case silently.
    m = Module("t")
    a = m.input("a", width=2)
    y = m.output_reg("y", width=2)
    with pytest.raises(RuntimeError, match="already ended"):  # noqa: PT012
        with m.always():
            with m.case(a) as c:
                with c.when(0):
                    with m.case(a) as c:
                        with c.when(1):
                            y.now = 1
                with c.when(1):
                    y.now = 2


def test_statement_directly_in_case_body_raises():
    # It used to land in the enclosing block, before the case.
    m = Module("t")
    a = m.input("a", width=2)
    y = m.output_reg("y", width=2)
    with pytest.raises(RuntimeError, match="inside a 'when' or 'default'"):  # noqa: PT012
        with m.always():
            with m.case(a) as c:
                y.now = 3
                with c.when(1):
                    y.now = 2


def test_lit_cast_and_negated_reductions():
    m = Module("t")
    a = m.input("a", width=8)
    y = m.output("y", width=16)
    z = m.output("z", width=3)
    m.assign(y, cast(4, a) + lit("16'hff00"))
    m.assign(z, cat(reduce_nor(a), reduce_nand(a), reduce_xnor(a)))
    text = emit_module(m.build())
    assert "4'(a)" in text and "16'hff00" in text
    assert "~|a" in text and "~&a" in text and ("~^a" in text or "^~a" in text)
    # ~|a is a 1-bit reduction (not `~(|a)`, which a wider context widens).
    assert _outputs(text, [{"a": 0x0F}, {"a": 0}], ["y", "z"]) == [
        ["16'b1111111100001111", "3'b011"],
        ["16'b1111111100000000", "3'b111"],
    ]


def test_lit_rejects_non_literals():
    with pytest.raises(Exception):  # noqa: B017, PT011
        lit("a + 1")


def test_unsupported_raises_with_description():
    with pytest.raises(NotImplementedError, match="can't express while loop"):
        unsupported("while loop")


@pytest.mark.parametrize(
    ("kind", "decl"),
    [("int", "int x;"), ("integer", "integer x;"), ("byte", "byte x;"), ("logic", "logic [3:0] x;")],
)
def test_reg_kinds(kind, decl):
    m = Module("t")
    x = m.reg("x", width=4, kind=kind)
    y = m.output_reg("y", width=8)
    with m.always():
        x.now = 3
        y.now = x
    assert decl in emit_module(m.build())


# ---------------------------------------------------------------------------
# Verilog -> DSL
# ---------------------------------------------------------------------------


def test_nested_case_round_trip():
    text = """module t(input [1:0] a, input [1:0] b, output reg [3:0] y);
  always @* begin
    y = 0;
    case (a)
      0: case (b) 0: y = 1; 1: y = 2; endcase
      1: y = 3;
      default: y = 4;
    endcase
  end
endmodule
"""
    drives = [{"a": a, "b": b} for a in range(4) for b in range(4)]
    assert _outputs(_round_trip(text), drives, ["y"]) == _outputs(text, drives, ["y"])


def test_empty_bodies_convert():
    # `if (a) ;` / `1'b1: ;` left a `with` line with no body (SyntaxError).
    text = """module t(input a, input b, output reg y);
  always @* begin
    y = 0;
    if (a) ; else y = 1;
    case (b) 1'b1: ; default: y = 1; endcase
  end
endmodule
"""
    drives = [{"a": a, "b": b} for a in range(2) for b in range(2)]
    assert _outputs(_round_trip(text), drives, ["y"]) == _outputs(text, drives, ["y"])


def test_module_var_is_used_in_statements():
    text = """module t(input clk, input a, output reg y);
  always @(posedge clk) if (a) y <= 1; else y <= 0;
endmodule
"""
    code = module_to_dsl(_parse(text).modules[0], module_var="mod")
    assert "with mod.if_(" in code and "with mod.else_()" in code
    assert "m." not in code.replace("mod.", "")
    _round_trip(text, module_var="mod")


@pytest.mark.parametrize(
    ("verilog", "drives"),
    [
        # Sized literals kept their value but lost their width: 2'b11 -> 3.
        ("assign y = {2'b11, a};", [{"a": 5}]),
        # Wide decimals came out as Python strings.
        ("assign y = 40'd1099511627775 >> a;", [{"a": 30}]),
        # x/z literals.
        ("assign y = a == 8'bx ? 16'd1 : 16'd2;", [{"a": 0}]),
        # `~|a` became `~reduce_or(a)`, a context-widened NOT.
        ("assign y = ~|a;", [{"a": 0}, {"a": 1}]),
        ("assign y = ~&a + ~^a;", [{"a": 0xFF}, {"a": 3}]),
    ],
)
def test_expression_round_trips(verilog, drives):
    text = f"module t(input [7:0] a, output [15:0] y);\n  {verilog}\nendmodule\n"
    assert _outputs(_round_trip(text), drives, ["y"]) == _outputs(text, drives, ["y"])


def test_int_and_logic_kinds_round_trip():
    # `int` became a 1-bit reg and `logic` a wire.
    text = """module t(input [7:0] a, output [31:0] y, output [3:0] z);
  int x;
  logic [3:0] w;
  always @* begin x = a - 200; w = a[3:0]; end
  assign y = x >>> 1;
  assign z = w;
endmodule
"""
    drives = [{"a": 7}, {"a": 250}]
    assert _outputs(_round_trip(text), drives, ["y", "z"]) == _outputs(text, drives, ["y", "z"])


@pytest.mark.parametrize(
    "body",
    [
        "always @* for (integer i = 0; i < 2; i = i + 1) y = y + 1;",
        "always @* begin y = 0; while (y < 3) y = y + 1; end",
        "task tk; y = 1; endtask\n  always @* tk;",
        "always @* y = {<<{a}};",
    ],
)
def test_unsupported_constructs_raise_instead_of_running_wrong(body):
    # A for loop used to run its body once; task calls were dropped.
    text = f"module t(input [7:0] a, output reg [7:0] y);\n  {body}\nendmodule\n"
    with pytest.raises(NotImplementedError, match="The DSL can't express"):
        _round_trip(text)


def test_literal_without_source_text():
    neg = Literal(-1, width=8)
    assert _expr_to_python(neg) == 'lit("8\'d255")'  # was 8'd1 (abs of the value)
    wide = Literal(0xDEAD, width=16)
    assert _expr_to_python(wide) == 'lit("16\'hdead")'
    assert _expr_to_python(Literal(-5)) == "-5"
