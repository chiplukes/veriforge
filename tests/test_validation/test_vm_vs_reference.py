"""Cross-validation: compare simulation engines against the reference engine.

For each test case we:
  1. Parse the same Verilog source through all engines.
  2. Run each through our Simulator (engine="reference", "vm", "vm-fast", "compiled").
  3. Record VCD output from each.
  4. Parse VCD strings and compare signal values at each time step.

This ensures every engine produces identical results to the reference
tree-walking evaluator/executor on a wide range of Verilog constructs.
"""

from __future__ import annotations

import io
import logging

from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.value import Value
from veriforge.sim.vcd import VcdWriter
from veriforge.sim.vcd_compare import compare_vcd, parse_vcd
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser

log = logging.getLogger(__name__)

# Check if the Cython VM fast path is available
try:
    from veriforge.sim.vm._interp_fast import CyContext as _CyCtx  # noqa: F401

    HAS_VM_FAST = True
except ImportError:
    HAS_VM_FAST = False

# Check if the compiled engine is available (requires Cython + C compiler)
try:
    from veriforge.sim.compiled.compiler import CythonCompiler

    _compiler = CythonCompiler()
    _test_src = "cpdef int _test(): return 1"
    _test_mod = _compiler.compile_pyx(_test_src, "_vm_ref_probe")
    HAS_COMPILED = True
except Exception:
    HAS_COMPILED = False


# ── Helpers ──────────────────────────────────────────────────────────


def _run_engine(
    verilog_src: str,
    engine: str,
    *,
    max_time: int = 1000,
) -> str:
    """Parse Verilog → model → simulate with *engine* → return VCD string."""
    parser = verilog_parser(start="source_text")
    tree = parser.build_tree(verilog_src)
    design = tree_to_design(tree)

    if not design.modules:
        raise RuntimeError("No modules found in Verilog source")

    module = design.modules[0]
    sim = Simulator(module, engine=engine)

    # Set up VCD recording via scheduler callback
    vcd_buf = io.StringIO()
    vcd_writer = VcdWriter(vcd_buf, timescale="1ns")

    for name in sorted(sim._sched.ctx._signals):
        sig_val = sim._sched.ctx._signals[name]
        vcd_writer.add_signal(name, width=sig_val.width)

    vcd_writer.write_header()

    initial_vals = {name: val for name, val in sim._sched.ctx._signals.items()}
    vcd_writer.write_initial(initial_vals)

    def _record_signals(sched) -> None:
        vcd_writer.set_time(sched.time)
        for name in sched.ctx._signals:
            vcd_writer.change(name, sched.ctx._signals[name])

    sim._sched._on_time_step = _record_signals

    # Bump loop limit for reference engine
    if engine == "reference":
        sim._sched.executor.loop_limit = max(100_000, max_time * 4)

    sim.run(max_time=max_time)
    vcd_writer.finalize()
    return vcd_buf.getvalue()


def _validate(
    verilog_src: str,
    *,
    max_time: int = 1000,
    signals: list[str] | None = None,
    ignore_signals: set[str] | None = None,
) -> list[str]:
    """Run all available engines and compare VCD output against reference.

    Always compares vm and vm-fast (pure-Python and Cython VM paths).
    When the compiled engine is available, also compares compiled vs reference.

    Returns list of differences (empty = match).
    """
    ref_vcd = _run_engine(verilog_src, "reference", max_time=max_time)
    ref = parse_vcd(ref_vcd, strip_hierarchy=True)

    diffs: list[str] = []

    for engine_name in ("vm", "vm-fast"):
        if engine_name == "vm-fast" and not HAS_VM_FAST:
            log.info("vm-fast skipped: Cython extension not built")
            continue
        engine_vcd = _run_engine(verilog_src, engine_name, max_time=max_time)
        engine_parsed = parse_vcd(engine_vcd, strip_hierarchy=True)
        engine_diffs = compare_vcd(
            ref, engine_parsed, signals=signals, ignore_signals=ignore_signals, max_time=max_time
        )
        diffs.extend(f"[{engine_name}] {d}" for d in engine_diffs)

    # Also validate compiled engine when available
    if HAS_COMPILED:
        try:
            compiled_vcd = _run_engine(verilog_src, "compiled", max_time=max_time)
            compiled = parse_vcd(compiled_vcd, strip_hierarchy=True)
            compiled_diffs = compare_vcd(
                ref, compiled, signals=signals, ignore_signals=ignore_signals, max_time=max_time
            )
            diffs.extend(f"[compiled] {d}" for d in compiled_diffs)
        except NotImplementedError as exc:
            log.info("Compiled engine skipped: %s", exc)

    return diffs


