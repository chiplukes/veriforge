"""Regression test for a third instance of the "concat-LHS write silently
dropped for a memory-backed member" bug family (see
test_whole_array_audit_followups.py's TestConcatenationLHSMemoryMember for
the first two: reference engine's `_concat_nba_accumulate` and compiled
engine's PROCEDURAL `_emit_concat_lhs`). This one is in the compiled
engine's CONTINUOUS-assign compiler (`_compile_concat_cont_assign`,
sim/compiled/_process_compiler.py) -- found via real-world feedback on the
`axis_pix_correction2` design, where a hierarchy-flattened submodule port
connection (`.m_axis_tdata({tuser, tlast, arr})`, `arr` a 2-D packed array)
left `arr` permanently X, stalling the whole downstream pipeline.

`TestContinuousAssignConcatLHSPlainWideSignal` below covers a fourth,
sibling instance of the same underlying bug class in the same function:
a concat-LHS member that is a PLAIN (non-array, non-memory) signal wider
than 64 bits. Every other branch of `_compile_concat_cont_assign` checks
`self._signal_widths[sid] > _WORD_BITS` before falling back to a
single-word scalar write; the bare-Identifier catch-all for a plain
signal didn't, so a wide plain-signal concat member (e.g. the real
`axis_pix_correction2.sv`'s `.m_axis_tdata({m_axis_pixout_tuser,
m_axis_pixout_tlast, m_axis_pixout_tdata})`, with `m_axis_pixout_tdata` a
flat 1536-bit vector, not an array) silently never received a driver at
all and stayed permanently X -- discovered only once the NBA_MEM_MAX
buffer-overflow bug above stopped masking it (that bug stalled the
pipeline entirely before this one could ever be observed).
"""

from __future__ import annotations

import pytest

from veriforge.project import parse_file
from veriforge.sim.testbench import Simulator

from .engines import ENGINES


def _parse(src: str, tmp_path):
    path = tmp_path / "dut.sv"
    path.write_text(src)
    return parse_file(path)


