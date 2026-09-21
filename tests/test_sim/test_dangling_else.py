"""Regression test for the parser's dangling-else ambiguity binding `else`
to the WRONG (outer) `if` instead of the nearest (inner) one.

Reported as a "row 7 hang" in an external project that turned out to be
neither an RTL bug nor a simulation-engine bug, but a genuine veriforge
Verilog *parser* bug. Minimal repro: a bare `if (A) if (B) X; else Y;` -- no
`begin`/`end` anywhere -- nested inside a properly-braced outer construct.
Building the equivalent logic directly as a DSL object (bypassing the
Verilog grammar entirely) behaved correctly; emitting that exact same logic
to Verilog text and re-parsing it did not, because the else fired
regardless of A's value, exactly as if the outer `if` never existed.

Root cause: `conditional_statement`'s grammar rule (`verilog.lark`) had a
single production with an *optional* trailing `else`:

    conditional_statement: KW_IF "(" expression ")" statement_or_null
        ( KW_ELSE statement_or_null )?
        | if_else_if_statement

For `if (a) if (b) x; else y;`, this is genuinely ambiguous -- the `else`
can be consumed by either the inner `if (b)` (correct, "always associate
with the closest unmatched if", IEEE 1800-2017 SS12.4) or left dangling
there and consumed by the outer `if (a)` instead (matching neither `if`'s
own text position). Both are valid derivations of the identical token
stream under the naive grammar. Lark's Earley parser resolves genuine
ambiguity with an internal cost heuristic that has no notion of Verilog's
actual disambiguation rule; empirically it picked the wrong (outer-binding)
parse every time, and the now-fully-redundant `if_else_if_statement`
alternative (any `if`/`else`-chain it can produce is already producible by
`conditional_statement`'s own recursion through its `else` branch) only
compounded the ambiguity.

Fixed with the standard "matched/unmatched statement" CFG split: a new
`matched_statement`/`matched_statement_or_null` pair of nonterminals that is
grammatically guaranteed to never end in a still-open (else-less) `if`, used
for the then-branch of any `if`/`else` and threaded through the handful of
other constructs that recurse through a single bare (non-`begin`/`end`)
statement and could otherwise leak the same ambiguity one level down
(`for`/`while`/`repeat`/`forever`, delay/event control, `wait`). See the
grammar's own "Dangling-else disambiguation" comment block (above
`statement_or_null` in `verilog.lark`) for the full design rationale, and
`transforms/_statements.py` for the corresponding extraction-side changes.

A follow-up report from the same external project initially suspected a
SEPARATE bug (a "simulator evaluation" bug, not a parser one) for a shape
that looked textually unambiguous: `if (a) if (b) begin s1; s2; end else
s3;` -- a real `begin`/`end` wraps `if (b)`'s own then-branch, so a human
reader has no trouble seeing `else` belongs to `if (b)`, not the outer,
bare `if (a)`. It turned out to be the exact same grammar defect: the OLD,
redundant `if_else_if_statement` alternative made EVERY `if`/`else`
construct in the grammar (ambiguous-looking or not) have more than one
valid parse derivation, and Lark's Earley parser occasionally still picked
the wrong one even here -- confirmed directly by diffing the raw parse
tree's `conditional_statement` node shapes before/after the fix: pre-fix,
the OUTER (`if (a)`)'s node had 5 children including a `KW_ELSE` (i.e. it
had gained an else it should never have), while the INNER (`if (b)`)'s own
node had only 3 (no else) -- a real, extraction-independent, tree-level
misparse despite the `begin`/`end` being right there in the source text and
the token stream containing no genuine textual ambiguity for a human
reader. See `test_begin_end_guarded_inner_if_still_misbound_pre_fix` below.
"""

from __future__ import annotations

import pytest

from veriforge.model.statements import IfStatement
from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES


def _parse(src: str, tmp_path):
    path = tmp_path / "dut.sv"
    path.write_text(src)
    return parse_file(path)