# ══════════════════════════════════════════════════════════════════════
# Test Cases
# ══════════════════════════════════════════════════════════════════════


class TestCombinationalLogic:
    """Validate combinational logic matches between engines."""

    def test_adder(self):
        verilog = """\
module test_adder;
    reg [7:0] a, b;
    wire [7:0] y;
    assign y = a + b;

    initial begin
        a = 8'd10; b = 8'd20;
        #10;
        a = 8'd100; b = 8'd50;
        #10;
        a = 8'd255; b = 8'd1;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_bitwise_ops(self):
        verilog = """\
module test_bitwise;
    reg [7:0] a, b;
    wire [7:0] y_and, y_or, y_xor, y_not;
    assign y_and = a & b;
    assign y_or  = a | b;
    assign y_xor = a ^ b;
    assign y_not = ~a;

    initial begin
        a = 8'hAA; b = 8'h55;
        #10;
        a = 8'hFF; b = 8'h0F;
        #10;
        a = 8'h00; b = 8'hFF;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y_and", "y_or", "y_xor", "y_not"])
        assert not diffs, "\n".join(diffs)

    def test_shift_ops(self):
        verilog = """\
module test_shift;
    reg [7:0] a;
    wire [7:0] y_shl, y_shr;
    assign y_shl = a << 2;
    assign y_shr = a >> 3;

    initial begin
        a = 8'hAA;
        #10;
        a = 8'h0F;
        #10;
        a = 8'hFF;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y_shl", "y_shr"])
        assert not diffs, "\n".join(diffs)

    def test_ternary(self):
        verilog = """\
module test_ternary;
    reg sel;
    reg [7:0] a, b;
    wire [7:0] y;
    assign y = sel ? a : b;

    initial begin
        sel = 0; a = 8'd10; b = 8'd20;
        #10;
        sel = 1;
        #10;
        a = 8'd99;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["sel", "a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_concatenation(self):
        verilog = """\
module test_concat;
    reg [3:0] hi, lo;
    wire [7:0] y;
    assign y = {hi, lo};

    initial begin
        hi = 4'hA; lo = 4'h5;
        #10;
        hi = 4'hF; lo = 4'h0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["hi", "lo", "y"])
        assert not diffs, "\n".join(diffs)

    def test_reduction_ops(self):
        verilog = """\
module test_reduction;
    reg [7:0] a;
    wire y_and, y_or, y_xor;
    assign y_and = &a;
    assign y_or  = |a;
    assign y_xor = ^a;

    initial begin
        a = 8'hFF;
        #10;
        a = 8'h00;
        #10;
        a = 8'hA5;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y_and", "y_or", "y_xor"])
        assert not diffs, "\n".join(diffs)

    def test_comparison_ops(self):
        verilog = """\
module test_compare;
    reg [7:0] a, b;
    wire eq, ne, lt, gt, le, ge;
    assign eq = (a == b);
    assign ne = (a != b);
    assign lt = (a < b);
    assign gt = (a > b);
    assign le = (a <= b);
    assign ge = (a >= b);

    initial begin
        a = 8'd10; b = 8'd10;
        #10;
        a = 8'd5; b = 8'd10;
        #10;
        a = 8'd20; b = 8'd10;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "eq", "ne", "lt", "gt", "le", "ge"])
        assert not diffs, "\n".join(diffs)

    def test_arithmetic(self):
        verilog = """\
