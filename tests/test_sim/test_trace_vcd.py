"""VCD tracing (``sim/trace.py``): one implementation across engines,
compiled-engine change detection in C (``trace_poll``), signal selection,
and ``$dumpvars(level, scope)``. See notes/plans/vcd_enhancement.md (V1/V2).
"""

from __future__ import annotations

import io

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.trace import attach_vcd, select_signals
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_DESIGN = """
module lane(input clk, input rst, input [7:0] d, output reg [7:0] q, output reg [99:0] w);
  reg [7:0] t;
  always @(posedge clk) begin
    if (rst) begin t <= 8'bx; q <= 0; w <= 0; end
    else begin t <= d; q <= t; w <= {w[91:0], t}; end
  end
endmodule
module top(input clk, output [15:0] q, output [7:0] m_out);
  reg rst = 1;
  reg [7:0] d = 0;
  reg [7:0] mem [0:3];
  reg [1:0] p;
  always @(negedge clk) begin
    if ($time > 20) rst <= 0;
    d <= d + 7;
  end
  genvar i;
  generate for (i = 0; i < 2; i = i + 1) begin : gen_lane
    lane u_lane(.clk(clk), .rst(rst), .d(d + i), .q(q[i*8 +: 8]), .w());
  end endgenerate
  always @(posedge clk) begin
    if (rst) p <= 0;
    else begin p <= p + 1; mem[p] <= d; end
  end
  assign m_out = mem[p];
endmodule
"""


def _design(source: str):
    tree = verilog_parser(start="source_text").build_tree(source)
    return tree_to_design(tree, source_file="no_such_file.v")


def _sim(engine: str) -> Simulator:
    design = _design(_DESIGN)
    top = next(m for m in design.modules if m.name == "top")
    sim = Simulator(top, engine=engine, design=design)
    sim.fork(Clock(sim.signal("clk"), period=10))
    return sim


def _body(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("$date"))


def _trace(engine: str, **select) -> str:
    """VCD of one ``run()`` with stimulus generated inside the design.
    (Between ``run()`` calls the engines differ in *when* a Python drive is
    reported: vm/compiled at the current time, reference at its next step.)
    Attached after a first run: before it, engines hold different
    not-yet-bootstrapped values."""
    sim = _sim(engine)
    sim.run(max_time=0)
    buf = io.StringIO()
    session = attach_vcd(sim, buf, **select)
    sim.run(max_time=150)
    session.close()
    return _body(buf.getvalue())


@pytest.mark.parametrize("engine", [e for e in ENGINES if e != "reference"])
@pytest.mark.parametrize(
    "select",
    [{}, {"scopes": ["gen_lane[1]"]}, {"signals": ["*q*"], "memories": ["mem"]}],
    ids=["everything", "one_lane", "globs"],
)
def test_vcd_matches_reference_engine(engine, select):
    """Every engine writes the same VCD (the compiled engine's comes from
    the C poll) -- wide signals, x values, memory elements, generate scopes."""
    want = _trace("reference", **select)
    got = _trace(engine, **select)
    assert got == want
    assert "#" in want.split("$end\n$dumpvars", 1)[-1]  # some changes were recorded


def test_compiled_uses_c_poll():
    sim = _sim("compiled")
    with attach_vcd(sim, io.StringIO()) as session:
        assert hasattr(sim._sched._sim, "trace_poll")
        assert len(session.signal_names) > 10


_NAMES = [
    "clk",
    "q",
    "mem[0]",
    "mem[1]",
    "gen_lane[0].u_lane.q",
    "gen_lane[0].u_lane.t",
    "gen_lane[1].u_lane.q",
    "gen_lane[1].sub.x",
    "__mem_0_wr",
]


@pytest.mark.parametrize(
    ("select", "want"),
    [
        (
            {},
            [
                "clk",
                "gen_lane[0].u_lane.q",
                "gen_lane[0].u_lane.t",
                "gen_lane[1].sub.x",
                "gen_lane[1].u_lane.q",
                "mem[0]",
                "mem[1]",
                "q",
            ],
        ),
        ({"scopes": ["gen_lane[1]"]}, ["gen_lane[1].sub.x", "gen_lane[1].u_lane.q"]),
        ({"scopes": ["gen_lane[1]"], "depth": 1}, []),
        ({"scopes": ["gen_lane[1].u_lane"], "depth": 1}, ["gen_lane[1].u_lane.q"]),
        ({"scopes": [""], "depth": 1}, ["clk", "q"]),
        ({"signals": ["gen_lane[*].u_lane.q"]}, ["gen_lane[0].u_lane.q", "gen_lane[1].u_lane.q"]),
        ({"exclude": ["gen_lane*"]}, ["clk", "mem[0]", "mem[1]", "q"]),
        ({"scopes": [""], "depth": 1, "memories": True}, ["clk", "mem[0]", "mem[1]", "q"]),
        (
            {"memories": False},
            ["clk", "gen_lane[0].u_lane.q", "gen_lane[0].u_lane.t", "gen_lane[1].sub.x", "gen_lane[1].u_lane.q", "q"],
        ),
    ],
)
def test_select_signals(select, want):
    assert select_signals(_NAMES, **select) == want


