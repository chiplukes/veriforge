"""Tests for the configurable Verilog formatter.

Covers the three begin/end styles (knr, allman, gnu), port alignment,
column-limit wrapping, and end-else placement.
"""

from __future__ import annotations

import textwrap

import pytest

from veriforge.codegen.format_style import FormatStyle
from veriforge.codegen.verilog_formatter import VerilogFormatter, format_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(src: str):
    """Parse Verilog source and return the first Module."""
    from veriforge.transforms.tree_to_model import tree_to_design
    from veriforge.verilog_parser import verilog_parser

    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=textwrap.dedent(src))
    design = tree_to_design(tree)
    return design.modules[0]


# ---------------------------------------------------------------------------
# FormatStyle presets
# ---------------------------------------------------------------------------


class TestFormatStylePresets:
    def test_knr_defaults(self):
        s = FormatStyle.knr()
        assert s.begin_end_style == "knr"
        assert s.end_else_same_line is True

    def test_allman_defaults(self):
        s = FormatStyle.allman()
        assert s.begin_end_style == "allman"
        assert s.end_else_same_line is False

    def test_gnu_defaults(self):
        s = FormatStyle.gnu()
        assert s.begin_end_style == "gnu"
        assert s.end_else_same_line is False

    def test_preset_override(self):
        s = FormatStyle.knr(indent_width=2, column_limit=80)
        assert s.indent_width == 2
        assert s.column_limit == 80
        assert s.begin_end_style == "knr"


# ---------------------------------------------------------------------------
# KNR style
# ---------------------------------------------------------------------------


