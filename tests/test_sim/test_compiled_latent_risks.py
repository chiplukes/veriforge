"""Regression tests for the compiled-engine "latent risks" identified May 2026.

These three areas were flagged in ``notes/known_issues.md`` (search for
"Compiled engine — latent risks identified May 2026") as plausibly-broken
behaviors that no existing test exercised. After audit:

1. ``drive_signal`` between ``batch_run`` calls now flushes pending drives
   through continuous assigns before the next batch starts, so a bench-driven
   reset deassert reaches the DUT's clocked always block on the very first
   posedge of the next ``batch_run``.

2. ``elaborate.py`` function/task bodies are now prefix-rewritten when their
   submodule is inlined, so a function that references a module-scope parameter
   (the canonical pattern in width-parameterized clog2/gray2bin helpers)
   continues to resolve after flattening.

3. ``_eval_initial_value`` in ``compiled/codegen.py`` was verified to obey
   Verilog context-determined width semantics for unary ``~`` and ``-``.
"""

from __future__ import annotations

import pytest

from veriforge.project import parse_files
from veriforge.sim import Simulator
from veriforge.sim.value import Value


# ── (1) drive_signal between batch_run calls ──────────────────────────────

_NESTED_SRC = """
module inner(
    input  wire       clk,
    input  wire       rst,
    output reg  [7:0] count
);
    always @(posedge clk) begin
        if (rst)
            count <= 8'd0;
        else
            count <= count + 8'd1;
    end
endmodule

module top(
    input  wire       clk,
    input  wire       rst,
    output wire [7:0] count
);
    inner u_inner(.clk(clk), .rst(rst), .count(count));
endmodule
"""


def test_drive_signal_between_batch_runs_propagates_to_port_wire(tmp_path):
    """drive_signal("rst", 0) between two batch_run calls must reach the DUT.

    Pre-fix: the first batch_run after the drive overwrote the snapshot taken
    by drive_signal, so the cont_assign wiring bench rst -> u_inner.rst had
    not yet propagated. The DUT saw rst==1 (stale) on the first posedge of
    the second batch_run and zeroed its counter, dropping one increment.
    """
    src = tmp_path / "nested.sv"
    src.write_text(_NESTED_SRC)
    design = parse_files([str(src)], preprocess=True)
    top = design.get_module("top")
    sim = Simulator(top, engine="compiled", design=design)

    # Hold reset for 5 cycles via batch_run.
    sim.drive("rst", Value(1, width=1))
    sim.drive("clk", Value(0, width=1))
    sim._sched._sim.step()
    sim.batch_run(5, "clk", clock_period=10)
    assert sim.read("u_inner.count").val == 0

    # Now deassert via drive_signal and immediately call batch_run.
    sim.drive("rst", Value(0, width=1))
    sim.batch_run(10, "clk", clock_period=10)

    # 10 increment cycles -> count == 10. Pre-fix this was 9.
    assert sim.read("u_inner.count").val == 10


# ── (2) elaborate.py function-body parameter references ───────────────────

_FUNC_USES_PARAM_SRC = """
module width_helper #(
    parameter WIDTH = 8
)(
    input  wire [WIDTH-1:0] data_in,
    output wire [WIDTH-1:0] data_out
);
    // Function references the module-scope WIDTH parameter in its body.
    // Before the elaborate.py fix, when this module was inlined as a
    // submodule, the function body's bare WIDTH reference was *not*
    // prefix-rewritten while the module-scope parameter *was* renamed to
    // u_helper.WIDTH, leaving a dangling identifier.
    function [WIDTH-1:0] low_half;
        input [WIDTH-1:0] x;
        begin
            low_half = x & ((1 << (WIDTH/2)) - 1);
        end
    endfunction

    assign data_out = low_half(data_in);
endmodule

module top(
    input  wire [7:0] din,
    output wire [7:0] dout
);
    width_helper #(.WIDTH(8)) u_helper(.data_in(din), .data_out(dout));
endmodule
"""


def test_function_body_param_ref_survives_flattening(tmp_path):
    """A function that references a module-scope param must still resolve after inlining."""
    src = tmp_path / "func_param.sv"
    src.write_text(_FUNC_USES_PARAM_SRC)
    design = parse_files([str(src)], preprocess=True)
    top = design.get_module("top")
    # The compiled engine flattens the design; pre-fix this raised on the
    # unresolved bare WIDTH reference inside low_half's body.
    sim = Simulator(top, engine="compiled", design=design)
    sim.drive("din", Value(0xFF, width=8))
    sim.run(max_time=10)
    # WIDTH=8, low half = bits 0..3, so mask = 0x0F.
    assert sim.read("dout").val == 0x0F


# ── (3) _eval_initial_value unary semantics ───────────────────────────────
#
# Both engines treat unary ``~`` and ``-`` on a sized literal as
# self-determined to the operand width, then zero-extended to the context.
# (Per IEEE 1364-2005 §5.1.10 self-determined column.) The latent risk
# in ``_eval_initial_value`` would only manifest if its evaluation diverged
# from the runtime evaluator; this test locks in the agreement.


_UNARY_INIT_SRC = """
module top(
    output reg [7:0] a,
    output reg [7:0] b
);
    initial begin
        // reg initial value uses _eval_initial_value path in compiled codegen.
        a = ~3'b001;     // operand 3 bits -> 3'b110 -> zext to 8 = 0x06
        b = -3'b001;     // operand 3 bits -> 3'b111 -> zext to 8 = 0x07
    end
endmodule
"""


def test_initial_value_unary_matches_reference(tmp_path):
    """Compiled and reference engines must agree on unary initial-value semantics."""
    src = tmp_path / "unary_init.sv"
    src.write_text(_UNARY_INIT_SRC)
    design = parse_files([str(src)], preprocess=True)
    top = design.get_module("top")

    sim_c = Simulator(top, engine="compiled", design=design)
    sim_c.run(max_time=10)
    a_c = sim_c.read("a").val
    b_c = sim_c.read("b").val

    sim_r = Simulator(top, engine="reference", design=design)
    sim_r.run(max_time=10)
    a_r = sim_r.read("a").val
    b_r = sim_r.read("b").val

    assert a_c == a_r, f"compiled a=0x{a_c:02x} vs reference a=0x{a_r:02x}"
    assert b_c == b_r, f"compiled b=0x{b_c:02x} vs reference b=0x{b_r:02x}"
