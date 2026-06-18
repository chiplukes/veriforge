"""Tests for multi-file Verilog project support."""

import json
from pathlib import Path

import pytest

from veriforge.__main__ import main as cli_main
from veriforge.project import (
    build_testbench,
    build_testbench_plan,
    export_dsl_project,
    generate_python_testbench_skeleton,
    parse_directory,
    parse_file,
    parse_files,
)
from veriforge.sim.bench import AmbiguousDomainError, PlannerOverrides, TestbenchPlan


# ── Verilog source text for multi-file tests ──────────────────────────


ADDER_V = """\
module adder(
    input  [7:0] a, b,
    output [7:0] sum
);
    assign sum = a + b;
endmodule
"""

INVERTER_V = """\
module inverter(
    input  [7:0] in,
    output [7:0] out
);
    assign out = ~in;
endmodule
"""

TOP_V = """\
module top(
    input  [7:0] x, y,
    output [7:0] result, inv_result
);
    wire [7:0] add_out;

    adder u_add(.a(x), .b(y), .sum(add_out));
    inverter u_inv(.in(add_out), .out(inv_result));

    assign result = add_out;
endmodule
"""

COUNTER_V = """\
module counter(
    input clk, rst,
    output reg [7:0] count
);
    always @(posedge clk)
        if (rst) count <= 8'd0;
        else count <= count + 8'd1;
endmodule
"""

TWO_DOMAIN_TOP_V = """\
module two_domain_top(
    input  wire        aclk,
    input  wire        aresetn,
    input  wire        m_axis_tready,
    output reg         m_axis_tvalid,
    output reg  [7:0]  m_axis_tdata,
    output reg         m_axis_tlast,

    input  wire        bclk,
    input  wire        brst_n,
    output reg         s_axis_tready,
    input  wire        s_axis_tvalid,
    input  wire [7:0]  s_axis_tdata,
    input  wire        s_axis_tlast
);
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            m_axis_tvalid <= 1'b0;
            m_axis_tdata  <= 8'h00;
            m_axis_tlast  <= 1'b0;
        end else begin
            m_axis_tvalid <= 1'b1;
            m_axis_tdata  <= 8'hAA;
            m_axis_tlast  <= 1'b0;
        end
    end

    always @(posedge bclk or negedge brst_n) begin
        if (!brst_n) s_axis_tready <= 1'b0;
        else         s_axis_tready <= s_axis_tvalid;
    end
endmodule
"""