module test_arith;
    reg [7:0] a, b;
    wire [7:0] y_sub, y_mul, y_mod;
    assign y_sub = a - b;
    assign y_mul = a * b;
    assign y_mod = a % b;

    initial begin
        a = 8'd100; b = 8'd30;
        #10;
        a = 8'd7; b = 8'd3;
        #10;
        a = 8'd0; b = 8'd1;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y_sub", "y_mul", "y_mod"])
        assert not diffs, "\n".join(diffs)

    def test_cascaded_assigns(self):
        """Delta cycle chain: a → b → c → d."""
        verilog = """\
module test_cascade;
    reg [7:0] a;
    wire [7:0] b, c, d;
    assign b = a + 8'd1;
    assign c = b + 8'd1;
    assign d = c + 8'd1;

    initial begin
        a = 8'd0;
        #10; a = 8'd10;
        #10; a = 8'd100;
        #10; a = 8'd252;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "c", "d"])
        assert not diffs, "\n".join(diffs)


class TestSequentialLogic:
    """Validate clocked (sequential) logic matches between engines."""

    def test_dff(self):
        verilog = """\
module test_dff;
    reg clk, d;
    reg q;

    always @(posedge clk)
        q <= d;

    initial begin
        clk = 0; d = 0; q = 0;
        #5; clk = 1;
        #5; clk = 0; d = 1;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0; d = 0;
        #5; clk = 1;
        #5; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "d", "q"])
        assert not diffs, "\n".join(diffs)

    def test_counter(self):
        verilog = """\
module test_counter;
    reg clk, rst;
    reg [3:0] count;

    always @(posedge clk) begin
        if (rst)
            count <= 4'd0;
        else
            count <= count + 4'd1;
    end

    initial begin
        clk = 0; rst = 1; count = 4'd0;
        #5; clk = 1;
        #5; clk = 0; rst = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "rst", "count"])
        assert not diffs, "\n".join(diffs)

    def test_shift_register(self):
        verilog = """\
module test_shiftreg;
    reg clk, din;
    reg [3:0] sr;

    always @(posedge clk)
        sr <= {sr[2:0], din};

    initial begin
        clk = 0; din = 0; sr = 4'd0;
        #5; din = 1; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5; din = 0; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "din", "sr"])
        assert not diffs, "\n".join(diffs)

    def test_pipeline(self):
        """3-stage pipeline — verifies NBA swap semantics."""
        verilog = """\
module test_pipeline;
    reg clk;
    reg [7:0] d;
    reg [7:0] stage1, stage2, stage3;

    always @(posedge clk) begin
        stage3 <= stage2;
        stage2 <= stage1;
        stage1 <= d;
    end

    initial begin
        clk = 0; d = 8'd0; stage1 = 8'd0; stage2 = 8'd0; stage3 = 8'd0;
        d = 8'd1;
        #5; clk = 1; #5; clk = 0;
        d = 8'd2;
        #5; clk = 1; #5; clk = 0;
        d = 8'd3;
        #5; clk = 1; #5; clk = 0;
        d = 8'd4;
        #5; clk = 1; #5; clk = 0;
        d = 8'd5;
        #5; clk = 1; #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "d", "stage1", "stage2", "stage3"])
        assert not diffs, "\n".join(diffs)

    def test_async_reset(self):
        verilog = """\
module test_async_rst;
    reg clk, rst, d;
    reg q;

    always @(posedge clk or posedge rst) begin
        if (rst)
            q <= 1'b0;
        else
            q <= d;
    end

    initial begin
        clk = 0; rst = 0; d = 0; q = 0;
        d = 1;
        #5; clk = 1; #5; clk = 0;
        d = 0;
        #5; clk = 1; #5; clk = 0;
        rst = 1;
        #5;
        rst = 0; d = 1;
        #5; clk = 1; #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "rst", "d", "q"])
        assert not diffs, "\n".join(diffs)


class TestInitialBlocks:
    """Validate initial block execution with delays."""

    def test_sequential_delays(self):
        verilog = """\
module test_delays;
    reg [7:0] a;

    initial begin
        a = 8'd0;
        #10; a = 8'd10;
        #10; a = 8'd20;
        #10; a = 8'd30;
        #10; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a"])
        assert not diffs, "\n".join(diffs)

    def test_if_else(self):
        verilog = """\
