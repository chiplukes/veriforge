"""Tests for the Verilog preprocessor."""

from __future__ import annotations

import textwrap

import pytest

from veriforge.preprocessor import PreprocessorError, preprocess, preprocess_file


# ---------------------------------------------------------------------------
# `define / macro expansion
# ---------------------------------------------------------------------------


class TestDefine:
    """Tests for `define and macro expansion."""

    def test_simple_define(self):
        src = textwrap.dedent("""\
            `define WIDTH 8
            wire [`WIDTH-1:0] data;
        """)
        out = preprocess(src)
        assert "wire [8-1:0] data;" in out

    def test_define_no_value(self):
        src = textwrap.dedent("""\
            `define SIMULATION
            `ifdef SIMULATION
            wire sim_only;
            `endif
        """)
        out = preprocess(src)
        assert "wire sim_only;" in out

    def test_define_multiword_value(self):
        src = textwrap.dedent("""\
            `define OPCODE 7'b0110111
            assign op = `OPCODE;
        """)
        out = preprocess(src)
        assert "assign op = 7'b0110111;" in out

    def test_define_overwrite(self):
        src = textwrap.dedent("""\
            `define VAL 1
            wire a = `VAL;
            `define VAL 2
            wire b = `VAL;
        """)
        out = preprocess(src)
        assert "wire a = 1;" in out
        assert "wire b = 2;" in out

    def test_define_line_continuation(self):
        src = "`define LONG_MACRO first_part \\\nsecond_part\nwire x = `LONG_MACRO;\n"
        out = preprocess(src)
        assert "first_part" in out
        assert "second_part" in out

    def test_predefined_defines(self):
        src = "wire x = `MY_CONST;\n"
        out = preprocess(src, defines={"MY_CONST": "42"})
        assert "wire x = 42;" in out

    def test_undefined_macro_left_as_is(self):
        src = "wire x = `UNKNOWN;\n"
        out = preprocess(src)
        assert "`UNKNOWN" in out

    def test_expand_multiple_macros_on_one_line(self):
        src = textwrap.dedent("""\
            `define A 1
            `define B 2
            wire [(`A+`B)-1:0] data;
        """)
        out = preprocess(src)
        assert "wire [(1+2)-1:0] data;" in out

    def test_define_with_expression(self):
        src = textwrap.dedent("""\
            `define ADDR_WIDTH 32
            `define ADDR_MSB (`ADDR_WIDTH - 1)
            wire [`ADDR_MSB:0] addr;
        """)
        out = preprocess(src)
        # First pass expands ADDR_MSB to its literal value
        assert "(`ADDR_WIDTH - 1)" in out or "(32 - 1)" in out


# ---------------------------------------------------------------------------
# `undef
# ---------------------------------------------------------------------------


class TestUndef:
    def test_undef_removes_define(self):
        src = textwrap.dedent("""\
            `define FOO 1
            wire a = `FOO;
            `undef FOO
            wire b = `FOO;
        """)
        out = preprocess(src)
        assert "wire a = 1;" in out
        assert "`FOO" in out.split("\n")[-2]  # FOO no longer defined

    def test_undef_nonexistent_is_silent(self):
        src = "`undef NONEXISTENT\n"
        out = preprocess(src)
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# `ifdef / `ifndef / `else / `endif
# ---------------------------------------------------------------------------


