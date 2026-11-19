"""Processes that differ only in constants share one generated body (_families).

A generate loop elaborates into one process per iteration. Emitted as one
function each, a 65536-iteration loop over a flip-flop was 1.3M lines of
Cython (an ibex reducer candidate; Cython takes about 13 GB per million
lines). Grouped, each member is a two-line wrapper passing its row of a
constant table to the family's shared body.
"""

from __future__ import annotations

import random

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.compiled._families import canonical_temps, join_constants, restore_temps, split_constants
from veriforge.sim.compiled.codegen import CythonCodegen
from veriforge.sim.elaborate import flatten_module
from veriforge.sim.sv_functions import lower_width_casts
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from ..engines import ENGINES

_LOOP = """\
module t #(parameter N = {n}) (input clk, input rst, input [N-1:0] d, output logic [N-1:0] q, output [N-1:0] c);
  for (genvar i = 0; i < N; i++) begin : g
    always_ff @(posedge clk) if (rst) q[i] <= 1'b0; else q[i] <= d[i] ^ q[(i + 1) % N];
    assign c[i] = q[i] & d[(i + 3) % N];
  end
endmodule
"""


def _generate(src: str, top: str = "t") -> str:
    design = parse_text(src)
    flat = flatten_module(design.get_module(top), design=design)
    lower_width_casts(flat)  # (as the Simulator does before code generation)
    return CythonCodegen().generate(flat)


def test_generate_loop_code_grows_by_a_wrapper_per_iteration(monkeypatch):
    small, big = (len(_generate(_LOOP.format(n=n))) for n in (256, 512))
    # (Each iteration: a sequential and a continuous wrapper, their table rows
    # and process-table entries, per-signal constants.)
    assert (big - small) / 256 < 800
    assert "_vf_fam_" in _generate(_LOOP.format(n=256))
    monkeypatch.setenv("VERIFORGE_COMPILE_FAMILY_MIN", "0")
    small0, big0 = (len(_generate(_LOOP.format(n=n))) for n in (256, 512))
    assert (big0 - small0) / (big - small) > 4


def test_below_the_threshold_output_is_unchanged(monkeypatch):
    src = _LOOP.format(n=16)
    shared = _generate(src)
    monkeypatch.setenv("VERIFORGE_COMPILE_FAMILY_MIN", "0")
    assert "_vf_fam_" not in shared
    assert _generate(src) == shared


_WIDE = """\
module t (input clk, input rst, input [71:0] d, input [129:0] w, output logic [71:0] q, output [23:0] k,
    output [23:0] s, output logic [129:0] r);
  for (genvar i = 0; i < 24; i++) begin : g
    localparam logic [63:0] K = 64'd1 << (i * 2 + 17);
    assign k[i] = |(K & d[63:0]);
    assign s[i] = w[i * 5 + 3] ^ d[71 - i];
    always_ff @(posedge clk)
      if (rst) begin q[i * 3 +: 3] <= 3'(i); r[i * 5 +: 5] <= '0; end
      else begin
        q[i * 3 +: 3] <= q[i * 3 +: 3] + d[i * 3 +: 3] + 3'(i);
        r[i * 5 +: 5] <= r[i * 5 +: 5] ^ w[(i * 7) % 126 +: 5] ^ 5'(i);
      end
  end
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_shared_bodies_agree_on_every_engine(engine, monkeypatch):
    """Sharing forced on for every repeated pair, with constants past 32 bits
    in shifts and values wider than 64 bits."""
    monkeypatch.setenv("VERIFORGE_COMPILE_FAMILY_MIN", "2")
    assert _generate(_WIDE).count("cdef void _vf_fam_") >= 3
    design = parse_text(_WIDE)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    ref_design = parse_text(_WIDE)
    ref = Simulator(ref_design.get_module("t"), engine="reference", design=ref_design)
    rng = random.Random(7)
    for cycle in range(12):
        drives = {"rst": int(cycle < 2), "d": rng.getrandbits(72), "w": rng.getrandbits(130)}
        for s in (sim, ref):
            s.drive("clk", 0)
            for name, value in drives.items():
                s.drive(name, value)
            s.settle()
            s.drive("clk", 1)
            s.settle()
        for out in ("q", "k", "s", "r"):
            assert sim.read(out) == ref.read(out), (cycle, out)
    assert sim.read("r") != Value(0, width=130)


@pytest.mark.parametrize(
    "body",
    [
        "    c.val[12] = (c.val[3] + 7) & wmask(32)\n    mark_dirty(c, 12)\n",
        '    cdef long long _sc0_v[4]\n    x = 1LL << 40\n    y = 0x1F + 1.5 + 007\n    s = "a 12 b" # 9\n',
        "    _et17_v = c.val[4]\n    _et3_m = c.mask[4]\n    c.val[5] = _et17_v & ~_et3_m & 18446744073709551615\n",
    ],
)
def test_split_and_restore_round_trip(body):
    canon, temps = canonical_temps(body)
    segments, values = split_constants(canon)
    assert restore_temps(join_constants(segments, values), temps) == body


def test_split_leaves_declarations_strings_and_suffixed_literals():
    segments, values = split_constants('    cdef long long a[4]\n    x = 1LL + 0x10 + 2.5 + "7" + 9\n')
    assert values == (9,)
    assert "a[4]" in segments[0] and "1LL" in segments[0] and '"7"' in segments[0]


def test_temporaries_are_numbered_by_first_appearance():
    canon, temps = canonical_temps("_et40_v = _et39_m\n_et40_m = _et41_v\n")
    assert canon == "_et0_v = _et1_m\n_et0_m = _et2_v\n"
    assert temps == (40, 39, 41)
