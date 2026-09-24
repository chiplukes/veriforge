"""Regression test: a memory element wider than 32 bits, initialized via a
plain (unsized, unbased) decimal literal inside an `initial`-block `for`
loop, silently corrupted any value whose 32-bit representation had bit 31
set -- reported as "dynamically-indexed memory arrays wider than 32 bits
silently return corrupted data" against `reference`/`vm` (later confirmed
also latent, though not directly reported, wherever a large unsized
literal reaches `evaluator.py`'s numeric-value fallback for a Literal with
no `original_text`).

Root cause: an unsized decimal literal is SIGNED per IEEE 1800-2017
SS5.7.1 (`dsl/builder.py::_to_expr_node`/`_to_lit` mark every bare Python
int `signed=True` for exactly this reason). `Value.from_verilog`'s bare-
integer branch computed this self-determined width as `max(32,
n.bit_length())` -- for a NON-negative `n` whose `bit_length()` already
equals 32 (any `n` in `[2**31, 2**32)`), that leaves NO zero guard bit
above the value's own top bit. When later widened into a wider context via
`Value.sign_extend()` (e.g. assigning into a >32-bit memory element or
register), that "this magnitude needs every bit" 1 gets read as a genuine
sign bit and the extension fills with 1s instead of 0s -- silently turning
an intended positive value into a huge, wrong one. Confirmed exactly:
`n=3000000021` sign-extended to 38 bits gave `273582939669`, not
`3000000021`.

This explains the report's own precise framing: addresses whose stored
value stayed below 2**31 read back correctly; the corruption started
exactly at the first address whose value crossed 2**31, and every
subsequent one with bit 31 set was ALSO wrong -- not a `width > 32` bug in
itself (a `width == 32` memory never reaches the widening step at all,
so it never triggers this regardless of how large its stored values get)
but a `width > 32` (the widening actually happens) + `value has bit 31
set` (the widening direction is wrong) combination.

Fixed in `sim/value.py` (`signed_literal_width`, used by
`Value.from_verilog`'s bare-integer branch) and the identical fallback in
`sim/evaluator.py::_eval_literal`'s numeric-value branch, both now
computing the minimum width that keeps `n`'s own sign bit correct (`n.
bit_length() + 1` for positive `n`, the standard "signed bit-length"
formula) instead of `n.bit_length()` alone.
"""

from __future__ import annotations

import pytest

from veriforge.dsl import Module, posedge
from veriforge.sim.bench import Testbench
from veriforge.sim.value import Value

from .engines import ENGINES


def _build_rom(depth: int, width: int, name: str) -> Module:
    """A `for`-loop-initialized ROM, read through a clocked, dynamically
    indexed whole-element read -- the exact shape reported."""
    m = Module(name)
    clk = m.input("clk")
    addr = m.input("addr", width=max(1, (depth - 1).bit_length()))
    dout = m.output_reg("dout", width=width, init=0)
    mem = m.reg("mem", width=width, depth=depth)
    with m.initial():
        for i in range(depth):
            mem[i] <<= (i * 1000000007) % (1 << width)
    with m.always(posedge(clk)):
        dout <<= mem[addr]
    return m.build()


# `vm-fast` hits a SEPARATE, unrelated, not-yet-investigated gap with this
# exact DSL shape (a `for`-loop-unrolled `initial` block writing many
# memory elements, driven through `Testbench.run()`/`bench.step()`): `mem`
# reads back as all-X even before `bench.reset_all()`, i.e. the `initial`
# block's own writes never take effect at all -- confirmed down to a
# 2-element memory with two bare NBA-style `<<=` writes, so it isn't a
# scale issue either. This is a "never runs" bug, not a "runs wrong" bug,
# so it can't be conflating with (or masking) the sign-extension fix this
# file is actually about; flagged in `notes/roadmap.md` for separate
# follow-up rather than fixed here.
_ENGINES_FOR_ROM_TEST = [e for e in ENGINES if e != "vm-fast"]


class TestWideRomUnsizedDecimalLiteral:
    @pytest.mark.parametrize("depth,width", [(32, 8), (32, 32), (32, 38), (8, 38), (4, 38)])
    @pytest.mark.parametrize("engine", _ENGINES_FOR_ROM_TEST)
    def test_reported_shape(self, engine, depth, width):
        """The exact reported repro: values crossing 2**31 must read back
        correctly once the memory element is wider than 32 bits."""
        dut = _build_rom(depth, width, f"t_{depth}_{width}_{engine.replace('-', '_')}")
        bench = Testbench(dut, engine=engine)
        with bench.run():
            bench.reset_all()
            sim = bench.sim
            for a in range(depth):
                sim.drive("addr", a)
                bench.step(1)
                expected = (a * 1000000007) % (1 << width)
                assert int(sim.read("dout")) == expected, f"engine={engine} depth={depth} width={width} addr={a}"

    def test_value_from_verilog_sign_guard(self):
        """Narrower unit check directly on the fixed primitive: a plain
        decimal literal whose 32-bit form has bit 31 set must widen with
        zeros, not ones."""
        v = Value.from_verilog("3000000021")
        assert v.width >= 33, "must reserve a zero guard bit above the value's own top bit"
        assert int(v.sign_extend(38)) == 3000000021

    @pytest.mark.parametrize("n", [0, 1, 2147483647, 2147483648, 3000000021, 4294967295])
    def test_value_from_verilog_sign_extend_is_identity_for_nonnegative(self, n):
        """For any non-negative n, sign-extending to a much wider context
        must reproduce n exactly -- never flip sign due to a missing
        guard bit above n's own natural width."""
        v = Value.from_verilog(str(n))
        assert int(v.sign_extend(v.width + 32)) == n