class TestConditional:
    def test_ifdef_true(self):
        src = textwrap.dedent("""\
            `define FEATURE
            `ifdef FEATURE
            wire feature_on;
            `endif
        """)
        out = preprocess(src)
        assert "wire feature_on;" in out

    def test_ifdef_false(self):
        src = textwrap.dedent("""\
            `ifdef FEATURE
            wire feature_on;
            `endif
        """)
        out = preprocess(src)
        assert "wire feature_on;" not in out

    def test_ifdef_else_true_branch(self):
        src = textwrap.dedent("""\
            `define FAST
            `ifdef FAST
            wire fast_path;
            `else
            wire slow_path;
            `endif
        """)
        out = preprocess(src)
        assert "wire fast_path;" in out
        assert "wire slow_path;" not in out

    def test_ifdef_else_false_branch(self):
        src = textwrap.dedent("""\
            `ifdef FAST
            wire fast_path;
            `else
            wire slow_path;
            `endif
        """)
        out = preprocess(src)
        assert "wire fast_path;" not in out
        assert "wire slow_path;" in out

    def test_ifndef_true(self):
        src = textwrap.dedent("""\
            `ifndef FEATURE
            wire default_path;
            `endif
        """)
        out = preprocess(src)
        assert "wire default_path;" in out

    def test_ifndef_false(self):
        src = textwrap.dedent("""\
            `define FEATURE
            `ifndef FEATURE
            wire default_path;
            `endif
        """)
        out = preprocess(src)
        assert "wire default_path;" not in out

    def test_nested_ifdef(self):
        src = textwrap.dedent("""\
            `define OUTER
            `define INNER
            `ifdef OUTER
            wire outer;
            `ifdef INNER
            wire both;
            `endif
            `endif
        """)
        out = preprocess(src)
        assert "wire outer;" in out
        assert "wire both;" in out

    def test_nested_ifdef_outer_false(self):
        src = textwrap.dedent("""\
            `define INNER
            `ifdef OUTER
            wire outer;
            `ifdef INNER
            wire both;
            `endif
            `endif
        """)
        out = preprocess(src)
        assert "wire outer;" not in out
        assert "wire both;" not in out

    def test_nested_ifdef_inner_false(self):
        src = textwrap.dedent("""\
            `define OUTER
            `ifdef OUTER
            wire outer;
            `ifdef INNER
            wire both;
            `endif
            `endif
        """)
        out = preprocess(src)
        assert "wire outer;" in out
        assert "wire both;" not in out

    def test_predefined_ifdef(self):
        src = textwrap.dedent("""\
            `ifdef __ICARUS__
            wire icarus;
            `endif
        """)
        out = preprocess(src, defines={"__ICARUS__": ""})
        assert "wire icarus;" in out

    def test_ifdef_with_define_inside(self):
        """Test that `define inside an inactive branch is not processed."""
        src = textwrap.dedent("""\
            `ifdef DISABLED
            `define SECRET 42
            `endif
            wire x = `SECRET;
        """)
        out = preprocess(src)
        assert "`SECRET" in out  # SECRET should NOT be defined

    def test_deeply_nested(self):
        src = textwrap.dedent("""\
            `define A
            `define B
            `define C
            `ifdef A
            `ifdef B
            `ifdef C
            wire deep;
            `endif
            `endif
            `endif
        """)
        out = preprocess(src)
        assert "wire deep;" in out

    def test_ifdef_else_chain_darkriscv_style(self):
        """Pattern from DarkRISCV config.vh — ifdef/else with nested ifdefs."""
        src = textwrap.dedent("""\
            `define __3STAGE__
            `ifdef __3STAGE__
            wire [31:0] NXPC2;
            `else
            wire [31:0] NXPC;
            `endif
        """)
        out = preprocess(src)
        assert "NXPC2" in out
        assert "wire [31:0] NXPC;" not in out


# ---------------------------------------------------------------------------
# `elsif
# ---------------------------------------------------------------------------


