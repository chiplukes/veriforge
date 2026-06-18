"""Tests for verilog/traceSignal extended handler helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pygls")

from lsprotocol.types import ExecuteCommandParams
from pygls.protocol.language_server import _prepare_command_arguments
from veriforge_lsp.handlers.extended import (
    _apply_collapse_payload,
    _apply_extract_payload,
    _build_trace,
    _collapse_code_actions,
    _execute_command_payload,
    _extract_code_actions,
    _hierarchy_graph_payload,
    _module_node,
    _preview_collapse_payload,
    _preview_extract_payload,
    _read_preview,
    _width_str,
)
from veriforge_lsp.protocol import path_to_uri
from veriforge_lsp.server import ls
from veriforge.analysis import analyze_design
from veriforge.project import parse_files

TOP_V = """\
module top(
    input  [7:0] x, y,
    output [7:0] result
);
    wire [7:0] add_out;
    adder u_add(.a(x), .b(y), .sum(add_out));
    assign result = add_out;
endmodule
"""

ADDER_V = """\
module adder(
    input  [7:0] a, b,
    output [7:0] sum
);
    assign sum = a + b;
endmodule
"""

CORE_V = """\
module core(
    input clk,
    input [7:0] din,
    output [7:0] dout
);
    assign dout = din;
endmodule
"""

WRAPPER_V = """\
module wrapper(
    input clk,
    input [7:0] data_i,
    output [7:0] data_o
);
    core u_core(.clk(clk), .din(data_i), .dout(data_o));
endmodule
"""

WRAPPER_TOP_V = """\
module top(
    input clk,
    input [7:0] data_i,
    output [7:0] data_o
);
    wrapper u_wrap(.clk(clk), .data_i(data_i), .data_o(data_o));
endmodule
"""

PARAM_CHILD_V = """\
module param_child #(
    parameter WIDTH = 4
)(
    input clk,
    input [WIDTH-1:0] din,
    output reg [WIDTH-1:0] dout
);
    reg [WIDTH-1:0] hold;
    always @(posedge clk) begin
        hold <= din;
        dout <= hold;
    end
endmodule
"""

PARAM_TOP_V = """\
module top(
    input clk,
    input [7:0] data_i,
    output [7:0] data_o
);
    wire [7:0] child_out;
    param_child #(
        .WIDTH(8)
    ) u_child (
        .clk(clk),
        .din(data_i),
        .dout(child_out)
    );
    assign data_o = child_out;
endmodule
"""

EXTRACT_CHAIN_V = """\
module top(
    input [7:0] a,
    input [7:0] b,
    input [7:0] c,
    output [7:0] y
);
    wire [7:0] mid;
    assign mid = a & b;
    assign y = mid | c;
endmodule
"""

EXTRACT_ALWAYS_V = """\
module top(
    input clk,
    input rst,
    input d,
    output reg q
);
    always @(posedge clk) begin
        if (rst) begin
            q <= 1'b0;
        end else begin
            q <= d;
        end
    end
endmodule
"""

CHILD_PULLUP_CHILD_V = """\
module pulse_child(
    input clk,
    input strobe_in,
    output pulse_out
);
    reg toggle;
    always @(posedge clk)
        begin
        toggle <= strobe_in ? ~toggle : toggle;
        end
    assign pulse_out = toggle;
endmodule
"""

CHILD_PULLUP_TOP_A_V = """\
module top_a(
    input clk,
    input strobe_a,
    output pulse_a
);
    pulse_child u_child(
        .clk(clk),
        .strobe_in(strobe_a),
        .pulse_out(pulse_a)
    );
endmodule
"""

CHILD_PULLUP_TOP_B_V = """\
module top_b(
    input clk,
    input strobe_b,
    output pulse_b
);
    pulse_child u_child_b(
        .clk(clk),
        .strobe_in(strobe_b),
        .pulse_out(pulse_b)
    );
endmodule
"""

CHILD_OUTPUT_PULLUP_CHILD_V = """\
module pulse_out_child(
    input clk,
    input strobe_in,
    output reg pulse_out
);
    always @(posedge clk)
        begin
        pulse_out <= strobe_in ? ~pulse_out : pulse_out;
        end
endmodule
"""

CHILD_OUTPUT_PULLUP_TOP_A_V = """\
module top_a(
    input clk,
    input strobe_a,
    output pulse_a
);
    pulse_out_child u_child(
        .clk(clk),
        .strobe_in(strobe_a),
        .pulse_out(pulse_a)
    );
endmodule
"""

CHILD_OUTPUT_PULLUP_TOP_B_V = """\
module top_b(
    input clk,
    input strobe_b,
    output pulse_b
);
    wire pulse_mid;
    pulse_out_child u_child_b(
        .clk(clk),
        .strobe_in(strobe_b),
        .pulse_out(pulse_mid)
    );
    assign pulse_b = pulse_mid;
endmodule
"""

CHILD_ASSIGN_PULLUP_CHILD_V = """\
module assign_child(
    input en,
    input data_in,
    output data_out
);
    wire mid;
    assign mid = en & data_in;
    assign data_out = mid;
endmodule
"""

CHILD_ASSIGN_PULLUP_TOP_A_V = """\
module top_a(
    input en_a,
    input data_a,
    output data_out_a
);
    assign_child u_child(
        .en(en_a),
        .data_in(data_a),
        .data_out(data_out_a)
    );
endmodule
"""

CHILD_ASSIGN_PULLUP_TOP_B_V = """\
module top_b(
    input en_b,
    input data_b,
    output data_out_b
);
    wire data_mid;
    assign_child u_child_b(
        .en(en_b),
        .data_in(data_b),
        .data_out(data_mid)
    );
    assign data_out_b = data_mid;
endmodule
"""

CHILD_ASSIGN_OUTPUT_PULLUP_CHILD_V = """\
module assign_out_child(
    input en,
    input data_in,
    output data_out
);
    assign data_out = en & data_in;
endmodule
"""

CHILD_ASSIGN_OUTPUT_PULLUP_TOP_A_V = """\
module top_a(
    input en_a,
    input data_a,
    output data_out_a
);
    assign_out_child u_child(
        .en(en_a),
        .data_in(data_a),
        .data_out(data_out_a)
    );
endmodule
"""

CHILD_ASSIGN_OUTPUT_PULLUP_TOP_B_V = """\
module top_b(
    input en_b,
    input data_b,
    output data_out_b
);
    wire data_mid;
    assign_out_child u_child_b(
        .en(en_b),
        .data_in(data_b),
        .data_out(data_mid)
    );
    assign data_out_b = data_mid;
endmodule
"""

STRUCT_PULLUP_LEAF_V = """\
module leaf_gate(
    input en,
    input data_in,
    output data_out
);
    assign data_out = en & data_in;
endmodule
"""

CHILD_INSTANCE_PULLUP_CHILD_V = """\
module struct_child(
    input en,
    input data_in,
    output data_out
);
    wire mid;
    leaf_gate u_leaf(
        .en(en),
        .data_in(data_in),
        .data_out(mid)
    );
    assign data_out = mid;
endmodule
"""

CHILD_INSTANCE_PULLUP_TOP_A_V = """\
module top_a(
    input en_a,
    input data_a,
    output data_out_a
);
    struct_child u_child(
        .en(en_a),
        .data_in(data_a),
        .data_out(data_out_a)
    );
endmodule
"""

CHILD_INSTANCE_PULLUP_TOP_B_V = """\
module top_b(
    input en_b,
    input data_b,
    output data_out_b
);
    wire data_mid;
    struct_child u_child_b(
        .en(en_b),
        .data_in(data_b),
        .data_out(data_mid)
    );
    assign data_out_b = data_mid;
endmodule
"""

CHILD_MIXED_PULLUP_CHILD_V = """\
module struct_mixed_child(
    input en,
    input data_in,
    output data_out
);
    wire mid;
    wire masked;
    leaf_gate u_leaf(
        .en(en),
        .data_in(data_in),
        .data_out(mid)
    );
    assign masked = mid & en;
    assign data_out = masked;
endmodule
"""

CHILD_MIXED_PULLUP_TOP_A_V = """\
module top_a(
    input en_a,
    input data_a,
    output data_out_a
);
    struct_mixed_child u_child(
        .en(en_a),
        .data_in(data_a),
        .data_out(data_out_a)
    );
endmodule
"""

CHILD_MIXED_PULLUP_TOP_B_V = """\
module top_b(
    input en_b,
    input data_b,
    output data_out_b
);
    wire data_mid;
    struct_mixed_child u_child_b(
        .en(en_b),
        .data_in(data_b),
        .data_out(data_mid)
    );
    assign data_out_b = data_mid;
endmodule
"""

STRUCT_COMPLEX_PULLUP_LEAF_V = """\
module leaf_pair(
    input en,
    input data_in,
    output [1:0] pair_out
);
    assign pair_out = {en, data_in};
endmodule
"""

CHILD_COMPLEX_INSTANCE_PULLUP_CHILD_V = """\
module complex_child(
    input en,
    input data_in,
    output [1:0] data_out
);
    wire hi;
    wire lo;
    leaf_pair u_leaf(
        .en(en),
        .data_in(data_in),
        .pair_out({hi, lo})
    );
    assign data_out = {hi, lo};
