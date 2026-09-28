"""Dependency scheduling must preserve batch values and observable activations."""

from array import array
from copy import deepcopy
from functools import lru_cache
from random import Random
from unittest.mock import patch

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


def _sim(src, engine="vm-fast", *, legacy=False):
    if legacy:
        with patch("veriforge.sim.vm.vm_scheduler.plan_continuous_order", return_value=None):
            return Simulator(deepcopy(_module(src)), engine=engine)
    return Simulator(deepcopy(_module(src)), engine=engine)


def _state(sim):
    return {
        name: (value.val, value.mask, value.width) for name in sim._sched.signal_names() for value in [sim.read(name)]
    }


def _network(width, reverse):
    assignments = [f"assign n{i} = {'q' if i == 0 else f'n{i - 1}'} + q;" for i in range(12)]
    if reverse:
        assignments.reverse()
    return (
        f"module network(input clk, output reg [{width - 1}:0] q, output reg [{width - 1}:0] sampled);"
        + " ".join(f"wire [{width - 1}:0] n{i};" for i in range(12))
        + " ".join(assignments)
        + " initial begin q=0; sampled=0; end"
        + " always @(posedge clk) begin q<=q+1; sampled<=n11; end endmodule"
    )


@pytest.mark.parametrize("width", [8, 64, 128, 256])
@pytest.mark.parametrize("reverse", [False, True])
def test_reconvergent_network_matches_legacy_batch_and_other_engines(width, reverse):
    src = _network(width, reverse)
    ordered, legacy = _sim(src), _sim(src, legacy=True)
    assert ordered._sched._cy_ctx.cont_topo_depth == 12
    assert legacy._sched._cy_ctx.cont_topo_depth == 0
    for _ in range(4):
        for sim in (ordered, legacy):
            assert sim.batch_run(8, "clk") == 8
        assert _state(ordered) == _state(legacy)
    assert ordered.read("n11").val == 13 * 32 % (1 << width)
    assert ordered.read("sampled").val == 13 * 31 % (1 << width)
    for engine in ("vm-fast", "vm", "reference"):
        step = _sim(src, engine)
        step.fork(Clock(step.signal("clk"), period=10))
        step.run(max_time=315)
        assert _state(ordered) == _state(step)


@pytest.mark.parametrize(
    "value,mask",
    [(0x80, 0), (0x7F, 0), (0x80, 1), (0, 0x80), (0, 0xFF)],
)
def test_signed_widening_unknowns_and_chunked_drives(value, mask):
    src = """module widths(input clk, input signed [7:0] data,
               output wire signed [127:0] out);
        reg signed [7:0] q;
        wire signed [63:0] mid;
        always @(posedge clk) q<=data;
        assign out=mid | q;
        assign mid=q;
    endmodule"""
    ordered, legacy = _sim(src), _sim(src, legacy=True)
    assert ordered._sched._cy_ctx.cont_topo_depth == 2
    for val in (Value(value, width=8, mask=mask), Value(0, width=8), Value(value, width=8, mask=mask)):
        for sim in (ordered, legacy):
            sim.drive("data", val)
            assert sim.batch_run(2, "clk") == 2
        assert _state(ordered) == _state(legacy)
        assert ordered.read("out") == val.sign_extend(128)


@pytest.mark.parametrize("seed", range(6))
def test_shuffled_dags_with_multiple_changing_inputs(seed):
    rng = Random(seed)
    assignments = []
    choices = ["q", "r"]
    for i in range(24):
        left, right = choices[-1], rng.choice(choices)
        op = rng.choice(["+", "^", "&", "|"])
        assignments.append(f"assign n{i}={left} {op} {right};")
        choices.append(f"n{i}")
    rng.shuffle(assignments)
    src = (
        "module generated(input clk, input [63:0] data, other); reg [63:0] q,r;"
        + " ".join(f"wire [127:0] n{i};" for i in range(24))
        + " ".join(assignments)
        + " always @(posedge clk) begin q<=data; r<=other; end endmodule"
    )
    ordered, legacy = _sim(src), _sim(src, legacy=True)
    assert ordered._sched._cy_ctx.cont_topo_depth == 24
    for _ in range(8):
        data = Value(rng.getrandbits(64), width=64, mask=rng.choice([0, 0, rng.getrandbits(64)]))
        other = rng.getrandbits(64)
        for sim in (ordered, legacy):
            sim.drive("data", data)
            sim.drive("other", other)
            sim.batch_run(2, "clk")
        assert _state(ordered) == _state(legacy)


def test_batch_stimulus_settles_dependencies_before_sequential_sampling():
    src = """module stimulus(input clk, input [7:0] a,b, output reg [7:0] sampled);
        wire [7:0] x,y,z;
        assign z=x+y;
        assign y=x+b;
        assign x=a+b;
        always @(posedge clk) sampled<=z;
    endmodule"""
    ordered, legacy = _sim(src), _sim(src, legacy=True)
    assert ordered._sched._cy_ctx.cont_topo_depth == 3
    for a, b in [(3, 7), (40, 99), (0, 0)]:
        for sim in (ordered, legacy):
            sim.batch_run(1, "clk", events=[(0, "a", a), (0, "b", b)])
        assert _state(ordered) == _state(legacy)
        assert ordered.read("sampled").val == (2 * a + 3 * b) % 256