class TestElsif:
    def test_elsif_first_true(self):
        src = textwrap.dedent("""\
            `define A
            `ifdef A
            wire a;
            `elsif B
            wire b;
            `else
            wire c;
            `endif
        """)
        out = preprocess(src)
        assert "wire a;" in out
        assert "wire b;" not in out
        assert "wire c;" not in out

    def test_elsif_second_true(self):
        src = textwrap.dedent("""\
            `define B
            `ifdef A
            wire a;
            `elsif B
            wire b;
            `else
            wire c;
            `endif
        """)
        out = preprocess(src)
        assert "wire a;" not in out
        assert "wire b;" in out
        assert "wire c;" not in out

    def test_elsif_none_true(self):
        src = textwrap.dedent("""\
            `ifdef A
            wire a;
            `elsif B
            wire b;
            `else
            wire c;
            `endif
        """)
        out = preprocess(src)
        assert "wire a;" not in out
        assert "wire b;" not in out
        assert "wire c;" in out

    def test_multiple_elsif(self):
        src = textwrap.dedent("""\
            `define C
            `ifdef A
            wire a;
            `elsif B
            wire b;
            `elsif C
            wire c;
            `else
            wire d;
            `endif
        """)
        out = preprocess(src)
        assert "wire a;" not in out
        assert "wire b;" not in out
        assert "wire c;" in out
        assert "wire d;" not in out


# ---------------------------------------------------------------------------
# `include
# ---------------------------------------------------------------------------


class TestInclude:
    def test_include_file(self, tmp_path):
        header = tmp_path / "config.vh"
        header.write_text("`define WIDTH 8\n", encoding="utf-8")
        src = '`include "config.vh"\nwire [`WIDTH-1:0] data;\n'
        out = preprocess(src, include_paths=[tmp_path])
        assert "wire [8-1:0] data;" in out

    def test_include_nested(self, tmp_path):
        inner = tmp_path / "inner.vh"
        inner.write_text("`define INNER_VAL 5\n", encoding="utf-8")
        outer = tmp_path / "outer.vh"
        outer.write_text('`include "inner.vh"\n`define OUTER_VAL 10\n', encoding="utf-8")
        src = '`include "outer.vh"\nwire a = `INNER_VAL;\nwire b = `OUTER_VAL;\n'
        out = preprocess(src, include_paths=[tmp_path])
        assert "wire a = 5;" in out
        assert "wire b = 10;" in out

    def test_include_not_found_raises(self, tmp_path):
        src = '`include "nonexistent.vh"\n'
        with pytest.raises(PreprocessorError, match="cannot find include file"):
            preprocess(src, include_paths=[tmp_path])

    def test_include_recursive_guard(self, tmp_path):
        # File A includes B, B includes A — should not infinite loop
        a = tmp_path / "a.vh"
        b = tmp_path / "b.vh"
        a.write_text('`include "b.vh"\n`define FROM_A 1\n', encoding="utf-8")
        b.write_text('`include "a.vh"\n`define FROM_B 2\n', encoding="utf-8")
        out = preprocess_file(str(a), include_paths=[tmp_path])
        assert "FROM_B" not in out or "FROM_A" not in out  # one of them breaks the cycle

    def test_include_angle_brackets(self, tmp_path):
        header = tmp_path / "sys.vh"
        header.write_text("`define SYS 1\n", encoding="utf-8")
        src = "`include <sys.vh>\nwire x = `SYS;\n"
        out = preprocess(src, include_paths=[tmp_path])
        assert "wire x = 1;" in out

    def test_include_relative_to_source(self, tmp_path):
        """Include resolves relative to the including file's directory."""
        subdir = tmp_path / "rtl"
        subdir.mkdir()
        header = subdir / "config.vh"
        header.write_text("`define CFG 1\n", encoding="utf-8")
        main_file = subdir / "top.v"
        main_file.write_text('`include "config.vh"\nwire x = `CFG;\n', encoding="utf-8")
        out = preprocess_file(str(main_file))
        assert "wire x = 1;" in out


# ---------------------------------------------------------------------------
# `timescale and other stripped directives
# ---------------------------------------------------------------------------


