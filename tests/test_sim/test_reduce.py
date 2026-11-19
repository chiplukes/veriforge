"""Design reducer (veriforge.reduce): delta debugging on the model, built-in predicates,
worker-process timeouts, and the generated regression-test source.
"""

from __future__ import annotations

import itertools
import shutil
import sys

import pytest

from veriforge.reduce import (
    AllOf,
    Command,
    EngineError,
    EngineMismatch,
    IcarusAccepts,
    Reducer,
    ReduceError,
    emit,
    parse_text,
    regression_test_source,
)
from veriforge.reduce.__main__ import main
from veriforge.reduce._reducer import Checker, size_of
from veriforge.sim.diverge import Stimulus, first_divergences, record_trace

_BIG = """\
module leaf #(parameter W = 4) (input clk, input [W-1:0] a, output reg [W-1:0] q);
  wire [W-1:0] n = a + 1;
  always @(posedge clk) begin
    if (a[0]) q <= n; else q <= ~n;
  end
endmodule
module other(input [7:0] p, output [7:0] r);
  assign r = p ^ 8'h5a;
endmodule
module top(input clk, input [3:0] x, input [7:0] z, output [3:0] y, output [7:0] w);
  wire [3:0] m;
  reg [7:0] acc;
  initial acc = 0;
  leaf #(.W(4)) u (.clk(clk), .a(x), .q(m));
  other o (.p(z), .r(w));
  always @(posedge clk) begin
    acc <= acc + z;
    case (x[1:0])
      2'd0: acc <= 0;
      default: acc <= acc - 1;
    endcase
  end
  assign y = m ^ acc[3:0];
endmodule
"""

_STIM = Stimulus(cycles=6, seed=1)


def _quiet(_msg: str) -> None:
    pass


def _fault_visible(text: str) -> bool:
    """Injected "bug": the outputs change when `+ 1` becomes `+ 2`."""
    if "+ 1" not in text:
        return False
    try:
        d1, d2 = parse_text(text), parse_text(text.replace("+ 1", "+ 2"))
        outs = [p.name for p in d1.get_module("top").output_ports()]
        t1 = record_trace(d1, "top", "reference", _STIM, signals=outs)
        t2 = record_trace(d2, "top", "reference", _STIM, signals=outs)
    except Exception:  # noqa: BLE001
        return False
    return not t1.error and not t2.error and bool(first_divergences(t1, t2))


def test_reduces_to_the_fault_path():
    reducer = Reducer(_fault_visible, timeout=None, log=_quiet)
    text = reducer.reduce(parse_text(_BIG))
    assert _fault_visible(text)
    assert len(text) < len(emit(parse_text(_BIG))) / 2
    for gone in ("other", "acc", "case", "~n", "z"):
        assert gone not in text, gone
    assert "+ 1" in text and "leaf" in text
    assert reducer.successes > 0


@pytest.mark.skipif(shutil.which("iverilog") is None, reason="Icarus Verilog not installed")
def test_icarus_guard_keeps_declarations():
    text = Reducer(AllOf((IcarusAccepts("top"), _fault_visible)), timeout=None, log=_quiet).reduce(parse_text(_BIG))
    assert IcarusAccepts("top")(text)
    assert "wire [3:0] m;" in text  # (without the guard the declaration goes: m becomes an implicit net)


def test_error_predicate_cli(tmp_path, capsys):
    src = tmp_path / "big.v"
    src.write_text(_BIG.replace("  assign y =", "  missing mm (.a(x));\n  assign y ="))
    out, test_out = tmp_path / "min.v", tmp_path / "test_min.py"
    rc = main(
        [
            *(str(src), "--top", "top", "--error", "Cannot resolve module 'missing'", "--timeout", "60"),
            *("-o", str(out), "--test-out", str(test_out)),
        ]
    )
    assert rc == 0, capsys.readouterr()
    text = out.read_text()
    assert "missing mm" in text
    assert "leaf" not in text and "always" not in text
    assert EngineError("top", "Cannot resolve module 'missing'")(text)
    assert "def test_reduced_no_error" in test_out.read_text()


def test_not_reproducing_is_an_error():
    with pytest.raises(ReduceError, match="does not reproduce"):
        Reducer(lambda _text: False, timeout=None, log=_quiet).reduce(parse_text(_BIG))


def test_engine_mismatch_predicate_on_agreeing_engines():
    pred = EngineMismatch("top", engines=("reference", "vm"), stimulus=_STIM)
    text = emit(parse_text(_BIG))
    assert not pred(text)
    assert not pred("module broken(")  # parse error: not interesting
    assert not EngineMismatch("nosuch", engines=("reference", "vm"))(text)
    assert pred.narrowed(text) is pred  # nothing diverges: unchanged


def test_regression_test_source_runs():
    pred = EngineMismatch("top", engines=("reference", "vm"), stimulus=_STIM)
    src = regression_test_source(pred, emit(parse_text(_BIG)))
    namespace: dict = {}
    exec(compile(src, "generated_test.py", "exec"), namespace)  # noqa: S102
    namespace["test_reduced_engines_agree"]()


def test_checker_timeout_kills_the_worker():
    sleeper = Command(f'"{sys.executable}" -c "import time; time.sleep(30)"')
    check = Checker(sleeper, timeout=1.0)
    try:
        assert check("module m; endmodule") is False
        assert check.timeouts == 1
        # A fresh worker serves the next check.
        check.predicate = Command(f'"{sys.executable}" -c "pass"')
        check.timeout = 60.0
        assert check("module m; endmodule") is True
    finally:
        check.close()


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as f:
            return "\nState:\tZ" not in f.read()  # (a zombie is dead, just not yet reaped)
    except OSError:
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_checker_timeout_kills_what_the_check_started(tmp_path):
    """A timed-out check's own children die with it. Killing only the worker
    left the compiled build's Cython/C compiler running beside the next
    check's build; on ibex that stacked builds until WSL ran out of memory."""
    import time

    pidfile = tmp_path / "pid"
    script = tmp_path / "spawn.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    check = Checker(Command(f'"{sys.executable}" "{script}"'), timeout=3.0)
    try:
        assert check("module m; endmodule") is False
    finally:
        check.close()
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(pid)


@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_AS is POSIX")
def test_checker_memory_cap_fails_the_check():
    """A check that outgrows the cap fails (MemoryError) instead of taking the machine down."""
    hog = Command(f'"{sys.executable}" -c "b = bytearray(6 << 30)"')
    check = Checker(hog, timeout=60.0, mem_limit_gb=2)
    try:
        assert check("module m; endmodule") is False
        check.predicate = Command(f'"{sys.executable}" -c "pass"')
        assert check("module m; endmodule") is True  # (the cap leaves normal checks alone)
    finally:
        check.close()


def test_checkpoint_gets_every_improvement():
    seen: list[str] = []
    text = Reducer(_fault_visible, timeout=None, log=_quiet, checkpoint=seen.append).reduce(parse_text(_BIG))
    assert seen and seen[-1] == text
    assert all(size_of(a) > size_of(b) for a, b in itertools.pairwise(seen))  # (characters, then identifiers)