_DUMP_DESIGN = """
module sub(input clk, output reg [3:0] c);
  always @(posedge clk) c <= c + 1;
  initial c = 0;
endmodule
module tb;
  reg clk = 0;
  wire [3:0] c0, c1;
  sub u0(.clk(clk), .c(c0));
  sub u1(.clk(clk), .c(c1));
  always #5 clk = ~clk;
  initial begin
    $dumpfile("DUMPFILE");
    $dumpvars(DUMPARGS);
    #40 $finish;
  end
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize(
    ("args", "want"),
    [
        ("0", {"top.c0", "top.c1", "top.clk", "top.u0.c", "top.u0.clk", "top.u1.c", "top.u1.clk"}),
        ("1, tb.u1", {"top.u1.c", "top.u1.clk"}),
        ("0, u0", {"top.u0.c", "top.u0.clk"}),
        ("1, tb", {"top.c0", "top.c1", "top.clk"}),
    ],
)
def test_dumpvars_level_and_scope(engine, args, want, tmp_path):
    path = tmp_path / "d.vcd"
    design = _design(_DUMP_DESIGN.replace("DUMPFILE", str(path)).replace("DUMPARGS", args))
    tb = next(m for m in design.modules if m.name == "tb")
    sim = Simulator(tb, engine=engine, design=design)
    sim.run(max_time=100)
    text = path.read_text()
    assert {f"{scope}.{name}" for scope, name in _vars(text)} == want
    assert "#" in text.split("$enddefinitions", 1)[1]


def _vars(text: str) -> list[tuple[str, str]]:
    """``(scope path, var name)`` for every ``$var`` in a VCD."""
    scope: list[str] = []
    out = []
    for line in text.splitlines():
        parts = line.split()
        if line.startswith("$scope"):
            scope.append(parts[2])
        elif line.startswith("$upscope"):
            scope.pop()
        elif line.startswith("$var"):
            out.append((".".join(scope), parts[4]))
    return out


def test_dump_survives_multiple_runs(tmp_path):
    """A $dumpvars file keeps recording across several run() calls (it used
    to be closed at the end of the first one)."""
    path = tmp_path / "d.vcd"
    design = _design(_DUMP_DESIGN.replace("DUMPFILE", str(path)).replace("DUMPARGS", "0"))
    tb = next(m for m in design.modules if m.name == "tb")
    sim = Simulator(tb, engine="reference", design=design)
    sim.run(max_time=12)
    sim.run(max_time=30)
    times = [int(line[1:]) for line in path.read_text().splitlines() if line.startswith("#")]
    assert max(times) >= 25


def _engine_compiled():
    return pytest.mark.skipif("compiled" not in ENGINES, reason="compiled engine unavailable")


@_engine_compiled()
def test_batch_trace_matches_run_trace():
    """V3: tracing inside run_cycles/batch_run (C-side change recording)
    writes exactly what tracing a run() of the same cycles writes."""
    a = _sim("compiled")
    buf_a = io.StringIO()
    with attach_vcd(a, buf_a):
        a.run(max_time=145)
    b = _sim("compiled")
    buf_b = io.StringIO()
    with attach_vcd(b, buf_b):
        assert b.run_cycles(15) == 15
    assert _body(buf_b.getvalue()) == _body(buf_a.getvalue())


_EVENT_DESIGN = """
module ev(input clk, input en, input [7:0] k, output reg [7:0] acc, output reg [99:0] hist);
  initial begin acc = 0; hist = 0; end
  always @(posedge clk) if (en) begin acc <= acc + k; hist <= {hist[91:0], acc}; end
