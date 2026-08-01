"""Power operator (`**`) regression suite (work plan item 2.7, tenth+ wave).

`**` was never covered by the differential fuzzer (`test_differential.py`
excludes it from its generated operator set, and every fuzzer case assigns
to a fixed 96-bit destination -- see the module docstring note below on why
that specifically doesn't work for `**` yet) and turned out to have three
independent, real bugs across all four engines, found and fixed by reading
the IEEE 1364-2005 primary spec text directly:

  1. Width: `**`'s exponent must always be self-determined (never
     context-propagated), and `**`'s own self-determined width is the
     BASE's width alone (Table 5-22 groups it with the shift row `>> <<
     ** >>> <<<` -> `L(i)`, not the generic `max(L(i),L(j))` row).
  2. Signedness was entirely unimplemented -- `Value.__pow__` (and every
     engine's equivalent) always treated both operands as unsigned raw
     bit patterns.
  3. Table 5-6's negative-base/negative-exponent special values (e.g.
     `0 ** -1` == 'bx, `2 ** -1` == 0, `(-1) ** -3` == -1) were not
     implemented at all.

Every case here is verified against Icarus Verilog (see the scratchpad
bisection scripts referenced in notes/known_issues.md's write-up for this
wave) before being encoded as a fixed oracle value in this file, following
the same cross-engine-parametrized pattern as `test_compiled_edge_shapes.py`.

**Not covered by this file**: `**` used in a >64-bit-destination context
on the compiled engine. That surfaced a SEPARATE, pre-existing (not
introduced by the `**` fix) architectural gap: `_compile_continuous_
assigns`'s last-resort narrow-scalar fallback (used when neither the
wide-scratch emitter nor the wide-Python-bignum emitter can handle an
expression -- true for ANY use of `**`, since neither has ever supported
it) always writes only `c.val[lhs_sid]`/`c.mask[lhs_sid]`, never the wide
`c.wide_val`/`c.wide_offset` storage a >64-bit destination actually uses
-- so the destination silently stays at its reset value of 0 forever,
with no warning. This is a general gap (would affect any other
wide-emitter-unsupported expression type assigned to a wide destination,
not something specific to `**`) documented in `notes/known_issues.md`
rather than fixed here; the one case below demonstrating it is a known,
strict xfail.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from veriforge.analysis.resolver import link_instances, resolve_port_connections
from veriforge.model.design import Design
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


def _parse_design(source: str) -> Design:
    vp = verilog_parser(start="source_text")
    tree = vp.build_tree(source)
    return tree_to_design(tree, source_file=None)


def _sim_for(source: str, engine: str) -> Simulator:
    design = _parse_design(source)
    link_instances(design)
    resolve_port_connections(design)
    top = next(m for m in design.modules if m.name == "t")
    return Simulator(top, engine=engine, design=design)


@dataclass(frozen=True)
class PowCase:
    id: str
    source: str
    drives: tuple[tuple[str, Value], ...]
    expected: tuple[tuple[str, Value], ...]
    xfail_engines: frozenset[str] = frozenset()
    """Engines with a known, documented (not this-fix's-scope) bug for this case."""


_RESULTS_CACHE: dict[tuple[str, str], dict[str, Value]] = {}


def _run_case(engine: str, case: PowCase) -> dict[str, Value]:
    key = (engine, case.id)
    cached = _RESULTS_CACHE.get(key)
    if cached is not None:
        return cached
    sim = _sim_for(case.source, engine)
    for name, val in case.drives:
        sim.drive(name, val)
    sim.settle()
    result = {name: sim.read(name) for name, _ in case.expected}
    _RESULTS_CACHE[key] = result
    return result


# =====================================================================
# Width / self-determination
# =====================================================================

_WIDTH_CASES = [
    PowCase(
        id="context_widens_base_before_computing",
        # 15**2=225 needs the base widened to the 8-bit destination
        # BEFORE squaring -- computing at the base's own 4-bit width
        # first (15**2 mod 16 = 1) then zero-extending would be wrong.
        source="module t(input clk, input [3:0] ua, output [7:0] y);\n    assign y = ua ** 2'd2;\nendmodule\n",
        drives=(("ua", Value(15, width=4)),),
        expected=(("y", Value(225, width=8)),),
    ),
    PowCase(
        id="self_determined_width_is_base_only",
        # Self-determined (concat member) width is the BASE's width (4),
        # not max(base, exponent)=8: 3**4=81, truncated to 4 bits = 1.
        source="module t(input clk, input [3:0] ua, input [7:0] ub, output [15:0] y);\n"
        "    assign y = {12'b0, (ua ** ub)};\nendmodule\n",
        drives=(("ua", Value(3, width=4)), ("ub", Value(4, width=8))),
        expected=(("y", Value(1, width=16)),),
    ),
    PowCase(
        id="nested_context_operator_in_exponent",
        # The exponent is self-determined but still resizes a NESTED
        # context-determined operator within it at its own width before
        # `**` runs: exp = ~(cond?1:3) at 2 bits = ~3'b...01 -> 2'b00 = 0
        # (cond=0 selects the false branch, 2'd3), so ua**0 = 1.
        source="module t(input clk, input [3:0] ua, input [1:0] cond, output [7:0] y);\n"
        "    assign y = {4'b0, (ua ** (~(cond ? 2'd1 : 2'd3)))};\nendmodule\n",
        drives=(("ua", Value(3, width=4)), ("cond", Value(0, width=2))),
        expected=(("y", Value(1, width=8)),),
    ),
]

# =====================================================================
# Signedness + IEEE 1364-2005 Table 5-6 special values
# =====================================================================

_TABLE_5_6_SOURCE = (
    "module t(input clk, input signed [3:0] sa, input signed [3:0] sb, output signed [3:0] y);\n"
    "    assign y = sa ** sb;\nendmodule\n"
)


def _t56(name: str, base: int, exp: int, expect_val: int | None) -> PowCase:
    """expect_val=None means the table's one genuinely-undefined 'bx cell."""
    if expect_val is None:
        expected = (("y", Value(0, width=4, mask=0xF)),)
    else:
        expected = (("y", Value(expect_val & 0xF, width=4)),)
    return PowCase(
        id=f"table56_{name}",
        source=_TABLE_5_6_SOURCE,
        drives=(("sa", Value(base & 0xF, width=4)), ("sb", Value(exp & 0xF, width=4))),
        expected=expected,
    )


_TABLE_5_6_CASES = [
    _t56("truncation_of_result_to_base_width", -3, 2, -7),  # 9 mod 16, as signed 4-bit
    _t56("large_base_negative_exp", 2, -1, 0),
    _t56("large_negative_base_negative_exp", -2, -1, 0),
    _t56("zero_base_negative_exp_is_x", 0, -1, None),
    _t56("zero_base_positive_exp", 0, 3, 0),
    _t56("zero_exp_always_one", 0, 0, 1),
    _t56("base_one_negative_exp", 1, -1, 1),
    _t56("base_minus_one_odd_negative_exp", -1, -1, -1),
    _t56("base_minus_one_even_negative_exp", -1, -2, 1),
    _t56("base_minus_one_even_positive_exp", -1, 4, 1),
    _t56("base_minus_one_odd_positive_exp", -1, 5, -1),
]

# =====================================================================
# Known, pre-existing, separately-scoped gap: `**` over a >64-bit
# operand/destination on the two C-based engines. Neither `OP_POW`/
# `OP_SPOW` (`_interp_fast.pyx`) nor the compiled engine's `**` codegen
# consult the wide (`wflag`/`wv`/`wm` or `c.wide_val`) representation at
# all -- both only ever read a signal's narrow low-word slot. On
# `vm-fast` this silently computes a WRONG (not just narrow-truncated)
# answer, since the exponent's own wide/narrow-slot bookkeeping gets
# misread too. On `compiled` it's worse: neither wide emitter handles
# `**`, so the top-level assign falls through to the last-resort
# narrow-scalar fallback in `_compile_continuous_assigns`, which only
# ever writes `c.val[lhs_sid]`/`c.mask[lhs_sid]` -- never touching the
# wide destination's real `c.wide_val` storage -- so the signal silently
# stays at its reset value of 0 forever, with no warning. `reference`
# and `vm` (pure Python, arbitrary-width `int`) are unaffected. See
# notes/known_issues.md for the full write-up; this is a general
# architectural gap (would affect any other wide-emitter-unsupported
# expression assigned to a wide destination), not something specific to
# `**`, and deliberately not fixed as part of this fix's scope.
# =====================================================================

_WIDE_DEST_CASES = [
    PowCase(
        id="wide_operand_not_yet_supported_on_c_engines",
        source="module t(input clk, input [79:0] wa, output [95:0] y);\n    assign y = wa ** 2'd2;\nendmodule\n",
        drives=(("wa", Value(3, width=80)),),
        expected=(("y", Value(9, width=96)),),
        xfail_engines=frozenset({"compiled", "vm-fast"}),
    ),
]

ALL_CASES: list[PowCase] = _WIDTH_CASES + _TABLE_5_6_CASES + _WIDE_DEST_CASES


def _combo_params() -> list:
    params = []
    for engine in ENGINES:
        for case in ALL_CASES:
            pid = f"{engine}-{case.id}"
            marks = []
            if engine in case.xfail_engines:
                marks.append(
                    pytest.mark.xfail(
                        strict=True,
                        reason="compiled engine: ** unsupported by both wide emitters; "
                        "last-resort narrow fallback silently no-ops for a wide "
                        "destination (see notes/known_issues.md)",
                    )
                )
            params.append(pytest.param(engine, case, id=pid, marks=marks))
    return params


@pytest.mark.cross_engine
@pytest.mark.parametrize("engine,case", _combo_params())
def test_power_operator(engine: str, case: PowCase) -> None:
    result = _run_case(engine, case)
    for name, oracle_value in case.expected:
        actual = result[name]
        assert actual.val == oracle_value.val, (
            f"{case.id} [{engine}]: {name}.val = {actual.val:#x}, expected {oracle_value.val:#x}"
        )
        assert actual.mask == oracle_value.mask, (
            f"{case.id} [{engine}]: {name}.mask = {actual.mask:#x}, expected {oracle_value.mask:#x}"
        )