module test_ifelse;
    reg [7:0] a, y;

    initial begin
        a = 8'd5;
        if (a > 8'd3)
            y = 8'd1;
        else
            y = 8'd0;
        #10;
        a = 8'd2;
        if (a > 8'd3)
            y = 8'd1;
        else
            y = 8'd0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "y"])
        assert not diffs, "\n".join(diffs)

    def test_case_statement(self):
        verilog = """\
module test_case;
    reg [1:0] sel;
    reg [7:0] y;

    initial begin
        sel = 2'd0; y = 8'd0;
        case (sel)
            2'd0: y = 8'd10;
            2'd1: y = 8'd20;
            2'd2: y = 8'd30;
            default: y = 8'd0;
        endcase
        #10;
        sel = 2'd2;
        case (sel)
            2'd0: y = 8'd10;
            2'd1: y = 8'd20;
            2'd2: y = 8'd30;
            default: y = 8'd0;
        endcase
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["sel", "y"])
        assert not diffs, "\n".join(diffs)

    def test_for_loop(self):
        verilog = """\
module test_for;
    reg [7:0] sum;
    integer i;

    initial begin
        sum = 8'd0;
        for (i = 0; i < 5; i = i + 1) begin
            sum = sum + 8'd1;
            #5;
        end
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["sum"], ignore_signals={"i"})
        assert not diffs, "\n".join(diffs)

    def test_while_loop(self):
        verilog = """\
module test_while;
    reg [7:0] count;
    integer i;

    initial begin
        count = 8'd0;
        i = 0;
        while (i < 6) begin
            count = count + 8'd1;
            i = i + 1;
            #5;
        end
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["count"], ignore_signals={"i"})
        assert not diffs, "\n".join(diffs)

    def test_repeat_loop(self):
        verilog = """\
module test_repeat;
    reg [7:0] val;

    initial begin
        val = 8'd0;
        repeat (4) begin
            val = val + 8'd10;
            #5;
        end
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["val"])
        assert not diffs, "\n".join(diffs)


class TestMixedLogic:
    """Validate combinations of continuous assigns and behavioral blocks."""

    def test_combo_with_initial(self):
        verilog = """\
module test_combo_init;
    reg [7:0] a, b;
    wire [7:0] y;
    assign y = a + b;

    initial begin
        a = 8'd0; b = 8'd0;
        #10; a = 8'd5;
        #10; b = 8'd3;
        #10; a = 8'd10; b = 8'd10;
        #10; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_always_combo(self):
        verilog = """\
module test_always_combo;
    reg [7:0] a, b;
    reg [7:0] y;

    always @(*) begin
        y = a + b;
    end

    initial begin
        a = 8'd0; b = 8'd0;
        #10; a = 8'd10;
        #10; b = 8'd20;
        #10; a = 8'd100; b = 8'd50;
        #10; $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_combo_and_seq_interaction(self):
        """Continuous assign reads from clocked register."""
        verilog = """\
module test_combo_seq;
    reg clk;
    reg [7:0] count;
    wire [7:0] doubled;

    assign doubled = count << 1;

    always @(posedge clk)
        count <= count + 8'd1;

    initial begin
        clk = 0; count = 8'd0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "count", "doubled"])
        assert not diffs, "\n".join(diffs)

    def test_alu_with_fsm(self):
        """ALU driven by FSM — complex mixed-logic design."""
        verilog = """\
module test_alu_fsm;
    reg clk, rst;
    reg [7:0] a, b;
    reg [7:0] result;
    reg [1:0] state, next_state;

    // FSM next-state logic
    always @(*) begin
        next_state = state;
        case (state)
            2'd0: next_state = 2'd1;
            2'd1: next_state = 2'd2;
            2'd2: next_state = 2'd3;
            2'd3: next_state = 2'd0;
        endcase
    end

    // FSM state register
    always @(posedge clk or posedge rst) begin
        if (rst)
            state <= 2'd0;
        else
            state <= next_state;
    end

    // ALU driven by state
    always @(posedge clk) begin
        if (!rst) begin
            case (state)
                2'd0: result <= a + b;
                2'd1: result <= a - b;
                2'd2: result <= a & b;
                2'd3: result <= a | b;
            endcase
        end
    end

    initial begin
        clk = 0; rst = 1; a = 8'd100; b = 8'd25;
        state = 2'd0; next_state = 2'd0; result = 8'd0;
        #5; clk = 1; #5; clk = 0;
        rst = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "rst", "state", "result"])
        assert not diffs, "\n".join(diffs)


