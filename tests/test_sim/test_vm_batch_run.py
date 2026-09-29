"""Native VM batching: compare state/phase behavior with event-driven execution."""

from copy import deepcopy
from functools import lru_cache

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.value import Value
from veriforge.sim.vm.vm_scheduler import _HAS_CYTHON
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser

pytestmark = pytest.mark.skipif(not _HAS_CYTHON, reason="requires native VM extension")


@lru_cache(maxsize=32)
def _module(src):
    return tree_to_design(verilog_parser(start="source_text").build_tree(src)).modules[0]


def _sim(src, engine="vm-fast"):
    return Simulator(deepcopy(_module(src)), engine=engine)


COUNTER = """
module counter(input clk, input rst, input [63:0] data,
               output reg [63:0] q, output reg [7:0] falls);
    wire reset_wire;
    assign reset_wire = rst;
    always @(posedge clk or posedge reset_wire)
        if (reset_wire) q <= 0; else q <= q + data;
    always @(negedge clk)
        if (reset_wire) falls <= 0; else falls <= falls + 1;
endmodule
"""


def _state(sim, names):
    return [(sim.read(n).val, sim.read(n).mask) for n in names]


@pytest.mark.parametrize("oracle", ["reference", "vm", "vm-fast"])
def test_batch_matches_event_loop(oracle):
    # Reset spans two edges; the ordinary event loop is an independent clock path.
    src = COUNTER.replace(
        "endmodule",
        """
    initial begin rst = 1; data = 3; #20 rst = 0; end
    endmodule
    """,
    )
    step = _sim(src, oracle)
    step.fork(Clock(step.signal("clk"), period=10))
    step.run(max_time=95)
    batch = _sim(COUNTER)
    assert batch.batch_run(10, "clk", events=[(0, "rst", 1), (0, "data", 3), (2, "rst", 0)]) == 10
    assert _state(batch, ["q", "falls", "clk"]) == _state(step, ["q", "falls", "clk"])
    assert batch.time == 100
    assert batch.read("q").val == 24
    assert batch.read("falls").val == 8


def test_chunked_reactive_drives_and_clock_inference():
    sim = _sim(COUNTER)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.drive("rst", 1)
    sim.drive("data", 1)
    assert sim.run_cycles(2) == 2
    sim.drive("rst", 0)
    total = 0
    for _ in range(6):
        data = 3 if total % 2 == 0 else 10
        sim.drive("data", data)
        assert sim.run_cycles(3) == 3
        total += data * 3
        assert sim.read("q").val == total
    assert sim.time == 200
    assert sim.read("falls").val == 18


@pytest.mark.parametrize("value", [0xFFFFFFFFFFFFFFFF, -1, 1 << 64, 1 << 63])
def test_event_integer_normalization(value):
    sim = _sim(COUNTER)
    sim.batch_run(2, "clk", events=[(0, "rst", 1), (1, "rst", 0), (1, "data", value)])
    assert sim.read("q").val == value & ((1 << 64) - 1)
    assert sim.read("q").mask == 0


def test_initial_blocks_memories_wide_nbas_and_unknowns():
    src = """
    module storage(input clk, input [127:0] din, output reg [127:0] q,
                   output wire [7:0] result);
        reg [7:0] mem [0:3];
        reg [1:0] addr;
        initial begin addr = 0; mem[0] = 3; mem[1] = 5; mem[2] = 7; mem[3] = 9; end
        assign result = mem[addr];
        always @(posedge clk) begin
            q <= din;
            mem[addr] <= mem[addr] + 1;
            addr <= addr + 1;
        end
    endmodule
    """
    value = Value((1 << 120) + 37, width=128, mask=1 << 90)
    batch, step = _sim(src), _sim(src)
    for sim in (batch, step):
        sim.drive("din", value)
    step.fork(Clock(step.signal("clk"), period=10))
    step.run(max_time=95)
    for _ in range(5):
        assert batch.batch_run(2, "clk") == 2
    names = ["q", "addr", "result"] + [f"mem[{i}]" for i in range(4)]
    assert _state(batch, names) == _state(step, names)
    assert batch.read("q").mask == 1 << 90
    assert batch.read("q").val == value.val


