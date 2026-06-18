"""Tests for DarkRISCV RISC-V design: preprocessing, parsing, and elaboration.

Uses the DarkRISCV files in examples/darkriscv/ as an integration test for
the full toolchain: preprocessor → parser → semantic model.
"""

import os
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "darkriscv"
RTL_DIR = BASE_DIR / "rtl"
SIM_DIR = BASE_DIR / "sim"
SRC_DIR = BASE_DIR / "src"

DEFINES = {"__ICARUS__": "1"}
INCLUDE_PATHS = [str(RTL_DIR)]

RTL_FILES = [
    SIM_DIR / "darksimv.v",
    RTL_DIR / "darksocv.v",
    RTL_DIR / "darkbridge.v",
    RTL_DIR / "darkriscv.v",
    RTL_DIR / "darkram.v",
    RTL_DIR / "darkio.v",
    RTL_DIR / "darkuart.v",
    RTL_DIR / "darkpll.v",
]

# Skip if example files not present
pytestmark = pytest.mark.skipif(
    not (RTL_DIR / "config.vh").exists(),
    reason="DarkRISCV example files not found",
)


class TestDarkRISCVPreprocess:
    """Validate preprocessor on DarkRISCV sources."""

    @pytest.mark.parametrize("filepath", RTL_FILES, ids=lambda p: p.name)
    def test_preprocess_each_file(self, filepath):
        """Each RTL file preprocesses without error."""
        from veriforge.preprocessor import preprocess_file

        result = preprocess_file(filepath, defines=DEFINES, include_paths=INCLUDE_PATHS)
        assert len(result) > 0
        # No unexpanded backtick macros should remain (except in comments/strings)
        # Check that key defines were expanded
        assert "`__3STAGE__" not in result
        assert "`__HARVARD__" not in result

    def test_define_comment_stripping(self):
        """Define values should not include trailing // comments."""
        from veriforge.preprocessor import preprocess_file

        result = preprocess_file(RTL_DIR / "darkriscv.v", defines=DEFINES, include_paths=INCLUDE_PATHS)
        # The opcode defines like `LUI = 7'b01101_11 should expand cleanly
        # without trailing comments becoming part of the substituted text
        assert "7'b01101_11;" in result  # LUI expanded with semicolon visible

    def test_nested_macro_expansion(self):
        """Nested macros like __BAUD__ → BOARD_CK/UARTSPEED fully resolve."""
        from veriforge.preprocessor import preprocess_file

        result = preprocess_file(RTL_DIR / "darkuart.v", defines=DEFINES, include_paths=INCLUDE_PATHS)
        # __BAUD__ = ((BOARD_CK/__UARTSPEED__)) → ((100000000/115200))
        assert "100000000" in result
        assert "115200" in result
        # No unexpanded `BOARD_CK or `__UARTSPEED__ should remain
        assert "`BOARD_CK" not in result
        assert "`__UARTSPEED__" not in result

    def test_simulation_define_active(self):
        """__ICARUS__ define enables SIMULATION code paths."""
        from veriforge.preprocessor import preprocess_file

        result = preprocess_file(RTL_DIR / "darkriscv.v", defines=DEFINES, include_paths=INCLUDE_PATHS)
        # SIMULATION paths should be active (register init, $display, etc.)
        assert "initial for" in result or "$display" in result


