"""Regression test: a packed multi-dimensional signal (`logic
[N-1:0][7:0] name;` -- no trailing UNPACKED dimension at all, just two
packed dimensions before the identifier) never showed up in a VCD trace at
all, on every engine.

Reported directly: "logic [NUM_PIXELS-1:0][7:0] inreg_gain_tdata; //Q2.16
Format ... don't show up in vcd dumps (it would be nice if they are named
nicely for vcd files)".

Root cause: any dimensioned net/var/port registers into
`EvalContext._memories`/`ctx._memory_names`, never `ctx._signals` --
`_declarations.py`'s own extraction deliberately treats a PURE packed
multi-dim declaration with no unpacked dim as if its outermost packed
range were an addressable "memory" dimension too (so `name[i]` keeps
working as an element-select; see `_memory_shape`'s and
`_extract_net_declaration`'s own "borrow-one-dim-for-addressing"
docstrings -- this is deliberate, existing behavior, not itself a bug).
`vm`/`vm-fast`/`compiled`'s own `signal_names()` already accounted for
this, emitting per-element `name[i]` entries for anything in their
`mem_map`/`mem_info` -- but the `reference` engine's `Scheduler.
signal_names()` simply returned `set(self.ctx._signals.keys())`, entirely
omitting anything registered as a memory. Since `trace.py::
VcdTraceSession` (VCD tracing) enumerates every design signal via exactly
this method, the array never appeared in a VCD dump on `reference` at
all -- and even on the three engines that DID already know about it,
their internal `__mem_{mid}_wr` dirty-marker helper signal leaked into
the same VCD output as if it were a real design signal.

Fixed: `Scheduler.signal_names()` (`sim/scheduler.py`) now also emits
`name[i]` for every memory-registered signal, matching `vm`/`compiled`'s
existing convention exactly (so all four engines agree on what a traced
design's signal list looks like) instead of introducing a new one. The
`__mem_{mid}_wr` marker leak is fixed separately, by teaching
`elaborate.py::is_synthesized_local_name` (already the established filter
`VcdTraceSession` uses for excluding synthesized process-local variables)
to also recognize that prefix.
"""

from __future__ import annotations

import pytest

from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator
from veriforge.sim.trace import attach_vcd

from .engines import ENGINES

_SRC = """
module dut (
    output logic [3:0][7:0] inreg_gain_tdata
);
    reg clk = 0;
    always #5 clk = ~clk;
    reg [7:0] pixel_in = 0;
    always #10 pixel_in = pixel_in + 1;

    always @(posedge clk) begin
        inreg_gain_tdata[0] <= pixel_in;
        inreg_gain_tdata[1] <= inreg_gain_tdata[0];
        inreg_gain_tdata[2] <= inreg_gain_tdata[1];
        inreg_gain_tdata[3] <= inreg_gain_tdata[2];
    end
endmodule
"""


class TestVcdPackedMultidimArray:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_packed_array_appears_in_signal_names(self, engine, tmp_path):
        path = tmp_path / "dut.sv"
        path.write_text(_SRC)
        design = parse_file(path)
        mod = design.get_module("dut")
        sim = Simulator(mod, engine=engine, design=design)
        names = sim._sched.signal_names()
        for i in range(4):
            assert f"inreg_gain_tdata[{i}]" in names, f"engine={engine}: missing inreg_gain_tdata[{i}] in {names}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_packed_array_traced_and_no_internal_marker_leaks(self, engine, tmp_path):
        path = tmp_path / "dut.sv"
        path.write_text(_SRC)
        design = parse_file(path)
        mod = design.get_module("dut")
        sim = Simulator(mod, engine=engine, design=design)
        vcd_path = tmp_path / "out.vcd"
        with attach_vcd(sim, vcd_path):
            sim.run(max_time=40)
        text = vcd_path.read_text()
        for i in range(4):
            assert f"inreg_gain_tdata[{i}]" in text, f"engine={engine}: inreg_gain_tdata[{i}] missing from VCD"
        assert "__mem_" not in text, f"engine={engine}: internal memory marker signal leaked into VCD"
        # A real value change (not just X) must show up for at least one
        # element once the clock has toggled a few times.
        assert "b00000001" in text or "b00000010" in text or "b00000011" in text
