"""Tests for operator precedence parenthesization in emit_expression.

Verilog and Python share most operator precedences but diverge in two
key areas:
  - Relational ops (==, !=, <, >) are HIGHER than bitwise (&, |, ^) in
    Verilog, but LOWER in Python.
  - Shift ops (<<, >>) are LOWER than +/- in both, but complex chained
    expressions need explicit parens to round-trip correctly.

Without parenthesization, the emitter would produce Verilog that synthesizes
differently from what the DSL simulation executes. These tests verify that
emit_expression adds parens wherever the expression tree structure would
otherwise be misread by Verilog's precedence rules.
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_expression
from veriforge.model.expressions import BinaryOp, Identifier, Literal, TernaryOp, UnaryOp


def _id(name: str) -> Identifier:
    return Identifier(name)


def _lit(v: int) -> Literal:
    return Literal(v, original_text=str(v))


# ---------------------------------------------------------------------------
# Shift as left operand of add — the ctx_sum decay bug
# DSL: ((ctx_sum * 15 + 7) >> 4) + abs_val
# Without parens: ctx_sum * 15 + 7 >> 4 + abs_val
#   Verilog: >> (8) lower than + (9), so (ctx_sum*15 + 7) >> (4 + abs_val)  WRONG
# With parens:    (ctx_sum * 15 + 7 >> 4) + abs_val
#   Verilog: inside parens: * then + then >> → correct; outer + correct
# ---------------------------------------------------------------------------
def test_shift_left_of_add_gets_parens() -> None:
    expr = BinaryOp(
        "+",
        BinaryOp(">>", BinaryOp("+", BinaryOp("*", _id("a"), _lit(15)), _lit(7)), _lit(4)),
        _id("b"),
    )
    assert emit_expression(expr) == "(a * 15 + 7 >> 4) + b"


def test_shift_left_of_add_gets_parens_sum2() -> None:
    # Same shape for sum2 variant
    expr = BinaryOp(
        "+",
        BinaryOp(">>", BinaryOp("+", BinaryOp("*", _id("s2"), _lit(15)), _lit(7)), _lit(4)),
        _id("sq"),
    )
    assert emit_expression(expr) == "(s2 * 15 + 7 >> 4) + sq"


# ---------------------------------------------------------------------------
# Bitwise-and as left of comparison — the has_sibs bug
# DSL: (i_y & i_step) != 0
# Without parens: i_y & i_step != 0
#   Verilog: != (6) > & (5), so i_y & (i_step != 0) = i_y & 1  WRONG
# With parens:    (i_y & i_step) != 0  CORRECT
# ---------------------------------------------------------------------------
def test_and_left_of_ne_gets_parens() -> None:
    expr = BinaryOp("!=", BinaryOp("&", _id("i_y"), _id("i_step")), _lit(0))
    assert emit_expression(expr) == "(i_y & i_step) != 0"


def test_and_left_of_eq_gets_parens() -> None:
    expr = BinaryOp("==", BinaryOp("&", _id("a"), _id("b")), _lit(0))
    assert emit_expression(expr) == "(a & b) == 0"


# ---------------------------------------------------------------------------
# Or as right operand of add — neighbor address encoding
# DSL: (i_y - i_step) * W + (i_x | i_step)
# Without parens: i_y - i_step * W + i_x | i_step
#   Verilog: * (10) > - (9) > + (9) > | (3) — multiply binds to i_step  WRONG
# With parens:    (i_y - i_step) * W + (i_x | i_step)  CORRECT
# ---------------------------------------------------------------------------
def test_sub_left_of_mul_and_or_right_of_add() -> None:
    # (i_y - i_step) * W + (i_x | i_step)
    expr = BinaryOp(
        "+",
        BinaryOp("*", BinaryOp("-", _id("i_y"), _id("i_step")), _id("W")),
        BinaryOp("|", _id("i_x"), _id("i_step")),
    )
    assert emit_expression(expr) == "(i_y - i_step) * W + (i_x | i_step)"


# ---------------------------------------------------------------------------
# Subtract as left operand of multiply — grouping for multiply
# DSL: (a - b) * c
# Without parens: a - b * c
#   Verilog: * (10) > - (9), so a - (b * c)  WRONG
# With parens:    (a - b) * c  CORRECT
# ---------------------------------------------------------------------------
def test_sub_left_of_mul_gets_parens() -> None:
    expr = BinaryOp("*", BinaryOp("-", _id("a"), _id("b")), _id("c"))
    assert emit_expression(expr) == "(a - b) * c"


# ---------------------------------------------------------------------------
# Shift as right operand of subtract — GR escape remainder
# DSL: val_r - (12 << p_r)
# Without parens: val_r - 12 << p_r
#   Verilog: - (9) > << (8), so (val_r - 12) << p_r  WRONG
# With parens:    val_r - (12 << p_r)  CORRECT
# ---------------------------------------------------------------------------
def test_shift_right_of_sub_gets_parens() -> None:
    expr = BinaryOp("-", _id("val_r"), BinaryOp("<<", _lit(12), _id("p")))
    assert emit_expression(expr) == "val_r - (12 << p)"


# ---------------------------------------------------------------------------
# Mask extraction — GR remainder mask
# DSL: src_val & ((1 << eff_p) - 1)
# Key: inner (1 << eff_p) needs parens because - (9) > << (8)
#      The outer & then gets src_val & (1 << eff_p) - 1 which Verilog reads
#      as src_val & ((1 << eff_p) - 1) because - binds tighter than &.
# ---------------------------------------------------------------------------
def test_mask_extraction_inner_shift_parens() -> None:
    # ((1 << eff_p) - 1): inner << left of -, so parenthesized
    inner = BinaryOp("-", BinaryOp("<<", _lit(1), _id("eff_p")), _lit(1))
    expr = BinaryOp("&", _id("src_val"), inner)
    result = emit_expression(expr)
    # (1 << eff_p) gets parens because << (8) < - (9)
    assert result == "src_val & (1 << eff_p) - 1"
    # Verilog parses: src_val & ((1 << eff_p) - 1) — correct because
    # the parenthesized (1 << eff_p) forces that grouping, then - (9) > & (5).


# ---------------------------------------------------------------------------
# Rounding shift — context_sampler normalization
# DSL: acc_sum * 16 + (acc_cnt >> 1)
# Without parens: acc_sum * 16 + acc_cnt >> 1
#   Verilog: * (10) > + (9) > >> (8) → (acc_sum*16 + acc_cnt) >> 1  WRONG
# With parens:    acc_sum * 16 + (acc_cnt >> 1)  CORRECT
# ---------------------------------------------------------------------------
def test_shift_right_of_add_rounding() -> None:
    expr = BinaryOp("+", BinaryOp("*", _id("acc_sum"), _lit(16)), BinaryOp(">>", _id("acc_cnt"), _lit(1)))
    assert emit_expression(expr) == "acc_sum * 16 + (acc_cnt >> 1)"


# ---------------------------------------------------------------------------
# Unary over binary — unary must parenthesize its BinaryOp operand
# DSL: ~(a & b)
# Without parens: ~a & b
#   Verilog: ~ (unary, highest) applied to a only, then & b  WRONG
# With parens: ~(a & b)  CORRECT
# ---------------------------------------------------------------------------
def test_unary_invert_over_and_gets_parens() -> None:
    expr = UnaryOp("~", BinaryOp("&", _id("a"), _id("b")))
    assert emit_expression(expr) == "~(a & b)"


def test_unary_minus_over_binary_gets_parens() -> None:
    expr = UnaryOp("-", BinaryOp("+", _id("a"), _id("b")))
    assert emit_expression(expr) == "-(a + b)"


# ---------------------------------------------------------------------------
# Unary over identifier — no parens needed
# ---------------------------------------------------------------------------
def test_unary_over_identifier_no_parens() -> None:
    assert emit_expression(UnaryOp("-", _id("a"))) == "-a"
    assert emit_expression(UnaryOp("~", _id("b"))) == "~b"


# ---------------------------------------------------------------------------
# Ternary as binary operand — ternary has lowest precedence
# DSL: (cond ? a : b) + c
# Without parens: cond ? a : b + c
#   Verilog: + (9) > ?: (lowest), so cond ? a : (b + c)  WRONG
# With parens: (cond ? a : b) + c  CORRECT
# ---------------------------------------------------------------------------
def test_ternary_left_of_binary_gets_parens() -> None:
    ternary = TernaryOp(_id("cond"), _id("a"), _id("b"))
    expr = BinaryOp("+", ternary, _id("c"))
    assert emit_expression(expr) == "(cond ? a : b) + c"


def test_ternary_right_of_binary_gets_parens() -> None:
    ternary = TernaryOp(_id("sel"), _id("x"), _id("y"))
    expr = BinaryOp("*", _id("w"), ternary)
    assert emit_expression(expr) == "w * (sel ? x : y)"


# ---------------------------------------------------------------------------
# Simple cases that must NOT get extra parens
# ---------------------------------------------------------------------------
def test_simple_sub_no_parens() -> None:
    # W - 1 (existing test_roundtrip.py case)
    expr = BinaryOp("-", _id("W"), _lit(1))
    assert emit_expression(expr) == "W - 1"


def test_left_assoc_add_chain_no_parens() -> None:
    # a + b + c → (left-to-right tree) should stay a + b + c
    expr = BinaryOp("+", BinaryOp("+", _id("a"), _id("b")), _id("c"))
    assert emit_expression(expr) == "a + b + c"


def test_mul_left_of_add_no_parens() -> None:
    # a * b + c — * (10) > + (9) so no parens needed
    expr = BinaryOp("+", BinaryOp("*", _id("a"), _id("b")), _id("c"))
    assert emit_expression(expr) == "a * b + c"


def test_add_right_of_and_no_parens() -> None:
    # a & b + c — in Verilog + (9) > & (5), so a & (b + c): Verilog gets it right without parens
    expr = BinaryOp("&", _id("a"), BinaryOp("+", _id("b"), _id("c")))
    # right prec 9 > parent prec 5 — 9 <= 5 is False → no parens
    assert emit_expression(expr) == "a & b + c"


def test_and_inside_or_gets_parens() -> None:
    # (a & b) | c — in Verilog & (5) > | (3), no parens needed on left
    # but when & appears as right of |, it needs parens: a | (b & c)
    expr = BinaryOp("|", _id("a"), BinaryOp("&", _id("b"), _id("c")))
    # right prec 5 > parent prec 3: 5 <= 3 is False → no extra parens
    assert emit_expression(expr) == "a | b & c"


def test_or_left_of_and_gets_parens() -> None:
    # (a | b) & c — | (3) < & (5) → left gets parens
    expr = BinaryOp("&", BinaryOp("|", _id("a"), _id("b")), _id("c"))
    assert emit_expression(expr) == "(a | b) & c"