class TestLhsTargets:
    """Validate assignment to bit/range/concat LHS targets."""

    def test_bit_select_lhs(self):
        verilog = """\
module test_bitsel_lhs;
    reg [7:0] data;

    initial begin
        data = 8'd0;
        #10; data[0] = 1'b1;
        #10; data[7] = 1'b1;
        #10; data[3] = 1'b1;
        #10; data[0] = 1'b0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["data"])
        assert not diffs, "\n".join(diffs)

    def test_range_select_lhs(self):
        verilog = """\
module test_rangesel_lhs;
    reg [7:0] data;

    initial begin
        data = 8'd0;
        #10; data[3:0] = 4'hA;
        #10; data[7:4] = 4'h5;
        #10; data[3:0] = 4'hF;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["data"])
        assert not diffs, "\n".join(diffs)

    def test_concat_lhs(self):
        verilog = """\
module test_concat_lhs;
    reg [3:0] hi, lo;

    initial begin
        hi = 4'd0; lo = 4'd0;
        #10; {hi, lo} = 8'hAB;
        #10; {hi, lo} = 8'h12;
        #10; {hi, lo} = 8'hFF;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["hi", "lo"])
        assert not diffs, "\n".join(diffs)

    def test_nba_bit_select(self):
        verilog = """\
module test_nba_bitsel;
    reg clk;
    reg [7:0] data;

    always @(posedge clk) begin
        data[0] <= ~data[0];
    end

    initial begin
        clk = 0; data = 8'd0;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5; clk = 0;
        #5; clk = 1;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "data"])
        assert not diffs, "\n".join(diffs)


class TestEdgeCases:
    """Validate edge cases and boundary conditions."""

    def test_overflow_8bit(self):
        verilog = """\
module test_overflow;
    reg [7:0] a, b;
    wire [7:0] y;
    assign y = a + b;

    initial begin
        a = 8'd255; b = 8'd1;
        #10;
        a = 8'd200; b = 8'd200;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "y"])
        assert not diffs, "\n".join(diffs)

    def test_16bit_arithmetic(self):
        verilog = """\
module test_16bit;
    reg [15:0] a, b;
    wire [15:0] sum, diff, prod;
    assign sum  = a + b;
    assign diff = a - b;
    assign prod = a * b;

    initial begin
        a = 16'd1000; b = 16'd2000;
        #10;
        a = 16'd65535; b = 16'd1;
        #10;
        a = 16'd256; b = 16'd256;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "sum", "diff", "prod"])
        assert not diffs, "\n".join(diffs)

    def test_sign_extension(self):
        verilog = """\
module test_signext;
    reg [3:0] narrow;
    wire [7:0] wide;
    assign wide = narrow;

    initial begin
        narrow = 4'hF;
        #10;
        narrow = 4'h5;
        #10;
        narrow = 4'h0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["narrow", "wide"])
        assert not diffs, "\n".join(diffs)

    def test_complex_expression(self):
        verilog = """\
module test_complex;
    reg [7:0] a, b, c;
    wire [7:0] y;
    assign y = ((a + b) ^ c) & 8'hF0 | (a - c);

    initial begin
        a = 8'd10; b = 8'd20; c = 8'd5;
        #10;
        a = 8'd255; b = 8'd1; c = 8'd128;
        #10;
        a = 8'd0; b = 8'd0; c = 8'd0;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "c", "y"])
        assert not diffs, "\n".join(diffs)