class TestDanglingElseBindsToNearestIf:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_nested_if_no_begin_end(self, engine, tmp_path):
        """`if (a) if (b) x = 1; else y = 1;` -- the reported shape.

        The `else` must bind to `if (b)`, not `if (a)`: with `a` held low,
        NEITHER branch should fire, regardless of `b`.
        """
        design = _parse(
            """
            module top (input a, input b, output reg x, output reg y);
                always @(*) begin
                    x = 0;
                    y = 0;
                    if (a)
                        if (b)
                            x = 1;
                        else
                            y = 1;
                end
            endmodule
            """,
            tmp_path,
        )
        sim = Simulator(design.modules[0], engine=engine)
        for a in (0, 1):
            for b in (0, 1):
                sim.drive("a", Value(a, width=1))
                sim.drive("b", Value(b, width=1))
                sim.settle()
                x = int(sim.read("x"))
                y = int(sim.read("y"))
                if not a:
                    assert (x, y) == (0, 0), f"a={a} b={b}: expected both low, got x={x} y={y}"
                elif b:
                    assert (x, y) == (1, 0), f"a={a} b={b}: expected x=1, got x={x} y={y}"
                else:
                    assert (x, y) == (0, 1), f"a={a} b={b}: expected y=1, got x={x} y={y}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_begin_end_still_binds_to_outer_if(self, engine, tmp_path):
        """`if (a) begin if (b) x = 1; end else y = 1;` -- explicit `begin`/
        `end` around the inner `if` closes it early, so here the `else`
        legitimately DOES belong to the outer `if (a)`."""
        design = _parse(
            """
            module top (input a, input b, output reg x, output reg y);
                always @(*) begin
                    x = 0;
                    y = 0;
                    if (a) begin
                        if (b)
                            x = 1;
                    end else
                        y = 1;
                end
            endmodule
            """,
            tmp_path,
        )
        sim = Simulator(design.modules[0], engine=engine)
        for a in (0, 1):
            for b in (0, 1):
                sim.drive("a", Value(a, width=1))
                sim.drive("b", Value(b, width=1))
                sim.settle()
                x = int(sim.read("x"))
                y = int(sim.read("y"))
                if not a:
                    assert (x, y) == (0, 1), f"a={a} b={b}: expected y=1, got x={x} y={y}"
                elif b:
                    assert (x, y) == (1, 0), f"a={a} b={b}: expected x=1, got x={x} y={y}"
                else:
                    assert (x, y) == (0, 0), f"a={a} b={b}: expected both low, got x={x} y={y}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_else_if_chain_still_correct(self, engine, tmp_path):
        """`if/else if/else if/else` chains must still work (the old,
        separately-ambiguous `if_else_if_statement` grammar alternative was
        removed as part of this fix; ordinary `conditional_statement`
        recursion through its `else` branch must fully replace it)."""
        design = _parse(
            """
            module top (
                input a, input b, input c,
                output reg x, output reg y, output reg z, output reg w
            );
                always @(*) begin
                    x = 0; y = 0; z = 0; w = 0;
                    if (a) x = 1;
                    else if (b) y = 1;
                    else if (c) z = 1;
                    else w = 1;
                end
            endmodule
            """,
            tmp_path,
        )
        sim = Simulator(design.modules[0], engine=engine)
        for a in (0, 1):
            for b in (0, 1):
                for c in (0, 1):
                    sim.drive("a", Value(a, width=1))
                    sim.drive("b", Value(b, width=1))
                    sim.drive("c", Value(c, width=1))
                    sim.settle()
                    got = tuple(int(sim.read(s)) for s in ("x", "y", "z", "w"))
                    if a:
                        expect = (1, 0, 0, 0)
                    elif b:
                        expect = (0, 1, 0, 0)
                    elif c:
                        expect = (0, 0, 1, 0)
                    else:
                        expect = (0, 0, 0, 1)
                    assert got == expect, f"a={a} b={b} c={c}: got {got}, expected {expect}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_if_inside_for_loop_no_begin_end(self, engine, tmp_path):
        """A bare (non-`begin`/`end`) `if`/`else` as a `for` loop's body,
        itself used as the then-branch of an outer bare `if`, must not leak
        its `else` up past the loop to the outer `if`."""
        design = _parse(
            """
            module top (input a, output reg [3:0] cnt);
                integer i;
                always @(*) begin
                    cnt = 0;
                    if (a)
                        for (i = 0; i < 4; i = i + 1)
                            if (i == 2)
                                cnt = cnt + 10;
                            else
                                cnt = cnt + 1;
                end
            endmodule
            """,
            tmp_path,
        )
        sim = Simulator(design.modules[0], engine=engine)
        sim.drive("a", Value(0, width=1))
        sim.settle()
        assert int(sim.read("cnt")) == 0, "outer if(a) false: loop must not run at all"

        sim.drive("a", Value(1, width=1))
        sim.settle()
        # i=0,1,3 take the else (+1 each = 3), i=2 takes the then (+10) = 13
        assert int(sim.read("cnt")) == 13

    def test_if_after_timing_control_no_begin_end_model_shape(self, tmp_path):
        """A bare `if`/`else` following inline `@(...)` event control (no
        `begin`/`end`), itself the then-branch of an outer bare `if`, must
        not leak its `else` up to the outer `if`.

        Checked at the model (AST) level rather than via simulation --
        mid-procedural-block event control (`@(posedge clk)` reached only
        conditionally, partway through an already-`posedge`-triggered
        `always` body) needs a second real clock edge to ever resolve on
        several engines and isn't what this grammar-level fix is about;
        the `for`-loop case above already exercises the same propagation
        path (`matched_loop_statement`) end-to-end via simulation, and
        `matched_procedural_timing_control_statement` is extracted by the
        exact same `_extract_procedural_timing_control_statement` code path
        (see `transforms/_statements.py`), so a structural check here is
        equally conclusive for the parser/model side of the fix.
        """
        design = _parse(
            """
            module top (input clk, input a, input b, output reg x, output reg y);
                always @(posedge clk)
                    if (a)
                        @(posedge clk)
                            if (b)
                                x <= 1;
                            else
                                y <= 1;
            endmodule
            """,
            tmp_path,
        )
        outer_if = design.modules[0].always_blocks[0].body
        assert isinstance(outer_if, IfStatement)
        assert outer_if.else_body is None, "outer if(a) must NOT have gained an else"
        timing_ctrl = outer_if.then_body
        inner_if = timing_ctrl.body if hasattr(timing_ctrl, "body") else timing_ctrl
        assert isinstance(inner_if, IfStatement)
        assert inner_if.else_body is not None, "inner if(b)'s else must bind to if(b), not if(a)"

    def test_begin_end_guarded_inner_if_still_misbound_pre_fix(self, tmp_path):
        """`if (a) if (b) begin s1; s2; end else s3;` -- a bare, `else`-less
        OUTER `if (a)` directly nesting an INNER `if (b)` whose own
        then-branch (only) is wrapped in `begin`/`end`, with `if (b)`'s own
        `else` following the closing `end`.

        Textually this is completely unambiguous to a human reader -- the
        `begin`/`end` around `if (b)`'s then-branch makes it obvious the
        trailing `else` belongs to `if (b)`, not the outer, bare `if (a)`.
        It nonetheless reproduced the exact same misparse pre-fix (see the
        module docstring): confirmed by an external project's real-world
        "silently dropped register write" bug report, reduced here to a
        minimal 3-level shape, and confirmed via `git worktree` bisection
        against the actual pre-fix commit that this reduction reproduces
        identically (outer `if (a)` spuriously gained the else, inner
        `if (b)` spuriously lost it) -- not just a shape that happens to
        look similar.
        """
        design = _parse(
            """
            module top (input a, input b, output reg x, output reg y, output reg z);
                always @(*) begin
                    x = 0; y = 0; z = 0;
                    if (a)
                        if (b)
                        begin
                            x = 1;
                            y = 1;
                        end
                        else
                            z = 1;
                end
            endmodule
            """,
            tmp_path,
        )
        outer_if = design.modules[0].always_blocks[0].body.statements[-1]
        assert isinstance(outer_if, IfStatement)
        assert outer_if.else_body is None, "outer if(a) must NOT have gained an else"
        inner_if = outer_if.then_body
        assert isinstance(inner_if, IfStatement)
        assert inner_if.else_body is not None, "inner if(b)'s else must bind to if(b), not if(a)"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_begin_end_guarded_inner_if_simulates_correctly(self, engine, tmp_path):
        """Simulation-level counterpart of the structural check above,
        against the reported real-world shape: a 2-beat "latch beat 0 of a
        burst, reset on the last beat" FSM, driven with a back-to-back
        (zero-gap) two-burst sequence -- reported as silently dropping the
        second burst's own beat-0 write."""
        design = _parse(
            """
            module top (
                input clk, input rst, input accept, input last, input [15:0] data,
                output reg cnt_r, output reg [15:0] buf_r0
            );
                always @(posedge clk)
                    if (rst)
                    begin
                        cnt_r <= 0;
                        buf_r0 <= 0;
                    end
                    else if (accept)
                        if (~last)
                        begin
                            if (cnt_r == 0)
                                buf_r0 <= data;
                            cnt_r <= cnt_r + 1;
                        end
                        else
                            cnt_r <= 0;
            endmodule
            """,
            tmp_path,
        )
        sim = Simulator(design.modules[0], engine=engine)
        sim.drive("rst", Value(1, width=1))
        sim.drive("accept", Value(0, width=1))
        sim.drive("last", Value(0, width=1))
        sim.drive("data", Value(0, width=16))
        sim.drive("clk", Value(0, width=1))
        sim.settle()
        sim.drive("clk", Value(1, width=1))
        sim.settle()

        def step(accept, last, data):
            sim.drive("rst", Value(0, width=1))
            sim.drive("accept", Value(accept, width=1))
            sim.drive("last", Value(last, width=1))
            sim.drive("data", Value(data, width=16))
            sim.drive("clk", Value(0, width=1))
            sim.settle()
            sim.drive("clk", Value(1, width=1))
            sim.settle()
            return int(sim.read("cnt_r")), int(sim.read("buf_r0"))

        # burst A: beat0=0x1111, beat1(last)=0x9999 -- then IMMEDIATELY
        # (zero gap) burst B: beat0=0x2222, beat1(last)=0x8888.
        assert step(1, 0, 0x1111) == (1, 0x1111)
        assert step(1, 1, 0x9999) == (0, 0x1111)
        assert step(1, 0, 0x2222) == (1, 0x2222), "burst B's own beat0 write must not be dropped"
        assert step(1, 1, 0x8888) == (0, 0x2222)
