"""Direct tests for ``src/veriforge/semantics.py`` (work plan item 4.2, Phase B).

Ports the Phase A fixture (see ``test_semantics_parity.py``) into tests of
the new unified module, plus new coverage for ``expr_width``/``expr_signed``
(outside Phase A's scope, since those weren't part of the parity
characterization — see that file's module docstring).
"""

from __future__ import annotations

from veriforge.analysis import analyze_design
from veriforge.analysis.resolver import link_instances, resolve_port_connections
from veriforge.model.expressions import BinaryOp, Identifier, Literal, Range, TernaryOp, UnaryOp
from veriforge.sim.elaborate import _build_param_env
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser
from veriforge.semantics import const_int, expr_signed, expr_width, net_width, range_width, var_width

_SRC = """
module top #(
    parameter W = 8,
    parameter N = 16,
    parameter IDX_BITS = $clog2(N),
    parameter [7:0] BASE = 8'hFF,
    parameter signed SW = -3
) (
    input [W-1:0] a,
    input [W-1:0] b,
    output [W-1:0] y,
    output [IDX_BITS-1:0] idx
);
    wire [W-1:0] c;
    wire [2*W-1:0] wide;
    reg [7:0] r0to7;
    integer i_var;
    real r_var;
    time t_var;
    byte b_var;
    shortint si_var;
    longint li_var;

    assign c = (a > b) ? a : b;
    assign wide = {a, b};
    assign y = c >> 1;
endmodule
"""


def _parse():
    vp = verilog_parser(start="source_text")
    tree = vp.build_tree(_SRC)
    design = tree_to_design(tree, source_file="test.v")
    link_instances(design)
    resolve_port_connections(design)
    analyze_design(design)
    return next(m for m in design.modules if m.name == "top")


# ── const_int ────────────────────────────────────────────────────────────


def test_const_int_plain_and_based_literal_parameters():
    top = _parse()
    env = _build_param_env(top)
    params = {p.name: p for p in top.parameters}

    for name, expected in [("W", 8), ("N", 16), ("BASE", 255), ("SW", -3)]:
        assert const_int(params[name].default_value, env) == expected


def test_const_int_clog2_derived_parameter():
    """Difference 1 resolution: env-dict-based lookup sees a parameter
    referenced inside ANOTHER parameter's own default-value expression."""
    top = _parse()
    env = _build_param_env(top)
    idx_bits_expr = next(p for p in top.parameters if p.name == "IDX_BITS").default_value
    assert const_int(idx_bits_expr, env) == 4


def test_const_int_non_constant_expr_is_none():
    """Difference 3 resolution: never raise, return None."""
    top = _parse()
    env = _build_param_env(top)
    y_rhs = next(ca.rhs for ca in top.continuous_assigns if str(ca.lhs) == "Identifier('y')")
    assert const_int(y_rhs, env) is None


def test_const_int_none_expr_is_none():
    assert const_int(None) is None


def test_const_int_no_env_for_identifier_is_none():
    assert const_int(Identifier("UNBOUND")) is None


def test_const_int_arithmetic_and_shift():
    env = {"W": 8}
    w_minus_1 = BinaryOp("-", Identifier("W"), Literal(1))
    assert const_int(w_minus_1, env) == 7
    shifted = BinaryOp("<<", Literal(1), Identifier("W"))
    assert const_int(shifted, env) == 256


def test_const_int_ternary_and_unary():
    env = {"W": 8}
    cond = TernaryOp(Literal(1), Identifier("W"), Literal(0))
    assert const_int(cond, env) == 8
    neg = UnaryOp("-", Identifier("W"))
    assert const_int(neg, env) == -8


# ── range_width / var_width / net_width ─────────────────────────────────


def test_range_width_literal_and_parametric():
    top = _parse()
    env = _build_param_env(top)
    nets = {n.name: n for n in top.nets}
    variables = {v.name: v for v in top.variables}

    assert range_width(nets["c"].width, env) == 8
    assert range_width(nets["wide"].width, env) == 16
    assert range_width(variables["r0to7"].width, env) == 8
    assert range_width(None, env) == 1