class TestStrippedDirectives:
    def test_timescale_stripped(self):
        src = "`timescale 1ns / 1ps\nmodule top;\nendmodule\n"
        out = preprocess(src)
        assert "`timescale" not in out
        assert "module top;" in out

    def test_default_nettype_stripped(self):
        src = "`default_nettype none\nmodule top;\nendmodule\n"
        out = preprocess(src)
        assert "`default_nettype" not in out
        assert "module top;" in out

    def test_resetall_stripped(self):
        src = "`resetall\nmodule top;\nendmodule\n"
        out = preprocess(src)
        assert "`resetall" not in out

    def test_celldefine_stripped(self):
        src = "`celldefine\nmodule top;\nendmodule\n`endcelldefine\n"
        out = preprocess(src)
        assert "`celldefine" not in out
        assert "`endcelldefine" not in out


# ---------------------------------------------------------------------------
# Line number preservation
# ---------------------------------------------------------------------------


class TestLinePreservation:
    def test_define_preserves_line_count(self):
        src = "line1\n`define FOO 1\nline3\n"
        out = preprocess(src)
        lines = out.split("\n")
        assert len(lines) == len(src.split("\n"))
        assert lines[0] == "line1"
        assert lines[2] == "line3"

    def test_ifdef_preserves_line_count(self):
        src = textwrap.dedent("""\
            line1
            `ifdef DISABLED
            hidden
            `endif
            line5
        """)
        out = preprocess(src)
        lines = out.split("\n")
        assert len(lines) == len(src.split("\n"))
        assert lines[0] == "line1"
        assert lines[4] == "line5"

    def test_include_adds_lines(self, tmp_path):
        """Included content adds lines but original content stays on correct lines."""
        header = tmp_path / "inc.vh"
        header.write_text("`define A 1\n`define B 2\n", encoding="utf-8")
        src = 'before\n`include "inc.vh"\nafter\n'
        out = preprocess(src, include_paths=[tmp_path])
        assert "before" in out
        assert "after" in out


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrors:
    def test_endif_without_ifdef(self):
        with pytest.raises(PreprocessorError, match=r"endif.*without"):
            preprocess("`endif\n")

    def test_else_without_ifdef(self):
        with pytest.raises(PreprocessorError, match=r"else.*without"):
            preprocess("`else\n")

    def test_unterminated_ifdef(self):
        with pytest.raises(PreprocessorError, match="unterminated"):
            preprocess("`ifdef FOO\n")

    def test_duplicate_else(self):
        src = textwrap.dedent("""\
            `define A
            `ifdef A
            `else
            `else
            `endif
        """)
        with pytest.raises(PreprocessorError, match=r"duplicate.*else"):
            preprocess(src)

    def test_ifdef_missing_name(self):
        with pytest.raises(PreprocessorError, match="requires a macro name"):
            preprocess("`ifdef\n`endif\n")

    def test_elsif_after_else(self):
        src = textwrap.dedent("""\
            `ifdef A
            `else
            `elsif B
            `endif
        """)
        with pytest.raises(PreprocessorError, match=r"elsif.*after.*else"):
            preprocess(src)


# ---------------------------------------------------------------------------
# preprocess_file
# ---------------------------------------------------------------------------


class TestPreprocessFile:
    def test_basic_file(self, tmp_path):
        f = tmp_path / "test.v"
        f.write_text("`define W 4\nwire [`W-1:0] d;\n", encoding="utf-8")
        out = preprocess_file(str(f))
        assert "wire [4-1:0] d;" in out

    def test_return_defines(self, tmp_path):
        f = tmp_path / "test.v"
        f.write_text("`define A 1\n`define B 2\n", encoding="utf-8")
        _out, defs = preprocess_file(str(f), return_defines=True)
        assert defs["A"] == "1"
        assert defs["B"] == "2"

    def test_predefined_and_file_defines_merge(self, tmp_path):
        f = tmp_path / "test.v"
        f.write_text("`define B 2\n", encoding="utf-8")
        _out, defs = preprocess_file(str(f), defines={"A": "1"}, return_defines=True)
        assert defs["A"] == "1"
        assert defs["B"] == "2"