TIMESCALE_ADDER_V = """\
`timescale 1ns / 1ps
module adder(
    input  [7:0] a, b,
    output [7:0] sum
);
    assign sum = a + b;
endmodule
"""


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def verilog_dir(tmp_path):
    """Create a temp directory with multiple Verilog files."""
    (tmp_path / "adder.v").write_text(ADDER_V, encoding="utf-8")
    (tmp_path / "inverter.v").write_text(INVERTER_V, encoding="utf-8")
    (tmp_path / "top.v").write_text(TOP_V, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def nested_dir(tmp_path):
    """Create a nested directory structure with Verilog files."""
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    sub = rtl / "subblocks"
    sub.mkdir()
    (rtl / "top.v").write_text(TOP_V, encoding="utf-8")
    (sub / "adder.v").write_text(ADDER_V, encoding="utf-8")
    (sub / "inverter.v").write_text(INVERTER_V, encoding="utf-8")
    # Also put a non-Verilog file to ensure it's skipped
    (sub / "readme.txt").write_text("not verilog", encoding="utf-8")
    return rtl


# ── parse_file ────────────────────────────────────────────────────────


class TestParseFile:
    def test_single_file(self, tmp_path):
        f = tmp_path / "adder.v"
        f.write_text(ADDER_V, encoding="utf-8")
        design = parse_file(f)
        assert len(design.modules) == 1
        assert design.modules[0].name == "adder"

    def test_single_file_with_timescale_directive(self, tmp_path):
        f = tmp_path / "adder.v"
        f.write_text(TIMESCALE_ADDER_V, encoding="utf-8")
        design = parse_file(f)
        assert len(design.modules) == 1
        assert design.modules[0].name == "adder"

    def test_source_file_tracked(self, tmp_path):
        f = tmp_path / "adder.v"
        f.write_text(ADDER_V, encoding="utf-8")
        design = parse_file(f)
        assert len(design.source_files) == 1
        assert design.source_files[0] == str(f)


# ── parse_files ───────────────────────────────────────────────────────


class TestParseFiles:
    def test_merge_multiple(self, verilog_dir):
        paths = [verilog_dir / "adder.v", verilog_dir / "inverter.v", verilog_dir / "top.v"]
        design = parse_files(paths)
        names = {m.name for m in design.modules}
        assert names == {"adder", "inverter", "top"}

    def test_source_files_tracked(self, verilog_dir):
        paths = [verilog_dir / "adder.v", verilog_dir / "inverter.v"]
        design = parse_files(paths)
        assert len(design.source_files) == 2

    def test_empty_list(self):
        design = parse_files([])
        assert len(design.modules) == 0

    def test_instance_linking(self, verilog_dir):
        paths = [verilog_dir / "adder.v", verilog_dir / "inverter.v", verilog_dir / "top.v"]
        design = parse_files(paths, analyze=True)
        top = next(m for m in design.modules if m.name == "top")
        # After linking, instances should have resolved_module set
        for inst in top.instances:
            assert inst.resolved_module is not None, f"Instance {inst.instance_name} not linked"

    def test_no_analyze(self, verilog_dir):
        paths = [verilog_dir / "adder.v", verilog_dir / "top.v"]
        design = parse_files(paths, analyze=False)
        top = next(m for m in design.modules if m.name == "top")
        # Without analysis, instances should NOT have resolved_module
        for inst in top.instances:
            assert inst.resolved_module is None

    def test_top_module_detection(self, verilog_dir):
        paths = [verilog_dir / "adder.v", verilog_dir / "inverter.v", verilog_dir / "top.v"]
        design = parse_files(paths)
        tops = design.get_top_modules()
        assert len(tops) == 1
        assert tops[0].name == "top"

    def test_nonexistent_file_skipped(self, tmp_path):
        """Nonexistent files are warned and skipped."""
        design = parse_files([tmp_path / "nonexistent.v"])
        assert len(design.modules) == 0

    def test_duplicate_module_dedup(self, tmp_path):
        """When the same module appears in two files, first definition wins."""
        (tmp_path / "a.v").write_text(ADDER_V, encoding="utf-8")
        (tmp_path / "b.v").write_text(ADDER_V, encoding="utf-8")
        design = parse_files([tmp_path / "a.v", tmp_path / "b.v"])
        adders = [m for m in design.modules if m.name == "adder"]
        assert len(adders) == 1

    def test_cache_key_isolated_by_path_for_identical_files(self, tmp_path):
        """Identical same-name files in different dirs must not reuse stale loc.file paths."""
        cache_dir = tmp_path / "cache"
        dir_a = tmp_path / "proj_a"
        dir_b = tmp_path / "proj_b"
        dir_a.mkdir()
        dir_b.mkdir()
        file_a = dir_a / "async_vector.v"
        file_b = dir_b / "async_vector.v"
        source = """\
module async_vector(
    input clk,
    input async_vector_in,
    output reg clkchngreg_falsep_vector_in
);
always @(posedge clk)
    clkchngreg_falsep_vector_in <= async_vector_in;
endmodule
"""
        file_a.write_text(source, encoding="utf-8")
        file_b.write_text(source, encoding="utf-8")

        design_a = parse_files([file_a], cache_dir=cache_dir)
        design_b = parse_files([file_b], cache_dir=cache_dir)

        assert design_a.modules[0].loc is not None
        assert design_b.modules[0].loc is not None
        assert design_a.modules[0].loc.file == str(file_a)
        assert design_b.modules[0].loc.file == str(file_b)
        assert design_b.source_files == [str(file_b)]


# ── parse_directory ───────────────────────────────────────────────────


class TestParseDirectory:
    def test_flat_directory(self, verilog_dir):
        design = parse_directory(verilog_dir)
        names = {m.name for m in design.modules}
        assert names == {"adder", "inverter", "top"}

    def test_recursive(self, nested_dir):
        design = parse_directory(nested_dir, recursive=True)
        names = {m.name for m in design.modules}
        assert names == {"adder", "inverter", "top"}

    def test_non_recursive(self, nested_dir):
        design = parse_directory(nested_dir, recursive=False)
        names = {m.name for m in design.modules}
        # Only top.v is directly in nested_dir
        assert names == {"top"}

    def test_extensions_filter(self, tmp_path):
        (tmp_path / "adder.v").write_text(ADDER_V, encoding="utf-8")
        (tmp_path / "inverter.sv").write_text(INVERTER_V, encoding="utf-8")
        # Only .v extension
        design = parse_directory(tmp_path, extensions=(".v",))
        names = {m.name for m in design.modules}
        assert names == {"adder"}

    def test_nonexistent_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_directory(tmp_path / "nope")

    def test_empty_dir(self, tmp_path):
        design = parse_directory(tmp_path)
        assert len(design.modules) == 0

    def test_exclude_pattern(self, tmp_path):
        (tmp_path / "adder.v").write_text(ADDER_V, encoding="utf-8")
        (tmp_path / "top_tb.v").write_text(TOP_V, encoding="utf-8")
        design = parse_directory(tmp_path, exclude=["*_tb.v"])
        names = {m.name for m in design.modules}
        assert "top" not in names
        assert "adder" in names


# ── export_dsl_project ────────────────────────────────────────────────


class TestExportDslProject:
    def test_one_file_per_module(self, verilog_dir, tmp_path):
        design = parse_directory(verilog_dir)
        out_dir = tmp_path / "dsl_out"
        written = export_dsl_project(design, out_dir)
        assert len(written) == 3
        names = {p.stem for p in written}
        assert names == {"adder", "inverter", "top"}
        # All files should be non-empty .py files
        for p in written:
            assert p.suffix == ".py"
            assert p.stat().st_size > 0

    def test_single_file_mode(self, verilog_dir, tmp_path):
        design = parse_directory(verilog_dir)
        out_dir = tmp_path / "dsl_single"
        written = export_dsl_project(design, out_dir, one_file_per_module=False)
        assert len(written) == 1
        assert written[0].name == "design.py"
        content = written[0].read_text(encoding="utf-8")
        assert "adder" in content
        assert "inverter" in content

    def test_output_dir_created(self, verilog_dir, tmp_path):
        out_dir = tmp_path / "new" / "nested" / "dir"
        assert not out_dir.exists()
        design = parse_directory(verilog_dir)
        export_dsl_project(design, out_dir)
        assert out_dir.is_dir()


class TestGeneratePythonTestbenchSkeleton:
    def test_returns_text_for_selected_module(self, verilog_dir):
        design = parse_directory(verilog_dir)

        text = generate_python_testbench_skeleton(design, module_name="top")

        assert 'def run_smoke_test(module, *, design=None, engine: str = "reference") -> None:' in text
        assert "No AXI-Stream or AXI-Lite interfaces were detected." in text

    def test_uses_single_top_by_default(self, verilog_dir):
        design = parse_directory(verilog_dir)

        text = generate_python_testbench_skeleton(design)

        assert "Auto-generated Python testbench skeleton." in text

    def test_requires_module_name_when_no_unique_top(self, tmp_path):
        (tmp_path / "adder.v").write_text(ADDER_V, encoding="utf-8")
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")
        design = parse_directory(tmp_path)

        with pytest.raises(ValueError, match="module_name is required"):
            generate_python_testbench_skeleton(design)

    def test_writes_output_file(self, verilog_dir, tmp_path):
        design = parse_directory(verilog_dir)
        output = tmp_path / "generated" / "tb_top.py"

        written = generate_python_testbench_skeleton(design, module_name="top", output_path=output)

        assert written == output
        assert output.read_text(encoding="utf-8").startswith('"""Auto-generated Python testbench skeleton.')


class TestBuildTestbenchPlan:
    def test_returns_plan_for_selected_top(self, tmp_path):
        (tmp_path / "two_domain.v").write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")
        design = parse_directory(tmp_path)

        plan = build_testbench_plan(design, top="two_domain_top")

        assert isinstance(plan, TestbenchPlan)
        assert plan.top == "two_domain_top"
        assert {d.name for d in plan.domains} == {"aclk", "bclk"}
        assert {b.prefix for b in plan.interfaces} == {"m_axis", "s_axis"}

    def test_uses_single_top_by_default(self, verilog_dir):
        design = parse_directory(verilog_dir)

        plan = build_testbench_plan(design)

        assert plan.top == "top"

    def test_requires_top_when_no_unique_top(self, tmp_path):
        (tmp_path / "adder.v").write_text(ADDER_V, encoding="utf-8")
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")
        design = parse_directory(tmp_path)

        with pytest.raises(ValueError, match="top is required"):
            build_testbench_plan(design)

    def test_unknown_module_raises(self, verilog_dir):
        design = parse_directory(verilog_dir)
        with pytest.raises(ValueError, match="Module not found"):
            build_testbench_plan(design, top="nonexistent")

    def test_overrides_dict_accepted(self, tmp_path):
        (tmp_path / "two_domain.v").write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")
        design = parse_directory(tmp_path)

        plan = build_testbench_plan(
            design,
            top="two_domain_top",
            overrides={"clock_periods": {"aclk": 8, "bclk": 12}},
        )

        periods = {d.clock.name: d.clock.period_hint for d in plan.domains}
        assert periods == {"aclk": 8, "bclk": 12}

    def test_overrides_instance_accepted(self, tmp_path):
        (tmp_path / "two_domain.v").write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")
        design = parse_directory(tmp_path)

        ov = PlannerOverrides(clock_periods={"aclk": 5})
        plan = build_testbench_plan(design, top="two_domain_top", overrides=ov)

        periods = {d.clock.name: d.clock.period_hint for d in plan.domains}
        assert periods["aclk"] == 5

    def test_strict_default_raises_on_ambiguity(self, tmp_path):
        ambiguous = """
module amb_top(
    input  wire        clk_a,
    input  wire        rst_a_n,
    input  wire        clk_b,
    input  wire        rst_b_n,
    input  wire        bus_tready,
    output wire        bus_tvalid,
    output wire [7:0]  bus_tdata,
    output wire        bus_tlast
);
    reg ta, tb;
    assign bus_tvalid = 1'b1;
    assign bus_tdata  = 8'h22;
    assign bus_tlast  = 1'b0;

    always @(posedge clk_a or negedge rst_a_n) begin
        if (!rst_a_n) ta <= 1'b0; else ta <= ~ta;
    end
    always @(posedge clk_b or negedge rst_b_n) begin
        if (!rst_b_n) tb <= 1'b0; else tb <= ~tb;
    end
endmodule
"""
        (tmp_path / "amb.v").write_text(ambiguous, encoding="utf-8")
        design = parse_directory(tmp_path)

        with pytest.raises(AmbiguousDomainError):
            build_testbench_plan(design, top="amb_top")

    def test_strict_false_picks_first_candidate(self, tmp_path):
        ambiguous = """
module amb_top2(
    input  wire        clk_a,
    input  wire        rst_a_n,
    input  wire        clk_b,
    input  wire        rst_b_n,
    input  wire        bus_tready,
    output wire        bus_tvalid,
    output wire [7:0]  bus_tdata,
    output wire        bus_tlast
);
    reg ta, tb;
    assign bus_tvalid = 1'b1;
    assign bus_tdata  = 8'h22;
    assign bus_tlast  = 1'b0;

    always @(posedge clk_a or negedge rst_a_n) begin
        if (!rst_a_n) ta <= 1'b0; else ta <= ~ta;
    end
    always @(posedge clk_b or negedge rst_b_n) begin
        if (!rst_b_n) tb <= 1'b0; else tb <= ~tb;
    end
endmodule
"""
        (tmp_path / "amb.v").write_text(ambiguous, encoding="utf-8")
        design = parse_directory(tmp_path)

        plan = build_testbench_plan(design, top="amb_top2", strict=False)

        binding = plan.interface("bus")
        assert binding.domain_name in {"clk_a", "clk_b"}


class TestCli:
    def test_top_level_help_prefers_subcommands(self, capsys):
        with pytest.raises(SystemExit, match="0"):
            cli_main(["--help"])

        captured = capsys.readouterr()
        assert "generate-python-testbench" in captured.out
        assert "reconstruct" in captured.out
        assert "Legacy flag mode remains supported for compatibility" in captured.out
        assert "--generate-python-testbench" not in captured.out

    def test_generate_python_testbench_help_omits_parser_options(self, capsys):
        with pytest.raises(SystemExit, match="0"):
            cli_main(["generate-python-testbench", "--help"])

        captured = capsys.readouterr()
        assert "--file" in captured.out
        assert "--log" in captured.out
        assert "--module" in captured.out
        assert "--output" in captured.out
        assert "--parser" not in captured.out
        assert "--debug" not in captured.out

    def test_tree_help_keeps_parser_options(self, capsys):
        with pytest.raises(SystemExit, match="0"):
            cli_main(["tree", "--help"])

        captured = capsys.readouterr()
        assert "--parser" in captured.out
        assert "--debug" in captured.out

    def test_parse_file_subcommand_prints_summary(self, tmp_path, capsys):
        source = tmp_path / "counter.v"
        source.write_text(COUNTER_V, encoding="utf-8")

        exit_code = cli_main(["parse-file", "--file", str(source)])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert f"Parsed project: {source}" in captured.out
        assert "Files: 1" in captured.out
        assert "Modules: 1" in captured.out
        assert "Top modules: counter" in captured.out

    def test_parse_file_subcommand_prints_json_summary(self, tmp_path, capsys):
        source = tmp_path / "counter.v"
        source.write_text(COUNTER_V, encoding="utf-8")

        exit_code = cli_main(["parse-file", "--file", str(source), "--json"])

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        summary = payload["result"]
        assert exit_code == 0
        assert payload["command"] == "parse-file"
        assert payload["success"] is True
        assert summary["root"] == str(source)
        assert summary["files"] == 1
        assert summary["modules"] == 1
        assert summary["top_modules"] == ["counter"]

    def test_parse_directory_subcommand_prints_summary(self, verilog_dir, capsys):
        exit_code = cli_main(["parse-directory", str(verilog_dir)])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Parsed project:" in captured.out
        assert "Files: 3" in captured.out
        assert "Modules: 3" in captured.out
        assert "Top modules: top" in captured.out

    def test_parse_directory_subcommand_prints_json_summary(self, verilog_dir, capsys):
        exit_code = cli_main(["parse-directory", str(verilog_dir), "--json"])

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        summary = payload["result"]
        assert exit_code == 0
        assert payload["command"] == "parse-directory"
        assert payload["success"] is True
        assert summary["root"] == str(verilog_dir)
        assert summary["files"] == 3
        assert summary["modules"] == 3
        assert summary["top_modules"] == ["top"]

    def test_export_dsl_subcommand_writes_files(self, verilog_dir, tmp_path, capsys):
        output_dir = tmp_path / "dsl_out"

        exit_code = cli_main(["export-dsl", str(verilog_dir), str(output_dir)])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Exported 3 file(s)" in captured.out
        assert (output_dir / "adder.py").is_file()
        assert (output_dir / "inverter.py").is_file()
        assert (output_dir / "top.py").is_file()

    def test_export_dsl_subcommand_single_file_mode(self, verilog_dir, tmp_path):
        output_dir = tmp_path / "dsl_single"

        exit_code = cli_main(["export-dsl", str(verilog_dir), str(output_dir), "--single-file"])

        assert exit_code == 0
        assert (output_dir / "design.py").is_file()

    def test_export_dsl_subcommand_prints_json_result(self, verilog_dir, tmp_path, capsys):
        output_dir = tmp_path / "dsl_json"

        exit_code = cli_main(["export-dsl", str(verilog_dir), str(output_dir), "--json"])

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        result = payload["result"]
        assert exit_code == 0
        assert payload["command"] == "export-dsl"
        assert payload["success"] is True
        assert result["output_dir"] == str(output_dir)
        assert len(result["written"]) == 3
        assert str(output_dir / "adder.py") in result["written"]

    def test_parse_file_subcommand_prints_json_error(self, tmp_path, capsys):
        missing = tmp_path / "missing.v"

        exit_code = cli_main(["parse-file", "--file", str(missing), "--json"])

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 1
        assert payload["command"] == "parse-file"
        assert payload["success"] is False
        assert payload["error"]["type"] == "FileNotFoundError"

    def test_parse_file_subcommand_prints_json_parse_error(self, capsys):
        exit_code = cli_main(["parse-file", "--json", "--bogus-option"])

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 2
        assert payload["command"] == "parse-file"
        assert payload["success"] is False
        assert payload["error"]["type"] == "ArgumentError"
        assert "unrecognized arguments" in payload["error"]["message"]

    def test_generate_python_testbench_subcommand_prints_json_error(self, tmp_path, capsys):
        source = tmp_path / "multi.v"
        source.write_text(f"{ADDER_V}\n{COUNTER_V}", encoding="utf-8")

        exit_code = cli_main(["generate-python-testbench", "--file", str(source), "--json"])

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 1
        assert payload["command"] == "generate-python-testbench"
        assert payload["success"] is False
        assert payload["error"]["type"] == "ValueError"
        assert "module_name is required" in payload["error"]["message"]

    def test_generate_python_testbench_subcommand_prints_json_parse_error(self, capsys):
        exit_code = cli_main(["generate-python-testbench", "--json", "--bogus-option"])

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 2
        assert payload["command"] == "generate-python-testbench"
        assert payload["success"] is False
        assert payload["error"]["type"] == "ArgumentError"
        assert "unrecognized arguments" in payload["error"]["message"]

    def test_generate_python_testbench_to_stdout(self, tmp_path, capsys):
        source = tmp_path / "top.v"
        source.write_text(TOP_V, encoding="utf-8")

        exit_code = cli_main(["--file", str(source), "--generate-python-testbench"])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Auto-generated Python testbench skeleton." in captured.out
        assert 'def run_smoke_test(module, *, design=None, engine: str = "reference") -> None:' in captured.out

    def test_generate_python_testbench_to_file_with_module_selection(self, tmp_path):
        source = tmp_path / "multi.v"
        source.write_text(f"{ADDER_V}\n{COUNTER_V}", encoding="utf-8")
        output = tmp_path / "generated" / "tb_counter.py"

        exit_code = cli_main(
            [
                "--file",
                str(source),
                "--generate-python-testbench",
                "--module",
                "counter",
                "--output",
                str(output),
            ]
        )

        assert exit_code == 0
        assert output == Path(output)
        assert output.read_text(encoding="utf-8").startswith('"""Auto-generated Python testbench skeleton.')

    def test_generate_python_testbench_subcommand_prints_json_result(self, tmp_path, capsys):
        source = tmp_path / "top.v"
        source.write_text(TOP_V, encoding="utf-8")

        exit_code = cli_main(["generate-python-testbench", "--file", str(source), "--json"])

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        result = payload["result"]
        assert exit_code == 0
        assert payload["command"] == "generate-python-testbench"
        assert payload["success"] is True
        assert result["module_name"] == "top"
        assert result["output_path"] is None
        assert "Auto-generated Python testbench skeleton." in result["text"]

    def test_generate_python_testbench_subcommand_writes_file_json_result(self, tmp_path, capsys):
        source = tmp_path / "multi.v"
        source.write_text(f"{ADDER_V}\n{COUNTER_V}", encoding="utf-8")
        output = tmp_path / "generated" / "tb_counter.py"

        exit_code = cli_main(
            [
                "generate-python-testbench",
                "--file",
                str(source),
                "--module",
                "counter",
                "--output",
                str(output),
                "--json",
            ]
        )

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        result = payload["result"]
        assert exit_code == 0
        assert payload["command"] == "generate-python-testbench"
        assert payload["success"] is True
        assert result["module_name"] == "counter"
        assert result["output_path"] == str(output)
        assert result["written"] is True
        assert output.read_text(encoding="utf-8").startswith('"""Auto-generated Python testbench skeleton.')

    def test_generate_python_testbench_subcommand_to_stdout(self, tmp_path, capsys):
        source = tmp_path / "top.v"
        source.write_text(TOP_V, encoding="utf-8")

        exit_code = cli_main(["generate-python-testbench", "--file", str(source)])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Auto-generated Python testbench skeleton." in captured.out

    def test_generate_python_testbench_explain_plan_prints_summary(self, tmp_path, capsys):
        source = tmp_path / "two.v"
        source.write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(["generate-python-testbench", "--file", str(source), "--explain-plan"])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "TestbenchPlan(top='two_domain_top')" in captured.out
        assert "domains:" in captured.out
        assert "aclk" in captured.out
        # No skeleton was generated (explain-only mode).
        assert "Auto-generated Python testbench skeleton" not in captured.out

    def test_generate_python_testbench_explain_plan_json(self, tmp_path, capsys):
        source = tmp_path / "two.v"
        source.write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(["generate-python-testbench", "--file", str(source), "--explain-plan", "--json"])

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 0
        assert payload["command"] == "generate-python-testbench"
        assert payload["success"] is True
        assert payload["result"]["plan"]["top"] == "two_domain_top"
        assert len(payload["result"]["plan"]["domains"]) == 2

    def test_generate_python_testbench_reset_override_flips_polarity(self, tmp_path, capsys):
        source = tmp_path / "two.v"
        source.write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(
            [
                "generate-python-testbench",
                "--file",
                str(source),
                "--explain-plan",
                "--json",
                "--reset-override",
                "brst_n=active_high",
            ]
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 0
        domains = payload["result"]["plan"]["domains"]
        rst = next(d["reset"] for d in domains if d["reset"] is not None and d["reset"]["name"] == "brst_n")
        assert rst["active_low"] is False

    def test_generate_python_testbench_reset_override_invalid_value(self, tmp_path, capsys):
        source = tmp_path / "two.v"
        source.write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(
            [
                "generate-python-testbench",
                "--file",
                str(source),
                "--reset-override",
                "brst_n=low",
                "--json",
            ]
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 1
        assert payload["success"] is False
        assert "active_high" in payload["error"]["message"]

    def test_generate_python_testbench_clock_override_sets_period(self, tmp_path, capsys):
        source = tmp_path / "two.v"
        source.write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(
            [
                "generate-python-testbench",
                "--file",
                str(source),
                "--explain-plan",
                "--json",
                "--clock-override",
                "aclk=8",
                "--clock-override",
                "bclk=12",
            ]
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 0
        domains = payload["result"]["plan"]["domains"]
        a = next(d for d in domains if d["clock"]["name"] == "aclk")
        b = next(d for d in domains if d["clock"]["name"] == "bclk")
        assert a["clock"]["period_hint"] == 8
        assert b["clock"]["period_hint"] == 12

    def test_generate_python_testbench_clock_override_invalid_value(self, tmp_path, capsys):
        source = tmp_path / "two.v"
        source.write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(
            [
                "generate-python-testbench",
                "--file",
                str(source),
                "--clock-override",
                "aclk=notanumber",
                "--json",
            ]
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 1
        assert payload["success"] is False
        assert "positive integer" in payload["error"]["message"]
        assert "aclk" in payload["error"]["message"]

    def test_generate_python_testbench_clock_override_zero_rejected(self, tmp_path, capsys):
        source = tmp_path / "two.v"
        source.write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(
            [
                "generate-python-testbench",
                "--file",
                str(source),
                "--clock-override",
                "aclk=0",
                "--json",
            ]
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 1
        assert payload["success"] is False
        assert "positive integer" in payload["error"]["message"]

    def test_generate_python_testbench_iface_domain_override(self, tmp_path, capsys):
        source = tmp_path / "two.v"
        source.write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(
            [
                "generate-python-testbench",
                "--file",
                str(source),
                "--explain-plan",
                "--json",
                "--iface-domain",
                "m_axis=bclk",
            ]
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 0
        ifaces = payload["result"]["plan"]["interfaces"]
        m = next(i for i in ifaces if i["prefix"] == "m_axis")
        assert m["domain_name"] == "bclk"

    def test_generate_python_testbench_domain_alias(self, tmp_path, capsys):
        source = tmp_path / "two.v"
        source.write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(
            [
                "generate-python-testbench",
                "--file",
                str(source),
                "--explain-plan",
                "--json",
                "--domain-alias",
                "aclk=axis_domain",
            ]
        )
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 0
        domains = payload["result"]["plan"]["domains"]
        names = [d["name"] for d in domains]
        assert "axis_domain" in names
        assert "aclk" not in names

    def test_style_bench_without_enhanced_generates_bench_framework(self, tmp_path, capsys):
        """--style bench without --enhanced should still produce bench-framework output."""
        source = tmp_path / "two.v"
        source.write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(["generate-python-testbench", "--file", str(source), "--style", "bench"])

        captured = capsys.readouterr()
        assert exit_code == 0
        # Bench-style imports — not legacy Simulator + step_drive
        assert "from veriforge.sim.bench import" in captured.out
        assert "Testbench" in captured.out
        # Legacy raw-simulator pattern must NOT appear
        assert "step_drive" not in captured.out

    def test_style_bench_engine_compiled_uses_native_scaffold(self, tmp_path, capsys):
        """--style bench --engine compiled with natively-lowerable AXIS DUT emits compile_native."""
        source = tmp_path / "two.v"
        source.write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(
            [
                "generate-python-testbench",
                "--file",
                str(source),
                "--style",
                "bench",
                "--engine",
                "compiled",
            ]
        )

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "compile_native" in captured.out
        assert "build_native_bench" in captured.out
        # Legacy Testbench framework must NOT appear (fully native path)
        assert "with bench.run(" not in captured.out

    def test_style_bench_clock_override_flows_into_scaffold(self, tmp_path, capsys):
        """--clock-override should change the period emitted inside a bench scaffold."""
        source = tmp_path / "two.v"
        source.write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(
            [
                "generate-python-testbench",
                "--file",
                str(source),
                "--style",
                "bench",
                "--clock-override",
                "aclk=6",
            ]
        )

        captured = capsys.readouterr()
        assert exit_code == 0
        # The generated Clock(sim, "aclk", period=6) line must appear
        assert "period=6" in captured.out

        source = tmp_path / "adder.v"
        source.write_text(ADDER_V, encoding="utf-8")

        exit_code = cli_main(["reconstruct", "--file", str(source)])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "module adder" in captured.out
        assert "None" not in captured.out

    def test_tree_subcommand_prints_tree(self, tmp_path, capsys):
        source = tmp_path / "adder.v"
        source.write_text(ADDER_V, encoding="utf-8")

        exit_code = cli_main(["tree", "--file", str(source)])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Tree(Token('RULE', 'verilog')" in captured.out

    def test_reconstruct_prints_only_reconstructed_text(self, tmp_path, capsys):
        source = tmp_path / "adder.v"
        source.write_text(ADDER_V, encoding="utf-8")

        exit_code = cli_main(["--file", str(source), "--reconstruct"])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "module adder" in captured.out
        assert "None" not in captured.out


# ── End-to-end: parse → simulate ─────────────────────────────────────


AXIS_PASSTHROUGH_V = """\
module axis_passthrough(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        m_axis_tvalid,
    output wire        m_axis_tready,
    input  wire [7:0]  m_axis_tdata,
    input  wire        m_axis_tlast,
    output wire        s_axis_tvalid,
    input  wire        s_axis_tready,
    output wire [7:0]  s_axis_tdata,
    output wire        s_axis_tlast
);
    assign s_axis_tvalid = m_axis_tvalid;
    assign s_axis_tdata  = m_axis_tdata;
    assign s_axis_tlast  = m_axis_tlast;
    assign m_axis_tready = s_axis_tready;
endmodule
"""


class TestBuildTestbench:
    """Tests for build_testbench() convenience entry-point."""

    def test_from_directory(self, tmp_path):
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")
        from veriforge.sim.bench.runtime import Testbench

        bench = build_testbench(tmp_path)

        assert isinstance(bench, Testbench)
        assert bench.plan.top == "counter"

    def test_from_directory_explicit_top(self, tmp_path):
        (tmp_path / "axis.v").write_text(AXIS_PASSTHROUGH_V, encoding="utf-8")
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")
        from veriforge.sim.bench.runtime import Testbench

        bench = build_testbench(tmp_path, top="counter")

        assert isinstance(bench, Testbench)
        assert bench.plan.top == "counter"

    def test_from_single_file(self, tmp_path):
        f = tmp_path / "counter.v"
        f.write_text(COUNTER_V, encoding="utf-8")
        from veriforge.sim.bench.runtime import Testbench

        bench = build_testbench(f)

        assert isinstance(bench, Testbench)
        assert bench.plan.top == "counter"

    def test_from_file_list(self, tmp_path):
        f = tmp_path / "counter.v"
        f.write_text(COUNTER_V, encoding="utf-8")
        from veriforge.sim.bench.runtime import Testbench

        bench = build_testbench([f])

        assert isinstance(bench, Testbench)
        assert bench.plan.top == "counter"

    def test_from_design_object(self, tmp_path):
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")
        from veriforge.sim.bench.runtime import Testbench

        design = parse_directory(tmp_path)
        bench = build_testbench(design)

        assert isinstance(bench, Testbench)
        assert bench.plan.top == "counter"

    def test_auto_detect_single_top(self, tmp_path):
        """When there is exactly one top module, top= can be omitted."""
        (tmp_path / "axis.v").write_text(AXIS_PASSTHROUGH_V, encoding="utf-8")
        from veriforge.sim.bench.runtime import Testbench

        bench = build_testbench(tmp_path)

        assert isinstance(bench, Testbench)
        assert bench.plan.top == "axis_passthrough"
        assert {b.prefix for b in bench.plan.interfaces} == {"m_axis", "s_axis"}

    def test_ambiguous_top_raises(self, tmp_path):
        """Multiple top modules with no top= must raise ValueError."""
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")
        (tmp_path / "axis.v").write_text(AXIS_PASSTHROUGH_V, encoding="utf-8")

        with pytest.raises(ValueError, match="top is required"):
            build_testbench(tmp_path)

    def test_module_not_found_raises(self, tmp_path):
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")

        with pytest.raises(ValueError, match="Module not found"):
            build_testbench(tmp_path, top="nonexistent")

    def test_design_carries_into_bench(self, tmp_path):
        """The Testbench should have a populated plan referencing the module."""
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")
        from veriforge.sim.bench import TestbenchPlan

        bench = build_testbench(tmp_path)

        assert isinstance(bench.plan, TestbenchPlan)
        assert bench.plan.top == "counter"

    def test_overrides_accepted(self, tmp_path):
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")

        bench = build_testbench(tmp_path, overrides=PlannerOverrides(clock_periods={"clk": 8}))

        period = bench.plan.domains[0].clock.period_hint
        assert period == 8

    def test_string_path_accepted(self, tmp_path):
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")
        from veriforge.sim.bench.runtime import Testbench

        bench = build_testbench(str(tmp_path))

        assert isinstance(bench, Testbench)

    def test_error_message_includes_available_modules(self, tmp_path):
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")

        with pytest.raises(ValueError, match="counter"):
            build_testbench(tmp_path, top="nonexistent")


class TestCliDirectoryInput:
    """Tests for generate-python-testbench --directory flag."""

    def test_generates_skeleton_from_directory(self, tmp_path, capsys):
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")

        exit_code = cli_main(["generate-python-testbench", "--directory", str(tmp_path), "--module", "counter"])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Auto-generated Python testbench skeleton." in captured.out

    def test_generates_skeleton_auto_detect_top(self, tmp_path, capsys):
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")

        exit_code = cli_main(["generate-python-testbench", "--directory", str(tmp_path)])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Auto-generated Python testbench skeleton." in captured.out

    def test_directory_json_output(self, tmp_path, capsys):
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")

        exit_code = cli_main(["generate-python-testbench", "--directory", str(tmp_path), "--json"])

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 0
        assert payload["success"] is True
        assert payload["result"]["module_name"] == "counter"

    def test_directory_explain_plan(self, tmp_path, capsys):
        (tmp_path / "two.v").write_text(TWO_DOMAIN_TOP_V, encoding="utf-8")

        exit_code = cli_main(
            [
                "generate-python-testbench",
                "--directory",
                str(tmp_path),
                "--explain-plan",
                "--json",
            ]
        )

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 0
        assert payload["result"]["plan"]["top"] == "two_domain_top"

    def test_directory_with_extension_filter(self, tmp_path, capsys):
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")
        (tmp_path / "ignored.txt").write_text("not verilog", encoding="utf-8")

        exit_code = cli_main(
            [
                "generate-python-testbench",
                "--directory",
                str(tmp_path),
                "--extension",
                ".v",
                "--json",
            ]
        )

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert exit_code == 0
        assert payload["success"] is True

    def test_directory_flag_short_form(self, tmp_path, capsys):
        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")

        exit_code = cli_main(["generate-python-testbench", "-d", str(tmp_path)])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "Auto-generated Python testbench skeleton." in captured.out


class TestEndToEnd:
    def test_parse_and_simulate(self, tmp_path):
        """Parse a counter, clock it, verify count increments."""
        from veriforge.sim.testbench import Clock, Simulator
        from veriforge.sim.value import Value

        (tmp_path / "counter.v").write_text(COUNTER_V, encoding="utf-8")
        design = parse_file(tmp_path / "counter.v")
        assert len(design.modules) == 1

        sim = Simulator(design.modules[0], engine="reference", design=design)
        clk = Clock(sim.signal("clk"), period=10)
        sim.fork(clk)

        # Hold reset for 15 time units
        sim.drive("rst", Value(1, width=1))
        sim.run(max_time=15)
        assert sim.read("count") == 0

        # Release reset and run a few clock cycles
        sim.drive("rst", Value(0, width=1))
        sim.run(max_time=55)
        assert sim.read("count").val > 0

    def test_multifile_hierarchy_simulate(self, verilog_dir):
        """Parse a multi-file project, simulate combinational logic."""
        from veriforge.sim.testbench import Simulator
        from veriforge.sim.value import Value

        design = parse_directory(verilog_dir)
        tops = design.get_top_modules()
        assert len(tops) == 1

        sim = Simulator(tops[0], engine="reference", design=design)
        sim.drive("x", Value(10, width=8))
        sim.drive("y", Value(20, width=8))
        sim.run(max_time=0)

        # result = x + y = 30
        assert sim.read("result") == 30
        # inv_result = ~(x + y) = ~30 = 225 (for 8-bit)
        assert sim.read("inv_result") == Value(0xFF ^ 30, width=8)