class TestContinuousAssignConcatLHSMemoryMember:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_plain_continuous_assign(self, engine, tmp_path):
        """`assign {tuser, tlast, arr} = wide_in;` with `arr` a 2-D packed array."""
        design = _parse(
            """
            module top (
                input logic [33:0] wide_in,
                output logic tuser,
                output logic tlast,
                output logic [3:0][7:0] arr
            );
            assign {tuser, tlast, arr} = wide_in;
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        wide = (1 << 33) | (0 << 32) | 0x44332211
        sim.drive("wide_in", wide)
        sim.run(max_time=0)
        assert int(sim.signal("tuser").value) == 1
        assert int(sim.signal("tlast").value) == 0
        assert int(sim.signal("arr").value) == 0x44332211
        assert int(sim.signal("arr[0]").value) == 0x11
        assert int(sim.signal("arr[3]").value) == 0x44

    @pytest.mark.parametrize("engine", ENGINES)
    def test_wide_rhs_source_via_submodule_port(self, engine, tmp_path):
        """Same shape as `test_via_submodule_port_connection`, but the
        submodule's port (and hence the flattened continuous assign's RHS)
        is itself >64 bits wide -- found via real-world feedback on
        `axis_row_correct.sv`'s `.dout({tuser, tfirst, tlast, tdata})`
        connection to an `xpm_fifo_sync` mock's 147-bit `dout` port.
        A first attempt at fixing `test_via_submodule_port_connection`'s
        gap only worked for a narrow (<=64-bit) RHS: the compiled engine's
        `_compile_concat_cont_assign` fell back to squeezing the whole RHS
        through the SCALAR `_emit_expr` (no >64-bit support at all) instead
        of slicing each memory element directly out of the wide RHS
        signal -- confirmed wrong: elements past roughly the 64-bit
        boundary read back corrupted/word-shifted values, not merely X."""
        src = """
            module leaf (
                output logic [146:0] dout
            );
            logic dout_tuser;
            logic dout_tfirst;
            logic dout_tlast;
            logic [143:0] dout_tdata;
            assign dout_tuser = 1'b1;
            assign dout_tfirst = 1'b0;
            assign dout_tlast = 1'b1;
            assign dout_tdata = 144'h0102030405060708090a0b0c0d0e0f1011;
            assign dout = {dout_tuser, dout_tfirst, dout_tlast, dout_tdata};
            endmodule

            module top (
                output logic tuser,
                output logic tfirst,
                output logic tlast,
                output logic [7:0][17:0] tdata
            );
            leaf u_leaf (.dout({tuser, tfirst, tlast, tdata}));
            endmodule
            """
        ref_design = _parse(src, tmp_path)
        ref = Simulator(ref_design.get_module("top"), engine="reference", design=ref_design)
        ref.run(max_time=0)
        design = _parse(src, tmp_path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.run(max_time=0)
        assert int(sim.signal("tuser").value) == int(ref.signal("tuser").value) == 1
        assert int(sim.signal("tfirst").value) == int(ref.signal("tfirst").value) == 0
        assert int(sim.signal("tlast").value) == int(ref.signal("tlast").value) == 1
        assert int(sim.signal("tdata").value) == int(ref.signal("tdata").value)
        for i in range(8):
            assert int(sim.signal(f"tdata[{i}]").value) == int(ref.signal(f"tdata[{i}]").value), f"lane {i}"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_via_submodule_port_connection(self, engine, tmp_path):
        """A hierarchy-flattened submodule port connection whose port maps
        to a concatenation at the parent level -- the exact shape that
        exposed this in the real `axis_row_correct.sv` RTL
        (`.m_axis_tdata({inreg_tuser, inreg_tlast, inreg_tdata})`)."""
        design = _parse(
            """
            module leaf (
                input logic [33:0] wide_in,
                output logic [33:0] m_axis_tdata
            );
            assign m_axis_tdata = wide_in;
            endmodule

            module top (
                input logic [33:0] wide_in,
                output logic tuser,
                output logic tlast,
                output logic [3:0][7:0] arr
            );
            leaf u_leaf (.wide_in(wide_in), .m_axis_tdata({tuser, tlast, arr}));
            endmodule
            """,
            tmp_path,
        )
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        wide = (1 << 33) | (0 << 32) | 0x44332211
        sim.drive("wide_in", wide)
        sim.run(max_time=0)
        assert int(sim.signal("tuser").value) == 1
        assert int(sim.signal("tlast").value) == 0
        assert int(sim.signal("arr").value) == 0x44332211
        assert int(sim.signal("arr[0]").value) == 0x11
        assert int(sim.signal("arr[3]").value) == 0x44


class TestContinuousAssignConcatLHSPlainWideSignal:
    @pytest.mark.parametrize("engine", ENGINES)
    def test_wide_plain_signal_via_submodule_port_connection(self, engine, tmp_path):
        """Same port-connection shape as
        `TestContinuousAssignConcatLHSMemoryMember.test_wide_rhs_source_via_submodule_port`,
        but the concat's wide member is a PLAIN (non-array) signal rather
        than a memory -- the exact shape that exposed this in the real
        `axis_pix_correction2.sv` RTL (`.m_axis_tdata({m_axis_pixout_tuser,
        m_axis_pixout_tlast, m_axis_pixout_tdata})`, with
        `m_axis_pixout_tdata` a flat 1536-bit vector). Left entirely X on
        every run before the fix."""
        src = """
            module leaf (
                output logic [145:0] dout
            );
            logic dout_tuser;
            logic dout_tlast;
            logic [143:0] dout_tdata;
            assign dout_tuser = 1'b1;
            assign dout_tlast = 1'b0;
            assign dout_tdata = 144'h0102030405060708090a0b0c0d0e0f1011;
            assign dout = {dout_tuser, dout_tlast, dout_tdata};
            endmodule

            module top (
                output logic tuser,
                output logic tlast,
                output logic [143:0] tdata
            );
            leaf u_leaf (.dout({tuser, tlast, tdata}));
            endmodule
            """
        ref_design = _parse(src, tmp_path)
        ref = Simulator(ref_design.get_module("top"), engine="reference", design=ref_design)
        ref.run(max_time=0)
        design = _parse(src, tmp_path)
        mod = design.get_module("top")
        sim = Simulator(mod, engine=engine, design=design)
        sim.run(max_time=0)
        assert int(sim.signal("tuser").value) == int(ref.signal("tuser").value) == 1
        assert int(sim.signal("tlast").value) == int(ref.signal("tlast").value) == 0
        assert int(sim.signal("tdata").value) == int(ref.signal("tdata").value) == 0x0102030405060708090A0B0C0D0E0F1011