endmodule
"""


def _event_trace(monkeypatch, cap: int | None) -> tuple[str, int]:
    import veriforge.sim.compiled.compiled_scheduler as cs

    if cap is not None:
        monkeypatch.setattr(cs, "_TRACE_RECORD_CAP", cap)
    design = _design(_EVENT_DESIGN)
    sim = Simulator(design.modules[0], engine="compiled", design=design)
    sim.drive("clk", 0)
    sim.drive("en", 0)
    sim.drive("k", 0)
    events = [(c, "en", c % 3 != 0) for c in range(0, 40, 2)] + [(c, "k", (c * 37) & 0xFF) for c in range(1, 40, 3)]
    events.sort(key=lambda e: e[0])
    buf = io.StringIO()
    with attach_vcd(sim, buf):
        done = sim.batch_run(40, "clk", 10, events=[(c, n, int(v)) for c, n, v in events])
    return _body(buf.getvalue()), done


@_engine_compiled()
def test_batch_trace_resumes_when_buffer_fills(monkeypatch):
    """A record buffer too small for more than one cycle forces batch_run to
    stop and resume every cycle -- with event cycle numbers rebased -- and
    the VCD is unchanged."""
    want, done_big = _event_trace(monkeypatch, None)
    got, done_small = _event_trace(monkeypatch, 1)
    assert done_big == done_small == 40
    assert got == want
    assert want.count("\n#") > 40


def _times(text: str) -> list[int]:
    return [int(line[1:]) for line in text.splitlines() if line.startswith("#")]


@pytest.mark.parametrize("engine", ENGINES)
def test_time_window(engine):
    """V4: start/stop -- the initial values are written when the window
    opens, nothing outside it; every engine agrees."""

    def window(eng: str) -> str:
        sim = _sim(eng)
        sim.run(max_time=0)
        buf = io.StringIO()
        with attach_vcd(sim, buf, scopes=["gen_lane[0]"], start=42, stop=101):
            sim.run(max_time=150)
        return _body(buf.getvalue())

    text = window(engine)
    times = _times(text)
    assert times[0] == 42
    assert all(42 <= t <= 101 for t in times)
    assert "#42\n$dumpvars" in text
    if engine != "reference":
        assert text == window("reference")


def test_pause_resume_dump_all():
    sim = _sim("reference")
    sim.run(max_time=0)
    buf = io.StringIO()
    session = attach_vcd(sim, buf, signals=["gen_lane[0].u_lane.q"])
    sim.run(max_time=40)
    session.pause()
    sim.run(max_time=80)
    session.resume()
    sim.run(max_time=100)
    session.dump_all()
    session.close()
    text = buf.getvalue()
    assert "#40\n$dumpoff\nbxxxxxxxx " in text
    assert "#80\n$dumpon\nb" in text
    assert "#100\n$dumpall\nb" in text
    assert not [t for t in _times(text) if 40 < t < 80]


_DUMPCTL_DESIGN = """
module tb;
  reg clk = 0;
  reg [7:0] c = 0;
  always #5 clk = ~clk;
  always @(posedge clk) c <= c + 1;
  initial begin
    $dumpfile("DUMPFILE");
    $dumpvars(0, tb);
    #30 $dumpoff;
    #30 $dumpon;
    #10 $dumpall;
    #10 $dumplimit(1);
    #20 $finish;
  end
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_hdl_dump_control(engine, tmp_path):
    """$dumpoff / $dumpon / $dumpall / $dumplimit from HDL, on every engine."""
    path = tmp_path / "d.vcd"
    design = _design(_DUMPCTL_DESIGN.replace("DUMPFILE", str(path)))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=200)
    text = path.read_text()
    assert "#30\n$dumpoff\n" in text
    assert "#60\n$dumpon\n" in text
    assert "#70\n$dumpall\n" in text
    assert "$comment $dumplimit reached $end" in text
    times = _times(text)
    assert not [t for t in times if 30 < t < 60]
    assert max(times) <= 80


# ── V5: captures ───────────────────────────────────────────────────────

_CAP_DESIGN = """
module cap(input clk, output reg [7:0] cnt, output reg pulse, output reg [99:0] wide);
  initial begin cnt = 0; pulse = 0; wide = 0; end
  always @(posedge clk) begin
    cnt <= cnt + 1;
    pulse <= (cnt[3:0] == 4'd9);
    wide <= {wide[91:0], cnt};
  end
endmodule
"""


def _cap_sim(engine: str, *, settle: bool = True) -> Simulator:
    design = _design(_CAP_DESIGN)
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.fork(Clock(sim.signal("clk"), period=10))
    if settle:
        sim.run(max_time=0)
    return sim