def test_initial_nba_and_initial_finish():
    src = "module init(input clk, output reg [7:0] q); initial q <= 7; always @(posedge clk) q <= q+1; endmodule"
    sim = _sim(src)
    assert sim.batch_run(2, "clk") == 2
    assert sim.read("q").val == 9
    src = "module stop(input clk, output reg [7:0] q); initial begin q=7; $finish; end endmodule"
    sim = _sim(src)
    assert sim.run_cycles(4, "clk", 10) == 0
    assert sim.read("q").val == 7
    assert sim.run_cycles(4, "clk", 10) == 0
    assert sim.time == 0


@pytest.mark.parametrize("edge,stop_time", [("posedge", 30), ("negedge", 35)])
def test_display_time_and_partial_finish(edge, stop_time):
    src = f"""
    module stop(input clk, output reg [7:0] q, output reg done);
        initial begin q=0; done=0; end
        always @({edge} clk) begin
            $display("tick %0d", $time);
            if (q == 3) begin done = 1; $finish; end
            q <= q + 1;
        end
    endmodule
    """
    sim = _sim(src)
    assert sim.batch_run(10, "clk") == 3
    assert sim.time == stop_time
    assert sim.read("q").val == 3
    assert sim.read("done").val == 1
    offset = 0 if edge == "posedge" else 5
    assert sim.display_output == [f"tick {i * 10 + offset}" for i in range(4)]
    assert sim.batch_run(10, "clk") == 0


def test_display_buffer_is_drained_during_long_batches():
    src = 'module out(input clk); always @(posedge clk) $display("%0d", $time); endmodule'
    sim = _sim(src)
    assert sim.batch_run(2000, "clk", clock_period=7) == 2000
    assert len(sim.display_output) == 2000
    assert sim.display_output[-1] == str(1999 * 7)
    assert sim.time == 14000


def test_zero_cycles_does_not_bootstrap():
    sim = _sim("module z(input clk, output reg q); initial q=1; endmodule")
    before = _state(sim, ["q", "clk"])
    assert sim.run_cycles(0, "clk", 10) == 0
    assert _state(sim, ["q", "clk"]) == before
    assert not sim._sched._bootstrapped


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"cycles": -1}, "cycles"),
        ({"clock_period": 1}, "clock_period"),
        ({"clock_name": "missing"}, "Unknown clock"),
        ({"clock_name": "data"}, "1-bit"),
        ({"events": [(1, "rst", 0), (0, "rst", 1)]}, "sorted"),
        ({"events": [(2, "rst", 1)]}, "outside"),
        ({"events": [(-1, "rst", 1)]}, "outside"),
        ({"events": [(0, "missing", 1)]}, "Unknown batch"),
        ({"events": [(0, "clk", 1)]}, "batch clock"),
    ],
)
def test_validation_before_initialization(kwargs, match):
    sim = _sim(COUNTER)
    args = dict(cycles=2, clock_name="clk") | kwargs
    with pytest.raises(ValueError, match=match):
        sim.batch_run(**args)
    assert not sim._sched._batch_started
    assert sim.time == 0


@pytest.mark.parametrize(
    "body,match",
    [
        ("initial #5 $finish;", "timed HDL"),
        ("always #5 clk=~clk;", "timed HDL"),
        ('initial $monitor("%0d", clk);', r"\$monitor"),
        ('initial begin $dumpfile("unused.vcd"); $dumpvars; end', "timed HDL"),
    ],
)
def test_unsupported_hdl_is_rejected(body, match):
    sim = _sim(f"module rejected(input clk); {body} endmodule")
    with pytest.raises(ValueError, match=match):
        sim.batch_run(2, "clk")
    assert not sim._sched._bootstrapped


