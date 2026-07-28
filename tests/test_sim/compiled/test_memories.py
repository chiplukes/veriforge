"""Compiled engine: memory arrays and $readmemh.

Split mechanically from tests/test_sim/test_compiled.py (work plan item 2.5).
"""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class TestMemoryArrayDimensionRegression:
    """Bug regression: reg [7:0] regfile [0:7] had dimensions=[]."""

    def test_reg_memory_dimensions_from_parser(self):
        """Parsed reg with array dimensions should have non-empty dimensions list."""
        from veriforge.transforms.tree_to_model import tree_to_design
        from veriforge.verilog_parser import verilog_parser

        parser = verilog_parser(start="source_text")
        tree = parser.build_tree(text="module t; reg [7:0] regfile [0:7]; endmodule")
        design = tree_to_design(tree)
        v = design.modules[0].variables[0]
        assert v.name == "regfile"
        assert len(v.dimensions) == 1
        assert int(v.dimensions[0].msb.value) == 0
        assert int(v.dimensions[0].lsb.value) == 7

    def test_net_memory_dimensions_from_parser(self):
        """Parsed wire with array dimensions should have non-empty dimensions list."""
        from veriforge.transforms.tree_to_model import tree_to_design
        from veriforge.verilog_parser import verilog_parser

        parser = verilog_parser(start="source_text")
        tree = parser.build_tree(text="module t; wire [3:0] mem_net [0:15]; endmodule")
        design = tree_to_design(tree)
        n = design.modules[0].nets[0]
        assert n.name == "mem_net"
        assert len(n.dimensions) == 1
        assert int(n.dimensions[0].msb.value) == 0
        assert int(n.dimensions[0].lsb.value) == 15


class TestCompiledReadmemh:
    """$readmemh support in the compiled engine."""

    def test_readmemh_loads_data(self, tmp_path):
        """$readmemh in initial block loads data into compiled memory."""
        hex_file = tmp_path / "data.hex"
        hex_file.write_text("AA\nBB\nCC\nDD\n")
        mod = _make_readmemh_module(str(hex_file))
        sim = Simulator(mod, engine="compiled")

        # Read mem[0] via combo out = mem[addr]
        sim.drive("addr", Value(0, width=2))
        sim.run(max_time=0)
        assert sim.read("out") == Value(0xAA, width=8), "mem[0] should be 0xAA"

        sim.drive("addr", Value(1, width=2))
        sim.run(max_time=10)
        assert sim.read("out") == Value(0xBB, width=8), "mem[1] should be 0xBB"

        sim.drive("addr", Value(3, width=2))
        sim.run(max_time=20)
        assert sim.read("out") == Value(0xDD, width=8), "mem[3] should be 0xDD"

    def test_readmemh_cross_validation(self, tmp_path):
        """$readmemh produces identical results across reference, vm, compiled."""
        hex_file = tmp_path / "cross.hex"
        hex_file.write_text("10\n20\n30\n40\n")

        results = {}
        for eng in ["reference", "vm", "compiled"]:
            mod = _make_readmemh_module(str(hex_file))
            sim = Simulator(mod, engine=eng)
            vals = []
            for addr_val in range(4):
                sim.drive("addr", Value(addr_val, width=2))
                sim.run(max_time=addr_val * 10)
                vals.append(int(sim.read("out")))
            results[eng] = vals

        assert results["compiled"] == results["reference"], (
            f"compiled {results['compiled']} != reference {results['reference']}"
        )
        assert results["compiled"] == results["vm"], f"compiled {results['compiled']} != vm {results['vm']}"