def _read(path) -> str:
    return _body(open(path).read())


@pytest.mark.parametrize("engine", ENGINES)
def test_capture_expression_trigger(engine, tmp_path):
    from veriforge.sim.trace import attach_capture

    sim = _cap_sim(engine)
    with attach_capture(sim, tmp_path / "c.vcd", pre=50, post=30, trigger="cnt == 8'd37") as cap:
        sim.run(max_time=600)
    assert len(cap.files) == 1
    (t_trig, reason) = cap.triggers[0]
    assert "cnt == 8'd37" in reason
    text = _read(cap.files[0])
    times = _times(text)
    assert times[0] == t_trig - 50  # the $dumpvars section at the window start
    assert max(times) <= t_trig + 30
    assert f"#{t_trig - 50}\n$dumpvars" in text
    if engine != "reference":
        ref = _cap_sim("reference")
        with attach_capture(ref, tmp_path / "r.vcd", pre=50, post=30, trigger="cnt == 8'd37") as rcap:
            ref.run(max_time=600)
        assert rcap.triggers == cap.triggers
        assert _read(rcap.files[0]) == text


@_engine_compiled()
def test_capture_in_batch_matches_run(tmp_path):
    """Triggers are evaluated on the change stream, so batch_run captures
    the same window as run()."""
    from veriforge.sim.trace import attach_capture

    # Fresh simulators for both: after a run(max_time=0) the clock is left
    # high, and run_cycles would start with a forced negedge plus another
    # posedge at t=0.
    a = _cap_sim("compiled", settle=False)
    with attach_capture(a, tmp_path / "a.vcd", pre=40, post=20, trigger="$rose(pulse)", max_captures=3) as ca:
        a.run(max_time=500)
    b = _cap_sim("compiled", settle=False)
    with attach_capture(b, tmp_path / "b.vcd", pre=40, post=20, trigger="$rose(pulse)", max_captures=3) as cb:
        b.run_cycles(50)
    assert len(ca.files) == len(cb.files) == 3
    assert ca.triggers == cb.triggers
    for fa, fb in zip(ca.files, cb.files, strict=True):
        assert _read(fa).replace("a_", "x_") == _read(fb).replace("b_", "x_")


def test_capture_rearms_and_manual_and_callable(tmp_path):
    from veriforge.sim.trace import attach_capture

    sim = _cap_sim("reference")
    with attach_capture(
        sim, tmp_path / "m{n}.vcd", pre=20, post=0, trigger=lambda t, v: v["cnt"].val == 5, max_captures=2
    ) as cap:
        sim.run(max_time=100)
        cap.trigger("scoreboard says so")
        sim.run(max_time=200)
    assert [p.rsplit("/", 1)[-1] for p in cap.files] == ["m000.vcd", "m001.vcd"]
    assert cap.triggers[1] == (100, "scoreboard says so")


def test_capture_on_failure(tmp_path):
    """An exception from the simulation writes the window up to the failure."""
    from veriforge.sim.trace import attach_capture, register_time_step_callback

    sim = _cap_sim("reference")
    cap = attach_capture(sim, tmp_path / "f.vcd", pre=60, post=100, trigger="cnt == 8'd250")

    def scoreboard(sched):
        if sched.time >= 125:
            raise AssertionError("mismatch at lane 3")

    register_time_step_callback(sim._sched, scoreboard)
    with pytest.raises(AssertionError, match="lane 3"):
        sim.run(max_time=400)
    cap.close()
    assert len(cap.files) == 1
    text = open(cap.files[0]).read()
    assert "failure: AssertionError: mismatch at lane 3" in text
    times = _times(_body(text))
    assert times[0] == 125 - 60
    assert max(times) == 125


def test_capture_with_block_failure(tmp_path):
    from veriforge.sim.trace import attach_capture

    sim = _cap_sim("reference")
    with pytest.raises(RuntimeError), attach_capture(sim, tmp_path / "w.vcd", pre=30) as cap:
        sim.run(max_time=90)
        raise RuntimeError("testbench gave up")
    assert len(cap.files) == 1
    assert "testbench gave up" in open(cap.files[0]).read()


def test_trigger_expression_rejects_unknown_signal():
    from veriforge.sim.trace import attach_capture

    sim = _cap_sim("reference")
    with pytest.raises(ValueError, match="nope"):
        attach_capture(sim, "unused.vcd", pre=10, trigger="nope && cnt == 1")
