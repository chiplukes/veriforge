"""Compiled-engine edge-case suites (work plan item 2.2).

Deterministic (not random) regression cases for the shape families behind
the recent compiled-engine bug class: nested ternaries, port boundary
crossings, word-seam (63/64/65-bit) operations, self-determined width
contexts, and dynamic part-selects near word seams. Same cross-engine
mechanics as item 2.1 (`test_assignment_matrix.py`): every case runs on
every engine, is checked against a Python oracle *and* the reference
engine's own result, and known compiled failures are filed in
`notes/known_issues.md` and pinned with strict xfail rather than weakening
the oracle.

This file is the regression home for future compiled bugs of this shape:
add the failing shape here before fixing it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from veriforge.analysis.resolver import link_instances, resolve_port_connections
from veriforge.model.design import Design
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

# =====================================================================
# Shared parsing / simulation plumbing (same pattern as test_assignment_matrix.py
# and tests/test_sim/test_structural_patterns.py)
# =====================================================================


def _parse_design(source: str) -> Design:
    vp = verilog_parser(start="source_text")
    tree = vp.build_tree(source)
    # source_file=None (not a real path): dynamic part-select direction
    # recovery (`+:` vs `-:`) falls back to reading the file from disk only
    # when source_file is truthy; with None it defaults to "+:", which is
    # the only direction used in this file's sources.
    return tree_to_design(tree, source_file=None)


def _sim_for(source: str, engine: str, top_name: str) -> Simulator:
    design = _parse_design(source)
    link_instances(design)
    resolve_port_connections(design)
    top = next(m for m in design.modules if m.name == top_name)
    return Simulator(top, engine=engine, design=design)


def _mask(width: int) -> int:
    return (1 << width) - 1


# =====================================================================
# Generic case + runner
# =====================================================================


@dataclass(frozen=True)
class EdgeCase:
    id: str
    family: str
    source: str
    top_name: str
    drives: tuple[tuple[str, Value], ...]
    expected: tuple[tuple[str, Value], ...]
    seq: bool = False
    """True if the shape needs a posedge clk edge (nonblocking) between drive and read."""
    skip_ref_crosscheck: bool = False
    """True if the reference engine is itself known-wrong for this shape (see
    notes/known_issues.md) -- the oracle assertion is still authoritative,
    only the redundant cross-check against a known-bad reference result is
    skipped so it doesn't manufacture a false failure for engines that are
    actually correct."""


_RESULTS_CACHE: dict[tuple[str, str], dict[str, Value]] = {}


def _run_case(engine: str, case: EdgeCase) -> dict[str, Value]:
    key = (engine, case.id)
    cached = _RESULTS_CACHE.get(key)
    if cached is not None:
        return cached
    sim = _sim_for(case.source, engine, case.top_name)
    sim.run(max_time=0)  # let any `initial` blocks (memory preload) run first
    if case.seq:
        sim.drive("clk", Value(0, width=1))
        sim.settle()
    for name, val in case.drives:
        sim.drive(name, val)
    sim.settle()
    if case.seq:
        sim.drive("clk", Value(1, width=1))
        sim.settle()
        sim.drive("clk", Value(0, width=1))
        sim.settle()
    result = {name: sim.read(name) for name, _ in case.expected}
    _RESULTS_CACHE[key] = result
    return result


# =====================================================================
# Family 1: nested ternaries
# =====================================================================


def _chain_expr(depth: int) -> str:
    expr = f"v{depth}"
    for lvl in range(depth - 1, -1, -1):
        expr = f"s{lvl} ? v{lvl} : ({expr})"
    return expr


def _build_ternary_chain_module(depth: int, width: int = 8) -> str:
    sels = ", ".join(f"input s{i}" for i in range(depth))
    vals = ", ".join(f"input [{width - 1}:0] v{i}" for i in range(depth + 1))
    expr = _chain_expr(depth)
    return f"module t({sels}, {vals}, output [{width - 1}:0] y);\n    assign y = {expr};\nendmodule\n"


def _ternary_chain_cases(depth: int, width: int = 8) -> list[EdgeCase]:
    source = _build_ternary_chain_module(depth, width)
    values = {f"v{i}": Value(0x10 + i, width=width) for i in range(depth + 1)}

    # "first": s0 true selects v0 regardless of deeper selectors.
    first_drives = [(f"s{i}", Value(1 if i == 0 else 0, width=1)) for i in range(depth)]
    first_drives += list(values.items())
    cases = [
        EdgeCase(
            id=f"ternary_depth{depth}_first",
            family="nested_ternary",
            source=source,
            top_name="t",
            drives=tuple(first_drives),
            expected=(("y", Value(0x10, width=width)),),
        )
    ]

    # "deepest": every selector false, falls through to the final else value.
    deepest_drives = [(f"s{i}", Value(0, width=1)) for i in range(depth)]
    deepest_drives += list(values.items())
    cases.append(
        EdgeCase(
            id=f"ternary_depth{depth}_deepest",
            family="nested_ternary",
            source=source,
            top_name="t",
            drives=tuple(deepest_drives),
            expected=(("y", Value(0x10 + depth, width=width)),),
        )
    )
    return cases


def _ternary_cond_position_cases() -> list[EdgeCase]:
    # (a ? b : c) ? d : e
    source = (
        "module t(input a, input b, input c, input [7:0] d, input [7:0] e, output [7:0] y);\n"
        "    assign y = (a ? b : c) ? d : e;\n"
        "endmodule\n"
    )
    return [
        EdgeCase(
            id="ternary_cond_position_true",
            family="nested_ternary",
            source=source,
            top_name="t",
            drives=(
                ("a", Value(1, width=1)),
                ("b", Value(1, width=1)),
                ("c", Value(0, width=1)),
                ("d", Value(0xAA, width=8)),
                ("e", Value(0x55, width=8)),
            ),
            expected=(("y", Value(0xAA, width=8)),),
        ),
        EdgeCase(
            id="ternary_cond_position_false",
            family="nested_ternary",
            source=source,
            top_name="t",
            drives=(
                ("a", Value(1, width=1)),
                ("b", Value(0, width=1)),
                ("c", Value(1, width=1)),
                ("d", Value(0xAA, width=8)),
                ("e", Value(0x55, width=8)),
            ),
            expected=(("y", Value(0x55, width=8)),),
        ),
    ]


def _ternary_mixed_width_cases() -> list[EdgeCase]:
    source = (
        "module t(input sel, input [3:0] a4, input [7:0] b8, output [15:0] y);\n"
        "    assign y = sel ? a4 : b8;\n"
        "endmodule\n"
    )
    return [
        EdgeCase(
            id="ternary_mixed_width_narrow_arm",
            family="nested_ternary",
            source=source,
            top_name="t",
            drives=(
                ("sel", Value(1, width=1)),
                ("a4", Value(0xF, width=4)),
                ("b8", Value(0xAB, width=8)),
            ),
            expected=(("y", Value(0x000F, width=16)),),
        ),
        EdgeCase(
            id="ternary_mixed_width_wide_arm",
            family="nested_ternary",
            source=source,
            top_name="t",
            drives=(
                ("sel", Value(0, width=1)),
                ("a4", Value(0xF, width=4)),
                ("b8", Value(0xAB, width=8)),
            ),
            expected=(("y", Value(0x00AB, width=16)),),
        ),
    ]


def _ternary_x_condition_case() -> EdgeCase:
    # sel is fully x (mask covers its only bit) -> ambiguous condition ->
    # bitwise merge of the two arms. a and b are exact bitwise complements,
    # so every bit position disagrees and the merged result is all-x.
    source = (
        "module t(input sel, input [7:0] a, input [7:0] b, output [7:0] y);\n    assign y = sel ? a : b;\nendmodule\n"
    )
    return EdgeCase(
        id="ternary_x_condition_merge",
        family="nested_ternary",
        source=source,
        top_name="t",
        drives=(
            ("sel", Value(0, width=1, mask=1)),
            ("a", Value(0xAA, width=8)),
            ("b", Value(0x55, width=8)),
        ),
        expected=(("y", Value(0, width=8, mask=0xFF)),),
    )


def _ternary_wide_arms_case() -> EdgeCase:
    width = 70
    a_val = (1 << (width - 1)) | 0x123
    b_val = 0x456
    source = (
        f"module t(input sel, input [{width - 1}:0] a, input [{width - 1}:0] b, output [{width - 1}:0] y);\n"
        "    assign y = sel ? a : b;\n"
        "endmodule\n"
    )
    return EdgeCase(
        id="ternary_wide_arms",
        family="nested_ternary",
        source=source,
        top_name="t",
        drives=(
            ("sel", Value(1, width=1)),
            ("a", Value(a_val, width=width)),
            ("b", Value(b_val, width=width)),
        ),
        expected=(("y", Value(a_val, width=width)),),
    )


def _ternary_as_index_cases() -> list[EdgeCase]:
    source = (
        "module t(input sel, input [3:0] i, input [3:0] j, output [7:0] y);\n"
        "    reg [7:0] mem [0:15];\n"
        "    integer k;\n"
        "    initial for (k = 0; k < 16; k = k + 1) mem[k] = k + 8'h10;\n"
        "    assign y = mem[sel ? i : j];\n"
        "endmodule\n"
    )
    return [
        EdgeCase(
            id="ternary_as_index_true",
            family="nested_ternary",
            source=source,
            top_name="t",
            drives=(("sel", Value(1, width=1)), ("i", Value(3, width=4)), ("j", Value(9, width=4))),
            expected=(("y", Value(0x13, width=8)),),
        ),
        EdgeCase(
            id="ternary_as_index_false",
            family="nested_ternary",
            source=source,
            top_name="t",
            drives=(("sel", Value(0, width=1)), ("i", Value(3, width=4)), ("j", Value(9, width=4))),
            expected=(("y", Value(0x19, width=8)),),
        ),
    ]


def _nested_ternary_cases() -> list[EdgeCase]:
    cases: list[EdgeCase] = []
    for depth in (2, 3, 4):
        cases += _ternary_chain_cases(depth)
    cases += _ternary_cond_position_cases()
    cases += _ternary_mixed_width_cases()
    cases.append(_ternary_x_condition_case())
    cases.append(_ternary_wide_arms_case())
    cases += _ternary_as_index_cases()
    return cases


# =====================================================================
# Family 2: port boundary crossings
# =====================================================================


def _passthrough_child(width: int, signed: bool = False) -> str:
    sig = "signed " if signed else ""
    return (
        f"module child(input {sig}[{width - 1}:0] in_port, output {sig}[{width - 1}:0] out_port);\n"
        "    assign out_port = in_port;\n"
        "endmodule\n"
    )


def _port_crossing_cases() -> list[EdgeCase]:
    cases: list[EdgeCase] = []

    # (a) narrower child port than parent net -> truncation, regardless of the
    #     child port's own declared signedness.
    child = _passthrough_child(8, signed=True)
    top = (
        "module top(input [15:0] src, output [7:0] dst);\n"
        "    child u_child(.in_port(src), .out_port(dst));\n"
        "endmodule\n"
    )
    cases.append(
        EdgeCase(
            id="port_narrow_signed_child_truncates",
            family="port_crossing",
            source=child + "\n" + top,
            top_name="top",
            drives=(("src", Value(0x1234, width=16)),),
            expected=(("dst", Value(0x34, width=8)),),
        )
    )

    # (b) wider (unsigned) child port fed by a *signed* narrower parent net ->
    #     sign-extension, driven by the source's declared signedness (not the
    #     destination child port's).
    child2 = _passthrough_child(16, signed=False)
    top2 = (
        "module top(input signed [7:0] src, output [15:0] dst);\n"
        "    child u_child(.in_port(src), .out_port(dst));\n"
        "endmodule\n"
    )
    cases.append(
        EdgeCase(
            id="port_wide_unsigned_child_sign_extends_from_signed_parent",
            family="port_crossing",
            source=child2 + "\n" + top2,
            top_name="top",
            drives=(("src", Value(0x80, width=8)),),
            expected=(("dst", Value(0xFF80, width=16)),),
        )
    )

    # (c) expression connection: .in_port(x + y)
    child3 = _passthrough_child(8)
    top3 = (
        "module top(input [7:0] x, input [7:0] y_in, output [7:0] dst);\n"
        "    child u_child(.in_port(x + y_in), .out_port(dst));\n"
        "endmodule\n"
    )
    cases.append(
        EdgeCase(
            id="port_expression_connection",
            family="port_crossing",
            source=child3 + "\n" + top3,
            top_name="top",
            drives=(("x", Value(10, width=8)), ("y_in", Value(20, width=8))),
            expected=(("dst", Value(30, width=8)),),
        )
    )

    # (d) constant connection: .in_port(8'hFF)
    child4 = _passthrough_child(8)
    top4 = "module top(output [7:0] dst);\n    child u_child(.in_port(8'hFF), .out_port(dst));\nendmodule\n"
    cases.append(
        EdgeCase(
            id="port_constant_connection",
            family="port_crossing",
            source=child4 + "\n" + top4,
            top_name="top",
            drives=(),
            expected=(("dst", Value(0xFF, width=8)),),
        )
    )

    # (e) concat connection: .in_port({hi, lo})
    child5 = _passthrough_child(8)
    top5 = (
        "module top(input [3:0] hi, input [3:0] lo, output [7:0] dst);\n"
        "    child u_child(.in_port({hi, lo}), .out_port(dst));\n"
        "endmodule\n"
    )
    cases.append(
        EdgeCase(
            id="port_concat_connection",
            family="port_crossing",
            source=child5 + "\n" + top5,
            top_name="top",
            drives=(("hi", Value(0xA, width=4)), ("lo", Value(0x5, width=4))),
            expected=(("dst", Value(0xA5, width=8)),),
        )
    )

    # (f) child output driving a range-select of a parent net.
    child6 = _passthrough_child(8)
    top6 = (
        "module top(input [7:0] src, output [7:0] probe);\n"
        "    wire [15:0] net;\n"
        "    assign net[3:0] = 4'h0;\n"
        "    assign net[15:12] = 4'h0;\n"
        "    child u_child(.in_port(src), .out_port(net[11:4]));\n"
        "    assign probe = net[11:4];\n"
        "endmodule\n"
    )
    cases.append(
        EdgeCase(
            id="port_output_drives_parent_range_select",
            family="port_crossing",
            source=child6 + "\n" + top6,
            top_name="top",
            drives=(("src", Value(0x7E, width=8)),),
            expected=(("probe", Value(0x7E, width=8)),),
        )
    )

    return cases


# =====================================================================
# Family 3: word-seam sweep
# =====================================================================

WORD_SEAM_WIDTHS = [63, 64, 65]

_OP_NAME = {
    "+": "add",
    "-": "sub",
    "*": "mul",
    "&": "and",
    "|": "or",
    "^": "xor",
    "==": "eq",
    "<": "lt",
    "<<": "shl",
    ">>": "shr",
}

_BIN_OPS: list[tuple[str, Callable[[int, int, int], int]]] = [
    ("+", lambda a, b, w: (a + b) & _mask(w)),
    ("-", lambda a, b, w: (a - b) & _mask(w)),
    ("*", lambda a, b, w: (a * b) & _mask(w)),
    ("&", lambda a, b, w: (a & b) & _mask(w)),
    ("|", lambda a, b, w: (a | b) & _mask(w)),
    ("^", lambda a, b, w: (a ^ b) & _mask(w)),
]

_CMP_OPS: list[tuple[str, Callable[[int, int], int]]] = [
    ("==", lambda a, b: int(a == b)),
    ("<", lambda a, b: int(a < b)),
]


def _word_seam_stimulus(w: int) -> tuple[int, int]:
    a = (0x5A5A5A5A5A5A5A5A5A5A5A5A & _mask(w)) | (1 << (w - 1))
    b = 0x3C3C3C3C3C3C3C3C3C3C3C3C & _mask(w)
    return a, b


def _word_seam_cases() -> list[EdgeCase]:
    cases: list[EdgeCase] = []
    for w in WORD_SEAM_WIDTHS:
        a, b = _word_seam_stimulus(w)
        av, bv = Value(a, width=w), Value(b, width=w)

        for op, fn in _BIN_OPS:
            source = f"module t(input [{w - 1}:0] a, input [{w - 1}:0] b, output [{w - 1}:0] y);\n    assign y = a {op} b;\nendmodule\n"
            cases.append(
                EdgeCase(
                    id=f"seam{w}_{_OP_NAME[op]}",
                    family="word_seam",
                    source=source,
                    top_name="t",
                    drives=(("a", av), ("b", bv)),
                    expected=(("y", Value(fn(a, b, w), width=w)),),
                )
            )

        for cmp_op, cmp_fn in _CMP_OPS:
            source = f"module t(input [{w - 1}:0] a, input [{w - 1}:0] b, output y);\n    assign y = a {cmp_op} b;\nendmodule\n"
            cases.append(
                EdgeCase(
                    id=f"seam{w}_{_OP_NAME[cmp_op]}",
                    family="word_seam",
                    source=source,
                    top_name="t",
                    drives=(("a", av), ("b", bv)),
                    expected=(("y", Value(cmp_fn(a, b), width=1)),),
                )
            )

        for op in ("<<", ">>"):
            for amt in (1, 31, 64):
                source = (
                    f"module t(input [{w - 1}:0] a, output [{w - 1}:0] y);\n    assign y = a {op} {amt};\nendmodule\n"
                )
                if op == "<<":
                    expected = (a << amt) & _mask(w)
                else:
                    expected = (a >> amt) & _mask(w) if amt < w else 0
                cases.append(
                    EdgeCase(
                        id=f"seam{w}_{_OP_NAME[op]}{amt}",
                        family="word_seam",
                        source=source,
                        top_name="t",
                        drives=(("a", av),),
                        expected=(("y", Value(expected, width=w)),),
                    )
                )

        # concat of two such signals
        source = f"module t(input [{w - 1}:0] a, input [{w - 1}:0] b, output [{2 * w - 1}:0] y);\n    assign y = {{a, b}};\nendmodule\n"
        expected = ((a << w) | b) & _mask(2 * w)
        cases.append(
            EdgeCase(
                id=f"seam{w}_concat",
                family="word_seam",
                source=source,
                top_name="t",
                drives=(("a", av), ("b", bv)),
                expected=(("y", Value(expected, width=2 * w)),),
            )
        )

        # &-reduction, all-ones stimulus so the result is meaningfully True
        ones = _mask(w)
        source = f"module t(input [{w - 1}:0] a, output y);\n    assign y = &a;\nendmodule\n"
        cases.append(
            EdgeCase(
                id=f"seam{w}_andreduce",
                family="word_seam",
                source=source,
                top_name="t",
                drives=(("a", Value(ones, width=w)),),
                expected=(("y", Value(1, width=1)),),
            )
        )

        # intermediate-overflow case (the aef7f13 class): both operands declared
        # <= 64 bits, but `hi << 32` alone already exceeds 64 bits.
        hi_w = w - 32
        lo_w = 32
        lo_val = _mask(lo_w)
        hi_val = _mask(hi_w)
        source = f"module t(input [{lo_w - 1}:0] lo, input [{hi_w - 1}:0] hi, output [{w - 1}:0] y);\n    assign y = lo | (hi << 32);\nendmodule\n"
        expected = (lo_val | (hi_val << 32)) & _mask(w)
        cases.append(
            EdgeCase(
                id=f"seam{w}_overflow",
                family="word_seam",
                source=source,
                top_name="t",
                drives=(("lo", Value(lo_val, width=lo_w)), ("hi", Value(hi_val, width=hi_w))),
                expected=(("y", Value(expected, width=w)),),
            )
        )

    return cases


# =====================================================================
# Family 4: self-determined width contexts
# =====================================================================


def _context_determined_unary(op: str, a_val: int, src_w: int, dst_w: int, src_signed: bool) -> int:
    """Verified-correct oracle for `op a` assigned into a wider target.

    Per IEEE 1364-2005, unary `-` and `~` are *context-determined*, not
    self-determined: the operand is first extended to the full assignment
    context width using its own declared signedness, and only then is the
    operator applied at that width. This was cross-checked against both
    Icarus Verilog and Verilator (see notes/known_issues.md) -- an earlier
    assumption in this codebase that these operators are self-determined
    (IEEE Table 5-22 lists them as self-determined only when they are a
    *subexpression* of a larger context-determined expression, not when
    they are the context-determined expression's own top-level operator)
    was incorrect and is corrected here.
    """
    if src_signed and (a_val >> (src_w - 1)) & 1:
        ext = ((1 << (dst_w - src_w)) - 1) << src_w
    else:
        ext = 0
    extended = (a_val & _mask(src_w)) | ext
    result = ~extended if op == "~" else -extended
    return result & _mask(dst_w)


def _unary_self_determined_cases() -> list[EdgeCase]:
    # Known bug (see notes/known_issues.md "unary ~ is wrongly self-determined"):
    # `~a` is computed self-determined (wrong) on reference/vm/vm-fast, and
    # on the compiled engine's narrow (<=64-bit) path; only the compiled
    # engine's wide (>64-bit) path happens to already be context-determined
    # (correct) here. `-a` is context-determined (correct) everywhere.
    cases: list[EdgeCase] = []
    a_val = (1 << 64) | 0x123  # 65-bit value, bit 64 set
    for op, opname in (("~", "not"), ("-", "neg")):
        for signed in (False, True):
            sig = "signed " if signed else ""
            source = f"module t(input {sig}[64:0] a, output {sig}[79:0] y);\n    assign y = {op}a;\nendmodule\n"
            expected = _context_determined_unary(op, a_val, 65, 80, signed)
            cases.append(
                EdgeCase(
                    id=f"self_det_unary_{opname}_65_to_80_{'signed' if signed else 'unsigned'}",
                    family="self_determined",
                    source=source,
                    top_name="t",
                    drives=(("a", Value(a_val, width=65)),),
                    expected=(("y", Value(expected, width=80)),),
                    # Only the unsigned `~` case needs this: reference/vm/vm-fast
                    # are wrong there while compiled is right, so cross-checking
                    # compiled's correct result against reference's wrong one
                    # would manufacture a false failure. See _known_engine_bug.
                    skip_ref_crosscheck=(op == "~" and not signed),
                )
            )
    return cases


def _if_condition_width_cases() -> list[EdgeCase]:
    # 71897f4 class: & | ^ used directly as an `if` condition must use the
    # full (self-determined) operand width, not truncate to 1 bit, before
    # testing "is the result nonzero".
    cases = []
    for op, opname, a_val, b_val, out_val in (
        ("&", "and", 0x02, 0x02, 1),  # a&b = 0x02 (nonzero); LSB-only bug would see 0&0=0
        ("|", "or", 0x00, 0x04, 1),  # a|b = 0x04 (nonzero); LSB-only bug would see 0|0=0
        ("^", "xor", 0x04, 0x02, 1),  # a^b = 0x06 (nonzero); LSB-only bug would see 0^0=0
    ):
        source = (
            f"module t(input [7:0] a, input [7:0] b, output reg y);\n"
            f"    always @(*) begin\n"
            f"        if (a {op} b) y = 1'b1;\n"
            f"        else y = 1'b0;\n"
            f"    end\n"
            f"endmodule\n"
        )
        cases.append(
            EdgeCase(
                id=f"self_det_if_{opname}_condition",
                family="self_determined",
                source=source,
                top_name="t",
                drives=(("a", Value(a_val, width=8)), ("b", Value(b_val, width=8))),
                expected=(("y", Value(out_val, width=1)),),
            )
        )
    return cases


def _x_shift_amount_cases() -> list[EdgeCase]:
    # A fully-x shift amount makes the whole result x (checked against the
    # reference engine's own semantics here rather than a hand-derived
    # oracle, since this is the reference engine's documented behavior).
    cases = []
    for op, opname in (("<<", "shl"), (">>", "shr")):
        source = (
            f"module t(input [7:0] a, input [7:0] shamt, output [7:0] y);\n    assign y = a {op} shamt;\nendmodule\n"
        )
        cases.append(
            EdgeCase(
                id=f"self_det_{opname}_x_amount",
                family="self_determined",
                source=source,
                top_name="t",
                drives=(("a", Value(0xAA, width=8)), ("shamt", Value(0, width=8, mask=0xFF))),
                expected=(("y", Value(0, width=8, mask=0xFF)),),
            )
        )
    return cases


def _self_determined_cases() -> list[EdgeCase]:
    return _unary_self_determined_cases() + _if_condition_width_cases() + _x_shift_amount_cases()


# =====================================================================
# Family 5: dynamic part-selects near seams
# =====================================================================

_PART_SELECT_BASES = [0, 56, 60, 63, 64, 120]
_PART_SELECT_SIG = sum(k << (8 * k) for k in range(16))  # 128-bit: byte k == k


def _dynamic_part_select_cases() -> list[EdgeCase]:
    cases: list[EdgeCase] = []
    read_source = (
        "module t(input [127:0] sig, input [7:0] base, output [7:0] y);\n    assign y = sig[base +: 8];\nendmodule\n"
    )
    write_source = (
        "module t(input [127:0] sig_in, input [7:0] base, input [7:0] wval, output [127:0] sig_out);\n"
        "    reg [127:0] sig;\n"
        "    always @(*) begin\n"
        "        sig = sig_in;\n"
        "        sig[base +: 8] = wval;\n"
        "    end\n"
        "    assign sig_out = sig;\n"
        "endmodule\n"
    )
    for base in _PART_SELECT_BASES:
        read_expected = (_PART_SELECT_SIG >> base) & 0xFF
        cases.append(
            EdgeCase(
                id=f"dyn_partsel_read_base{base}",
                family="dynamic_part_select",
                source=read_source,
                top_name="t",
                drives=(("sig", Value(_PART_SELECT_SIG, width=128)), ("base", Value(base, width=8))),
                expected=(("y", Value(read_expected, width=8)),),
            )
        )

        wval = 0xC3
        write_mask = 0xFF << base
        write_expected = (_PART_SELECT_SIG & ~write_mask & _mask(128)) | (wval << base)
        cases.append(
            EdgeCase(
                id=f"dyn_partsel_write_base{base}",
                family="dynamic_part_select",
                source=write_source,
                top_name="t",
                drives=(
                    ("sig_in", Value(_PART_SELECT_SIG, width=128)),
                    ("base", Value(base, width=8)),
                    ("wval", Value(wval, width=8)),
                ),
                expected=(("sig_out", Value(write_expected, width=128)),),
            )
        )
    return cases


# =====================================================================
# Known engine bugs (see notes/known_issues.md)
# =====================================================================


def _known_engine_bug(engine: str, case: EdgeCase) -> str | None:
    """Return a known_issues.md-linked reason if *engine* is known-broken for *case*.

    Full empirically-verified (Icarus/Verilator cross-checked) truth table for
    the two "self_det_unary_*_65_to_80_*" sub-families:

        op=~ unsigned: reference/vm/vm-fast WRONG (self-determined); compiled OK
        op=~ signed:   reference/vm/vm-fast OK; compiled WRONG (ignores `signed`)
        op=- unsigned: all OK
        op=- signed:   reference/vm/vm-fast OK; compiled WRONG (ignores `signed`)
    """
    if case.id == "self_det_unary_not_65_to_80_unsigned" and engine != "compiled":
        return "known reference/vm/vm-fast unary ~ self-determined-width bug (see notes/known_issues.md)"
    if case.id in ("self_det_unary_not_65_to_80_signed", "self_det_unary_neg_65_to_80_signed") and engine == "compiled":
        return "known compiled wide-emitter unary op ignores declared signedness (see notes/known_issues.md)"
    if case.id in ("seam63_shl64", "seam63_shr64", "seam64_shl64", "seam64_shr64") and engine == "compiled":
        return "known compiled narrow-path shift-by-word-width wraparound bug (see notes/known_issues.md)"
    return None


# =====================================================================
# Case enumeration
# =====================================================================

ALL_CASES: list[EdgeCase] = (
    _nested_ternary_cases()
    + _port_crossing_cases()
    + _word_seam_cases()
    + _self_determined_cases()
    + _dynamic_part_select_cases()
)


def _combo_params() -> list:
    params = []
    for engine in ENGINES:
        for case in ALL_CASES:
            pid = f"{engine}-{case.id}"
            bug = _known_engine_bug(engine, case)
            marks = [pytest.mark.xfail(strict=True, reason=bug)] if bug else []
            params.append(pytest.param(engine, case, id=pid, marks=marks))
    return params


# =====================================================================
# The test
# =====================================================================


@pytest.mark.cross_engine
@pytest.mark.parametrize("engine,case", _combo_params())
def test_edge_shape(engine: str, case: EdgeCase) -> None:
    result = _run_case(engine, case)
    ref_result = _run_case("reference", case)
    for name, oracle_value in case.expected:
        actual = result[name]
        assert actual == oracle_value, f"{case.id} [{name}]: {engine} got {actual}, oracle {oracle_value}"
        if not case.skip_ref_crosscheck:
            assert actual == ref_result[name], f"{case.id} [{name}]: {engine}={actual} reference={ref_result[name]}"