@pytest.mark.parametrize("count", [65, 8193])
def test_ready_set_word_boundaries_and_sparse_groups(count):
    # Cross both 64-bit word and 4096-assignment summary-group boundaries.
    # Changing only data leaves the middle summary group entirely inactive.
    # Build bytecode directly so the large boundary case does not benchmark
    # parsing thousands of identical declarations in every CI run.
    from veriforge.sim.vm._interp_fast import CyContext
    from veriforge.sim.vm.compiler import CompiledProcess, ProcessType
    from veriforge.sim.vm.opcodes import Op, instr
    from veriforge.sim.vm.propagation import plan_continuous_order

    sources = [1 if i in (0, count - 1) else 2 for i in range(count)] + [count + 2]
    processes = [
        CompiledProcess(
            ProcessType.CONTINUOUS,
            [instr(Op.LOAD_SIG, source), instr(Op.STORE_SIG, index + 3), instr(Op.PROC_END)],
            {source},
        )
        for index, source in enumerate(sources)
    ]
    processes[-1].program[1:1] = [instr(Op.LOAD_SIG, 1), instr(Op.BIT_OR)]
    processes[-1].sensitivity.add(1)
    contexts = [CyContext(), CyContext()]
    signal_count = count + 4
    for ctx in contexts:
        ctx.setup(
            [0] * signal_count, [0] * signal_count, [1] * signal_count, [], [], [], [p.program for p in processes]
        )
        ctx.setup_processes(
            [0] * len(processes),
            [[] for _ in range(signal_count)],
            list(range(len(processes))),
            [sorted(p.sensitivity) for p in processes],
            [[] for _ in processes],
        )
    plan = plan_continuous_order(processes)
    assert plan is not None and plan[1] == 2
    contexts[0].setup_continuous_order(*plan)
    for events in ([(1, 1), (2, 1)], [(1, 0)], [(1, 1)], [(2, 0)]):
        for ctx in contexts:
            completed, stopped, _ = ctx.batch_run(
                1,
                0,
                10,
                array("q", [0] * len(events)),
                array("i", [sid for sid, _ in events]),
                array("Q", [value for _, value in events]),
                10000,
            )
            assert completed == 1 and not stopped
        assert [contexts[0].read_signal(sid) for sid in range(signal_count)] == [
            contexts[1].read_signal(sid) for sid in range(signal_count)
        ]
        assert contexts[0].read_signal(count + 3) == contexts[0].read_signal(1)


@pytest.mark.parametrize(
    "body",
    [
        # Single-input chains have no repeated evaluations to eliminate.
        "assign a=data; assign b=a;",
        # Combinational observer: transient changes may be observable.
        "assign a=data; assign b=a; reg [7:0] observed; always @* observed=a^b;",
        # Cyclic dependencies must still iterate to a fixed point.
        "assign a=b & data; assign b=a | data;",
        # Two continuous writers must retain their existing execution order.
        "assign a=data; assign a=~data; assign b=a;",
        # A procedural writer prevents assuming a pure single-writer network.
        "assign a=data; assign b=a; always @(posedge clk) a<=data+1;",
        # Partial writes retain the generic path.
        "assign a[3:0]=data[3:0]; assign b=a;",
        # Memory dependencies are deliberately excluded from this first path.
        "reg [7:0] mem [0:3]; initial mem[0]=7; assign a=mem[0]; assign b=a;",
        # Inlined functions use scratch signal writes, even for a pure function.
        "function [7:0] f; input [7:0] x; begin f=x+1; end endfunction assign a=f(data); assign b=a;",
        # A derived clock is a procedural observer too.
        "assign a=data; assign b=a; reg [7:0] edges; initial edges=0; always @(posedge b) edges<=edges+1;",
    ],
)
def test_ineligible_network_retains_legacy_batch_behavior(body):
    src = f"module fallback(input clk, input [7:0] data); reg [7:0] a; wire [7:0] b; {body} endmodule"
    ordered, legacy = _sim(src), _sim(src, legacy=True)
    assert ordered._sched._cy_ctx.cont_topo_depth == 0
    for sim in (ordered, legacy):
        assert sim.batch_run(8, "clk", events=[(0, "data", 0), (2, "data", 3), (5, "data", 0)]) == 8
    assert _state(ordered) == _state(legacy)
    assert ordered.display_output == legacy.display_output


def test_random_expression_cannot_be_reordered():
    src = """module side_effect(input clk, input [7:0] data);
        wire [7:0] a,b;
        assign a=data+$random;
        assign b=a;
    endmodule"""
    assert _sim(src)._sched._cy_ctx.cont_topo_depth == 0


def test_small_delta_limit_retains_legacy_behavior():
    src = _network(8, True)
    ordered, legacy = _sim(src), _sim(src, legacy=True)
    assert ordered._sched._cy_ctx.cont_topo_depth == 12
    # Settle initialization first, then constrain propagation below DAG depth.
    for sim in (ordered, legacy):
        sim.batch_run(1, "clk")
        sim._sched.delta_limit = 3
        sim.batch_run(1, "clk")
    assert _state(ordered) == _state(legacy)


def test_settling_glitch_still_activates_procedural_observer():
    src = """module glitch(input clk, output reg q, output reg [7:0] activations);
        wire a,b,c,y;
        reg observed;
        assign y=a^b;
        assign a=q;
        assign b=c;
        assign c=q;
        initial begin q=0; activations=0; end
        always @(posedge clk) q<=~q;
        always @* begin observed=y; activations=activations+1; end
    endmodule"""
    ordered, legacy = _sim(src), _sim(src, legacy=True)
    assert ordered._sched._cy_ctx.cont_topo_depth == 0
    for sim in (ordered, legacy):
        sim.batch_run(1, "clk")
    before = ordered.read("activations").val
    for sim in (ordered, legacy):
        sim.batch_run(4, "clk")
    assert ordered.read("y").val == 0
    assert ordered.read("activations").val == before + 4
    assert _state(ordered) == _state(legacy)