def test_range_width_clog2_derived_range():
    top = _parse()
    env = _build_param_env(top)
    idx_range = next(p for p in top.ports if p.name == "idx").width
    assert range_width(idx_range, env) == 4


def test_range_width_ascending_range_uses_abs():
    """Difference 2 resolution: abs(msb - lsb) + 1 unconditionally, fixing
    the scheduler.py/vm/compiler.py fast-path bug for an ascending range."""
    ascending = Range(Literal(0), Literal(7))
    assert range_width(ascending, {}) == 8


def test_var_width_special_kinds():
    top = _parse()
    env = _build_param_env(top)
    variables = {v.name: v for v in top.variables}

    for name, expected in [
        ("r0to7", 8),
        ("i_var", 32),
        ("r_var", 64),
        ("t_var", 64),
        ("b_var", 8),
        ("si_var", 16),
        ("li_var", 64),
    ]:
        assert var_width(variables[name], env) == expected, name


def test_net_width():
    top = _parse()
    env = _build_param_env(top)
    nets = {n.name: n for n in top.nets}
    assert net_width(nets["c"], env) == 8
    assert net_width(nets["wide"], env) == 16


# ── expr_width (self-determined, IEEE Table 5-22) ───────────────────────


def _width_of(widths):
    return lambda name: widths[name]


def test_expr_width_literal_identifier_reduction_comparison():
    widths = {"a": 8, "b": 16}
    assert expr_width(Literal(1, width=4), _width_of(widths)) == 4
    assert expr_width(Literal(5), _width_of(widths)) == 32  # unsized default
    assert expr_width(Identifier("a"), _width_of(widths)) == 8
    assert expr_width(UnaryOp("&", Identifier("a")), _width_of(widths)) == 1  # reduction
    assert expr_width(UnaryOp("~", Identifier("a")), _width_of(widths)) == 8  # width-preserving
    cmp_expr = BinaryOp("==", Identifier("a"), Identifier("b"))
    assert expr_width(cmp_expr, _width_of(widths)) == 1


def test_expr_width_shift_uses_left_operand_only():
    widths = {"a": 8, "b": 16}
    shifted = BinaryOp("<<", Identifier("a"), Identifier("b"))
    assert expr_width(shifted, _width_of(widths)) == 8


def test_expr_width_multiply_uses_sum_rule_not_max():
    """Table 5-22: '*' self-determined width is the SUM of operand widths,
    not max — unlike most other binary arithmetic operators."""
    widths = {"a": 8, "b": 16}
    mul = BinaryOp("*", Identifier("a"), Identifier("b"))
    assert expr_width(mul, _width_of(widths)) == 24
    add = BinaryOp("+", Identifier("a"), Identifier("b"))
    assert expr_width(add, _width_of(widths)) == 16


def test_expr_width_ternary_concat_replication():
    widths = {"a": 8, "b": 16}
    tern = TernaryOp(Literal(1), Identifier("a"), Identifier("b"))
    assert expr_width(tern, _width_of(widths)) == 16
    from veriforge.model.expressions import Concatenation, Replication

    concat = Concatenation([Identifier("a"), Identifier("b")])
    assert expr_width(concat, _width_of(widths)) == 24
    repl = Replication(Literal(3), Identifier("a"))
    assert expr_width(repl, _width_of(widths)) == 24


def test_expr_width_selects_are_their_own_slice_width():
    from veriforge.model.expressions import BitSelect, PartSelect, RangeSelect

    widths = {"a": 64}
    bit_sel = BitSelect(Identifier("a"), Literal(3))
    assert expr_width(bit_sel, _width_of(widths)) == 1
    range_sel = RangeSelect(Identifier("a"), Literal(15), Literal(8))
    assert expr_width(range_sel, _width_of(widths)) == 8
    part_sel = PartSelect(Identifier("a"), Literal(0), Literal(8), "+:")
    assert expr_width(part_sel, _width_of(widths)) == 8