class TestKNRStyle:
    """K&R: ``begin`` on same line as control keyword."""

    @pytest.fixture
    def fmt(self):
        return VerilogFormatter(FormatStyle.knr())

    def test_always_begin(self, fmt):
        mod = _parse("""
            module top(input clk, input d, output reg q);
                always @(posedge clk) begin
                    q <= d;
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        assert "always @(posedge clk) begin" in result
        assert "        q <= d;" in result

    def test_if_begin(self, fmt):
        mod = _parse("""
            module top(input clk, input rst, input d, output reg q);
                always @(posedge clk) begin
                    if (rst) begin
                        q <= 0;
                    end
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        assert "if (rst) begin" in result

    def test_end_else_same_line(self, fmt):
        mod = _parse("""
            module top(input clk, input rst, input d, output reg q);
                always @(posedge clk) begin
                    if (rst) begin
                        q <= 0;
                    end else begin
                        q <= d;
                    end
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        assert "end else begin" in result

    def test_end_else_separate_lines(self):
        fmt = VerilogFormatter(FormatStyle.knr(end_else_same_line=False))
        mod = _parse("""
            module top(input clk, input rst, input d, output reg q);
                always @(posedge clk) begin
                    if (rst) begin
                        q <= 0;
                    end else begin
                        q <= d;
                    end
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        # end and else on separate lines
        lines = result.splitlines()
        end_lines = [i for i, ln in enumerate(lines) if ln.strip() == "end"]
        else_lines = [i for i, ln in enumerate(lines) if ln.strip().startswith("else")]
        # At least one end followed by else on next line
        assert any(e + 1 in else_lines for e in end_lines)

    def test_else_if_chain(self, fmt):
        mod = _parse("""
            module top(input [1:0] sel, output reg [1:0] y);
                always @(*) begin
                    if (sel == 0)
                        y = 0;
                    else if (sel == 1)
                        y = 1;
                    else
                        y = 2;
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        assert "else if" in result

    def test_for_loop(self, fmt):
        mod = _parse("""
            module top;
                integer i;
                initial begin
                    for (i = 0; i < 10; i = i + 1) begin
                        $display(i);
                    end
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        assert "for (i = 0; i < 10; i = i + 1) begin" in result

    def test_case_begin(self, fmt):
        mod = _parse("""
            module top(input [1:0] sel, input a, input b, output reg y);
                always @(*) begin
                    case (sel)
                        2'b00: begin
                            y = a;
                        end
                        default: y = b;
                    endcase
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        # Case item with begin on same line
        lines = result.splitlines()
        case_begin = [ln for ln in lines if "00:" in ln and "begin" in ln]
        assert len(case_begin) >= 1


# ---------------------------------------------------------------------------
# Allman style
# ---------------------------------------------------------------------------


class TestAllmanStyle:
    """Allman: ``begin`` on next line, indented to match contents and ``end``."""

    @pytest.fixture
    def fmt(self):
        return VerilogFormatter(FormatStyle.allman())

    def test_always_begin_next_line(self, fmt):
        mod = _parse("""
            module top(input clk, input d, output reg q);
                always @(posedge clk) begin
                    q <= d;
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()
        # always line should NOT contain begin
        always_lines = [ln for ln in lines if "always" in ln]
        assert len(always_lines) == 1
        assert "begin" not in always_lines[0]

        # begin should be on own line, indented further
        begin_lines = [ln for ln in lines if ln.strip() == "begin"]
        assert len(begin_lines) >= 1
        begin_line = begin_lines[0]
        always_line = always_lines[0]
        assert len(begin_line) - len(begin_line.lstrip()) > len(always_line) - len(always_line.lstrip())

    def test_begin_end_code_same_indent(self, fmt):
        """begin, statements, and end share the same indentation."""
        mod = _parse("""
            module top(input clk, input d, output reg q);
                always @(posedge clk) begin
                    q <= d;
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()

        begin_line = next(ln for ln in lines if ln.strip() == "begin")
        end_line = next(ln for ln in lines if ln.strip() == "end")
        stmt_line = next(ln for ln in lines if "q <= d;" in ln)

        begin_indent = len(begin_line) - len(begin_line.lstrip())
        end_indent = len(end_line) - len(end_line.lstrip())
        stmt_indent = len(stmt_line) - len(stmt_line.lstrip())

        assert begin_indent == end_indent
        assert begin_indent == stmt_indent

    def test_if_else_allman(self, fmt):
        mod = _parse("""
            module top(input clk, input rst, input d, output reg q);
                always @(posedge clk) begin
                    if (rst) begin
                        q <= 0;
                    end else begin
                        q <= d;
                    end
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()

        # 'begin' should NOT appear on any 'if' or 'else' line
        if_lines = [ln for ln in lines if ln.strip().startswith("if")]
        else_lines = [ln for ln in lines if ln.strip().startswith("else")]
        for ln in if_lines + else_lines:
            assert "begin" not in ln, f"begin should not be on control line: {ln!r}"

    def test_nested_if_allman(self, fmt):
        mod = _parse("""
            module top(input clk, input a, input b, output reg [1:0] q);
                always @(posedge clk) begin
                    if (a) begin
                        if (b) begin
                            q <= 2'b11;
                        end else begin
                            q <= 2'b10;
                        end
                    end else begin
                        q <= 2'b00;
                    end
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()

        # Count begin/end pairs
        begins = [ln for ln in lines if ln.strip() == "begin"]
        ends = [ln for ln in lines if ln.strip() == "end"]
        # 3 begins: always body, outer if, inner if, outer else, inner else
        # Actually: always (1), outer_if_then (2), inner_if_then (3),
        #           inner_else (4), outer_else (5) = 5 begins
        assert len(begins) >= 4
        assert len(ends) >= 4

    def test_for_loop_allman(self, fmt):
        mod = _parse("""
            module top;
                integer i;
                initial begin
                    for (i = 0; i < 8; i = i + 1) begin
                        $display(i);
                    end
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()
        for_lines = [ln for ln in lines if "for" in ln]
        assert len(for_lines) >= 1
        assert "begin" not in for_lines[0]


# ---------------------------------------------------------------------------
# GNU style
# ---------------------------------------------------------------------------


class TestGNUStyle:
    """GNU: ``begin`` on next line at control-keyword indent."""

    @pytest.fixture
    def fmt(self):
        return VerilogFormatter(FormatStyle.gnu())

    def test_always_begin_same_indent(self, fmt):
        mod = _parse("""
            module top(input clk, input d, output reg q);
                always @(posedge clk) begin
                    q <= d;
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()
        always_line = next(ln for ln in lines if "always" in ln)
        begin_line = next(ln for ln in lines if ln.strip() == "begin")

        always_indent = len(always_line) - len(always_line.lstrip())
        begin_indent = len(begin_line) - len(begin_line.lstrip())
        assert always_indent == begin_indent

    def test_if_gnu(self, fmt):
        mod = _parse("""
            module top(input clk, input rst, input d, output reg q);
                always @(posedge clk) begin
                    if (rst) begin
                        q <= 0;
                    end else begin
                        q <= d;
                    end
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()
        # if and begin at same indent
        [ln for ln in lines if ln.strip().startswith("if")]
        begin_after_if = None
        for i, ln in enumerate(lines):
            if ln.strip().startswith("if"):
                # next line should be begin
                if i + 1 < len(lines) and lines[i + 1].strip() == "begin":
                    begin_after_if = lines[i + 1]
                    if_indent = len(ln) - len(ln.lstrip())
                    begin_indent = len(begin_after_if) - len(begin_after_if.lstrip())
                    assert if_indent == begin_indent
                    break

    def test_stmts_indented_from_begin(self, fmt):
        mod = _parse("""
            module top(input clk, input d, output reg q);
                always @(posedge clk) begin
                    q <= d;
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()
        begin_line = next(ln for ln in lines if ln.strip() == "begin")
        stmt_line = next(ln for ln in lines if "q <= d;" in ln)
        begin_indent = len(begin_line) - len(begin_line.lstrip())
        stmt_indent = len(stmt_line) - len(stmt_line.lstrip())
        assert stmt_indent > begin_indent


# ---------------------------------------------------------------------------
# Port alignment
# ---------------------------------------------------------------------------


class TestPortAlignment:
    def test_aligned_ports(self):
        fmt = VerilogFormatter(FormatStyle.knr(align_ports=True))
        mod = _parse("""
            module top(
                input clk,
                input rst,
                input [7:0] data_in,
                output reg [7:0] data_out,
                output valid
            );
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()

        # Find port lines (indented, containing input/output)
        port_lines = [ln for ln in lines if ln.strip().startswith(("input", "output")) and "module" not in ln]
        assert len(port_lines) == 5

        # Extract the position of signal names
        names = ["clk", "rst", "data_in", "data_out", "valid"]
        positions = []
        for pl, name in zip(port_lines, names):
            idx = pl.index(name)
            positions.append(idx)

        # All names should start at the same column
        assert len(set(positions)) == 1, f"Name positions not aligned: {positions}"

    def test_unaligned_default(self):
        fmt = VerilogFormatter(FormatStyle.knr(align_ports=False))
        mod = _parse("""
            module top(
                input clk,
                input rst,
                input [7:0] data_in,
                output reg [7:0] data_out,
                output valid
            );
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()
        port_lines = [ln for ln in lines if ln.strip().startswith(("input", "output")) and "module" not in ln]
        assert len(port_lines) == 5

        # Without alignment, names are at different columns
        names = ["clk", "rst", "data_in", "data_out", "valid"]
        positions = []
        for pl, name in zip(port_lines, names):
            positions.append(pl.index(name))
        # "output reg [7:0]" is wider than "input", so names are NOT all at same column
        assert len(set(positions)) > 1


# ---------------------------------------------------------------------------
# Column-limit wrapping
# ---------------------------------------------------------------------------


class TestColumnLimit:
    def test_instance_wraps_on_long_line(self):
        fmt = VerilogFormatter(FormatStyle.knr(column_limit=40))
        mod = _parse("""
            module top(input clk, input rst, input d, output q);
                submod u1 (.clk(clk), .rst(rst), .d(d), .q(q));
            endmodule
        """)
        result = fmt.format_module(mod)
        # With a tight column limit, ports should be on separate lines
        lines = result.splitlines()
        port_conn_lines = [ln for ln in lines if ln.strip().startswith(".")]
        assert len(port_conn_lines) >= 2

    def test_instance_no_wrap_when_short(self):
        fmt = VerilogFormatter(FormatStyle.knr(column_limit=200))
        mod = _parse("""
            module top(input a, output y);
                inv u1 (.a(a), .y(y));
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()
        inst_lines = [ln for ln in lines if "inv" in ln]
        assert len(inst_lines) == 1  # all on one line

    def test_column_limit_zero_disables(self):
        fmt = VerilogFormatter(FormatStyle.knr(column_limit=0))
        mod = _parse("""
            module top(input clk, input rst, input d, output q);
                submod u1 (.clk(clk), .rst(rst), .d(d), .q(q));
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()
        inst_lines = [ln for ln in lines if "submod" in ln]
        assert len(inst_lines) == 1  # all on one line


# ---------------------------------------------------------------------------
# Indent width
# ---------------------------------------------------------------------------


class TestIndentWidth:
    def test_two_space_indent(self):
        fmt = VerilogFormatter(FormatStyle.knr(indent_width=2))
        mod = _parse("""
            module top(input clk, input d, output reg q);
                always @(posedge clk) begin
                    q <= d;
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()
        stmt_line = next(ln for ln in lines if "q <= d;" in ln)
        # Should be indented 2*2 = 4 spaces (module body + always body)
        assert stmt_line.startswith("    q <= d;")

    def test_eight_space_indent(self):
        fmt = VerilogFormatter(FormatStyle.knr(indent_width=8))
        mod = _parse("""
            module top(input clk, input d, output reg q);
                always @(posedge clk) begin
                    q <= d;
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()
        stmt_line = next(ln for ln in lines if "q <= d;" in ln)
        # 8 (module body) + 8 (always body) = 16 spaces
        assert stmt_line.startswith(" " * 16 + "q <= d;")


# ---------------------------------------------------------------------------
# format_module convenience function
# ---------------------------------------------------------------------------


class TestConvenienceFunction:
    def test_format_module_default_style(self):
        mod = _parse("""
            module top(input a, output y);
                assign y = a;
            endmodule
        """)
        result = format_module(mod)
        assert "module top" in result
        assert "assign y = a;" in result
        assert "endmodule" in result

    def test_format_module_with_style(self):
        mod = _parse("""
            module top(input clk, input d, output reg q);
                always @(posedge clk) begin
                    q <= d;
                end
            endmodule
        """)
        result_knr = format_module(mod, FormatStyle.knr())
        result_allman = format_module(mod, FormatStyle.allman())
        # They should differ in begin placement
        assert result_knr != result_allman


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_module(self):
        fmt = VerilogFormatter(FormatStyle.knr())
        mod = _parse("""
            module empty;
            endmodule
        """)
        result = fmt.format_module(mod)
        assert "module empty;" in result
        assert "endmodule" in result

    def test_single_statement_no_begin(self):
        """Single-statement if bodies should not get begin/end."""
        fmt = VerilogFormatter(FormatStyle.knr())
        mod = _parse("""
            module top(input clk, input rst, output reg q);
                always @(posedge clk)
                    if (rst)
                        q <= 0;
            endmodule
        """)
        result = fmt.format_module(mod)
        # No begin/end since there's no SeqBlock
        assert "begin" not in result

    def test_initial_block(self):
        fmt = VerilogFormatter(FormatStyle.allman())
        mod = _parse("""
            module top;
                reg [7:0] x;
                initial begin
                    x = 0;
                    #10 x = 1;
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()
        init_lines = [ln for ln in lines if "initial" in ln]
        assert len(init_lines) == 1
        assert "begin" not in init_lines[0]

    def test_while_loop(self):
        fmt = VerilogFormatter(FormatStyle.knr())
        mod = _parse("""
            module top;
                integer i;
                initial begin
                    i = 0;
                    while (i < 5) begin
                        i = i + 1;
                    end
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        assert "while (i < 5) begin" in result

    def test_case_statement(self):
        fmt = VerilogFormatter(FormatStyle.allman())
        mod = _parse("""
            module top(input [1:0] sel, input a, input b, output reg y);
                always @(*) begin
                    case (sel)
                        2'b00: y = a;
                        2'b01: y = b;
                        default: y = 0;
                    endcase
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        assert "case (sel)" in result
        assert "endcase" in result
        # Simple case items stay inline
        assert "2'b00:" in result

    def test_parameters(self):
        fmt = VerilogFormatter(FormatStyle.knr())
        mod = _parse("""
            module top #(parameter WIDTH = 8) (
                input [WIDTH-1:0] data_in,
                output [WIDTH-1:0] data_out
            );
                assign data_out = data_in;
            endmodule
        """)
        result = fmt.format_module(mod)
        assert "#(" in result
        assert "parameter WIDTH = 8" in result

    def test_instance(self):
        fmt = VerilogFormatter(FormatStyle.knr())
        mod = _parse("""
            module top(input a, output y);
                sub #(.W(4)) u1 (.a(a), .y(y));
            endmodule
        """)
        result = fmt.format_module(mod)
        assert "sub" in result
        assert ".W(4)" in result
        assert ".a(a)" in result

    def test_instance_empty_param_override(self):
        fmt = VerilogFormatter(FormatStyle.knr())
        mod = _parse("""
            module top(input a, output y);
                sub #() u1 (.a(a), .y(y));
            endmodule
        """)
        result = fmt.format_module(mod)
        assert "sub #() u1" in result

    def test_forever_loop(self):
        fmt = VerilogFormatter(FormatStyle.knr())
        mod = _parse("""
            module top;
                reg clk;
                initial begin
                    clk = 0;
                    forever #5 clk = ~clk;
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        assert "forever" in result

    def test_allman_full_example(self):
        """Full example matching user's preferred style."""
        fmt = VerilogFormatter(FormatStyle.allman())
        mod = _parse("""
            module counter(input clk, input rst, output reg [7:0] count);
                always @(posedge clk) begin
                    if (rst) begin
                        count <= 0;
                    end else begin
                        count <= count + 1;
                    end
                end
            endmodule
        """)
        result = fmt.format_module(mod)
        lines = result.splitlines()

        # Verify the allman pattern: control keyword on one line,
        # begin/stmts/end on next lines at same indent
        always_line = next(ln for ln in lines if "always" in ln)
        assert "begin" not in always_line

        # Find the first begin after always
        always_idx = lines.index(always_line)
        begin_line = lines[always_idx + 1]
        assert begin_line.strip() == "begin"

        # begin should be indented more than always
        always_indent = len(always_line) - len(always_line.lstrip())
        begin_indent = len(begin_line) - len(begin_line.lstrip())
        assert begin_indent > always_indent
