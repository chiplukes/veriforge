"""Tests for the bench-framework-style Python testbench generator (--style bench)."""

from __future__ import annotations

import compileall
import subprocess
import sys
from pathlib import Path

from veriforge.dsl.testbench import generate_python_testbench
from veriforge.sim.bench import PlannerOverrides
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

DUT_PATH = Path(__file__).resolve().parents[2] / "examples" / "python_testbench" / "dut" / "multi_domain_axis_dut.v"
AXIL_REGFILE_PATH = Path(__file__).resolve().parents[2] / "examples" / "multi_iface_project" / "rtl" / "axil_regfile.v"
AXI4_RAM_PATH = Path(__file__).resolve().parents[2] / "examples" / "multi_iface_project" / "rtl" / "axi4_ram.v"


def _parse(src: str):
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src)
    design = tree_to_design(tree)
    return design.modules[0]


def _generate_for_multi_domain_dut() -> str:
    dut = _parse(DUT_PATH.read_text())
    overrides = PlannerOverrides(
        iface_domains={
            "pix_in": "pclk",
            "pix_out": "pclk",
            "rtr_in": "rclk",
            "rtr_out": "rclk",
        },
    )
    return generate_python_testbench(
        dut,
        enhanced=True,
        style="bench",
        dut_source_path=str(DUT_PATH),
        overrides=overrides,
    )


def test_bench_style_emits_testbench_framework_imports():
    text = _generate_for_multi_domain_dut()
    assert "from veriforge.sim.bench import BenchTimeoutError, PlannerOverrides, Testbench" in text
    # legacy step_drive imports must NOT appear in --style bench output
    assert "step_drive" not in text
    assert "Simulator(" not in text


def test_bench_style_emits_iface_layouts_for_tkeep_bundle():
    text = _generate_for_multi_domain_dut()
    # pix_in has 4-bit TKEEP and 48-bit TDATA -> epb=4, esb=12
    assert "'pix_in': {'elements_per_beat': 4, 'element_size_bits': 12, 'endian': 'little'}" in text
    # rtr_in has no TKEEP and 8-bit TDATA -> epb=1, esb=8 == TDATA, omitted
    assert "'rtr_in':" not in text.split("iface_layouts={")[1].split("},\n        }")[0]


def test_bench_style_emits_per_iface_stubs_and_main():
    text = _generate_for_multi_domain_dut()
    for fn in ("def drive_pix_in(", "def drive_rtr_in(", "def expect_pix_out(", "def expect_rtr_out("):
        assert fn in text, f"missing stub: {fn}"
    assert 'parser.add_argument(\n        "--vcd",' in text
    assert "with bench.run(vcd=args.vcd):" in text
    assert "bench.reset_all()" in text
    assert "BenchTimeoutError" in text


def test_bench_style_iface_domains_pre_filled():
    text = _generate_for_multi_domain_dut()
    assert "'pix_in': 'pclk'" in text
    assert "'rtr_in': 'rclk'" in text


def test_bench_style_dut_source_path_is_embedded():
    text = _generate_for_multi_domain_dut()
    assert "DUT_PATH = Path(r" in text
    assert "multi_domain_axis_dut.v" in text


def test_bench_style_output_is_valid_python(tmp_path):
    text = _generate_for_multi_domain_dut()
    out = tmp_path / "tb.py"
    out.write_text(text, encoding="utf-8")
    # py_compile-level syntax check
    assert compileall.compile_file(str(out), quiet=1, force=True), "generated scaffold did not compile"


def test_bench_style_generated_scaffold_runs(tmp_path):
    text = _generate_for_multi_domain_dut()
    out = tmp_path / "tb.py"
    out.write_text(text, encoding="utf-8")
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(out)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"scaffold failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "received pix_out:" in proc.stdout
    assert "received rtr_out:" in proc.stdout
    assert "caught (as expected)" in proc.stdout


def test_bench_style_requires_no_axis_emits_raw_signal_hint():
    src = """
    module no_iface_dut (
        input  wire clk,
        input  wire rst_n,
        input  wire [3:0] din,
        output wire [3:0] dout
    );
        assign dout = din;
    endmodule
    """
    dut = _parse(src)
    text = generate_python_testbench(dut, enhanced=True, style="bench")
    assert "No protocol bundles were detected" in text
    assert 'DUT_PATH = Path("path/to/your_dut.v")' in text


