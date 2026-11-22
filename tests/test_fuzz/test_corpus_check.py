"""tools/corpus_check.py: reset detection, parameter variants, one-module checks in a
subprocess, and the report's grouping of mismatches by root driver.
"""

from __future__ import annotations

import importlib.util
import json
import pickle
import random
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from veriforge.reduce import parse_text

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "corpus_check.py"


@pytest.fixture(scope="module")
def cc():
    sys.path.insert(0, str(_TOOL.parent))
    try:
        spec = importlib.util.spec_from_file_location("corpus_check", _TOOL)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(_TOOL.parent))
    return mod


def test_detect_resets(cc):
    got = cc.detect_resets(
        ["clk", "rst", "rst_n", "rst_ni", "i_rst", "aresetn", "rstn_i", "reset", "first", "burst_len"]
    )
    assert got == {
        "rst": (1, 3),
        "rst_n": (0, 3),
        "rst_ni": (0, 3),
        "i_rst": (1, 3),
        "aresetn": (0, 3),
        "rstn_i": (0, 3),
        "reset": (1, 3),
    }


def test_param_variants(cc):
    mod = parse_text(
        'module m #(parameter W = 8, parameter S = "x", parameter BIG = 1000)'
        " (input [W-1:0] a, output [W-1:0] y); localparam L = 4; assign y = a; endmodule\n"
    ).get_module("m")
    variants = cc.param_variants(mod, 3, random.Random(0))
    assert variants and len(variants) <= 3
    for v in variants:
        assert list(v) == ["W"]  # not a string, out-of-range or local parameter
        assert 1 <= v["W"] <= 65 and v["W"] != 8
    assert cc.param_variants(mod, 0, random.Random(0)) == []


_SRC = """\
module m #(parameter W = 4) (input clk, input rst, input [W-1:0] a, output reg [W-1:0] q, output [W:0] y);
  always @(posedge clk) if (rst) q <= 0; else q <= q + a;
  assign y = {1'b0, q} + 1;
endmodule
"""


def _job(tmp_path, engine: str, **kw) -> dict:
    pkl = tmp_path / "d.pkl"
    if not pkl.exists():
        pkl.write_bytes(pickle.dumps(parse_text(_SRC)))
    return {
        "kind": "engine",
        "project": "p",
        "module": "m",
        "seed": 0,
        "params": None,
        "resets": {"rst": [1, 3]},
        "pickle": str(pkl),
        "cycles": 8,
        "engine": engine,
        "trace_out": str(tmp_path / f"{engine}{kw.get('params') or ''}.pkl"),
        **kw,
    }


def test_engine_runs_compare_and_parameters(cc, tmp_path):
    traces = {}
    for params in (None, {"W": 70}):
        for engine in ("reference", "vm"):
            job = _job(tmp_path, engine, params=params)
            run = cc.run_engine(job)
            assert run["status"] == "ok", run
            assert run["rss_mb"] is None or run["rss_mb"] > 0
            assert run["setup_s"] >= 0 and run["run_s"] >= 0
            traces[engine] = job["trace_out"]
        cmp = cc.compare({**_job(tmp_path, "reference", params=params), "kind": "compare", "traces": traces})
        assert cmp == {"mismatches": {}}
    with open(traces["vm"], "rb") as f:
        assert len(pickle.load(f).changes["y"][-1][1]) == 71  # noqa: S301 -- W = 70 applied


def test_group_outcome(cc):
    ok = {"status": "ok"}
    eng = ["reference", "vm", "compiled"]
    assert cc.group_outcome(eng, {"reference": ok, "vm": ok, "compiled": ok}, {})[0] == "agree"
    mem = {"status": "memory", "error": "over the 6 GB limit"}
    assert cc.group_outcome(eng, {"reference": ok, "vm": ok, "compiled": mem}, {})[:2] == (
        "engine-resource",
        {"compiled": "over the 6 GB limit"},
    )
    err = {"status": "error", "error": "ValueError: x"}
    assert cc.group_outcome(eng, {"reference": ok, "vm": err, "compiled": ok}, {})[0] == "engine-error"
    assert cc.group_outcome(eng, {"reference": ok, "vm": err, "compiled": ok}, {"compiled": {}})[0] == "mismatch"
    assert cc.group_outcome(eng, {"reference": mem, "vm": ok, "compiled": ok}, {})[0] == "memory"


def test_run_job_subprocess_and_timeout(cc, tmp_path):
    job = _job(tmp_path, "reference")
    res = cc._run_job(job, 120)
    assert res["status"] == "ok", res
    assert cc._run_job(job, 0.01)["status"] == "timeout"