# ── expr_signed (IEEE §5.5, §5.5.1) ──────────────────────────────────────


def _signed_of(signs):
    return lambda name: signs.get(name, False)


def test_expr_signed_identifier_and_literal():
    signs = {"a": True, "b": False}
    assert expr_signed(Identifier("a"), _signed_of(signs)) is True
    assert expr_signed(Identifier("b"), _signed_of(signs)) is False
    assert expr_signed(Literal(1, signed=True), _signed_of(signs)) is True
    assert expr_signed(Literal(1), _signed_of(signs)) is False


def test_expr_signed_selects_always_unsigned():
    """IEEE §5.5.1: bit/range/part-select is always unsigned regardless of
    the sliced signal's own declared signedness."""
    from veriforge.model.expressions import BitSelect, PartSelect, RangeSelect

    signs = {"a": True}
    assert expr_signed(BitSelect(Identifier("a"), Literal(0)), _signed_of(signs)) is False
    assert expr_signed(RangeSelect(Identifier("a"), Literal(7), Literal(0)), _signed_of(signs)) is False
    assert expr_signed(PartSelect(Identifier("a"), Literal(0), Literal(8), "+:"), _signed_of(signs)) is False


def test_expr_signed_concat_and_replication_always_unsigned():
    from veriforge.model.expressions import Concatenation, Replication

    signs = {"a": True, "b": True}
    concat = Concatenation([Identifier("a"), Identifier("b")])
    assert expr_signed(concat, _signed_of(signs)) is False
    repl = Replication(Literal(2), Identifier("a"))
    assert expr_signed(repl, _signed_of(signs)) is False


def test_expr_signed_binary_requires_both_signed_except_shift():
    signs = {"a": True, "b": False}
    add_both_signed = BinaryOp("+", Identifier("a"), Identifier("a"))
    assert expr_signed(add_both_signed, _signed_of(signs)) is True
    add_mixed = BinaryOp("+", Identifier("a"), Identifier("b"))
    assert expr_signed(add_mixed, _signed_of(signs)) is False
    # Shift signedness depends only on the left operand.
    shift_signed_left = BinaryOp("<<", Identifier("a"), Identifier("b"))
    assert expr_signed(shift_signed_left, _signed_of(signs)) is True
    shift_unsigned_left = BinaryOp("<<", Identifier("b"), Identifier("a"))
    assert expr_signed(shift_unsigned_left, _signed_of(signs)) is False


def test_expr_signed_comparison_and_logical_always_unsigned():
    signs = {"a": True, "b": True}
    cmp_expr = BinaryOp("==", Identifier("a"), Identifier("b"))
    assert expr_signed(cmp_expr, _signed_of(signs)) is False
    logical = BinaryOp("&&", Identifier("a"), Identifier("b"))
    assert expr_signed(logical, _signed_of(signs)) is False


def test_expr_signed_ternary_both_branches_must_be_signed():
    signs = {"a": True, "b": False}
    both_signed = TernaryOp(Literal(1), Identifier("a"), Identifier("a"))
    assert expr_signed(both_signed, _signed_of(signs)) is True
    mixed = TernaryOp(Literal(1), Identifier("a"), Identifier("b"))
    assert expr_signed(mixed, _signed_of(signs)) is False


def test_expr_signed_unary():
    signs = {"a": True, "b": False}
    assert expr_signed(UnaryOp("-", Identifier("a")), _signed_of(signs)) is True
    assert expr_signed(UnaryOp("~", Identifier("b")), _signed_of(signs)) is False
    assert expr_signed(UnaryOp("!", Identifier("a")), _signed_of(signs)) is False
    assert expr_signed(UnaryOp("&", Identifier("a")), _signed_of(signs)) is False  # reduction