class TestWideSignalMemory:
    """Cross-engine validation for wide-element (>64-bit) memory operations.

    Exercises element NBA write, blocking sequential write, and combo read
    paths with element widths that span multiple 64-bit words (65, 96, 129 bits).
    """

    @staticmethod
    def _make_clocked_sim(module_fn, elem_width: int, engine: str, n_cycles: int = 1) -> Simulator:
        """Create a Simulator with a single clock pre-scheduled for n_cycles posedges."""
        sim = Simulator(module_fn(elem_width), engine=engine)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)
        sim._schedule_clock_events(clk, (n_cycles + 1) * 20)
        return sim

    @staticmethod
    def _advance_one_posedge(sim: Simulator) -> None:
        """Advance the simulation by one posedge (two run_step calls)."""
        for _ in range(2):
            sim.run_step()

    @pytest.mark.parametrize("elem_width", [65, 96, 129])
    def test_wide_mem_nba_write_same_addr_read_cross_engine(self, elem_width):
        """NBA write to addr N, combo read from same addr N: value round-trips."""
        mask_all = (1 << elem_width) - 1
        write_val = ((1 << (elem_width - 1)) | 0xDEADBEEFCAFEBABE) & mask_all

        results = {}
        for eng in ("reference", "vm", "compiled"):
            sim = self._make_clocked_sim(_make_wide_mem_nba_write_combo_read, elem_width, eng)
            sim.drive("data_in", Value(write_val, width=elem_width))
            sim.drive("wr_addr", Value(2, width=2))
            sim.drive("rd_addr", Value(2, width=2))
            self._advance_one_posedge(sim)
            results[eng] = sim.read("data_out")

        ref = results["reference"]
        for eng in ("vm", "compiled"):
            assert results[eng] == ref, f"elem_width={elem_width} {eng}: got {results[eng]}, expected {ref}"

    @pytest.mark.parametrize("elem_width", [65, 96, 129])
    def test_wide_mem_nba_write_different_addr_read_cross_engine(self, elem_width):
        """NBA write to addr 1, read from addr 3: unwritten slot reads as zero."""
        mask_all = (1 << elem_width) - 1
        write_val = ((1 << (elem_width - 1)) | 0xABCDEF0123456789) & mask_all

        results = {}
        for eng in ("reference", "vm", "compiled"):
            sim = self._make_clocked_sim(_make_wide_mem_nba_write_combo_read, elem_width, eng)
            sim.drive("data_in", Value(write_val, width=elem_width))
            sim.drive("wr_addr", Value(1, width=2))
            sim.drive("rd_addr", Value(3, width=2))
            self._advance_one_posedge(sim)
            results[eng] = sim.read("data_out")

        ref = results["reference"]
        for eng in ("vm", "compiled"):
            assert results[eng] == ref, f"elem_width={elem_width} {eng}: got {results[eng]}, expected {ref}"

    @pytest.mark.parametrize("elem_width", [65, 96, 129])
    def test_wide_mem_nba_two_sequential_writes_cross_engine(self, elem_width):
        """Two sequential NBA writes to different addresses: both slots hold correct values.

        Reference engine excluded: it does not support incremental run_step() for
        multi-cycle scenarios (same limitation as TestPhase2CrossValidation).
        """
        mask_all = (1 << elem_width) - 1
        val_a = ((1 << (elem_width - 1)) | 0x1111111111111111) & mask_all
        val_b = ((1 << (elem_width // 2)) | 0xAAAAAAAAAAAAAAAA) & mask_all

        for rd_addr, expected_val in ((0, val_a), (3, val_b)):
            results = {}
            for eng in ("vm", "compiled"):
                sim = self._make_clocked_sim(_make_wide_mem_nba_write_combo_read, elem_width, eng, n_cycles=3)
                sim.drive("wr_addr", Value(0, width=2))
                sim.drive("data_in", Value(val_a, width=elem_width))
                self._advance_one_posedge(sim)
                sim.drive("wr_addr", Value(3, width=2))
                sim.drive("data_in", Value(val_b, width=elem_width))
                self._advance_one_posedge(sim)
                sim.drive("rd_addr", Value(rd_addr, width=2))
                sim.run_step()
                results[eng] = sim.read("data_out")

            assert results["vm"] == results["compiled"], (
                f"elem_width={elem_width} rd_addr={rd_addr}: vm={results['vm']} != compiled={results['compiled']}"
            )
            assert results["compiled"] == Value(expected_val, width=elem_width), (
                f"elem_width={elem_width} rd_addr={rd_addr}: "
                f"got {results['compiled']}, expected {Value(expected_val, width=elem_width)}"
            )

    @pytest.mark.parametrize("elem_width", [65, 96, 129])
    def test_wide_mem_blocking_seq_write_combo_read_cross_engine(self, elem_width):
        """Blocking write in posedge block + combo read: value round-trips."""
        mask_all = (1 << elem_width) - 1
        write_val = ((1 << (elem_width - 1)) | 0xFEDCBA9876543210) & mask_all

        results = {}
        for eng in ("reference", "vm", "compiled"):
            sim = self._make_clocked_sim(_make_wide_mem_blocking_seq_write_combo_read, elem_width, eng)
            sim.drive("data_in", Value(write_val, width=elem_width))
            sim.drive("wr_addr", Value(1, width=2))
            sim.drive("rd_addr", Value(1, width=2))
            self._advance_one_posedge(sim)
            results[eng] = sim.read("data_out")

        ref = results["reference"]
        for eng in ("vm", "compiled"):
            assert results[eng] == ref, f"elem_width={elem_width} {eng}: got {results[eng]}, expected {ref}"

    @pytest.mark.parametrize("elem_width", [65, 96, 129])
    def test_wide_mem_overwrite_same_addr_cross_engine(self, elem_width):
        """Writing same address twice: second write wins.

        Reference engine excluded: does not support incremental multi-cycle run_step().
        """
        mask_all = (1 << elem_width) - 1
        val_first = ((1 << (elem_width - 1)) | 0x1234567890ABCDEF) & mask_all
        val_second = ((1 << (elem_width // 3)) | 0xFEDCBA0987654321) & mask_all

        results = {}
        for eng in ("vm", "compiled"):
            sim = self._make_clocked_sim(_make_wide_mem_nba_write_combo_read, elem_width, eng, n_cycles=3)
            sim.drive("wr_addr", Value(2, width=2))
            sim.drive("rd_addr", Value(2, width=2))
            sim.drive("data_in", Value(val_first, width=elem_width))
            self._advance_one_posedge(sim)
            sim.drive("data_in", Value(val_second, width=elem_width))
            self._advance_one_posedge(sim)
            results[eng] = sim.read("data_out")

        assert results["vm"] == results["compiled"], (
            f"elem_width={elem_width}: vm={results['vm']} != compiled={results['compiled']}"
        )
        assert results["compiled"] == Value(val_second, width=elem_width), (
            f"elem_width={elem_width}: got {results['compiled']}, expected second write value"
        )