def test_report_groups_mismatches_by_root(cc):
    report_text = "Root: x\n\nDriver: assign at f.v:3\n    assign y = a + b;\n"
    run = {"status": "ok", "setup_s": 0.5, "run_s": 1.0, "rss_mb": 100.0, "child_rss_mb": 0.0, "gen_kb": None}
    big = {**run, "rss_mb": 900.0, "child_rss_mb": 2500.0, "gen_kb": 4096.0}
    engines = {"reference": run, "vm": run}
    results = [
        {"project": "c/p", "module": m, "seed": 0, "params": None, "outcome": "mismatch", "resets": {},
         "engines": engines, "engine_errors": {},
         "mismatches": {"vm": {"signals": 2, "first_time": 0, "report": report_text}}}
        for m in ("m1", "m2")
    ] + [
        {"project": "c/p", "module": "m3", "seed": 0, "params": None, "outcome": "error", "engines": engines,
         "error": "ValueError: bad 'w' 12"},
        {"project": "c/p", "module": "m4", "seed": 0, "params": None, "outcome": "error", "engines": engines,
         "error": "ValueError: bad 'v' 3"},
        {"project": "c/p", "module": "m5", "seed": 0, "params": None, "outcome": "engine-resource",
         "engines": {"reference": run, "vm": {"status": "memory", "rss_mb": None}},
         "engine_errors": {"vm": "over the 6 GB limit"}, "mismatches": {}},
        {"project": "c/p", "module": "m6", "seed": 0, "params": None, "outcome": "agree",
         "engines": {"reference": run, "vm": big}, "engine_errors": {}, "mismatches": {}},
    ]  # fmt: skip
    args = Namespace(engines=["reference", "vm"], cycles=8, seeds=1, param_variants=0, timeout=10.0, mem_limit_gb=6.0)
    text = cc.build_report(results, {"c/p": {}}, {"c/p": ["f.v"]}, args)
    assert "## Mismatches (2)" in text
    assert text.count("### vm: assign y = a + b;") == 1
    assert "2 run(s): p:m1, p:m2" in text
    assert "| 2 | `ValueError: bad '…' N` |" in text
    assert "uv run -m veriforge.reduce f.v --preprocess --top m1" in text
    assert "- p:m5: vm memory" in text
    heavy = [ln for ln in text.split("### Heaviest runs by peak memory")[1].splitlines() if ln.startswith("| p:")]
    assert heavy[0].startswith("| p:m6 | vm | ok | 2500 | 900 / 2500 | 25.0 |")  # heaviest first, x reference
    json.dumps(results)  # (results.json must serialize)


@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_AS is POSIX-only")
def test_memory_limit_reports_memory_outcome(cc, tmp_path, monkeypatch):
    # A job that allocates far past a 1 GB cap ends as "memory", not by taking the machine down.
    hog = tmp_path / "hog.py"
    hog.write_text("x = bytearray(4 << 30)\n")
    monkeypatch.setattr(cc, "__file__", str(hog))
    res = cc._run_job({"kind": "engine"}, 60, mem_limit_gb=1.0)
    assert res["status"] == "memory", res
    assert cc._memory_limiter(0) is None


def test_scalar_type_params_used_as_structs(cc):
    src = """\
module bad #(parameter type resp_t = logic, parameter type data_t = logic [7:0])
    (input data_t d, output resp_t resp_o, output data_t q);
  resp_t r;
  always_comb begin resp_o.ready = 1'b1; r.b.resp = 2'b00; q = d; end
endmodule
module ok #(parameter type dtype = logic [7:0], parameter W = 8) (input dtype d, output dtype q);
  assign q = d;
endmodule
"""
    design = parse_text(src)
    assert cc.scalar_type_params_used_as_structs(design.get_module("bad")) == ["r", "resp_o"]
    assert cc.scalar_type_params_used_as_structs(design.get_module("ok")) == []


def test_zero_width_ports_by_default(cc):
    src = """\
module needs_override #(parameter N = 0, parameter W = 8) (input [N-1:0] a, input [W-1:0] b, output [0:3] c);
  assign c = 4'd0;
endmodule
module fine #(parameter N = 1) (input [N-1:0] a, output y);
  assign y = a[0];
endmodule
"""
    design = parse_text(src)
    assert cc.zero_width_ports_by_default(design.get_module("needs_override")) == ["a"]  # (an ascending [0:3] is fine)
    assert cc.zero_width_ports_by_default(design.get_module("fine")) == []