class TestClockDriven:
    """Validate clock-driven designs using the testbench Clock API."""

    def test_counter_clock_api(self):
        """Counter using Clock() API instead of initial block clock gen."""
        verilog = """\
module test_ctr(
    input wire clk
);
    reg [7:0] count;

    initial count = 8'd0;

    always @(posedge clk)
        count <= count + 8'd1;
endmodule
"""

        def _run(engine: str) -> dict[str, Value]:
            parser = verilog_parser(start="source_text")
            tree = parser.build_tree(verilog)
            design = tree_to_design(tree)
            module = design.modules[0]

            sim = Simulator(module, engine=engine)
            clk = sim.signal("clk")
            sim.fork(Clock(clk, period=10))

            sim.run(max_time=100)
            return {name: sim.read(name) for name in ["count"]}

        ref_vals = _run("reference")
        for engine in ("vm", "vm-fast"):
            if engine == "vm-fast" and not HAS_VM_FAST:
                continue
            engine_vals = _run(engine)
            for name in ref_vals:
                assert engine_vals[name] == ref_vals[name], (
                    f"[{engine}] Signal {name} mismatch: got={engine_vals[name]}, ref={ref_vals[name]}"
                )
        if HAS_COMPILED:
            compiled_vals = _run("compiled")
            for name in ref_vals:
                assert compiled_vals[name] == ref_vals[name], (
                    f"[compiled] Signal {name} mismatch: got={compiled_vals[name]}, ref={ref_vals[name]}"
                )

    def test_lfsr_clock_api(self):
        """LFSR using Clock() API."""
        verilog = """\
module test_lfsr(
    input wire clk
);
    reg [3:0] lfsr;
    wire feedback;

    assign feedback = lfsr[3] ^ lfsr[2];

    always @(posedge clk)
        lfsr <= {lfsr[2:0], feedback};

    // Initialize
    initial lfsr = 4'b0001;
endmodule
"""

        def _run(engine: str) -> dict[str, Value]:
            parser = verilog_parser(start="source_text")
            tree = parser.build_tree(verilog)
            design = tree_to_design(tree)
            module = design.modules[0]

            sim = Simulator(module, engine=engine)
            clk = sim.signal("clk")
            sim.fork(Clock(clk, period=10))

            sim.run(max_time=200)
            return {name: sim.read(name) for name in ["lfsr", "feedback"]}

        ref_vals = _run("reference")
        for engine in ("vm", "vm-fast"):
            if engine == "vm-fast" and not HAS_VM_FAST:
                continue
            engine_vals = _run(engine)
            for name in ref_vals:
                assert engine_vals[name] == ref_vals[name], (
                    f"[{engine}] Signal {name} mismatch: got={engine_vals[name]}, ref={ref_vals[name]}"
                )
        if HAS_COMPILED:
            compiled_vals = _run("compiled")
            for name in ref_vals:
                assert compiled_vals[name] == ref_vals[name], (
                    f"[compiled] Signal {name} mismatch: got={compiled_vals[name]}, ref={ref_vals[name]}"
                )

    def test_up_down_counter(self):
        """Up/down counter using Clock() API."""
        verilog = """\
module test_updown(
    input wire clk
);
    reg dir;
    reg [7:0] count;

    always @(posedge clk) begin
        if (dir)
            count <= count + 8'd1;
        else
            count <= count - 8'd1;
    end

    initial begin
        dir = 1;
        count = 8'd0;
    end
endmodule
"""

        def _run(engine: str) -> dict[str, Value]:
            parser = verilog_parser(start="source_text")
            tree = parser.build_tree(verilog)
            design = tree_to_design(tree)
            module = design.modules[0]

            sim = Simulator(module, engine=engine)
            clk = sim.signal("clk")
            sim.fork(Clock(clk, period=10))

            sim.run(max_time=100)
            return {name: sim.read(name) for name in ["count", "dir"]}

        ref_vals = _run("reference")
        for engine in ("vm", "vm-fast"):
            if engine == "vm-fast" and not HAS_VM_FAST:
                continue
            engine_vals = _run(engine)
            for name in ref_vals:
                assert engine_vals[name] == ref_vals[name], (
                    f"[{engine}] Signal {name} mismatch: got={engine_vals[name]}, ref={ref_vals[name]}"
                )
        if HAS_COMPILED:
            compiled_vals = _run("compiled")
            for name in ref_vals:
                assert compiled_vals[name] == ref_vals[name], (
                    f"[compiled] Signal {name} mismatch: got={compiled_vals[name]}, ref={ref_vals[name]}"
                )