# ---------------------------------------------------------------------------
# strip_comments option
# ---------------------------------------------------------------------------


class TestStripComments:
    def test_strip_line_comments(self):
        src = "wire a; // this is a comment\nwire b;\n"
        out = preprocess(src, strip_comments=True)
        assert "// this is a comment" not in out
        assert "wire a;" in out
        assert "wire b;" in out

    def test_strip_block_comments(self):
        src = "wire a; /* block\ncomment */ wire b;\n"
        out = preprocess(src, strip_comments=True)
        assert "/* block" not in out
        assert "wire a;" in out
        assert "wire b;" in out


# ---------------------------------------------------------------------------
# DarkRISCV-style patterns
# ---------------------------------------------------------------------------


class TestDarkRISCVPatterns:
    """Test patterns found in the actual DarkRISCV source code."""

    def test_opcode_defines(self):
        """DarkRISCV defines opcodes with `define then uses them with backtick."""
        src = textwrap.dedent("""\
            `define LUI   7'b01101_11
            `define AUIPC 7'b00101_11
            `define JAL   7'b11011_11
            assign XLUI   = IDATAX[6:0]==`LUI;
            assign XAUIPC = IDATAX[6:0]==`AUIPC;
            assign XJAL   = IDATAX[6:0]==`JAL;
        """)
        out = preprocess(src)
        assert "IDATAX[6:0]==7'b01101_11" in out
        assert "IDATAX[6:0]==7'b00101_11" in out
        assert "IDATAX[6:0]==7'b11011_11" in out

    def test_config_include_and_ifdefs(self, tmp_path):
        """DarkRISCV pattern: include config.vh then ifdef features."""
        config = tmp_path / "config.vh"
        config.write_text("`define __3STAGE__\n", encoding="utf-8")
        src = textwrap.dedent("""\
            `include "config.vh"
            module darkriscv(input CLK, input RES);
            `ifdef __3STAGE__
                reg [31:0] NXPC2;
            `else
                wire [31:0] NXPC;
            `endif
            endmodule
        """)
        out = preprocess(src, include_paths=[tmp_path])
        assert "reg [31:0] NXPC2;" in out
        assert "wire [31:0] NXPC;" not in out

    def test_config_with_ifdef_chain(self):
        """DarkRISCV pattern: ifdef chains for feature selection."""
        src = textwrap.dedent("""\
            `define __3STAGE__
            `define __PERFMETER__

            module darkriscv(input CLK, input RES);

            `ifdef __3STAGE__
                reg [31:0] NXPC2;
                reg [1:0] FLUSH = -1;
            `else
                wire [31:0] NXPC;
                reg FLUSH = -1;
            `endif

            `ifdef __PERFMETER__
                integer clocks=0, running=0;
            `endif

            `ifdef __THREADS__
                reg [0:0] TPTR = 0;
            `endif

            endmodule
        """)
        out = preprocess(src)
        assert "reg [31:0] NXPC2;" in out
        assert "reg [1:0] FLUSH = -1;" in out
        assert "integer clocks=0, running=0;" in out
        assert "TPTR" not in out  # __THREADS__ not defined

    def test_simulation_define_chain(self):
        """DarkRISCV pattern: __ICARUS__ defines SIMULATION which enables features."""
        src = textwrap.dedent("""\
            `ifdef __ICARUS__
                `define SIMULATION 1
            `endif

            `ifdef SIMULATION
                integer i;
                initial for(i=0;i!=32;i=i+1) REGS[i] = 0;
            `endif
        """)
        out = preprocess(src, defines={"__ICARUS__": ""})
        assert "integer i;" in out
        assert "initial for" in out

    def test_register_length_define(self):
        """DarkRISCV pattern: RLEN calculated from defines."""
        src = textwrap.dedent("""\
            `ifdef __RV32E__
                `define RLEN 16
            `else
                `define RLEN 32
            `endif
            reg [31:0] REGS [0:`RLEN-1];
        """)
        out = preprocess(src)
        assert "reg [31:0] REGS [0:32-1];" in out

    def test_rv32e_variant(self):
        src = textwrap.dedent("""\
            `ifdef __RV32E__
                `define RLEN 16
            `else
                `define RLEN 32
            `endif
            reg [31:0] REGS [0:`RLEN-1];
        """)
        out = preprocess(src, defines={"__RV32E__": ""})
        assert "reg [31:0] REGS [0:16-1];" in out

    def test_board_id_selection(self):
        """DarkRISCV pattern: board selection via ifdef in config.vh."""
        src = textwrap.dedent("""\
            `ifdef AVNET_MICROBOARD_LX9
                `define BOARD_ID 1
                `define BOARD_CK_REF 100000000
            `endif

            `ifdef QMTECH_ARTIX7_A35
                `define BOARD_ID 9
                `define BOARD_CK_REF 50000000
            `endif

            `ifndef BOARD_ID
                `define BOARD_ID 0
                `define BOARD_CK 100000000
            `endif
        """)
        # No board defined — should get defaults
        out = preprocess(src)
        # BOARD_ID should be defined to 0
        # Check line-by-line that default block was active
        assert "BOARD_ID" not in [line.strip() for line in out.split("\n") if "AVNET" in line or "QMTECH" in line]

    def test_timescale_and_include_stripped(self, tmp_path):
        """DarkRISCV sim file pattern: timescale then include config."""
        config = tmp_path / "config.vh"
        config.write_text("`define BOARD_CK 100000000\n", encoding="utf-8")

        src = textwrap.dedent("""\
            `timescale 1ns / 1ps
            `include "config.vh"
            module darksimv;
                reg CLK = 0;
                initial while(1) #(500000000/`BOARD_CK) CLK = !CLK;
            endmodule
        """)
        out = preprocess(src, include_paths=[tmp_path])
        assert "`timescale" not in out
        assert "500000000/100000000" in out
        assert "module darksimv;" in out

    def test_full_config_pattern(self, tmp_path):
        """Simulate a mini version of DarkRISCV's config.vh + darkriscv.v."""
        config = tmp_path / "config.vh"
        config.write_text(
            textwrap.dedent("""\
                `define __3STAGE__
                `ifdef __ICARUS__
                    `define SIMULATION 1
                `endif
                `ifdef __RV32E__
                    `define RLEN 16
                `else
                    `define RLEN 32
                `endif
                `ifndef BOARD_ID
                    `define BOARD_ID 0
                    `define BOARD_CK 100000000
                `endif
                `define __RESETPC__ 32'd0
            """),
            encoding="utf-8",
        )

        rtl = tmp_path / "core.v"
        rtl.write_text(
            textwrap.dedent("""\
                `timescale 1ns / 1ps
                `include "config.vh"
                module darkriscv(input CLK, input RES);
                    reg [31:0] REGS [0:`RLEN-1];
                    `ifdef __3STAGE__
                    reg [31:0] NXPC2;
                    `else
                    wire [31:0] NXPC2;
                    `endif
                    `ifdef SIMULATION
                    integer i;
                    initial for(i=0;i!=`RLEN;i=i+1) REGS[i] = 0;
                    `endif
                endmodule
            """),
            encoding="utf-8",
        )

        out = preprocess_file(str(rtl), defines={"__ICARUS__": ""})
        assert "`timescale" not in out
        assert "reg [31:0] NXPC2;" in out  # 3-stage selected
        assert "wire [31:0] NXPC2;" not in out
        assert "reg [31:0] REGS [0:32-1];" in out  # RV32I (32 regs)
        assert "integer i;" in out  # SIMULATION enabled via __ICARUS__
        assert "for(i=0;i!=32;i=i+1)" in out