def test_callbacks_queued_events_and_missing_extension_are_rejected():
    sim = _sim(COUNTER)
    sim._sched._on_time_step = lambda sched: None
    with pytest.raises(ValueError, match="callbacks"):
        sim.batch_run(2, "clk")
    sim._sched._on_time_step = None
    sim._sched.schedule_at(1, ("clock_toggle", "rst", Value(1)))
    with pytest.raises(ValueError, match="queued events"):
        sim.batch_run(2, "clk")
    sim = _sim(COUNTER)
    sim._sched._cy_ctx = None
    with pytest.raises(NotImplementedError, match="native extension"):
        sim.batch_run(2, "clk")


def test_event_loop_cannot_follow_batch():
    sim = _sim(COUNTER)
    sim.batch_run(2, "clk", events=[(0, "rst", 1)])
    with pytest.raises(ValueError, match="Cannot mix"):
        sim.run(max_time=100)
    with pytest.raises(ValueError, match="Cannot mix"):
        sim.run_step()


def test_inferred_duty_cycle_is_not_silently_changed():
    sim = _sim(COUNTER)
    sim.fork(Clock(sim.signal("clk"), period=10, duty=0.3))
    with pytest.raises(ValueError, match="duty"):
        sim.run_cycles(2)


def test_drained_event_queue_cannot_switch_to_batch():
    sim = _sim(COUNTER)
    sim._sched.schedule_at(0, ("clock_toggle", "rst", Value(1)))
    sim.run_step()
    assert not sim._sched._event_queue
    with pytest.raises(ValueError, match="Cannot mix"):
        sim.batch_run(2, "clk")


def test_multiple_forked_clocks_are_rejected_even_with_explicit_clock():
    sim = _sim(COUNTER)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.fork(Clock(sim.signal("rst"), period=20))
    with pytest.raises(ValueError, match="one forked clock"):
        sim.run_cycles(2, "clk", 10)


def test_wide_and_memory_events_are_explicitly_rejected():
    src = "module w(input clk, input [127:0] wide); reg [7:0] mem [0:1]; endmodule"
    sim = _sim(src)
    with pytest.raises(ValueError, match="64 bits"):
        sim.batch_run(2, "clk", events=[(0, "wide", 1)])
    with pytest.raises(ValueError, match="memory-element"):
        sim.batch_run(2, "clk", events=[(0, "mem[0]", 1)])


def test_duplicate_event_targets_keep_last_value_and_time_overflow_is_rejected():
    sim = _sim(COUNTER)
    sim.batch_run(2, "clk", events=[(0, "rst", 1), (1, "rst", 0), (1, "data", 7), (1, "data", 9)])
    assert sim.read("q").val == 9
    with pytest.raises(OverflowError, match="64-bit"):
        sim.batch_run(1 << 62, "clk")
    assert sim.time == 20


def test_delta_limit_error_is_reported():
    sim = _sim(COUNTER)
    sim.batch_run(2, "clk", events=[(0, "rst", 1)])
    sim.drive("rst", 0)
    sim.drive("data", 1)
    sim.settle()
    sim._sched.delta_limit = 1
    with pytest.raises(RuntimeError, match="Delta cycle limit"):
        sim.batch_run(2, "clk")


def test_finish_during_reactive_settle_stops_before_next_batch():
    src = """module reactive(input clk, input stop, output reg [7:0] q);
    initial q=0;
    always @(posedge clk) q <= q+1;
    always @(posedge stop) $finish;
    endmodule"""
    sim = _sim(src)
    sim.drive("stop", 0)
    sim.run_cycles(2, "clk", 10)
    sim.drive("stop", 1)
    assert sim.run_cycles(4, "clk", 10) == 0
    assert sim.read("q").val == 2
    assert sim.time == 20


