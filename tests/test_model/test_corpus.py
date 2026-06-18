"""Corpus tests: parse → model → emit → verify with iverilog.

Tests real-world Verilog patterns against the full pipeline and
validates emitted output is syntactically correct using Icarus Verilog.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from veriforge.codegen.verilog_emitter import emit_design
from veriforge.transforms.comment_extractor import extract_comments
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

# Icarus Verilog path — prefer shutil.which so any install location works
import shutil as _shutil

_iverilog_which = _shutil.which("iverilog")
IVERILOG = Path(_iverilog_which) if _iverilog_which else Path(r"C:\iverilog\bin\iverilog.exe")
IVERILOG_AVAILABLE = _iverilog_which is not None

# Session-scoped parser to avoid re-parsing the grammar for every test
_parser = None


def _get_parser():
    global _parser
    if _parser is None:
        _parser = verilog_parser(start="verilog")
    return _parser


def _full_roundtrip(source: str, *, with_comments: bool = True) -> str:
    """Parse → model → emit. Returns emitted Verilog text."""
    parser = _get_parser()
    if with_comments:
        cleaned, comments = extract_comments(source)
    else:
        cleaned, comments = source, None
    tree = parser.build_tree(cleaned)
    design = tree_to_design(tree, source_file="corpus.v", comments=comments, source_text=source)
    return emit_design(design)


def _iverilog_check(verilog_text: str, *, expect_pass: bool = True) -> tuple[bool, str]:
    """Run iverilog syntax check on Verilog text. Returns (success, stderr)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".v", delete=False, encoding="utf-8") as f:
        f.write(verilog_text)
        f.flush()
        tmp_path = f.name
    try:
        result = subprocess.run(
            [str(IVERILOG), "-t", "null", "-g2005", tmp_path],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        success = result.returncode == 0
        return success, result.stderr.strip()
    finally:
        os.unlink(tmp_path)


# ---- Corpus sources ----

CORPUS_SIMPLE_MODULE = """\
module simple_counter #(
    parameter WIDTH = 8
) (
    input clk,
    input rst,
    output reg [WIDTH-1:0] count
);

always @(posedge clk or posedge rst)
    if (rst)
        count <= 0;
    else
        count <= count + 1;

endmodule
"""

CORPUS_MULTI_MODULE = """\
module mux2 (
    input a,
    input b,
    input sel,
    output y
);
assign y = sel ? b : a;
endmodule

module top (
    input clk,
    input [1:0] d,
    input sel,
    output q
);
wire mux_out;
mux2 u_mux (.a(d[0]), .b(d[1]), .sel(sel), .y(mux_out));
reg q_reg;
always @(posedge clk)
    q_reg <= mux_out;
assign q = q_reg;
endmodule
"""

CORPUS_COMMENTS = """\
// Simple register with comments
module commented_reg (
    input clk,        // system clock
    input rst,        // async reset
    input [7:0] d,    // data input
    output reg [7:0] q // data output
);

// Reset and capture logic
always @(posedge clk or posedge rst) begin
    if (rst)
        q <= 8'h00;
    else
        q <= d;
end

endmodule
"""

CORPUS_COMPLEX_EXPRESSIONS = """\
module expr_test (
    input [7:0] a, b, c,
    output [7:0] y1, y2, y3, y4
);

assign y1 = (a & b) | (~c);
assign y2 = a ? b : c;
assign y3 = {a[3:0], b[7:4]};
assign y4 = {4{a[0]}};

endmodule
"""

CORPUS_GENERATE = """\
module gen_test #(parameter N = 4) (
    input [N-1:0] a,
    output [N-1:0] y
);

genvar i;
generate
    for (i = 0; i < N; i = i + 1) begin : gen_inv
        assign y[i] = ~a[i];
    end
endgenerate

endmodule
"""

CORPUS_FUNCTION_TASK = """\
module func_task_test (
    input [7:0] a,
    output [7:0] y
);

function [7:0] add_one;
    input [7:0] val;
    begin
        add_one = val + 1;
    end
endfunction

assign y = add_one(a);

endmodule
"""

CORPUS_INITIAL_BLOCK = """\
module tb_test;

reg clk;
reg [7:0] data;

initial begin
    clk = 0;
    data = 8'hFF;
end

always #5 clk = ~clk;

endmodule
"""

CORPUS_SPECIFY = """\
module buf_with_timing (
    input a,
    output y
);

assign y = a;

specify
    (a => y) = 5;
endspecify

endmodule
"""

CORPUS_MULTIPORT_INSTANCE = """\
module child (input a, input b, output c);
assign c = a & b;
endmodule

module parent (
    input x,
    input y,
    output z
);
child u_child (.a(x), .b(y), .c(z));
endmodule
"""

CORPUS_LOCALPARAM = """\
module param_test #(
    parameter WIDTH = 8,
    parameter DEPTH = 16
) (
    input [WIDTH-1:0] data_in,
    output [WIDTH-1:0] data_out
);

localparam MASK = {WIDTH{1'b1}};
assign data_out = data_in & MASK;

endmodule
"""

CORPUS_MULTI_NET_TYPES = """\
module net_types (
    input a,
    output b
);

wire w1;
wire [3:0] w2;
wire signed [7:0] w3;
tri t1;

assign w1 = a;
assign w2 = {4{a}};
assign w3 = 8'd127;
assign t1 = a;
assign b = w1;

endmodule
"""

CORPUS_CASE_STATEMENT = """\
module decoder (
    input [1:0] sel,
    output reg [3:0] y
);

always @(*) begin
    case (sel)
        2'b00: y = 4'b0001;
        2'b01: y = 4'b0010;
        2'b10: y = 4'b0100;
        2'b11: y = 4'b1000;
        default: y = 4'b0000;
    endcase
end

endmodule
"""

CORPUS_NESTED_IF = """\
module priority_enc (
    input [3:0] req,
    output reg [1:0] grant
);

always @(*) begin
    if (req[3])
        grant = 2'd3;
    else if (req[2])
        grant = 2'd2;
    else if (req[1])
        grant = 2'd1;
    else
        grant = 2'd0;
end

endmodule
"""

CORPUS_FOR_LOOP = """\
module shift_reg #(parameter N = 8) (
    input clk,
    input din,
    output dout
);

reg [N-1:0] sr;
integer i;

always @(posedge clk) begin
    sr[0] <= din;
    for (i = 1; i < N; i = i + 1)
        sr[i] <= sr[i-1];
end

assign dout = sr[N-1];

endmodule
"""


# All corpus entries: (name, source)
CORPUS = [
    ("simple_counter", CORPUS_SIMPLE_MODULE),
    ("multi_module", CORPUS_MULTI_MODULE),
    ("comments", CORPUS_COMMENTS),
    ("complex_expr", CORPUS_COMPLEX_EXPRESSIONS),
    ("generate", CORPUS_GENERATE),
    ("function_task", CORPUS_FUNCTION_TASK),
    ("initial_block", CORPUS_INITIAL_BLOCK),
    ("specify", CORPUS_SPECIFY),
    ("multiport_instance", CORPUS_MULTIPORT_INSTANCE),
    ("localparam", CORPUS_LOCALPARAM),
    ("multi_net_types", CORPUS_MULTI_NET_TYPES),
    ("case_statement", CORPUS_CASE_STATEMENT),
    ("nested_if", CORPUS_NESTED_IF),
    ("for_loop", CORPUS_FOR_LOOP),
]


# ---- Parse tests (can we parse each?) ----


class TestCorpusParse:
    """Every corpus entry must parse without error."""

    @pytest.mark.parametrize("name,source", CORPUS, ids=[c[0] for c in CORPUS])
    def test_parses(self, name, source):
        parser = _get_parser()
        tree = parser.build_tree(source)
        assert tree is not None


# ---- Model extraction tests ----


class TestCorpusModel:
    """Every corpus entry must produce a Design with at least one module."""

    @pytest.mark.parametrize("name,source", CORPUS, ids=[c[0] for c in CORPUS])
    def test_model_extraction(self, name, source):
        parser = _get_parser()
        cleaned, comments = extract_comments(source)
        tree = parser.build_tree(cleaned)
        design = tree_to_design(tree, source_file=f"{name}.v", comments=comments, source_text=source)
        assert len(design.modules) >= 1
        for m in design.modules:
            assert m.name


# ---- Emission tests ----


class TestCorpusEmission:
    """Every corpus entry must emit non-empty Verilog text."""

    @pytest.mark.parametrize("name,source", CORPUS, ids=[c[0] for c in CORPUS])
    def test_emits(self, name, source):
        emitted = _full_roundtrip(source)
        assert "module" in emitted
        assert "endmodule" in emitted


# ---- iverilog syntax validation ----


@pytest.mark.skipif(not IVERILOG_AVAILABLE, reason="iverilog not found")
class TestCorpusIverilog:
    """Emitted output must be syntactically valid per iverilog."""

    @pytest.mark.parametrize("name,source", CORPUS, ids=[c[0] for c in CORPUS])
    def test_iverilog_original(self, name, source):
        """Original source passes iverilog."""
        ok, err = _iverilog_check(source)
        if not ok:
            pytest.skip(f"Original source fails iverilog: {err}")

    @pytest.mark.parametrize("name,source", CORPUS, ids=[c[0] for c in CORPUS])
    def test_iverilog_emitted(self, name, source):
        """Emitted output passes iverilog."""
        emitted = _full_roundtrip(source)
        ok, err = _iverilog_check(emitted)
        assert ok, f"iverilog failed on emitted {name}:\n{err}\n\n--- emitted ---\n{emitted}"


# ---- Round-trip re-parse tests ----


class TestCorpusRoundTrip:
    """Emitted output must re-parse and produce same module structure."""

    @pytest.mark.parametrize("name,source", CORPUS, ids=[c[0] for c in CORPUS])
    def test_reparse(self, name, source):
        """Emitted text re-parses to same module count / names."""
        parser = _get_parser()
        # Pass 1
        tree1 = parser.build_tree(source)
        design1 = tree_to_design(tree1, source_text=source)
        emitted = emit_design(design1)
        # Pass 2
        tree2 = parser.build_tree(emitted)
        design2 = tree_to_design(tree2, source_text=emitted)

        assert len(design1.modules) == len(design2.modules)
        for m1, m2 in zip(design1.modules, design2.modules):
            assert m1.name == m2.name
            assert len(m1.ports) == len(m2.ports)
            assert len(m1.parameters) == len(m2.parameters)


# ---- File-based corpus tests ----


class TestCorpusFiles:
    """Parse existing .v files in the test directory."""

    VERILOG_DIR = Path(__file__).parent.parent / "test_verilog_parser" / "verilog"

    @pytest.fixture(scope="class")
    def verilog_files(self):
        """Collect all .v files."""
        if not self.VERILOG_DIR.exists():
            pytest.skip("No verilog test directory")
        files = list(self.VERILOG_DIR.glob("*.v"))
        if not files:
            pytest.skip("No .v files found")
        return files

    def test_all_files_parse(self, verilog_files):
        """Every .v file in the test corpus must parse."""
        parser = _get_parser()
        for vf in verilog_files:
            source = vf.read_text(encoding="utf-8", errors="replace")
            tree = parser.build_tree(source)
            assert tree is not None, f"Failed to parse {vf.name}"

    def test_all_files_model(self, verilog_files):
        """Every .v file produces a model with modules."""
        parser = _get_parser()
        for vf in verilog_files:
            source = vf.read_text(encoding="utf-8", errors="replace")
            tree = parser.build_tree(source)
            design = tree_to_design(tree, source_file=str(vf), source_text=source)
            assert len(design.modules) >= 1, f"No modules in {vf.name}"

    def test_all_files_emit(self, verilog_files):
        """Every .v file emits valid Verilog that re-parses."""
        parser = _get_parser()
        for vf in verilog_files:
            source = vf.read_text(encoding="utf-8", errors="replace")
            tree = parser.build_tree(source)
            design = tree_to_design(tree, source_file=str(vf), source_text=source)
            emitted = emit_design(design)
            # Must re-parse
            tree2 = parser.build_tree(emitted)
            design2 = tree_to_design(tree2)
            assert len(design.modules) == len(design2.modules), f"Module count mismatch for {vf.name}"

    @pytest.mark.skipif(not IVERILOG_AVAILABLE, reason="iverilog not found")
    def test_all_files_iverilog(self, verilog_files):
        """Emitted output from .v files passes iverilog."""
        parser = _get_parser()
        for vf in verilog_files:
            source = vf.read_text(encoding="utf-8", errors="replace")
            # Check original passes iverilog first
            ok_orig, _ = _iverilog_check(source)
            if not ok_orig:
                continue  # skip files that don't pass iverilog themselves

            tree = parser.build_tree(source)
            design = tree_to_design(tree, source_file=str(vf), source_text=source)
            emitted = emit_design(design)
            ok, err = _iverilog_check(emitted)
            assert ok, f"iverilog failed on emitted {vf.name}:\n{err}"
