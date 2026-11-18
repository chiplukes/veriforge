"""First-divergence locator (veriforge.sim.diverge): traces, comparison, and the walk back
to the driver where two runs part. A "bug" is injected by tracing a mutated copy of the
design as the second run and locating against the original's drivers.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.diverge import (
    Stimulus,
    Trace,
    align_trace,
    first_divergences,
    locate_divergence,
    main,
    record_trace,
    trace_from_vcd,
    value_bits,
)
from veriforge.sim.value import Value

from .engines import ENGINES

_DESIGN = """\
module leaf(input clk, input [3:0] a, output reg [3:0] q);
  wire [3:0] n = a + 1;
  always @(posedge clk) q <= n;
endmodule
module top(input clk, input [3:0] x, output [3:0] y, output [99:0] wide);
  wire [3:0] m;
  reg [99:0] acc;
  initial acc = 0;
  always @(posedge clk) acc <= {acc[95:0], x} ^ {4'd0, acc[99:4]};
  leaf u (.clk(clk), .a(x), .q(m));
  assign y = ~m;
  assign wide = acc;
endmodule
"""

_STIM = Stimulus(cycles=8, seed=3)


def _locate(mutated: str, *, engine_b: str = "reference", **kw):
    design = parse_text(_DESIGN)
    a = record_trace(design, "top", "reference", _STIM)
    b = record_trace(parse_text(mutated), "top", engine_b, _STIM)
    return locate_divergence(a, b, design, "top", **kw)


@pytest.mark.parametrize("engine", ENGINES)
def test_engines_agree_on_the_example(engine):
    design = parse_text(_DESIGN)
    a = record_trace(design, "top", "reference", _STIM)
    b = record_trace(parse_text(_DESIGN), "top", engine, _STIM)
    assert a.error is None and b.error is None
    assert {"y", "m", "u.q", "u.n", "acc", "wide"} <= a.changes.keys()
    assert not first_divergences(a, b)
    report = locate_divergence(a, b, design, "top")
    assert not report.diverged
    assert "No divergence" in report.format()


def test_record_selected_signals_skips_missing_names():
    tr = record_trace(parse_text(_DESIGN), "top", "reference", _STIM, signals=["y", "no_such", "u.q"])
    assert set(tr.changes) == {"y", "u.q"}
    assert tr.error is None


def test_initial_blocks_run_at_start():
    tr = record_trace(parse_text(_DESIGN), "top", "reference", _STIM)
    assert tr.changes["acc"][0] == (0, "0" * 100)


def test_combinational_root_through_register_and_port():
    report = _locate(_DESIGN.replace("a + 1", "a + 2"))
    assert [s.signal for s in report.chain] == ["y", "m", "u.q", "u.n"]
    root = report.root
    assert root is not None
    assert root.drivers[0].kind == "assign" and root.drivers[0].where.endswith(":2")
    assert "a + 1" in root.drivers[0].text
    assert report.chain[1].drivers[0].kind == "port"
    assert report.chain[2].drivers[0].clocked
    # The root's input agreed in both runs.
    assert [(n, va == vb) for n, va, vb in report.root_inputs] == [("u.a", True)]
    text = report.format()
    assert "Root: u.n" in text and "u.a" in text and "not in both traces" not in text


def test_edge_triggered_root():
    # A register whose data input agrees, but which stores something else.
    report = _locate(_DESIGN.replace("q <= n;", "q <= n ^ 4'd1;"))
    root = report.root
    assert root is not None and root.signal == "u.q"
    assert root.drivers[0].clocked
    assert report.diffs["u.q"].time % 3 == 1  # a posedge sample
    assert "before that time" in report.format()


def test_wide_signal_divergence():
    report = _locate(_DESIGN.replace("{4'd0, acc[99:4]}", "{4'd1, acc[99:4]}"))
    assert report.root is not None and report.root.signal == "acc"
    assert [s.signal for s in report.chain] == ["wide", "acc"]
    d = report.diffs["acc"]
    assert len(d.a) == len(d.b) == 100
    assert "100'h" in report.format()


def test_start_signal_and_ignore_x():
    report = _locate(_DESIGN.replace("a + 1", "a + 2"), start="u.q")
    assert [s.signal for s in report.chain] == ["u.q", "u.n"]
    with pytest.raises(ValueError, match="does not diverge"):
        _locate(_DESIGN.replace("a + 1", "a + 2"), start="acc")
    a, b = Trace(), Trace()
    a.record("s", 0, "1x0")
    b.record("s", 0, "110")
    assert first_divergences(a, b)
    assert not first_divergences(a, b, ignore_x=True)


def test_value_bits_and_stimulus():
    assert value_bits(Value(0b1010, width=4, mask=0b0001)) == "101x"
    assert value_bits(Value(1 << 70, width=72)) == "01" + "0" * 70
    s = Stimulus(seed=5, resets={"rst": (1, 2)}, x_prob=1.0)
    assert [s.value("rst", 1, c).val for c in range(4)] == [1, 1, 0, 0]
    # A value depends only on (seed, name, cycle), and x_prob=1 always puts x somewhere.
    xv = s.value("a", 130, 3)
    assert xv.mask
    assert xv.val == Stimulus(seed=5).value("a", 130, 3).val & ~xv.mask
    assert Stimulus().clock_names(["clk", "rst", "sys_clk", "d"]) == ["clk", "sys_clk"]
    # By name token: wb_clk_i (picorv32_wb) and aclk count, clock-named controls don't.
    names = ["wb_clk_i", "s_aclk", "clk0", "clk_en", "clk_gate", "click", "clkdiv_o"]
    assert Stimulus().clock_names(names) == ["wb_clk_i", "s_aclk", "clk0"]


_VCD = """\
$timescale 1ns $end
$scope module tb $end
$var wire 4 ! x [3:0] $end
$scope module dut $end
$var wire 4 ! x [3:0] $end
$var wire 4 " y [3:0] $end
$scope module u $end
$var wire 4 # q [3:0] $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
bx !
b1 "
b0 #
#5
b11 !
b1z00 "
"""


def test_trace_from_vcd_aliases_extension_and_alignment():
    tr = trace_from_vcd(_VCD)
    # Both names sharing identifier ! get the changes; values are left-extended; z -> x.
    assert tr.changes["tb.x"] == tr.changes["tb.dut.x"] == [(0, "xxxx"), (5, "0011")]
    assert tr.changes["tb.dut.y"] == [(0, "0001"), (5, "1x00")]
    aligned = align_trace(tr, {"x", "y", "u.q"})
    assert set(aligned.changes) == {"x", "y", "u.q"}


@pytest.mark.skipif(shutil.which("iverilog") is None, reason="Icarus Verilog not installed")
def test_cli_compares_icarus_vcds(tmp_path, capsys):
    tb = """\
module tb; reg clk = 0; reg [3:0] x = 0; wire [3:0] y; wire [99:0] wide;
  top dut(.clk(clk), .x(x), .y(y), .wide(wide));
  initial begin $dumpfile(`VCD); $dumpvars(0, tb);
    repeat (6) begin #5 x = $random; #5 clk = 1; #5 clk = 0; end $finish; end
endmodule
"""
    (tmp_path / "tb.v").write_text(tb)
    (tmp_path / "a.v").write_text(_DESIGN)
    (tmp_path / "b.v").write_text(_DESIGN.replace("a + 1", "a + 2"))
    for name in ("a", "b"):
        vvp = tmp_path / f"{name}.vvp"
        vcd = f'"{tmp_path / name}.vcd"'
        subprocess.run(
            ["iverilog", "-g2012", f"-DVCD={vcd}", "-o", str(vvp), str(tmp_path / "tb.v"), str(tmp_path / f"{name}.v")],
            check=True,
        )
        subprocess.run(["vvp", "-n", str(vvp)], check=True, capture_output=True, cwd=tmp_path)
    rc = main([str(tmp_path / "a.v"), "--top", "top", "--vcd", str(tmp_path / "a.vcd"), str(tmp_path / "b.vcd")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "Root: u.n" in out


def test_cli_engines_agree(tmp_path, capsys):
    (tmp_path / "a.v").write_text(_DESIGN)
    rc = main([str(tmp_path / "a.v"), "--top", "top", "--engines", "reference,vm", "--cycles", "4"])
    assert rc == 0
    assert "No divergence" in capsys.readouterr().out