endmodule
"""

CHILD_COMPLEX_INSTANCE_PULLUP_TOP_A_V = """\
module top_a(
    input en_a,
    input data_a,
    output [1:0] data_out_a
);
    complex_child u_child(
        .en(en_a),
        .data_in(data_a),
        .data_out(data_out_a)
    );
endmodule
"""

CHILD_COMPLEX_INSTANCE_PULLUP_TOP_B_V = """\
module top_b(
    input en_b,
    input data_b,
    output [1:0] data_out_b
);
    complex_child u_child_b(
        .en(en_b),
        .data_in(data_b),
        .data_out(data_out_b)
    );
endmodule
"""

CHILD_COMPLEX_MIXED_PULLUP_CHILD_V = """\
module complex_mixed_child(
    input en,
    input data_in,
    output [1:0] data_out
);
    wire [1:0] pair;
    leaf_pair u_leaf(
        .en(en),
        .data_in(data_in),
        .pair_out({pair[1], pair[0]})
    );
    assign data_out = pair;
endmodule
"""

CHILD_COMPLEX_MIXED_PULLUP_TOP_A_V = """\
module top_a(
    input en_a,
    input data_a,
    output [1:0] data_out_a
);
    complex_mixed_child u_child(
        .en(en_a),
        .data_in(data_a),
        .data_out(data_out_a)
    );
endmodule
"""

CHILD_COMPLEX_MIXED_PULLUP_TOP_B_V = """\
module top_b(
    input en_b,
    input data_b,
    output [1:0] data_out_b
);
    complex_mixed_child u_child_b(
        .en(en_b),
        .data_in(data_b),
        .data_out(data_out_b)
    );
endmodule
"""

PARAM_ASSIGN_PULLUP_CHILD_V = """\
module shift_child #(
    parameter SHIFT = 1
) (
    input [3:0] data_in,
    output [3:0] data_out
);
    assign data_out = data_in << SHIFT;
endmodule
"""

PARAM_ASSIGN_PULLUP_TOP_A_V = """\
module top_a(
    input [3:0] data_a,
    output [3:0] data_out_a
);
    shift_child u_child(
        .data_in(data_a),
        .data_out(data_out_a)
    );
endmodule
"""

PARAM_ASSIGN_PULLUP_TOP_B_V = """\
module top_b(
    input [3:0] data_b,
    output [3:0] data_out_b
);
    shift_child #(
        .SHIFT(2)
    ) u_child_b (
        .data_in(data_b),
        .data_out(data_out_b)
    );
endmodule
"""

LOCALPARAM_PROC_PULLUP_CHILD_V = """\
module reset_child(
    input clk,
    input strobe_in,
    output reg pulse_out
);
    localparam RESET_VALUE = 1'b0;
    always @(posedge clk)
        begin
        pulse_out <= strobe_in ? ~pulse_out : RESET_VALUE;
        end
endmodule
"""

LOCALPARAM_PROC_PULLUP_TOP_A_V = """\
module top_a(
    input clk,
    input strobe_a,
    output pulse_a
);
    reset_child u_child(
        .clk(clk),
        .strobe_in(strobe_a),
        .pulse_out(pulse_a)
    );
endmodule
"""

LOCALPARAM_PROC_PULLUP_TOP_B_V = """\
module top_b(
    input clk,
    input strobe_b,
    output pulse_b
);
    wire pulse_mid;
    reset_child u_child_b(
        .clk(clk),
        .strobe_in(strobe_b),
        .pulse_out(pulse_mid)
    );
    assign pulse_b = pulse_mid;
endmodule
"""

FUNCTION_PROC_PULLUP_CHILD_V = """\
module func_child(
    input clk,
    input strobe_in,
    output reg pulse_out
);
    function calc_next;
        input current;
        input strobe;
        begin
            calc_next = strobe ? ~current : current;
        end
    endfunction
    always @(posedge clk) pulse_out <= calc_next(pulse_out, strobe_in);
endmodule
"""

FUNCTION_PROC_PULLUP_TOP_A_V = """\
module top_a(
    input clk,
    input strobe_a,
    output pulse_a
);
    func_child u_child(
        .clk(clk),
        .strobe_in(strobe_a),
        .pulse_out(pulse_a)
    );
endmodule
"""

FUNCTION_PROC_PULLUP_TOP_B_V = """\
module top_b(
    input clk,
    input strobe_b,
    output pulse_b
);
    wire pulse_mid;
    func_child u_child_b(
        .clk(clk),
        .strobe_in(strobe_b),
        .pulse_out(pulse_mid)
    );
    assign pulse_b = pulse_mid;
endmodule
"""

LOCAL_FUNCTION_PULLUP_CHILD_V = """\
module local_func_child(
    input [3:0] data_in,
    output [3:0] data_out
);
    function [3:0] add_one;
        input [3:0] value;
        begin
            add_one = value + 1'b1;
        end
    endfunction
    assign data_out = add_one(data_in);
endmodule
"""

LOCAL_FUNCTION_PULLUP_TOP_V = """\
module top(
    input [3:0] data_in,
    output [3:0] data_out
);
    local_func_child u_child(
        .data_in(data_in),
        .data_out(data_out)
    );
endmodule
"""

GENERATE_TOLERANT_PULLUP_CHILD_V = """\
module gen_child(
    input en,
    input data_in,
    output data_out
);
    wire mid;
    generate
        if (1) begin : keep_logic
            wire keep_tap;
            assign keep_tap = data_in;
        end
    endgenerate
    assign mid = en & data_in;
    assign data_out = mid;
endmodule
"""

GENERATE_TOLERANT_PULLUP_TOP_A_V = """\
module top_a(
    input en_a,
    input data_a,
    output data_out_a
);
    gen_child u_child(
        .en(en_a),
        .data_in(data_a),
        .data_out(data_out_a)
    );
endmodule
"""

GENERATE_TOLERANT_PULLUP_TOP_B_V = """\
module top_b(
    input en_b,
    input data_b,
    output data_out_b
);
    wire data_mid;
    gen_child u_child_b(
        .en(en_b),
        .data_in(data_b),
        .data_out(data_mid)
    );
    assign data_out_b = data_mid;
endmodule
"""

GENERATE_SITE_PULLUP_CHILD_V = """\
module gen_site_child(
    input en,
    input data_in,
    output data_out
);
    assign data_out = en & data_in;
endmodule
"""

GENERATE_SITE_PULLUP_TOP_A_V = """\
module top_a(
    input en_a,
    input data_a,
    output data_out_a
);
    gen_site_child u_child(
        .en(en_a),
        .data_in(data_a),
        .data_out(data_out_a)
    );
endmodule
"""

GENERATE_SITE_PULLUP_TOP_B_V = """\
module top_b(
    input en_b,
    input data_b,
    output data_out_b
);
    generate
        if (1) begin : g_child
            gen_site_child u_child_b(
                .en(en_b),
                .data_in(data_b),
                .data_out(data_out_b)
            );
        end
    endgenerate
endmodule
"""

CHILD_GENERATE_ASSIGN_PULLUP_CHILD_V = """\
module gen_inner_child(
    input en,
    input data_in,
    output data_out
);
    generate
        if (1) begin : g_logic
            assign data_out = en & data_in;
        end
    endgenerate
endmodule
"""

CHILD_GENERATE_ASSIGN_PULLUP_TOP_A_V = """\
module top_a(
    input en_a,
    input data_a,
    output data_out_a
);
    gen_inner_child u_child(
        .en(en_a),
        .data_in(data_a),
        .data_out(data_out_a)
    );
endmodule
"""

CHILD_GENERATE_ASSIGN_PULLUP_TOP_B_V = """\
module top_b(
    input en_b,
    input data_b,
    output data_out_b
);
    generate
        if (1) begin : g_child
            gen_inner_child u_child_b(
                .en(en_b),
                .data_in(data_b),
                .data_out(data_out_b)
            );
        end
    endgenerate
endmodule
"""

CHILD_GENERATE_PROC_PULLUP_CHILD_V = """\
module gen_proc_child(
    input clk,
    input strobe_in,
    output reg pulse_out
);
    generate
        if (1) begin : g_logic
            always @(posedge clk) pulse_out <= strobe_in ? ~pulse_out : pulse_out;
        end
    endgenerate
endmodule
"""

CHILD_GENERATE_PROC_PULLUP_TOP_A_V = """\
module top_a(
    input clk,
    input strobe_a,
    output pulse_a
);
    gen_proc_child u_child(
        .clk(clk),
        .strobe_in(strobe_a),
        .pulse_out(pulse_a)
    );
endmodule
"""

CHILD_GENERATE_PROC_PULLUP_TOP_B_V = """\
module top_b(
    input clk,
    input strobe_b,
    output pulse_b
);
    wire pulse_mid;
    generate
        if (1) begin : g_child
            gen_proc_child u_child_b(
                .clk(clk),
                .strobe_in(strobe_b),
                .pulse_out(pulse_mid)
            );
        end
    endgenerate
    assign pulse_b = pulse_mid;
endmodule
"""

UNCONNECTED_PORT_PULLUP_CHILD_V = """\
module async_like_child(
    input clk,
    input async_vector_in,
    output reg sync_vector_out,
    output changing
);
    always @(posedge clk)
        sync_vector_out <= async_vector_in;
    assign changing = sync_vector_out;
endmodule
"""

UNCONNECTED_PORT_PULLUP_TOP_A_V = """\
module top_a(
    input clk,
    input async_a,
    output sync_a,
    output changing_a
);
    async_like_child u_child(
        .clk(clk),
        .async_vector_in(async_a),
        .sync_vector_out(sync_a),
        .changing(changing_a)
    );