def test_scalar_type_params_passed_to_a_struct_user(cc):
    src = """\
module child #(parameter type resp_t = logic) (output resp_t resp_o);
  assign resp_o.ready = 1'b1;
endmodule
module parent #(parameter type my_resp_t = logic, parameter type other_t = logic) (output my_resp_t r, output other_t o);
  child #(.resp_t(my_resp_t)) u (.resp_o(r));
  assign o = 1'b0;
endmodule
"""
    design = parse_text(src)
    assert cc.scalar_type_params_used_as_structs(design.get_module("parent"), design) == ["u.resp_t"]
    assert cc.scalar_type_params_used_as_structs(design.get_module("parent")) == []  # (no design: own uses only)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_discover_units_splits_a_project_with_conflicting_definitions(cc, tmp_path):
    """pulp holds six different `axi_pkg.sv` copies; merged into one design the
    first won, so the xbar benches got one without their types. A conflicting
    project splits into units (a directory with an `rtl*` subdirectory), each
    with its own copy of a file even when another unit's is identical."""
    corpus = tmp_path / "corpus"
    pkg_a = "package p;\n  localparam int A = 1;\nendpackage\n"
    pkg_b = "package p;\n  localparam int A = 1;\n  localparam int B = 2;\nendpackage\n"
    shared = "module s(input a, output y);\n  assign y = a;\nendmodule\n"
    _write(corpus / "proj/x/one/rtl/p.sv", pkg_a)
    _write(corpus / "proj/x/one/rtl/s.sv", shared)
    _write(corpus / "proj/x/one/tb/tb.sv", "module tb_one; endmodule\n")
    _write(corpus / "proj/x/two/rtl_flat/p.sv", pkg_b)
    _write(corpus / "proj/x/two/rtl_flat/s.sv", shared)
    _write(corpus / "plain/a.v", "module a(input i, output o); assign o = i; endmodule\n")
    _write(corpus / "plain/sub/rtl/b.v", "module b(input i, output o); assign o = i; endmodule\n")

    units = cc.discover_units([corpus])
    files = {u: sorted(Path(p).relative_to(corpus).as_posix() for p, _ in e) for u, e in units.items()}
    assert files == {
        "corpus/plain": ["plain/a.v", "plain/sub/rtl/b.v"],  # (no conflict: one unit)
        "corpus/proj/x/one": ["proj/x/one/rtl/p.sv", "proj/x/one/rtl/s.sv", "proj/x/one/tb/tb.sv"],
        "corpus/proj/x/two": ["proj/x/two/rtl_flat/p.sv", "proj/x/two/rtl_flat/s.sv"],
    }
    assert [u for u in units if cc._selected(u, ["proj"])] == ["corpus/proj/x/one", "corpus/proj/x/two"]
    assert [u for u in units if cc._selected(u, ["two"])] == ["corpus/proj/x/two"]
    assert [u for u in units if cc._selected(u, ["corpus/plain"])] == ["corpus/plain"]


def test_discover_test_files_leaves_out_expected_failures(cc, tmp_path):
    """A test-suite corpus: each file is its own unit; sv-tests' negative and
    UVM tests and ivtest's non-`normal` entries are left out."""
    sv = tmp_path / "tests"
    _write(sv / "chapter-5/ok.sv", "/*\n:name: ok\n:tags: 5.6\n*/\nmodule top; endmodule\n")
    _write(sv / "chapter-5/bad.sv", "/*\n:should_fail_because: syntax\n*/\nmodule top(; endmodule\n")
    _write(sv / "uvm/u.sv", "/*\n:tags: uvm uvm-classes\n*/\nmodule top; endmodule\n")
    assert list(cc.discover_test_files(sv)) == ["tests/chapter-5/ok"]

    iv = tmp_path / "ivtest"
    _write(iv / "regress-vlg.list", "a  normal  ivltests\nb  CE  ivltests\nc  normal,-g2012  ivltests # note\n")
    _write(iv / "regress-sv.list", "b  normal,-g2012  ivltests\nd  NI  ivltests\n")
    for name in "abcde":
        _write(iv / f"ivltests/{name}.v", "module main; endmodule\n")
    assert list(cc.discover_test_files(iv)) == ["ivtest/ivltests/a", "ivtest/ivltests/b", "ivtest/ivltests/c"]


def test_closed_testbench_is_checked_through_time(cc, tmp_path):
    """A top with no ports runs as a testbench (`record_testbench_trace`), and a
    `$display` difference counts as a mismatch."""
    design = parse_text(
        'module tb; reg [3:0] c = 0; always #5 c = c + 1; initial #23 $display("c=%0d", c); endmodule\n'
    )
    pkl = tmp_path / "d.pkl"
    pkl.write_bytes(pickle.dumps(design))
    traces = {}
    for engine in ("reference", "vm"):
        job = {
            "module": "tb",
            "engine": engine,
            "seed": 0,
            "resets": {},
            "pickle": str(pkl),
            "cycles": 4,
            "testbench_time": 100,
            "trace_out": str(tmp_path / f"{engine}.pkl"),
        }
        assert cc.run_engine(job)["status"] == "ok"
        traces[engine] = job["trace_out"]
    assert cc.compare({"module": "tb", "pickle": str(pkl), "traces": traces}) == {"mismatches": {}}
    assert cc._display_difference(["c=4"], ["c=5"]).startswith("$display line 1 differs")
    assert cc._instantiated_names(parse_text("module a; endmodule\nmodule b; a u(); endmodule\n")) == {"a"}