class TestMultipleBlocks:
    """Validate multiple initial/always blocks interacting."""

    def test_multiple_initial_blocks(self):
        verilog = """\
module test_multi_init;
    reg [7:0] a, b;
    wire [7:0] sum;
    assign sum = a + b;

    initial begin
        a = 8'd0;
        #10; a = 8'd5;
        #10; a = 8'd10;
        #10;
    end

    initial begin
        b = 8'd0;
        #15; b = 8'd3;
        #10; b = 8'd7;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "sum"])
        assert not diffs, "\n".join(diffs)

    def test_two_always_blocks(self):
        verilog = """\
module test_two_always;
    reg clk;
    reg [7:0] a, b;

    always @(posedge clk)
        a <= a + 8'd1;

    always @(posedge clk)
        b <= b + 8'd2;

    initial begin
        clk = 0; a = 8'd0; b = 8'd0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5; clk = 1; #5; clk = 0;
        #5;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "a", "b"])
        assert not diffs, "\n".join(diffs)

    def test_forever_with_finish(self):
        verilog = """\
module test_forever;
    reg clk;
    reg [3:0] count;

    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    initial begin
        count = 4'd0;
        #10;
        count = 4'd1;
        #10;
        count = 4'd2;
        #10;
        count = 4'd3;
        #3;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["clk", "count"])
        assert not diffs, "\n".join(diffs)


class TestAlwaysCombo:
    """Validate combinational always blocks (always @(*))."""

    def test_priority_encoder(self):
        verilog = """\
module test_prienc;
    reg [3:0] inp;
    reg [1:0] out;
    reg valid;

    always @(*) begin
        if (inp[3]) begin
            out = 2'd3; valid = 1'b1;
        end else if (inp[2]) begin
            out = 2'd2; valid = 1'b1;
        end else if (inp[1]) begin
            out = 2'd1; valid = 1'b1;
        end else if (inp[0]) begin
            out = 2'd0; valid = 1'b1;
        end else begin
            out = 2'd0; valid = 1'b0;
        end
    end

    initial begin
        inp = 4'b0000;
        #10; inp = 4'b0001;
        #10; inp = 4'b0010;
        #10; inp = 4'b0100;
        #10; inp = 4'b1000;
        #10; inp = 4'b1010;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["inp", "out", "valid"])
        assert not diffs, "\n".join(diffs)

    def test_alu_case(self):
        verilog = """\
module test_alu;
    reg [7:0] a, b;
    reg [2:0] op;
    reg [7:0] result;

    always @(*) begin
        case (op)
            3'd0: result = a + b;
            3'd1: result = a - b;
            3'd2: result = a & b;
            3'd3: result = a | b;
            3'd4: result = a ^ b;
            3'd5: result = ~a;
            3'd6: result = a << 1;
            3'd7: result = a >> 1;
        endcase
    end

    initial begin
        a = 8'd100; b = 8'd25; op = 3'd0;
        #10; op = 3'd1;
        #10; op = 3'd2;
        #10; op = 3'd3;
        #10; op = 3'd4;
        #10; op = 3'd5;
        #10; op = 3'd6;
        #10; op = 3'd7;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["a", "b", "op", "result"])
        assert not diffs, "\n".join(diffs)

    def test_decoder_3to8(self):
        verilog = """\
module test_dec;
    reg [2:0] sel;
    reg [7:0] y;

    always @(*) begin
        case (sel)
            3'd0: y = 8'b00000001;
            3'd1: y = 8'b00000010;
            3'd2: y = 8'b00000100;
            3'd3: y = 8'b00001000;
            3'd4: y = 8'b00010000;
            3'd5: y = 8'b00100000;
            3'd6: y = 8'b01000000;
            3'd7: y = 8'b10000000;
        endcase
    end

    initial begin
        sel = 3'd0;
        #10; sel = 3'd1;
        #10; sel = 3'd4;
        #10; sel = 3'd7;
        #10;
        $finish;
    end
endmodule
"""
        diffs = _validate(verilog, signals=["sel", "y"])
        assert not diffs, "\n".join(diffs)