def test_legacy_style_unchanged_when_style_omitted():
    """Default style='legacy' continues to emit the original Simulator scaffold."""
    dut = _parse(DUT_PATH.read_text())
    overrides = PlannerOverrides(
        iface_domains={"pix_in": "pclk", "pix_out": "pclk", "rtr_in": "rclk", "rtr_out": "rclk"},
    )
    text = generate_python_testbench(dut, enhanced=True, overrides=overrides)  # style defaults to "legacy"
    assert "step_drive" in text
    assert "Simulator(" in text
    assert "bench.iface(" not in text


# ---------------------------------------------------------------------------
# Engine-native scaffold (--engine compiled / vm)
# ---------------------------------------------------------------------------


def _generate_native_for_multi_domain_dut(engine: str = "compiled") -> str:
    dut = _parse(DUT_PATH.read_text())
    overrides = PlannerOverrides(
        iface_domains={
            "pix_in": "pclk",
            "pix_out": "pclk",
            "rtr_in": "rclk",
            "rtr_out": "rclk",
        },
    )
    return generate_python_testbench(
        dut,
        enhanced=True,
        style="bench",
        dut_source_path=str(DUT_PATH),
        overrides=overrides,
        engine=engine,
    )


def test_engine_compiled_emits_compile_native_imports():
    text = _generate_native_for_multi_domain_dut()
    assert "compile_native" in text
    assert "AXIStreamSourceLowering" in text
    assert "AXIStreamSinkLowering" in text
    assert "LoweredDesign" in text
    # Python Testbench framework imports must NOT appear
    assert "BenchTimeoutError" not in text
    assert "bench.iface(" not in text


def test_engine_compiled_emits_build_native_bench():
    text = _generate_native_for_multi_domain_dut()
    assert "def build_native_bench() -> LoweredDesign:" in text
    assert "compile_native(bench, lowerings=lowerings)" in text
    assert "AXIStreamSourceLowering(" in text
    assert "AXIStreamSinkLowering(" in text


def test_engine_compiled_emits_run_bench_with_simulator():
    text = _generate_native_for_multi_domain_dut()
    assert "def run_bench(" in text
    assert "lowered.run(engine" in text
    assert "lowered.capture_signals" in text
    # VCD threaded through run()
    assert "vcd=vcd" in text


def test_engine_compiled_data_widths_inferred():
    text = _generate_native_for_multi_domain_dut()
    # pix_in/pix_out are 48-bit TDATA
    assert "data_width=48" in text
    # rtr_in/rtr_out are 8-bit TDATA
    assert "data_width=8" in text


def test_engine_compiled_emits_engine_default():
    text = _generate_native_for_multi_domain_dut(engine="compiled")
    assert "default='compiled'" in text
    text_vm = _generate_native_for_multi_domain_dut(engine="vm")
    assert "default='vm'" in text_vm


def test_engine_reference_unchanged():
    """engine='reference' (the default) still emits the Python Testbench scaffold."""
    text = _generate_for_multi_domain_dut()  # no engine= → "reference"
    assert "BenchTimeoutError" in text
    assert "bench.iface(" in text
    assert "compile_native" not in text


def test_engine_compiled_output_is_valid_python(tmp_path):
    text = _generate_native_for_multi_domain_dut()
    out = tmp_path / "tb_native.py"
    out.write_text(text, encoding="utf-8")
    assert compileall.compile_file(str(out), quiet=1, force=True), "native scaffold did not compile"


def test_engine_compiled_non_lowerable_falls_back():
    """DUT with a 'stream' protocol interface falls back to Python Testbench scaffold with warning."""
    src = """
    module pulp_stream_dut (
        input  wire clk,
        input  wire rst_ni,
        input  wire        valid_i,
        output wire        ready_o,
        input  wire [7:0]  data_i
    );
        assign ready_o = 1'b1;
    endmodule
    """
    dut = _parse(src)
    text = generate_python_testbench(dut, enhanced=True, style="bench", engine="compiled")
    # Should contain the warning comment
    assert "NOTE: `--engine` requested native lowering" in text
    # And fall back to the Python Testbench framework
    assert "BenchTimeoutError" in text
    assert "bench.run(" in text


# ---------------------------------------------------------------------------
# CLI smoke tests: generate → execute unmodified for real DUTs
# ---------------------------------------------------------------------------


def _parse_file(path: Path):
    """Parse a single Verilog file and return the first module."""
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=path.read_text())
    design = tree_to_design(tree)
    return design.modules[0]