endmodule
"""

UNCONNECTED_PORT_PULLUP_TOP_B_V = """\
module top_b(
    input clk,
    input async_b,
    output sync_b
);
    async_like_child u_child_b(
        .clk(clk),
        .async_vector_in(async_b),
        .sync_vector_out(sync_b),
        .changing()
    );
endmodule
"""


@pytest.fixture(scope="module")
def two_file_design(tmp_path_factory):
    d = tmp_path_factory.mktemp("trace")
    top_path = d / "top.v"
    adder_path = d / "adder.v"
    top_path.write_text(TOP_V, encoding="utf-8")
    adder_path.write_text(ADDER_V, encoding="utf-8")
    design = parse_files([str(top_path), str(adder_path)])
    return design, str(top_path), str(adder_path)


@pytest.fixture
def wrapper_design(tmp_path):
    top_path = tmp_path / "top.v"
    wrapper_path = tmp_path / "wrapper.v"
    core_path = tmp_path / "core.v"
    top_path.write_text(WRAPPER_TOP_V, encoding="utf-8")
    wrapper_path.write_text(WRAPPER_V, encoding="utf-8")
    core_path.write_text(CORE_V, encoding="utf-8")
    design = parse_files([str(top_path), str(wrapper_path), str(core_path)])
    return design, str(top_path)


@pytest.fixture
def output_reg_pull_up_design(tmp_path):
    top_path = tmp_path / "top.v"
    child_path = tmp_path / "param_child.v"
    top_path.write_text(PARAM_TOP_V, encoding="utf-8")
    child_path.write_text(PARAM_CHILD_V, encoding="utf-8")
    design = parse_files([str(top_path), str(child_path)])
    return design, str(top_path)


@pytest.fixture
def extract_design(tmp_path):
    top_path = tmp_path / "top.v"
    top_path.write_text(EXTRACT_CHAIN_V, encoding="utf-8")
    design = parse_files([str(top_path)])
    return design, str(top_path)


@pytest.fixture
def extract_always_design(tmp_path):
    top_path = tmp_path / "top.v"
    top_path.write_text(EXTRACT_ALWAYS_V, encoding="utf-8")
    design = parse_files([str(top_path)])
    return design, str(top_path)


@pytest.fixture
def child_logic_pull_up_design(tmp_path):
    child_path = tmp_path / "pulse_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_output_logic_pull_up_design(tmp_path):
    child_path = tmp_path / "pulse_out_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_OUTPUT_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_OUTPUT_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_OUTPUT_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_assign_logic_pull_up_design(tmp_path):
    child_path = tmp_path / "assign_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_ASSIGN_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_ASSIGN_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_ASSIGN_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_assign_output_logic_pull_up_design(tmp_path):
    child_path = tmp_path / "assign_out_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_ASSIGN_OUTPUT_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_ASSIGN_OUTPUT_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_ASSIGN_OUTPUT_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_instance_logic_pull_up_design(tmp_path):
    leaf_path = tmp_path / "leaf_gate.v"
    child_path = tmp_path / "struct_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    leaf_path.write_text(STRUCT_PULLUP_LEAF_V, encoding="utf-8")
    child_path.write_text(CHILD_INSTANCE_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_INSTANCE_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_INSTANCE_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(leaf_path), str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_mixed_logic_pull_up_design(tmp_path):
    leaf_path = tmp_path / "leaf_gate.v"
    child_path = tmp_path / "struct_mixed_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    leaf_path.write_text(STRUCT_PULLUP_LEAF_V, encoding="utf-8")
    child_path.write_text(CHILD_MIXED_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_MIXED_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_MIXED_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(leaf_path), str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_complex_instance_logic_pull_up_design(tmp_path):
    leaf_path = tmp_path / "leaf_pair.v"
    child_path = tmp_path / "complex_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    leaf_path.write_text(STRUCT_COMPLEX_PULLUP_LEAF_V, encoding="utf-8")
    child_path.write_text(CHILD_COMPLEX_INSTANCE_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_COMPLEX_INSTANCE_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_COMPLEX_INSTANCE_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(leaf_path), str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_complex_mixed_logic_pull_up_design(tmp_path):
    leaf_path = tmp_path / "leaf_pair.v"
    child_path = tmp_path / "complex_mixed_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    leaf_path.write_text(STRUCT_COMPLEX_PULLUP_LEAF_V, encoding="utf-8")
    child_path.write_text(CHILD_COMPLEX_MIXED_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_COMPLEX_MIXED_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_COMPLEX_MIXED_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(leaf_path), str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_selected_parameter_pull_up_design(tmp_path):
    child_path = tmp_path / "shift_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(PARAM_ASSIGN_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(PARAM_ASSIGN_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(PARAM_ASSIGN_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_selected_localparam_pull_up_design(tmp_path):
    child_path = tmp_path / "reset_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(LOCALPARAM_PROC_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(LOCALPARAM_PROC_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(LOCALPARAM_PROC_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_function_logic_pull_up_design(tmp_path):
    child_path = tmp_path / "func_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(FUNCTION_PROC_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(FUNCTION_PROC_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(FUNCTION_PROC_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def local_function_pull_up_design(tmp_path):
    top_path = tmp_path / "top.v"
    child_path = tmp_path / "local_func_child.v"
    top_path.write_text(LOCAL_FUNCTION_PULLUP_TOP_V, encoding="utf-8")
    child_path.write_text(LOCAL_FUNCTION_PULLUP_CHILD_V, encoding="utf-8")
    design = parse_files([str(top_path), str(child_path)])
    return design, str(top_path)


@pytest.fixture
def child_generate_tolerant_pull_up_design(tmp_path):
    child_path = tmp_path / "gen_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(GENERATE_TOLERANT_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(GENERATE_TOLERANT_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(GENERATE_TOLERANT_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_generate_site_pull_up_design(tmp_path):
    child_path = tmp_path / "gen_site_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(GENERATE_SITE_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(GENERATE_SITE_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(GENERATE_SITE_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_generate_assign_pull_up_design(tmp_path):
    child_path = tmp_path / "gen_inner_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_GENERATE_ASSIGN_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_GENERATE_ASSIGN_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_GENERATE_ASSIGN_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_generate_proc_pull_up_design(tmp_path):
    child_path = tmp_path / "gen_proc_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_GENERATE_PROC_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_GENERATE_PROC_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_GENERATE_PROC_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


@pytest.fixture
def child_unconnected_port_pull_up_design(tmp_path):
    child_path = tmp_path / "async_like_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(UNCONNECTED_PORT_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(UNCONNECTED_PORT_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(UNCONNECTED_PORT_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    return design, str(child_path), str(top_a_path), str(top_b_path)


def _line_containing(path: str, needle: str) -> int:
    for index, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if needle in line:
            return index
    raise AssertionError(f"{needle!r} not found in {path}")


def _last_line_containing(path: str, needle: str) -> int:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    for index in range(len(lines), 0, -1):
        if needle in lines[index - 1]:
            return index
    raise AssertionError(f"{needle!r} not found in {path}")


class _FakeWorkspace:
    def __init__(self, design, *, top_module="top", stale_files=()):
        self.design = design
        self.top_module = top_module
        self._stale_files = set(stale_files)

    def get_hierarchy_roots(self):
        top = self.design.get_module(self.top_module)
        return [top] if top is not None else []

    def is_stale(self, path):
        return path in self._stale_files

    def parse_workspace_async(self):
        return None


class TestBuildTrace:
    def test_trace_returns_signal_info(self, two_file_design):
        design, top_path, _ = two_file_design
        top_mod = next(m for m in design.modules if m.name == "top")
        net = next((n for n in (top_mod.nets or []) if n.name == "add_out"), None)
        if net is None:
            pytest.skip("add_out net not found in parsed design")

        result = _build_trace(net, design, top_path)
        assert result["signal"]["name"] == "add_out"
        assert "drivers" in result
        assert "loads" in result

    def test_trace_result_has_file_field(self, two_file_design):
        design, top_path, _ = two_file_design
        top_mod = next(m for m in design.modules if m.name == "top")
        net = next((n for n in (top_mod.nets or []) if n.name == "add_out"), None)
        if net is None:
            pytest.skip("add_out net not found")

        result = _build_trace(net, design, top_path)
        sig = result["signal"]
        assert "file" in sig
        assert sig["file"].startswith("file")


class TestWidthStr:
    def test_empty_when_no_width(self):
        node = MagicMock(spec=[])
        assert _width_str(node) == ""

    def test_returns_bracket_notation(self):
        rng = MagicMock(msb=7, lsb=0)
        node = MagicMock(width=rng)
        assert _width_str(node) == "[7:0]"


class TestReadPreview:
    def test_returns_lines_around_target(self, tmp_path):
        f = tmp_path / "test.v"
        f.write_text("\n".join(f"line{i}" for i in range(20)), encoding="utf-8")
        preview = _read_preview(str(f), 10)  # lark 1-based line 10
        assert "line9" in preview

    def test_missing_file_returns_empty(self):
        assert _read_preview("/nonexistent/file.v", 1) == ""


class TestHierarchyBuilders:
    def test_module_node_has_name(self, two_file_design):
        design, _, _ = two_file_design
        top = next(m for m in design.modules if m.name == "top")
        node = _module_node(top, design, depth=1)
        assert node["name"] == "top"
        assert "children" in node

    def test_module_node_children_depth_zero(self, two_file_design):
        design, _, _ = two_file_design
        top = next(m for m in design.modules if m.name == "top")
        node = _module_node(top, design, depth=0)
        assert node["children"] == []
        assert node["hasMoreChildren"] is True

    def test_instance_node_has_refactor_metadata(self, wrapper_design):
        design, _ = wrapper_design
        top = design.get_module("top")
        node = _module_node(top, design, depth=1)
        child = node["children"][0]
        assert child["instancePath"] == "top/u_wrap"
        assert child["wrapperClass"] == "pure_pass_through"
        assert "previewCollapse" in child["refactorActions"]

    def test_hierarchy_graph_payload_returns_visualization(self, wrapper_design):
        design, _ = wrapper_design
        payload = _hierarchy_graph_payload(_FakeWorkspace(design), {"top": "top", "format": "mermaid"})
        assert payload["ok"] is True
        assert payload["visualization"].startswith("flowchart TD")
        assert payload["hierarchyGraph"]["wrappers"][0]["instancePath"] == "top/u_wrap"

    def test_execute_command_dispatches_hierarchy_pull_up_preview(self, wrapper_design):
        design, _ = wrapper_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyPullUp",
            [{"selection": {"kind": "instance", "instancePath": "top/u_wrap"}, "targetParentPath": "top"}],
        )

        assert payload["ok"] is True
        assert payload["preview"]["applyReady"] is True
        assert "edit" in payload
        assert "review" in payload
        assert payload["preview"]["targetParentPath"] == "top"
        assert payload["preview"]["source"]["instancePath"] == "top/u_wrap"
        assert payload["preview"]["afterHierarchy"]["mergedIntoPath"] == "top"

    def test_hierarchy_pull_up_review_applies_same_file_multi_edit(self, output_reg_pull_up_design):
        design, top_path = output_reg_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyPullUp",
            [{"selection": {"kind": "instance", "instancePath": "top/u_child"}, "targetParentPath": "top"}],
        )

        top_uri = path_to_uri(top_path)
        assert payload["ok"] is True
        assert payload["preview"]["applyReady"] is True
        assert len(payload["edit"]["changes"][top_uri]) == 2
        review_files = payload["review"]["files"]
        assert payload["review"]["atomic"] is True
        assert payload["review"]["applyStrategy"] == "workspace-edit"
        assert payload["review"]["fileCount"] == 1
        assert len(review_files) == 1
        assert review_files[0]["uri"] == top_uri
        assert review_files[0]["presentationOnly"] is True
        assert review_files[0]["acceptsWholeEdit"] is True
        assert "param_child #(" in review_files[0]["currentText"]
        assert "param_child #(" not in review_files[0]["proposedText"]
        assert "reg [7:0] child_out;" in review_files[0]["proposedText"]
        assert "child_out <= u_child__hold;" in review_files[0]["proposedText"]

    def test_execute_command_dispatches_hierarchy_push_down_preview(self, wrapper_design):
        design, _ = wrapper_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyPushDown",
            [
                {
                    "selection": {"kind": "module", "moduleName": "top"},
                    "newModuleName": "top_partition",
                    "newInstanceName": "u_partition",
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["applyReady"] is True
        assert "edit" in payload
        assert "review" in payload
        assert payload["preview"]["afterHierarchy"] == {
            "createdModule": "top_partition",
            "createdInstance": "u_partition",
            "rewrittenModule": "top",
        }

    def test_execute_command_blocks_push_down_without_new_module_name(self, wrapper_design):
        design, _ = wrapper_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyPushDown",
            [{"selection": {"kind": "module", "moduleName": "top"}}],
        )

        assert payload["ok"] is False
        assert payload["diagnostics"][0]["code"] == "new-module-name-required"

    def test_push_down_routes_range_selection_to_extract(self, extract_design):
        design, top_path = extract_design
        child_path = str(Path(top_path).with_name("extracted_logic.v"))

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyPushDown",
            [
                {
                    "selection": {
                        "kind": "range",
                        "moduleName": "top",
                        "textDocument": {"uri": path_to_uri(top_path)},
                        "range": {"start": {"line": 7, "character": 0}, "end": {"line": 8, "character": 30}},
                    },
                    "newModuleName": "extracted_logic",
                    "newInstanceName": "u_extracted_logic",
                }
            ],
        )

        assert payload["ok"] is True
        preview = payload["preview"]
        assert preview["metadata"]["pushDownMode"] == "range"
        assert preview["metadata"]["origin"] == "extract"
        assert preview["boundary"] == {"inputs": ["a", "b", "c"], "outputs": ["y"], "internals": ["mid"]}
        assert path_to_uri(top_path) in payload["edit"]["changes"]
        assert path_to_uri(child_path) in payload["edit"]["changes"]
        top_edits = payload["edit"]["changes"][path_to_uri(top_path)]
        assert "extracted_logic u_extracted_logic" in top_edits[0]["newText"]

    def test_push_down_range_blocks_target_parent_path(self, extract_design):
        design, top_path = extract_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyPushDown",
            [
                {
                    "selection": {
                        "kind": "range",
                        "moduleName": "top",
                        "textDocument": {"uri": path_to_uri(top_path)},
                        "range": {"start": {"line": 7, "character": 0}, "end": {"line": 8, "character": 30}},
                    },
                    "newModuleName": "extracted_logic",
                    "targetParentPath": "top",
                }
            ],
        )

        assert payload["ok"] is False
        assert payload["diagnostics"][0]["code"] == "push-down-target-not-supported"

    def test_push_down_routes_range_via_start_end_line_fields(self, extract_design):
        design, top_path = extract_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyPushDown",
            [
                {
                    "selection": {
                        "kind": "range",
                        "moduleName": "top",
                        "file": top_path,
                        "startLine": 8,
                        "endLine": 9,
                    },
                    "newModuleName": "extracted_logic",
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["metadata"]["pushDownMode"] == "range"

    def test_pygls_execute_command_argument_preparation_accepts_refactor_commands(self):
        commands = ls.protocol.fm.commands
        command_requests = {
            "verilog/hierarchyGraph": [{"format": "json"}],
            "verilog/previewCollapseHierarchy": [{"instancePath": "top/u_wrap"}],
            "verilog/applyCollapseHierarchy": [{"instancePath": "top/u_wrap"}],
            "verilog/previewExtractModule": [{"textDocument": {"uri": "file:///top.v"}}],
            "verilog/applyExtractModule": [{"textDocument": {"uri": "file:///top.v"}}],
            "verilog/previewHierarchyPullUp": [{"selection": {"kind": "instance", "instancePath": "top/u_wrap"}}],
            "verilog/previewHierarchyPushDown": [
                {"selection": {"kind": "module", "moduleName": "top"}, "newModuleName": "top_partition"}
            ],
        }

        for command, arguments in command_requests.items():
            assert command in commands
            args, kwargs = _prepare_command_arguments(
                commands[command],
                ExecuteCommandParams(command=command, arguments=arguments),
                ls.protocol._converter,
            )
            assert args == (arguments[0],)
            assert kwargs == {}

        assert "verilog.reparse" in commands
        args, kwargs = _prepare_command_arguments(
            commands["verilog.reparse"],
            ExecuteCommandParams(command="verilog.reparse", arguments=[]),
            ls.protocol._converter,
        )
        assert args == ()
        assert kwargs == {}

    def test_preview_collapse_payload_includes_workspace_edit(self, wrapper_design):
        design, top_path = wrapper_design
        payload = _preview_collapse_payload(_FakeWorkspace(design), {"instancePath": "top/u_wrap"})
        edit = payload["edit"]
        top_edits = edit["changes"][path_to_uri(top_path)]
        review_file = payload["review"]["files"][0]
        assert payload["ok"] is True
        assert "core u_wrap__u_core" in top_edits[0]["newText"]
        assert review_file["uri"] == path_to_uri(top_path)
        assert "wrapper u_wrap" in review_file["currentText"]
        assert "core u_wrap__u_core" in review_file["proposedText"]

    def test_apply_collapse_payload_returns_workspace_edit_without_writing(self, wrapper_design):
        design, top_path = wrapper_design
        before = Path(top_path).read_text(encoding="utf-8")
        payload = _apply_collapse_payload(_FakeWorkspace(design), {"instancePath": "top/u_wrap"})
        after = Path(top_path).read_text(encoding="utf-8")
        assert payload["ok"] is True
        assert payload["appliedByServer"] is False
        assert "edit" in payload
        assert after == before

    def test_apply_collapse_payload_rejects_stale_source(self, wrapper_design):
        design, top_path = wrapper_design
        payload = _apply_collapse_payload(
            _FakeWorkspace(design, stale_files={top_path}), {"instancePath": "top/u_wrap"}
        )
        assert payload["ok"] is False
        assert payload["preview"]["ok"] is False
        assert payload["preview"]["diagnostics"][-1]["code"] == "stale-source"
        assert "edit" not in payload

    def test_code_actions_expose_collapse_commands_for_safe_wrapper(self, wrapper_design):
        design, top_path = wrapper_design
        top = design.get_module("top")
        instance = top.instances[0]
        params = {
            "textDocument": {"uri": path_to_uri(top_path)},
            "range": {
                "start": {
                    "line": instance.loc.line - 1,
                    "character": instance.loc.column - 1,
                }
            },
        }

        actions = _collapse_code_actions(_FakeWorkspace(design), params)

        commands = [action["command"]["command"] for action in actions]
        assert commands == ["verilog/previewHierarchyBoundaryMove", "verilog/applyHierarchyBoundaryMove"]
        assert actions[0]["command"]["arguments"] == [
            {"direction": "collapse", "selection": {"kind": "instance", "instancePath": "top/u_wrap"}}
        ]

    def test_preview_extract_payload_includes_workspace_edit(self, extract_design):
        design, top_path = extract_design
        child_path = str(Path(top_path).with_name("extracted_logic.v"))
        payload = _preview_extract_payload(
            _FakeWorkspace(design),
            {
                "textDocument": {"uri": path_to_uri(top_path)},
                "range": {"start": {"line": 7, "character": 0}, "end": {"line": 8, "character": 30}},
                "extractedModuleName": "extracted_logic",
            },
        )

        top_edits = payload["edit"]["changes"][path_to_uri(top_path)]
        child_edits = payload["edit"]["changes"][path_to_uri(child_path)]
        review_files = {item["file"]: item for item in payload["review"]["files"]}
        preview = payload["preview"]
        presentation = preview["presentation"]
        sections = {section["kind"]: section for section in presentation["sections"]}
        assert payload["ok"] is True
        assert preview["boundary"] == {"inputs": ["a", "b", "c"], "outputs": ["y"], "internals": ["mid"]}
        assert "extracted_logic u_extracted_logic" in top_edits[0]["newText"]
        assert child_edits[0]["newText"].startswith("module extracted_logic")
        assert "assign mid = a & b;" in presentation["selectionText"]
        assert "extracted_logic u_extracted_logic" in presentation["replacementText"]
        assert any("continuous_assign: mid" in line for line in presentation["normalizedLines"])
        assert presentation["boundaryLines"] == ["Inputs: a, b, c", "Outputs: y", "Internals: mid"]
        assert "module extracted_logic" in presentation["generatedModuleText"]
        assert "---" in presentation["diffText"]
        assert "Selected source" == sections["selected-source"]["title"]
        assert "assign y = mid | c;" in sections["selected-source"]["text"]
        assert "Boundary ports" == sections["boundary"]["title"]
        assert "Inputs: a, b, c" in sections["boundary"]["text"]
        assert "Generated module" in sections["generated-module"]["title"]
        assert "Parent replacement" == sections["parent-replacement"]["title"]
        assert review_files[top_path]["uri"] == path_to_uri(top_path)
        assert "assign mid = a & b;" in review_files[top_path]["currentText"]
        assert "extracted_logic u_extracted_logic" in review_files[top_path]["proposedText"]
        assert review_files[child_path]["currentText"] == ""
        assert "module extracted_logic" in review_files[child_path]["proposedText"]

    def test_apply_extract_payload_returns_workspace_edit_without_writing(self, extract_design):
        design, top_path = extract_design
        before = Path(top_path).read_text(encoding="utf-8")

        payload = _apply_extract_payload(
            _FakeWorkspace(design),
            {
                "textDocument": {"uri": path_to_uri(top_path)},
                "range": {"start": {"line": 7, "character": 0}, "end": {"line": 8, "character": 30}},
                "extractedModuleName": "extracted_logic",
            },
        )

        assert payload["ok"] is True
        assert payload["appliedByServer"] is False
        assert "edit" in payload
        assert Path(top_path).read_text(encoding="utf-8") == before

    def test_extract_payload_rejects_stale_source(self, extract_design):
        design, top_path = extract_design

        payload = _preview_extract_payload(
            _FakeWorkspace(design, stale_files={top_path}),
            {
                "textDocument": {"uri": path_to_uri(top_path)},
                "range": {"start": {"line": 7, "character": 0}, "end": {"line": 8, "character": 30}},
                "extractedModuleName": "extracted_logic",
            },
        )

        assert payload["ok"] is False
        assert payload["preview"]["ok"] is False
        assert payload["preview"]["diagnostics"][-1]["code"] == "stale-source"
        assert "edit" not in payload

    def test_execute_command_dispatches_extract_always_selection(self, extract_always_design):
        design, top_path = extract_always_design
        child_uri = path_to_uri(str(Path(top_path).with_name("extracted_logic.v")))

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewExtractModule",
            [
                {
                    "textDocument": {"uri": path_to_uri(top_path)},
                    "range": {"start": {"line": 6, "character": 0}, "end": {"line": 12, "character": 7}},
                    "extractedModuleName": "extracted_logic",
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["boundary"] == {"inputs": ["clk", "rst", "d"], "outputs": ["q"], "internals": []}
        assert "always @(posedge clk)" in payload["preview"]["generatedModule"]
        assert any("always_block: sequential" in line for line in payload["preview"]["presentation"]["normalizedLines"])
        assert payload["preview"]["presentation"]["boundaryLines"] == [
            "Inputs: clk, rst, d",
            "Outputs: q",
            "Internals: -",
        ]
        assert "edit" in payload
        assert child_uri in payload["edit"]["changes"]

    def test_preview_extract_payload_includes_presentation_on_blocked_preview(self, extract_design):
        design, top_path = extract_design

        payload = _preview_extract_payload(
            _FakeWorkspace(design, stale_files={top_path}),
            {
                "textDocument": {"uri": path_to_uri(top_path)},
                "range": {"start": {"line": 7, "character": 0}, "end": {"line": 8, "character": 30}},
                "extractedModuleName": "extracted_logic",
            },
        )

        presentation = payload["preview"]["presentation"]
        sections = {section["kind"]: section for section in presentation["sections"]}
        assert payload["ok"] is False
        assert "assign mid = a & b;" in presentation["selectionText"]
        assert "continuous_assign: mid" in "\n".join(presentation["normalizedLines"])
        assert "[ERROR] stale-source:" in sections["diagnostics"]["text"]

    def test_preview_extract_payload_includes_selection_suggestions_for_partial_overlap(self, extract_always_design):
        design, top_path = extract_always_design

        payload = _preview_extract_payload(
            _FakeWorkspace(design),
            {
                "textDocument": {"uri": path_to_uri(top_path)},
                "range": {"start": {"line": 6, "character": 0}, "end": {"line": 6, "character": 30}},
                "extractedModuleName": "extracted_logic",
            },
        )

        preview = payload["preview"]
        normalization = preview["metadata"]["selectionNormalization"]
        suggestions = normalization["suggestions"]
        assert payload["ok"] is False
        assert any(d["code"] == "partial-selection" for d in preview["diagnostics"])
        assert any(s["kind"] == "expand-to-node" for s in suggestions)
        expand = next(s for s in suggestions if s["kind"] == "expand-to-node")
        assert expand["startLine"] == 7
        assert expand["endLine"] == 13
        assert expand["nodeKind"] == "always_block"
        presentation = preview["presentation"]
        sections = {section["kind"]: section for section in presentation["sections"]}
        assert "selection-suggestions" in sections
        assert "(lines 7-13)" in sections["selection-suggestions"]["text"]
        assert any("(lines 7-13)" in line for line in presentation["suggestionLines"])

    def test_execute_command_dispatches_extract_preview(self, extract_design):
        design, top_path = extract_design
        child_uri = path_to_uri(str(Path(top_path).with_name("extracted_logic.v")))

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewExtractModule",
            [
                {
                    "textDocument": {"uri": path_to_uri(top_path)},
                    "range": {"start": {"line": 7, "character": 0}, "end": {"line": 8, "character": 30}},
                    "extractedModuleName": "extracted_logic",
                }
            ],
        )

        assert payload["ok"] is True
        assert "edit" in payload
        assert child_uri in payload["edit"]["changes"]

    def test_code_actions_expose_extract_commands_for_selection(self, extract_design):
        _design, top_path = extract_design
        params = {
            "textDocument": {"uri": path_to_uri(top_path)},
            "range": {"start": {"line": 7, "character": 0}, "end": {"line": 8, "character": 30}},
        }

        actions = _extract_code_actions(_FakeWorkspace(_design), params)

        commands = [action["command"]["command"] for action in actions]
        assert commands == ["verilog/previewHierarchyBoundaryMove", "verilog/applyHierarchyBoundaryMove"]
        assert actions[0]["command"]["arguments"][0]["direction"] == "extract"
        assert actions[0]["command"]["arguments"][0]["extractedModuleName"] == "extracted_logic"

    def test_legacy_hierarchy_commands_log_deprecation_warning(self, wrapper_design, extract_design, caplog):
        wrapper_design_obj, _wrapper_top_path = wrapper_design
        _extract_design_obj, extract_top_path = extract_design
        caplog.set_level(logging.WARNING, logger="veriforge_lsp.handlers.extended")
        command_requests = {
            "verilog/previewCollapseHierarchy": (
                _FakeWorkspace(wrapper_design_obj),
                [{"instancePath": "top/u_wrap"}],
                "verilog/previewHierarchyBoundaryMove (direction='collapse')",
            ),
            "verilog/applyCollapseHierarchy": (
                _FakeWorkspace(wrapper_design_obj),
                [{"instancePath": "top/u_wrap"}],
                "verilog/applyHierarchyBoundaryMove (direction='collapse')",
            ),
            "verilog/previewExtractModule": (
                _FakeWorkspace(_extract_design_obj),
                [
                    {
                        "textDocument": {"uri": path_to_uri(extract_top_path)},
                        "range": {"start": {"line": 7, "character": 0}, "end": {"line": 8, "character": 30}},
                        "extractedModuleName": "extracted_logic",
                    }
                ],
                "verilog/previewHierarchyBoundaryMove (direction='extract')",
            ),
            "verilog/applyExtractModule": (
                _FakeWorkspace(_extract_design_obj),
                [
                    {
                        "textDocument": {"uri": path_to_uri(extract_top_path)},
                        "range": {"start": {"line": 7, "character": 0}, "end": {"line": 8, "character": 30}},
                        "extractedModuleName": "extracted_logic",
                    }
                ],
                "verilog/applyHierarchyBoundaryMove (direction='extract')",
            ),
            "verilog/previewHierarchyPullUp": (
                _FakeWorkspace(wrapper_design_obj),
                [{"selection": {"kind": "instance", "instancePath": "top/u_wrap"}, "targetParentPath": "top"}],
                "verilog/previewHierarchyBoundaryMove (direction='pull_up')",
            ),
            "verilog/previewHierarchyPushDown": (
                _FakeWorkspace(wrapper_design_obj),
                [{"selection": {"kind": "module", "moduleName": "top"}, "newModuleName": "top_partition"}],
                "verilog/previewHierarchyBoundaryMove (direction='push_down')",
            ),
        }

        for command, (workspace, arguments, replacement) in command_requests.items():
            caplog.clear()
            _execute_command_payload(workspace, command, arguments)
            assert any(
                record.message == f"veriforge-lsp: deprecated command {command} invoked; use {replacement} instead"
                for record in caplog.records
            )


class TestPreviewExtractTraceNeighborhood:
    def test_signal_selection_matches_range_mode_items(self, extract_design):
        design, top_path = extract_design
        analyze_design(design)

        signal_payload = _preview_extract_payload(
            _FakeWorkspace(design),
            {
                "selection": {"signal": "mid", "module": "top"},
                "extractedModuleName": "extracted_via_signal",
            },
        )
        range_payload = _preview_extract_payload(
            _FakeWorkspace(design),
            {
                "textDocument": {"uri": path_to_uri(top_path)},
                "range": {"start": {"line": 7, "character": 0}, "end": {"line": 8, "character": 30}},
                "extractedModuleName": "extracted_via_range",
            },
        )

        assert signal_payload["ok"] is True
        assert range_payload["ok"] is True
        signal_items = {
            (i["kind"], i["name"]) for i in signal_payload["preview"]["metadata"]["selectionNormalization"]["items"]
        }
        range_items = {
            (i["kind"], i["name"]) for i in range_payload["preview"]["metadata"]["selectionNormalization"]["items"]
        }
        assert signal_items == range_items == {("continuous_assign", "mid"), ("continuous_assign", "y")}
        assert signal_payload["preview"]["selection"]["signal"] == "mid"
        assert signal_payload["preview"]["selection"]["signalModule"] == "top"

    def test_signal_selection_unknown_signal(self, extract_design):
        design, _top_path = extract_design
        analyze_design(design)

        payload = _preview_extract_payload(
            _FakeWorkspace(design),
            {
                "selection": {"signal": "nope", "module": "top"},
                "extractedModuleName": "extracted",
            },
        )

        assert payload["ok"] is False
        codes = [d["code"] for d in payload["preview"]["diagnostics"]]
        assert "unknown-trace-signal" in codes

    def test_signal_selection_module_mismatch(self, extract_design):
        design, _top_path = extract_design
        analyze_design(design)

        payload = _preview_extract_payload(
            _FakeWorkspace(design),
            {
                "moduleName": "top",
                "selection": {"signal": "mid", "module": "other"},
                "extractedModuleName": "extracted",
            },
        )

        assert payload["ok"] is False
        codes = [d["code"] for d in payload["preview"]["diagnostics"]]
        assert "trace-module-mismatch" in codes


class TestUnifiedHierarchyBoundaryMoveCommand:
    """Phase 6: canonical ``verilog/{preview,apply}HierarchyBoundaryMove`` commands."""

    def test_unified_pull_up_returns_boundary_engine_payload(self, wrapper_design):
        design, _ = wrapper_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {"kind": "instance", "instancePath": "top/u_wrap"},
                    "targetParentPath": "top",
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["engineKind"] == "boundary"
        assert "details" not in payload
        assert payload["preview"]["applyReady"] is True
        assert "edit" in payload
        assert "review" in payload

    def test_unified_pull_up_range_resolves_selected_source_instance(self, output_reg_pull_up_design):
        design, top_path = output_reg_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(top_path)},
                        "range": {"start": {"line": 6, "character": 0}, "end": {"line": 12, "character": 6}},
                    },
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["engineKind"] == "boundary"
        assert payload["preview"]["applyReady"] is True
        assert payload["preview"]["source"]["instanceName"] == "u_child"
        assert payload["preview"]["parent"]["moduleName"] == "top"
        assert "edit" in payload

    def test_unified_pull_up_range_moves_child_logic_into_all_parent_sites(self, child_logic_pull_up_design):
        design, child_path, top_a_path, top_b_path = child_logic_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {"start": {"line": 5, "character": 0}, "end": {"line": 9, "character": 11}},
                    },
                }
            ],
        )

        assert payload["ok"] is True
        preview = payload["preview"]
        assert preview["engineKind"] == "boundary"
        assert preview["applyReady"] is True
        assert preview["source"]["moduleName"] == "pulse_child"
        assert preview["metadata"]["scope"] == "design-wide"
        assert preview["metadata"]["siteCount"] == 2
        edit_changes = payload["edit"]["changes"]
        assert set(edit_changes) == {
            path_to_uri(child_path),
            path_to_uri(top_a_path),
            path_to_uri(top_b_path),
        }
        assert payload["review"]["atomic"] is True
        assert payload["review"]["applyStrategy"] == "workspace-edit"
        assert payload["review"]["fileCount"] == 3
        assert "atomic refactor" in payload["review"]["message"]
        assert all(entry["presentationOnly"] is True for entry in payload["review"]["files"])
        assert all(entry["acceptsWholeEdit"] is True for entry in payload["review"]["files"])
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "input toggle" in review_files["pulse_child.v"]
        assert ".toggle(u_child__toggle)" in review_files["top_a.v"]
        assert "u_child__toggle <= strobe_a ? ~u_child__toggle : u_child__toggle;" in review_files["top_a.v"]
        assert ".toggle(u_child_b__toggle)" in review_files["top_b.v"]
        assert "u_child_b__toggle <= strobe_b ? ~u_child_b__toggle : u_child_b__toggle;" in review_files["top_b.v"]

    def test_unified_pull_up_range_moves_child_output_logic_into_parent_sites(self, child_output_logic_pull_up_design):
        design, child_path, top_a_path, top_b_path = child_output_logic_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {"start": {"line": 5, "character": 0}, "end": {"line": 8, "character": 11}},
                    },
                }
            ],
        )

        assert payload["ok"] is True
        preview = payload["preview"]
        assert preview["engineKind"] == "boundary"
        assert preview["applyReady"] is True
        assert preview["source"]["moduleName"] == "pulse_out_child"
        edit_changes = payload["edit"]["changes"]
        assert set(edit_changes) == {
            path_to_uri(child_path),
            path_to_uri(top_a_path),
            path_to_uri(top_b_path),
        }
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "input pulse_out" in review_files["pulse_out_child.v"]
        assert "output reg pulse_a" in review_files["top_a.v"]
        assert "pulse_a <= strobe_a ? ~pulse_a : pulse_a;" in review_files["top_a.v"]
        assert "reg pulse_mid;" in review_files["top_b.v"]
        assert "pulse_mid <= strobe_b ? ~pulse_mid : pulse_mid;" in review_files["top_b.v"]
        assert "u_child__pulse_out" not in review_files["top_a.v"]
        assert "u_child_b__pulse_out" not in review_files["top_b.v"]

    def test_unified_pull_up_range_moves_child_assigns_into_parent_sites(self, child_assign_logic_pull_up_design):
        design, child_path, _top_a_path, _top_b_path = child_assign_logic_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {"start": {"line": 6, "character": 0}, "end": {"line": 6, "character": 29}},
                    },
                }
            ],
        )

        assert payload["ok"] is True
        preview = payload["preview"]
        assert preview["engineKind"] == "boundary"
        assert preview["applyReady"] is True
        assert preview["metadata"]["selectedAssignments"] == 1
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "input wire mid" in review_files["assign_child.v"]
        assert ".mid(u_child__mid)" in review_files["top_a.v"]
        assert "assign u_child__mid = en_a & data_a;" in review_files["top_a.v"]
        assert ".mid(u_child_b__mid)" in review_files["top_b.v"]
        assert "assign u_child_b__mid = en_b & data_b;" in review_files["top_b.v"]

    def test_unified_pull_up_range_moves_child_assign_output_logic_into_parent_sites(
        self, child_assign_output_logic_pull_up_design
    ):
        design, child_path, _top_a_path, _top_b_path = child_assign_output_logic_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {"start": {"line": 5, "character": 0}, "end": {"line": 5, "character": 34}},
                    },
                }
            ],
        )

        assert payload["ok"] is True
        preview = payload["preview"]
        assert preview["engineKind"] == "boundary"
        assert preview["applyReady"] is True
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "input data_out" in review_files["assign_out_child.v"]
        assert "assign data_out_a = en_a & data_a;" in review_files["top_a.v"]
        assert "assign data_mid = en_b & data_b;" in review_files["top_b.v"]
        assert "u_child__data_out" not in review_files["top_a.v"]
        assert "u_child_b__data_out" not in review_files["top_b.v"]

    def test_unified_pull_up_range_moves_child_nested_instance_into_parent_sites(
        self, child_instance_logic_pull_up_design
    ):
        design, child_path, _top_a_path, _top_b_path = child_instance_logic_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {"start": {"line": 6, "character": 0}, "end": {"line": 10, "character": 6}},
                    },
                }
            ],
        )

        assert payload["ok"] is True
        preview = payload["preview"]
        assert preview["engineKind"] == "boundary"
        assert preview["applyReady"] is True
        assert preview["metadata"]["selectedInstances"] == 1
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "input wire mid" in review_files["struct_child.v"]
        assert "leaf_gate u_child__u_leaf" in review_files["top_a.v"]
        assert ".data_out(u_child__mid)" in review_files["top_a.v"]
        assert ".mid(u_child__mid)" in review_files["top_a.v"]
        assert "leaf_gate u_child_b__u_leaf" in review_files["top_b.v"]
        assert ".data_out(u_child_b__mid)" in review_files["top_b.v"]
        assert ".mid(u_child_b__mid)" in review_files["top_b.v"]

    def test_unified_pull_up_range_moves_child_mixed_structural_logic_into_parent_sites(
        self, child_mixed_logic_pull_up_design
    ):
        design, child_path, _top_a_path, _top_b_path = child_mixed_logic_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {"start": {"line": 7, "character": 0}, "end": {"line": 12, "character": 29}},
                    },
                }
            ],
        )

        assert payload["ok"] is True
        preview = payload["preview"]
        assert preview["engineKind"] == "boundary"
        assert preview["applyReady"] is True
        assert preview["metadata"]["selectedAssignments"] == 1
        assert preview["metadata"]["selectedInstances"] == 1
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "input wire masked" in review_files["struct_mixed_child.v"]
        assert "leaf_gate u_child__u_leaf" in review_files["top_a.v"]
        assert "assign u_child__masked = u_child__mid & en_a;" in review_files["top_a.v"]
        assert ".masked(u_child__masked)" in review_files["top_a.v"]
        assert "leaf_gate u_child_b__u_leaf" in review_files["top_b.v"]
        assert "assign u_child_b__masked = u_child_b__mid & en_b;" in review_files["top_b.v"]
        assert ".masked(u_child_b__masked)" in review_files["top_b.v"]

    def test_unified_pull_up_range_moves_child_instance_with_complex_outputs_into_parent_sites(
        self, child_complex_instance_logic_pull_up_design
    ):
        design, child_path, _top_a_path, _top_b_path = child_complex_instance_logic_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {
                            "start": {"line": _line_containing(child_path, "leaf_pair u_leaf(") - 1, "character": 0},
                            "end": {"line": _last_line_containing(child_path, ");") - 1, "character": 6},
                        },
                    },
                }
            ],
        )

        assert payload["ok"] is True
        preview = payload["preview"]
        assert preview["engineKind"] == "boundary"
        assert preview["applyReady"] is True
        assert preview["metadata"]["selectedInstances"] == 1
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "input wire hi" in review_files["complex_child.v"]
        assert "input wire lo" in review_files["complex_child.v"]
        assert "leaf_pair u_child__u_leaf" in review_files["top_a.v"]
        assert ".pair_out({u_child__hi, u_child__lo})" in review_files["top_a.v"]
        assert ".hi(u_child__hi)" in review_files["top_a.v"]
        assert ".lo(u_child__lo)" in review_files["top_a.v"]
        assert "leaf_pair u_child_b__u_leaf" in review_files["top_b.v"]
        assert ".pair_out({u_child_b__hi, u_child_b__lo})" in review_files["top_b.v"]
        assert ".hi(u_child_b__hi)" in review_files["top_b.v"]
        assert ".lo(u_child_b__lo)" in review_files["top_b.v"]

    def test_unified_pull_up_range_moves_child_mixed_structural_complex_outputs_into_parent_sites(
        self, child_complex_mixed_logic_pull_up_design
    ):
        design, child_path, _top_a_path, _top_b_path = child_complex_mixed_logic_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {
                            "start": {"line": _line_containing(child_path, "leaf_pair u_leaf(") - 1, "character": 0},
                            "end": {
                                "line": _line_containing(child_path, "assign data_out = pair;") - 1,
                                "character": 27,
                            },
                        },
                    },
                }
            ],
        )

        assert payload["ok"] is True
        preview = payload["preview"]
        assert preview["engineKind"] == "boundary"
        assert preview["applyReady"] is True
        assert preview["metadata"]["selectedAssignments"] == 1
        assert preview["metadata"]["selectedInstances"] == 1
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "input [1:0] data_out" in review_files["complex_mixed_child.v"]
        assert "leaf_pair u_child__u_leaf" in review_files["top_a.v"]
        assert ".pair_out({u_child__pair[1], u_child__pair[0]})" in review_files["top_a.v"]
        assert "assign data_out_a = u_child__pair;" in review_files["top_a.v"]
        assert ".data_out(data_out_a)" in review_files["top_a.v"]
        assert "leaf_pair u_child_b__u_leaf" in review_files["top_b.v"]
        assert ".pair_out({u_child_b__pair[1], u_child_b__pair[0]})" in review_files["top_b.v"]
        assert "assign data_out_b = u_child_b__pair;" in review_files["top_b.v"]
        assert ".data_out(data_out_b)" in review_files["top_b.v"]

    def test_unified_pull_up_range_allows_selected_parameter_with_child_assign(
        self, child_selected_parameter_pull_up_design
    ):
        design, child_path, _top_a_path, _top_b_path = child_selected_parameter_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {
                            "start": {"line": _line_containing(child_path, "parameter SHIFT = 1") - 1, "character": 0},
                            "end": {
                                "line": _line_containing(child_path, "assign data_out = data_in << SHIFT;") - 1,
                                "character": 35,
                            },
                        },
                    },
                }
            ],
        )

        assert payload["ok"] is True
        preview = payload["preview"]
        assert preview["engineKind"] == "boundary"
        assert preview["applyReady"] is True
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "parameter SHIFT = 1" in review_files["shift_child.v"]
        assert "assign data_out = data_in << SHIFT;" not in review_files["shift_child.v"]
        assert "assign data_out_a = data_a << 1;" in review_files["top_a.v"]
        assert "assign data_out_b = data_b << 2;" in review_files["top_b.v"]

    def test_unified_pull_up_range_moves_selected_localparam_with_child_procedural_logic(
        self, child_selected_localparam_pull_up_design
    ):
        design, child_path, _top_a_path, _top_b_path = child_selected_localparam_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {
                            "start": {
                                "line": _line_containing(child_path, "localparam RESET_VALUE = 1'b0;") - 1,
                                "character": 0,
                            },
                            "end": {"line": _line_containing(child_path, "end") - 1, "character": 11},
                        },
                    },
                }
            ],
        )

        assert payload["ok"] is True
        preview = payload["preview"]
        assert preview["engineKind"] == "boundary"
        assert preview["applyReady"] is True
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "localparam RESET_VALUE = 1'b0;" not in review_files["reset_child.v"]
        assert "always @(posedge clk)" not in review_files["reset_child.v"]
        assert "output reg pulse_a" in review_files["top_a.v"]
        assert "pulse_a <= strobe_a ? ~pulse_a : 1'b0;" in review_files["top_a.v"]
        assert "reg pulse_mid;" in review_files["top_b.v"]
        assert "pulse_mid <= strobe_b ? ~pulse_mid : 1'b0;" in review_files["top_b.v"]

    def test_unified_pull_up_range_moves_child_procedural_logic_with_functions_into_parent_sites(
        self, child_function_logic_pull_up_design
    ):
        design, child_path, _top_a_path, _top_b_path = child_function_logic_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {
                            "start": {
                                "line": _line_containing(child_path, "pulse_out <= calc_next(pulse_out, strobe_in);")
                                - 1,
                                "character": 0,
                            },
                            "end": {
                                "line": _line_containing(child_path, "pulse_out <= calc_next(pulse_out, strobe_in);")
                                - 1,
                                "character": 56,
                            },
                        },
                    },
                }
            ],
        )

        assert payload["ok"] is True
        preview = payload["preview"]
        assert preview["engineKind"] == "boundary"
        assert preview["applyReady"] is True
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "function calc_next;" in review_files["func_child.v"]
        assert "function u_child__calc_next;" in review_files["top_a.v"]
        assert "pulse_a <= u_child__calc_next(pulse_a, strobe_a);" in review_files["top_a.v"]
        assert "function u_child_b__calc_next;" in review_files["top_b.v"]
        assert "pulse_mid <= u_child_b__calc_next(pulse_mid, strobe_b);" in review_files["top_b.v"]

    def test_unified_pull_up_instance_supports_child_functions(self, local_function_pull_up_design):
        design, _top_path = local_function_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyBoundaryMove",
            [{"direction": "pull_up", "selection": {"kind": "instance", "instancePath": "top/u_child"}}],
        )

        assert payload["ok"] is True
        assert payload["preview"]["engineKind"] == "boundary"
        assert payload["preview"]["applyReady"] is True
        top_review = next(entry for entry in payload["review"]["files"] if Path(entry["file"]).name == "top.v")
        assert "function [3:0]" in top_review["proposedText"]
        assert "u_child__add_one" in top_review["proposedText"]
        assert "assign data_out = u_child__add_one(data_in);" in top_review["proposedText"]

    def test_unified_pull_up_range_allows_unselected_generate_blocks_in_child_module(
        self, child_generate_tolerant_pull_up_design
    ):
        design, child_path, _top_a_path, _top_b_path = child_generate_tolerant_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {
                            "start": {
                                "line": _line_containing(child_path, "assign mid = en & data_in;") - 1,
                                "character": 0,
                            },
                            "end": {
                                "line": _line_containing(child_path, "assign mid = en & data_in;") - 1,
                                "character": 24,
                            },
                        },
                    },
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["engineKind"] == "boundary"
        assert payload["preview"]["applyReady"] is True
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "begin : keep_logic" in review_files["gen_child.v"]
        assert "assign keep_tap = data_in;" in review_files["gen_child.v"]
        assert "assign mid = en & data_in;" not in review_files["gen_child.v"]
        assert "assign u_child__mid = en_a & data_a;" in review_files["top_a.v"]
        assert "assign u_child_b__mid = en_b & data_b;" in review_files["top_b.v"]

    def test_unified_pull_up_range_supports_generate_nested_parent_sites(self, child_generate_site_pull_up_design):
        design, child_path, _top_a_path, _top_b_path = child_generate_site_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {
                            "start": {
                                "line": _line_containing(child_path, "assign data_out = en & data_in;") - 1,
                                "character": 0,
                            },
                            "end": {
                                "line": _line_containing(child_path, "assign data_out = en & data_in;") - 1,
                                "character": 29,
                            },
                        },
                    },
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["engineKind"] == "boundary"
        assert payload["preview"]["applyReady"] is True
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "assign data_out = en & data_in;" not in review_files["gen_site_child.v"]
        assert "assign data_out_a = en_a & data_a;" in review_files["top_a.v"]
        assert "begin : g_child" in review_files["top_b.v"]
        assert "assign data_out_b = en_b & data_b;" in review_files["top_b.v"]
        assert "gen_site_child u_child_b" in review_files["top_b.v"]

    def test_unified_pull_up_range_supports_child_generate_assign_selection(self, child_generate_assign_pull_up_design):
        design, child_path, _top_a_path, _top_b_path = child_generate_assign_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {
                            "start": {
                                "line": _line_containing(child_path, "assign data_out = en & data_in;") - 1,
                                "character": 0,
                            },
                            "end": {
                                "line": _line_containing(child_path, "assign data_out = en & data_in;") - 1,
                                "character": 29,
                            },
                        },
                    },
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["engineKind"] == "boundary"
        assert payload["preview"]["applyReady"] is True
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "assign data_out = en & data_in;" not in review_files["gen_inner_child.v"]
        assert "begin : g_logic" in review_files["top_a.v"]
        assert "assign data_out_a = en_a & data_a;" in review_files["top_a.v"]
        assert "begin : g_child" in review_files["top_b.v"]
        assert "begin : g_logic" in review_files["top_b.v"]
        assert "assign data_out_b = en_b & data_b;" in review_files["top_b.v"]

    def test_unified_pull_up_range_supports_child_generate_procedural_selection(
        self, child_generate_proc_pull_up_design
    ):
        design, child_path, _top_a_path, _top_b_path = child_generate_proc_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {
                            "start": {
                                "line": _line_containing(
                                    child_path, "always @(posedge clk) pulse_out <= strobe_in ? ~pulse_out : pulse_out;"
                                )
                                - 1,
                                "character": 0,
                            },
                            "end": {
                                "line": _line_containing(
                                    child_path, "always @(posedge clk) pulse_out <= strobe_in ? ~pulse_out : pulse_out;"
                                )
                                - 1,
                                "character": 76,
                            },
                        },
                    },
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["engineKind"] == "boundary"
        assert payload["preview"]["applyReady"] is True
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert (
            "always @(posedge clk) pulse_out <= strobe_in ? ~pulse_out : pulse_out;"
            not in review_files["gen_proc_child.v"]
        )
        assert "output reg pulse_a" in review_files["top_a.v"]
        assert "begin : g_logic" in review_files["top_a.v"]
        assert "pulse_a <= strobe_a ? ~pulse_a : pulse_a;" in review_files["top_a.v"]
        assert "reg pulse_mid;" in review_files["top_b.v"]
        assert "begin : g_child" in review_files["top_b.v"]
        assert "begin : g_logic" in review_files["top_b.v"]
        assert "pulse_mid <= strobe_b ? ~pulse_mid : pulse_mid;" in review_files["top_b.v"]

    def test_unified_pull_up_range_allows_unconnected_irrelevant_child_ports(
        self, child_unconnected_port_pull_up_design
    ):
        design, child_path, _top_a_path, _top_b_path = child_unconnected_port_pull_up_design

        payload = _execute_command_payload(
            _FakeWorkspace(design, top_module="top_a"),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "pull_up",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(child_path)},
                        "range": {
                            "start": {
                                "line": _line_containing(child_path, "always @(posedge clk)") - 1,
                                "character": 0,
                            },
                            "end": {
                                "line": _line_containing(child_path, "sync_vector_out <= async_vector_in;") - 1,
                                "character": 44,
                            },
                        },
                    },
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["engineKind"] == "boundary"
        assert payload["preview"]["applyReady"] is True
        assert not any(diag["code"] == "unconnected-port-unsupported" for diag in payload["preview"]["diagnostics"])
        review_files = {Path(entry["file"]).name: entry["proposedText"] for entry in payload["review"]["files"]}
        assert "always @(posedge clk)" not in review_files["async_like_child.v"]
        assert "output reg sync_a" in review_files["top_a.v"]
        assert "sync_a <= async_a;" in review_files["top_a.v"]
        assert "output reg sync_b" in review_files["top_b.v"]
        assert "sync_b <= async_b;" in review_files["top_b.v"]
        assert ".changing()" in review_files["top_b.v"]

    def test_unified_push_down_returns_boundary_engine_payload(self, wrapper_design):
        design, _ = wrapper_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "push_down",
                    "selection": {"kind": "module", "moduleName": "top"},
                    "newModuleName": "top_partition",
                    "newInstanceName": "u_partition",
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["engineKind"] == "boundary"
        assert "details" not in payload
        assert "edit" in payload

    def test_unified_collapse_returns_collapse_engine_details(self, wrapper_design):
        design, _ = wrapper_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "collapse",
                    "selection": {"kind": "instance", "instancePath": "top/u_wrap"},
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["engineKind"] == "collapse"
        assert "details" in payload
        assert "edit" in payload
        assert "review" in payload

    def test_unified_extract_range_returns_extract_engine_details(self, extract_design):
        design, top_path = extract_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "extract",
                    "textDocument": {"uri": path_to_uri(top_path)},
                    "range": {"start": {"line": 7, "character": 0}, "end": {"line": 8, "character": 30}},
                    "extractedModuleName": "extracted_logic",
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["preview"]["engineKind"] == "extract"
        assert "details" in payload
        # Parity with legacy verilog/previewExtractModule: extract details
        # carry a ``presentation`` block so unified-API clients (peovim's
        # partial-selection picker, etc.) match the legacy UX surface.
        assert isinstance(payload["details"].get("presentation"), dict)
        assert "sections" in payload["details"]["presentation"]
        assert "edit" in payload
        child_uri = path_to_uri(str(Path(top_path).with_name("extracted_logic.v")))
        assert child_uri in payload["edit"]["changes"]

    def test_unified_push_down_range_routes_to_extract_with_metadata(self, extract_design):
        design, top_path = extract_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyBoundaryMove",
            [
                {
                    "direction": "push_down",
                    "selection": {
                        "kind": "range",
                        "textDocument": {"uri": path_to_uri(top_path)},
                        "range": {
                            "start": {"line": 7, "character": 0},
                            "end": {"line": 8, "character": 30},
                        },
                    },
                    "newModuleName": "extracted_logic",
                }
            ],
        )

        assert payload["ok"] is True
        # Routed to extract engine; details carry the extract preview, including
        # pushDownMode/origin metadata so editors can distinguish range
        # push-down from a standalone extract.
        assert payload["preview"]["engineKind"] == "extract"
        details = payload["details"]
        metadata = details.get("metadata", {})
        assert metadata.get("pushDownMode") == "range"
        assert metadata.get("origin") == "extract"
        assert isinstance(details.get("presentation"), dict)

    def test_unified_apply_returns_workspace_edit_without_writing(self, wrapper_design):
        design, _ = wrapper_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/applyHierarchyBoundaryMove",
            [
                {
                    "direction": "collapse",
                    "selection": {"kind": "instance", "instancePath": "top/u_wrap"},
                }
            ],
        )

        assert payload["ok"] is True
        assert payload["applied"] is False
        assert payload["appliedByServer"] is False
        assert "edit" in payload

    def test_unified_rejects_invalid_direction(self, wrapper_design):
        design, _ = wrapper_design

        payload = _execute_command_payload(
            _FakeWorkspace(design),
            "verilog/previewHierarchyBoundaryMove",
            [{"direction": "sideways", "selection": {"kind": "module", "moduleName": "top"}}],
        )

        assert payload["ok"] is False
        assert payload["diagnostics"][0]["code"] == "invalid-direction"
