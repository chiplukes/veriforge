"""Frozen reads retain clock history, wide data, and conservative fallbacks."""

import hashlib

import pytest

from veriforge.sim.compiled._snapshots import SnapshotReads, copy_spans
from veriforge.sim.compiled.codegen import CythonCodegen
from veriforge.sim.compiled.compiled_scheduler import _compute_elab_hash
from veriforge.sim.compiled.compiler import _cache_key
from veriforge.sim.elaborate import flatten_module
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from ..engines import ENGINES


@pytest.mark.parametrize(
    "body, data, full",
    [
        ("    x = sv[2] | sm[7]", {2, 7}, False),
        ("    x = sv[k]", set(), True),
        ("    new_helper(c, sv, sm, 9)", set(), True),
        ("    new_helper_sv(c, 9)", set(), True),
        ("    # sv[123]\n    x = 'sm[999]'", set(), False),
        ("    _whole_stage_signal_s_sv(c, sv, sm, 2, 7)", {7}, False),
        ("    _whole_stage_insert_signal_slice_sv(c, sv, sm, 2, (x + f(y)), 9, 0, 128)", {9}, False),
        ("    _wmem3_stage_insert_signal_slice_sv(c, sv, sm, (f(a, b)), 2, 7, 0, 128)", {7}, False),
        ("    x = _sig_extract_word_val_sv(sv, sm, c, 9, 64)", {9}, False),
        ("    x = _sig_extract_word_mask_sv(sm, c, 7, 64)", {7}, False),
        ("    _whole_stage_signal_sv(c, sv, sm, 2, k)", set(), True),
    ],
)
def test_snapshot_dependencies_and_unknown_uses(monkeypatch, body, data, full):
    monkeypatch.setenv("VERIFORGE_COMPILE_SNAPSHOTS", "needed")
    reads = SnapshotReads(10, {0})
    reads.record(body.splitlines())
    assert reads.data == data
    assert reads.edges == {0}
    assert reads.full is full


def test_snapshot_span_density_and_coalescing():
    assert copy_spans({1, 2, 7}, 100, full=False) == [(1, 2), (7, 1)]
    assert copy_spans({0, 2, 3}, 5, full=False) == [(0, 5)]
    assert copy_spans(set(), 8, full=True) == [(0, 8)]
    assert copy_spans(set(), 0, full=True) == []
    assert copy_spans(set(range(0, 260, 4)), 260, full=False) == [(0, 260)]


SOURCE = """module top(input clk, rst_n, input [127:0] d,
    output reg [127:0] q, lag, output reg [255:0] part);
  wire [127:0] sample;
  assign sample = d ^ 128'h80000000000000010000000000000001;
  for (genvar i=0; i<64; i=i+1) begin : unused
    wire [255:0] padding;
  end
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin q <= 0; lag <= 0; part <= 0; end
    else begin
      q <= sample;
      lag <= q;
      part[135:8] <= sample;
    end
  end
endmodule"""


@pytest.mark.parametrize("delta", ["scan", "queue"])
@pytest.mark.parametrize("snapshots", ["needed", "all"])
def test_wide_frozen_reads_match_engines_and_batch_events(parser, monkeypatch, delta, snapshots):
    monkeypatch.setenv("VERIFORGE_DELTA_ENGINE", delta)
    monkeypatch.setenv("VERIFORGE_COMPILE_SNAPSHOTS", snapshots)
    sims = {}
    for engine in ENGINES:
        design = tree_to_design(parser.build_tree(SOURCE))
        sims[engine] = Simulator(design.modules[0], engine=engine)

    def compare():
        states = []
        for sim in sims.values():
            states.append(tuple((v.val, v.mask, v.width) for v in (sim.read(n) for n in ("q", "lag", "part"))))
        assert len(set(states)) == 1, states

    for cycle in range(7):
        for sim in sims.values():
            sim.drive("clk", 0)
            sim.drive("rst_n", int(cycle != 0))
            sim.drive("d", Value((1 << (70 + cycle)) + cycle, mask=(1 << 100) if cycle == 3 else 0, width=128))
            sim.settle()
            sim.drive("clk", 1)
            sim.settle()
        compare()
        if cycle == 1:
            assert int(sims["compiled"].read("q")) == ((1 << 71) + 1) ^ ((1 << 127) | (1 << 64) | 1)
            assert int(sims["compiled"].read("lag")) == 0
    for engine, sim in sims.items():
        sim.drive("clk", 0)
        sim.settle()
        sim.drive("d", Value((1 << 126) + 5, width=128))
        if engine == "compiled":
            sim.batch_run(8, "clk", events=[(0, "rst_n", 0), (2, "rst_n", 1)])
        else:
            for cycle in range(8):
                sim.drive("rst_n", int(cycle >= 2))
                sim.settle()
                sim.drive("clk", 1)
                sim.settle()
                sim.drive("clk", 0)
                sim.settle()
    compare()
    assert int(sims["compiled"].read("q")) == ((1 << 126) + 5) ^ ((1 << 127) | (1 << 64) | 1)


def test_streaming_selection_and_cache_identity(parser, monkeypatch, tmp_path):
    design = tree_to_design(parser.build_tree(SOURCE))
    flat = flatten_module(design.modules[0])
    keys = []
    for mode in ("needed", "all"):
        monkeypatch.setenv("VERIFORGE_COMPILE_SNAPSHOTS", mode)
        cg = CythonCodegen()
        source = cg.generate(flat)
        path = tmp_path / f"{mode}.pyx"
        digest = CythonCodegen().generate_to_file(flat, str(path))
        assert digest == hashlib.sha256(source.encode()).hexdigest()
        assert path.read_text() == source
        assert cg._snapshot_reads.full == (mode == "all")
        keys.append((_cache_key("identical source"), _compute_elab_hash(flat, [])))
        if mode == "needed":
            assert cg._snapshot_reads.data == {cg._signal_map[n] for n in ("q", "sample")}
    assert keys[0][0] != keys[1][0]
    assert keys[0][1] != keys[1][1]


@pytest.mark.parametrize("width", [8, 128])
def test_sparse_snapshot_tables_execute_with_bounded_code(parser, width):
    padding = ", ".join(f"padding_{i}" for i in range(32))
    source = f"""module top(input clk, rst, output [{64 * width - 1}:0] y);
      for (genvar i=0; i<64; i=i+1) begin : g
        reg [{width - 1}:0] q, lag, {padding};
        always @(posedge clk) begin
          if (rst) begin q <= 0; lag <= 0; end
          else begin q <= q + {width}'d1; lag <= q; end
        end
        assign y[i*{width} +: {width}] = lag;
      end
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    flat = flatten_module(design.modules[0])
    cg = CythonCodegen()
    generated = cg.generate(flat)
    body = generated.split("cdef inline void _signal_snapshot(", 1)[1].split("ctypedef void", 1)[0]
    assert "for _j in range(65)" in body
    assert len(body.splitlines()) < 20
    sim = Simulator(flat, engine="compiled")
    sim.drive("clk", 0)
    sim.drive("rst", 1)
    sim.batch_run(2, "clk")
    sim.drive("rst", 0)
    sim.batch_run(3, "clk")
    assert int(sim.read("y")) == sum(2 << (i * width) for i in range(64))