class TestDarkRISCVParse:
    """Validate parsing DarkRISCV into a semantic model."""

    @pytest.fixture(scope="class")
    def design(self):
        """Parse all DarkRISCV files into a unified Design."""
        from veriforge.project import parse_files

        return parse_files(
            [str(f) for f in RTL_FILES],
            preprocess=True,
            defines=DEFINES,
            include_paths=INCLUDE_PATHS,
        )

    def test_module_count(self, design):
        """All 8 modules are parsed."""
        assert len(design.modules) == 8

    def test_module_names(self, design):
        expected = {
            "darksimv",
            "darksocv",
            "darkbridge",
            "darkriscv",
            "darkram",
            "darkio",
            "darkuart",
            "darkpll",
        }
        actual = {m.name for m in design.modules}
        assert actual == expected

    def test_top_module(self, design):
        """darksimv is the only top-level module."""
        tops = design.get_top_modules()
        assert len(tops) == 1
        assert tops[0].name == "darksimv"

    def test_darkriscv_ports(self, design):
        """CPU core has expected port structure."""
        cpu = design.get_module("darkriscv")
        assert cpu is not None
        port_names = {p.name for p in cpu.ports}
        # Key ports
        assert "CLK" in port_names
        assert "RES" in port_names
        assert "IDATA" in port_names
        assert "IADDR" in port_names
        assert "DATAI" in port_names
        assert "DATAO" in port_names
        assert "DADDR" in port_names

    def test_darkriscv_always_blocks(self, design):
        """CPU has always blocks (pipeline logic)."""
        cpu = design.get_module("darkriscv")
        assert len(cpu.always_blocks) >= 1

    def test_darkriscv_initial_block(self, design):
        """CPU has initial block (register init loop)."""
        cpu = design.get_module("darkriscv")
        assert len(cpu.initial_blocks) >= 1

    def test_darkram_has_initial(self, design):
        """RAM module has initial block with $readmemh."""
        ram = design.get_module("darkram")
        assert ram is not None
        assert len(ram.initial_blocks) >= 1

    def test_darksocv_instances(self, design):
        """SoC instantiates darkpll, darkbridge, darkram, darkio."""
        soc = design.get_module("darksocv")
        inst_modules = {inst.module_name for inst in soc.instances}
        assert "darkpll" in inst_modules
        assert "darkbridge" in inst_modules
        assert "darkram" in inst_modules
        assert "darkio" in inst_modules


# Simulation-specific defines (superset of preprocessing defines)
SIM_DEFINES = {"SIMULATION": "", "__ICARUS__": "", "__RESETPC__": "32'd0"}

# Simulation needs config.vh in the file list
SIM_FILES = [RTL_DIR / "config.vh", *RTL_FILES]


class TestDarkRISCVSimulation:
    """Run the DarkRISCV SoC simulation and validate boot behaviour."""

    @pytest.fixture(scope="class")
    def sim(self):
        """Parse, elaborate, and run simulation for 10K time units."""
        from veriforge.project import parse_files
        from veriforge.sim.testbench import Simulator

        # $readmemh paths in darkram.v are relative ("../src/darksocv.mem")
        saved_cwd = os.getcwd()
        os.chdir(str(SIM_DIR))
        try:
            design = parse_files(
                [str(f) for f in SIM_FILES],
                preprocess=True,
                defines=SIM_DEFINES,
                include_paths=INCLUDE_PATHS,
            )
            top = design.get_top_modules()[0]
            simulator = Simulator(top, engine="reference", design=design)
            simulator.run(max_time=10_000)
            return simulator
        finally:
            os.chdir(saved_cwd)

    def test_reset_released(self, sim):
        """PLL releases reset (RES=0) within 10K time units."""
        assert sim.read("RES") == 0

    def test_pc_advances(self, sim):
        """PC advances past the old stall point (0x14)."""
        pc = int(sim.read("soc0.bridge0.core0.PC"))
        assert pc > 0x14

    def test_cpu_not_halted(self, sim):
        """CPU is not stuck in HLT state."""
        assert sim.read("soc0.bridge0.core0.HLT") == 0

    def test_display_reset_message(self, sim):
        """darksimv.v emits 'reset' on startup."""
        output = "\n".join(sim.display_output)
        assert "reset" in output.lower()

    def test_display_dpram_message(self, sim):
        """darkram.v announces memory configuration."""
        output = "\n".join(sim.display_output)
        assert "dpram" in output.lower()