def test_finish_status_is_preserved_in_ordinary_vm_event_loop():
    src = """module stop(input clk, output reg done);
    initial done=0;
    always @(posedge clk) begin done=1; $finish; end
    endmodule"""
    sim = _sim(src)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=100)
    assert sim.time == 0
    assert sim.read("done").val == 1


def test_reverse_ordered_diamond_propagation_matches_reference_and_vm():
    src = """module diamond(input clk, output reg [7:0] q, output wire [7:0] y);
    wire [7:0] a, b, c, d, e;
    assign e = c ^ d;
    assign c = a + b;
    assign d = a + 8'd2;
    assign b = q + 8'd1;
    assign a = q + 8'd3;
    assign y = e;
    initial q = 0;
    always @(posedge clk) q <= q + 1;
    endmodule"""
    batch = _sim(src)
    assert batch.batch_run(40, "clk") == 40
    for engine in ("reference", "vm", "vm-fast"):
        step = _sim(src, engine)
        step.fork(Clock(step.signal("clk"), period=10))
        step.run(max_time=395)
        assert _state(batch, ["q", "a", "b", "c", "d", "e", "y"]) == _state(step, ["q", "a", "b", "c", "d", "e", "y"])


@pytest.mark.parametrize(
    "src,names",
    [
        (
            "module p(input clk, output reg [7:0] q); initial q=0; always @(posedge clk) q<=q+1; endmodule",
            ["q", "clk"],
        ),
        (
            "module n(input clk, output reg [7:0] q); initial q=0; always @(negedge clk) q<=q+1; endmodule",
            ["q", "clk"],
        ),
        (
            "module c(input clk, output wire out); assign out=clk; endmodule",
            ["out", "clk"],
        ),
        (
            """module d(input clk, input rst, output reg [7:0] q);
            wire inv; assign inv = ~clk;
            initial q=0;
            always @(posedge inv) if (rst) q<=0; else q<=q+1;
            endmodule""",
            ["q", "inv", "clk"],
        ),
    ],
)
def test_batch_falling_edge_activity_matches_event_loop(src, names):
    step, batch = _sim(src), _sim(src)
    events = None
    if "rst" in step._sched.compiler.signal_map:
        step.drive("rst", 1)
        step._sched.schedule_at(10, ("clock_toggle", "rst", Value(0)))
        events = [(0, "rst", 1), (1, "rst", 0)]
    step.fork(Clock(step.signal("clk"), period=10))
    step.run(max_time=95)
    assert batch.batch_run(10, "clk", events=events) == 10
    assert _state(batch, names) == _state(step, names)


def test_sparse_edge_snapshots_preserve_async_reset_and_both_clock_edges():
    declarations = " ".join(f"reg [7:0] idle{i}; wire [7:0] out{i}; assign out{i}=idle{i};" for i in range(32))
    src = f"""module sparse_edges(input clk, input rst, output reg [7:0] q, falls);
        wire rst_wire; assign rst_wire=rst;
        {declarations}
        initial begin q=0; falls=0; end
        always @(posedge clk or posedge rst_wire)
            if (rst_wire) q<=0; else q<=q+1;
        always @(negedge clk) falls<=falls+1;
    endmodule"""
    events = [(0, "rst", 1), (2, "rst", 0), (5, "rst", 1), (7, "rst", 0)]
    batch = _sim(src)
    assert batch.batch_run(10, "clk", events=events) == 10
    step_src = src.replace("endmodule", "initial begin rst=1; #20 rst=0; #30 rst=1; #20 rst=0; end endmodule")
    for engine in ("vm-fast", "vm", "reference"):
        step = _sim(step_src, engine)
        step.fork(Clock(step.signal("clk"), period=10))
        step.run(max_time=95)
        assert _state(batch, ["q", "falls", "rst_wire", "clk", "out0", "out31"]) == _state(
            step, ["q", "falls", "rst_wire", "clk", "out0", "out31"]
        ), engine