def _run_scaffold(tmp_path: Path, text: str) -> subprocess.CompletedProcess:
    out = tmp_path / "tb.py"
    out.write_text(text, encoding="utf-8")
    return subprocess.run(  # noqa: S603
        [sys.executable, str(out)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


# ── axil_regfile ──────────────────────────────────────────────────────────


def test_axil_regfile_scaffold_is_valid_python(tmp_path):
    """Generated scaffold for axil_regfile must be syntactically valid Python."""
    dut = _parse_file(AXIL_REGFILE_PATH)
    text = generate_python_testbench(dut, enhanced=True, style="bench", dut_source_path=str(AXIL_REGFILE_PATH))
    out = tmp_path / "tb.py"
    out.write_text(text, encoding="utf-8")
    assert compileall.compile_file(str(out), quiet=1, force=True), "axil_regfile scaffold did not compile"


def test_axil_regfile_scaffold_detects_axi_lite_slave():
    """axil_regfile must be detected as an axi_lite/slave interface."""
    dut = _parse_file(AXIL_REGFILE_PATH)
    text = generate_python_testbench(dut, enhanced=True, style="bench", dut_source_path=str(AXIL_REGFILE_PATH))
    assert "def axi_lite_s_axi(" in text
    assert "iface.write(0x0, 0xDEADBEEF)" in text
    assert "iface.read(0x0)" in text


def test_axil_regfile_scaffold_runs_without_error(tmp_path):
    """Generated axil_regfile scaffold must execute unmodified and write+read-back successfully."""
    dut = _parse_file(AXIL_REGFILE_PATH)
    text = generate_python_testbench(dut, enhanced=True, style="bench", dut_source_path=str(AXIL_REGFILE_PATH))
    proc = _run_scaffold(tmp_path, text)
    assert proc.returncode == 0, f"axil_regfile scaffold failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "s_axi reg[0] = 0xdeadbeef" in proc.stdout


# ── axi4_ram ──────────────────────────────────────────────────────────────


def test_axi4_ram_scaffold_is_valid_python(tmp_path):
    """Generated scaffold for axi4_ram must be syntactically valid Python."""
    dut = _parse_file(AXI4_RAM_PATH)
    text = generate_python_testbench(dut, enhanced=True, style="bench", dut_source_path=str(AXI4_RAM_PATH))
    out = tmp_path / "tb.py"
    out.write_text(text, encoding="utf-8")
    assert compileall.compile_file(str(out), quiet=1, force=True), "axi4_ram scaffold did not compile"


def test_axi4_ram_scaffold_detects_axi4_slave():
    """axi4_ram must be detected as an axi4/slave interface."""
    dut = _parse_file(AXI4_RAM_PATH)
    text = generate_python_testbench(dut, enhanced=True, style="bench", dut_source_path=str(AXI4_RAM_PATH))
    assert "def axi4_s_axi(" in text
    assert "iface.write(0x0, [0xDEADBEEF])" in text
    assert "iface.read(0x0, length=1)" in text


def test_axi4_ram_scaffold_runs_without_error(tmp_path):
    """Generated axi4_ram scaffold must execute unmodified and write+read-back successfully."""
    dut = _parse_file(AXI4_RAM_PATH)
    text = generate_python_testbench(dut, enhanced=True, style="bench", dut_source_path=str(AXI4_RAM_PATH))
    proc = _run_scaffold(tmp_path, text)
    assert proc.returncode == 0, f"axi4_ram scaffold failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "s_axi read[0] = 0xdeadbeef" in proc.stdout


# ── no-interface DUT ──────────────────────────────────────────────────────


def test_no_iface_scaffold_runs_without_error(tmp_path):
    """No-interface scaffold must execute unmodified and sample raw signals cleanly."""
    src = """\
module raw_dut (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  din,
    output wire [7:0]  dout
);
    assign dout = din;
endmodule
"""
    dut_v = tmp_path / "raw_dut.v"
    dut_v.write_text(src, encoding="utf-8")
    dut = _parse(src)
    text = generate_python_testbench(dut, enhanced=True, style="bench", dut_source_path=str(dut_v))
    # Scaffold should contain raw-signal poke/peek code
    assert "bench.sim.signal(" in text
    proc = _run_scaffold(tmp_path, text)
    assert proc.returncode == 0, f"no-iface scaffold failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
