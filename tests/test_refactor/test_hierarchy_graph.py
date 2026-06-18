"""Tests for hierarchy graph and wrapper classification."""

from __future__ import annotations

import json
import os
from pathlib import Path

from veriforge.analysis import analyze_design
from veriforge.project import parse_files
from veriforge.__main__ import main as cli_main
from veriforge.refactor import (
    BoundaryMoveRequest,
    BoundaryMoveSelection,
    ExtractSelection,
    apply_collapse_preview,
    apply_extract_preview,
    apply_hierarchy_boundary_move_preview,
    build_hierarchy_graph,
    hierarchy_graph_to_dot,
    hierarchy_graph_to_mermaid,
    normalize_extract_selection,
    preview_collapse_hierarchy,
    preview_extract_submodule,
    preview_hierarchy_boundary_move,
    resolve_extract_selection,
)


CORE_V = """\
module core(
    input clk,
    input [7:0] din,
    output [7:0] dout
);
    assign dout = din;
endmodule
"""

PURE_RENAME_WRAPPER_V = """\
module wrapper(
    input clk,
    input [7:0] data_i,
    output [7:0] data_o
);
    core u_core(.clk(clk), .din(data_i), .dout(data_o));
endmodule
"""

PURE_ALIAS_WRAPPER_V = """\
module wrapper(
    input clk,
    input [7:0] data_i,
    output [7:0] data_o
);
    wire [7:0] data_o_i;
    core u_core(.clk(clk), .din(data_i), .dout(data_o_i));
    assign data_o = data_o_i;
endmodule
"""

STRUCTURAL_WRAPPER_V = """\
module wrapper(
    input clk,
    input [7:0] data_i,
    output [7:0] data_o
);
    wire [7:0] mid;
    core u_first(.clk(clk), .din(data_i), .dout(mid));
    core u_second(.clk(clk), .din(mid), .dout(data_o));
endmodule
"""

BEHAVIORAL_WRAPPER_V = """\
module wrapper(
    input clk,
    input [7:0] data_i,
    output reg [7:0] data_o
);
    always @(posedge clk)
        data_o <= data_i;
endmodule
"""

PARAM_CHILD_V = """\
module param_child #(
    parameter WIDTH = 4
) (
    input clk,
    input [WIDTH-1:0] din,
    output reg [WIDTH-1:0] dout
);
    reg [WIDTH-1:0] hold;
    always @(posedge clk)
        begin
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

PARENT_ORDER_PULLUP_TOP_A_V = """\
module top_a(
    input clk,
    input strobe_a,
    output pulse_a
);
    function keep_fn;
        input value;
        begin
            keep_fn = value;
        end
    endfunction
    pulse_child u_child(
        .clk(clk),
        .strobe_in(strobe_a),
        .pulse_out(pulse_a)
    );
endmodule
"""

PARENT_GENERATE_PRESERVE_TOP_A_V = """\
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
    generate
        if (1) begin : g_keep
            wire keep_tap;
            assign keep_tap = strobe_a;
        end
    endgenerate
endmodule
"""

PARENT_NOISE_PRESERVE_TOP_A_V = """\
module top_a #(
    parameter KEEP = 1, // keep parameter comment
    parameter WIDTH = 1
) (
    input clk,
    input strobe_a,
    output pulse_a
);
    (* keep = "true" *) wire preserved_wire = 1'b0;
    pulse_child u_child(
        .clk(clk),
        .strobe_in(strobe_a),
        .pulse_out(pulse_a)
    );
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

MULTI_INSTANCE_TOP_V = """\
module top(
    input clk,
    input [7:0] a,
    input [7:0] b,
    output [7:0] y0,
    output [7:0] y1
);
    core u_first(.clk(clk), .din(a), .dout(y0)), u_second(.clk(clk), .din(b), .dout(y1));
endmodule
"""

TYPICAL_LEAF_V = """\
module typical_leaf(
    input [7:0] din,
    output [7:0] dout
);
    assign dout = din ^ 8'h5a;
endmodule
"""

TYPICAL_CHILD_V = """\
module typical_child #(
    parameter WIDTH = 8,
    parameter INIT = 8'h00
) (
    input clk,
    input rst,
    input en,
    input [WIDTH-1:0] ctrl,
    output reg [WIDTH-1:0] data_out,
    output wire flag_out
);
    localparam [WIDTH-1:0] MASK = INIT;
    wire [WIDTH-1:0] masked;
    reg [WIDTH-1:0] state;
    reg [1:0] mode;

    typical_leaf u_leaf(.din(ctrl), .dout(masked));
    assign flag_out = |masked;

    always @(posedge clk)
        begin
        if (rst)
            begin
            state <= INIT;
            data_out <= INIT;
            mode <= 2'd0;
            end
        else if (en)
            begin
            case (ctrl[1:0])
                2'd0: state <= masked;
                2'd1: state <= {ctrl[WIDTH-2:0], ctrl[WIDTH-1]};
                default: state <= state ^ MASK;
            endcase
            data_out <= state;
            mode <= ctrl[1:0];
            end
        end
endmodule
"""

TYPICAL_PARENT_V = """\
module top(
    input clk,
    input rst,
    input en,
    input [7:0] ctrl,
    output [7:0] data_o,
    output flag_o
);
    wire [7:0] data_wire;
    typical_child #(.WIDTH(8), .INIT(8'hA5)) u_logic(
        .clk(clk),
        .rst(rst),
        .en(en),
        .ctrl(ctrl),
        .data_out(data_wire),
        .flag_out(flag_o)
    );
    assign data_o = data_wire;
endmodule
"""

UNRESOLVED_CHILD_WRAPPER_V = """\
module wrapper(
    input clk,
    input [7:0] data_i,
    output [7:0] data_o
);
    missing_core u_missing(.clk(clk), .din(data_i), .dout(data_o));
endmodule
"""

TOP_V = """\
module top(
    input clk,
    input [7:0] data_i,
    output [7:0] data_o
);
    wrapper u_wrap(.clk(clk), .data_i(data_i), .data_o(data_o));
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

EXTRACT_WITH_HELPERS_V = """\
module top(
    input [7:0] a,
    input [7:0] b,
    output [7:0] y
);
    localparam [7:0] MASK = 8'hf0;
    wire [7:0] mid;
    assign mid = (a & b) ^ MASK;
    assign y = mid;
endmodule
"""

EXTRACT_WITH_PARENT_PARAMETER_V = """\
module top(
    input [7:0] a,
    input [7:0] b,
    output [7:0] y
);
    parameter WIDTH = 8;
    wire [WIDTH-1:0] mid;
    assign mid = (a & b);
    assign y = mid;
endmodule
"""

EXTRACT_WITH_PARAMETER_DEPENDENCY_V = """\
module top(
    input [7:0] a,
    output y
);
    parameter BASE = 4;
    parameter WIDTH = BASE + 4;
    wire [WIDTH-1:0] mid;
    assign mid = a;
    assign y = mid[0];
endmodule
"""

EXTRACT_AUTO_COPY_LOCALPARAM_V = """\
module top(
    input  [7:0] a,
    output [7:0] y
);
    localparam W = 8;
    localparam OFFSET = 8'd1;
    wire [W-1:0] mid;
    assign mid = a + OFFSET;
    assign y = mid;
endmodule
"""

EXTRACT_AUTO_FORWARD_PARAMETER_V = """\
module top #(parameter W = 8)(
    input  [W-1:0] a,
    output [W-1:0] y
);
    wire [W-1:0] mid;
    assign mid = a;
    assign y = mid;
endmodule
"""

EXTRACT_AUTO_COPY_LOCALPARAM_CHAIN_V = """\
module top(
    input  [15:0] a,
    output [15:0] y
);
    localparam BASE = 8;
    localparam W = BASE + 8;
    wire [W-1:0] mid;
    assign mid = a;
    assign y = mid;
endmodule
"""

EXTRACT_AUTO_COPY_LOCALPARAM_DEPENDS_ON_SIGNAL_V = """\
module top(
    input  [7:0] a,
    input  [7:0] b,
    output [7:0] y
);
    localparam BAD = b;
    assign y = a + BAD;
endmodule
"""

EXTRACT_AUTO_COPY_LOCALPARAM_PROCEDURAL_V = """\
module top(
    input clk,
    input  [7:0] a,
    output reg [7:0] q
);
    localparam W = 8;
    localparam OFFSET = 8'd1;
    always @(posedge clk) begin
        q <= a + OFFSET;
    end
endmodule
"""

EXTRACT_AUTO_COPY_BLOCK_LOCAL_SHADOW_V = """\
module top(
    input clk,
    input  [7:0] a,
    output reg [7:0] q
);
    localparam W = 8;
    always @(posedge clk) begin : myblock
        reg [7:0] W;
        W = a;
        q <= W;
    end
endmodule
"""

EXTRACT_WITH_BOUNDARY_DECL_V = """\
module top(
    input [7:0] a,
    output [7:0] y
);
    wire [7:0] y_int;
    assign y_int = a;
    assign y = y_int;
endmodule
"""

EXTRACT_ALWAYS_WITH_BOUNDARY_DECL_V = """\
module top(
    input clk,
    input a,
    output y
);
    reg tick = 0;
    reg cnt = 0;
    always @(posedge clk) begin
        tick <= a;
        cnt <= tick;
    end
    assign y = tick;
endmodule
"""

EXTRACT_ALWAYS_WITH_EXTRA_DECL_V = """\
module top(
    input clk,
    input a,
    output y
);
    reg tick = 0;
    reg cnt = 0;
    always @(posedge clk) begin
        tick <= a;
        cnt <= tick;
    end
    (* keep = "true" *) reg unused = 0;
    assign y = tick;
endmodule
"""

EXTRACT_UNSUPPORTED_LHS_V = """\
module top(
    input a,
    input b,
    output y
);
    wire mid;
    assign {y, mid} = {a, b};
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

EXTRACT_ALWAYS_COMPLEX_LHS_V = """\
module top(
    input clk,
    input d,
    output reg [1:0] q
);
    always @(posedge clk) begin
        q[0] <= d;
    end
endmodule
"""

EXTRACT_ALWAYS_MULTIPLE_DRIVER_V = """\
module top(
    input clk,
    input rst,
    input d,
    output reg q
);
    always @(posedge clk) begin
        q <= d;
    end

    always @(posedge rst) begin
        q <= 1'b0;
    end
endmodule
"""

EXTRACT_INITIAL_V = """\
module top(
    input d,
    output reg q
);
    initial begin
        q = d;
    end
endmodule
"""

EXTRACT_ALWAYS_WITH_PARENT_PARAMETER_V = """\
module top(
    input clk,
    input d,
    output reg [WIDTH-1:0] q
);
    parameter WIDTH = 4;
    always @(posedge clk) begin
        q <= d;
    end
endmodule
"""

EXTRACT_INSTANCE_GROUP_V = """\
module and_leaf(
    input [7:0] a,
    input [7:0] b,
    output [7:0] y
);
    assign y = a & b;
endmodule

module or_leaf(
    input [7:0] a,
    input [7:0] b,
    output [7:0] y
);
    assign y = a | b;
endmodule

module top(
    input [7:0] a,
    input [7:0] b,
    input [7:0] c,
    output [7:0] y
);
    wire [7:0] mid;
    and_leaf u_and(.a(a), .b(b), .y(mid));
    or_leaf u_or(.a(mid), .b(c), .y(y));
endmodule
"""

EXTRACT_INSTANCE_GROUP_WITH_PARAMETER_V = """\
module buf_leaf #(
    parameter WIDTH = 8
) (
    input [WIDTH-1:0] a,
    output [WIDTH-1:0] y
);
    assign y = a;
endmodule

module top(
    input [7:0] a,
    output [7:0] y
);
    parameter WIDTH = 8;
    wire [WIDTH-1:0] mid;
    buf_leaf #(.WIDTH(WIDTH)) u_left(.a(a), .y(mid));
    buf_leaf #(.WIDTH(WIDTH)) u_right(.a(mid), .y(y));
endmodule
"""

EXTRACT_INSTANCE_GROUP_COMPLEX_CONNECTION_V = """\
module and_leaf(
    input [7:0] a,
    input [7:0] b,
    output [7:0] y
);
    assign y = a & b;
endmodule

module top(
    input [7:0] a,
    input [7:0] b,
    input [7:0] c,
    output [7:0] y
);
    and_leaf u_and(.a(a & b), .b(c), .y(y));
endmodule
"""

EXTRACT_INSTANCE_GROUP_FUNCALL_CONNECTION_V = """\
module and_leaf(
    input [7:0] a,
    input [7:0] b,
    output [7:0] y
);
    assign y = a & b;
endmodule

module top(
    input [7:0] a,
    input [7:0] b,
    output [7:0] y
);
    and_leaf u_and(.a($signed(a)), .b(b), .y(y));
endmodule
"""

EXTRACT_INSTANCE_GROUP_OUTPUT_SLICE_CONNECTION_V = """\
module and_leaf(
    input [7:0] a,
    input [7:0] b,
    output [7:0] y
);
    assign y = a & b;
endmodule

module top(
    input [7:0] a,
    input [7:0] b,
    output [15:0] y
);
    and_leaf u_and(.a(a), .b(b), .y(y[7:0]));
endmodule
"""

EXTRACT_INSTANCE_GROUP_INPUT_SHAPES_V = """\
module and_leaf(
    input [7:0] a,
    input [7:0] b,
    output [7:0] y
);
    assign y = a & b;
endmodule

module top(
    input [7:0] a,
    input [7:0] b,
    input [3:0] lo,
    input [3:0] hi,
    input [15:0] wide,
    input sel,
    output [7:0] y
);
    and_leaf u_and(.a({hi, lo} & wide[7:0] | (sel ? a : 8'hFF)), .b(~b + 8'd1), .y(y));
endmodule
"""

EXTRACT_INSTANCE_GROUP_PARAM_DEP_CONNECTION_V = """\
module buf_leaf #(parameter WIDTH = 8)(
    input  [WIDTH-1:0] a,
    output [WIDTH-1:0] y
);
    assign y = a;
endmodule

module top #(parameter WIDTH = 8)(
    input  [WIDTH-1:0] a,
    output [WIDTH-1:0] y
);
    buf_leaf #(.WIDTH(WIDTH)) u_buf(.a(a & {WIDTH{1'b1}}), .y(y));
endmodule
"""

EXTRACT_MIXED_STRUCTURAL_V = """\
module and_leaf(
    input [7:0] a,
    input [7:0] b,
    output [7:0] y
);
    assign y = a & b;
endmodule

module or_leaf(
    input [7:0] a,
    input [7:0] b,
    output [7:0] y
);
    assign y = a | b;
endmodule

module top(
    input [7:0] a,
    input [7:0] b,
    input [7:0] c,
    output [7:0] y
);
    wire [7:0] a_i;
    wire [7:0] mid;
    wire [7:0] y_i;
    assign a_i = a;
    and_leaf u_and(.a(a_i), .b(b), .y(mid));
    or_leaf u_or(.a(mid), .b(c), .y(y_i));
    assign y = y_i;
endmodule
"""


def _write_design(tmp_path: Path, wrapper_text: str, *, include_core: bool = True) -> list[Path]:
    paths = []
    top = tmp_path / "top.v"
    wrapper = tmp_path / "wrapper.v"
    top.write_text(TOP_V, encoding="utf-8")
    wrapper.write_text(wrapper_text, encoding="utf-8")
    paths.extend([top, wrapper])
    if include_core:
        core = tmp_path / "core.v"
        core.write_text(CORE_V, encoding="utf-8")
        paths.append(core)
    return paths


def _write_single(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "top.v"
    path.write_text(text, encoding="utf-8")
    return path


def _apply_preview_edits(text: str, edits: list[dict]) -> str:
    line_offsets = [0]
    for index, char in enumerate(text):
        if char == "\n":
            line_offsets.append(index + 1)

    def _offset(position: dict) -> int:
        line = int(position.get("line", 0))
        character = int(position.get("character", 0))
        if line >= len(line_offsets):
            return len(text)
        return min(len(text), line_offsets[line] + character)

    ranges = []
    for edit in edits:
        edit_range = edit["range"]
        ranges.append((_offset(edit_range["start"]), _offset(edit_range["end"]), edit["replacement"]))

    result = text
    for start, end, replacement in sorted(ranges, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def _preview_edits_by_file(payload: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for edit in payload["edits"]:
        grouped.setdefault(edit["file"], []).append(edit)
    return grouped


def _preview_texts_by_name(preview) -> dict[str, str]:
    grouped: dict[str, list[dict]] = {}
    for edit in preview.edits:
        grouped.setdefault(edit.file, []).append(edit.to_dict())
    texts: dict[str, str] = {}
    for file_path, edits in grouped.items():
        original = Path(file_path).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        texts[Path(file_path).name] = _apply_preview_edits(original, edits)
    return texts


def _line_containing(path: Path, needle: str) -> int:
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if needle in line:
            return index
    raise AssertionError(f"line containing {needle!r} not found")


def _last_line_containing(path: Path, needle: str) -> int:
    matches = [
        index for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1) if needle in line
    ]
    if not matches:
        raise AssertionError(f"line containing {needle!r} not found")
    return matches[-1]


def _wrapper_summary(tmp_path: Path, wrapper_text: str, *, include_core: bool = True) -> dict:
    design = parse_files(_write_design(tmp_path, wrapper_text, include_core=include_core))
    graph = build_hierarchy_graph(design, top="top")
    wrappers = {node["instancePath"]: node for node in graph.to_dict()["wrappers"]}
    return wrappers["top/u_wrap"]


def test_classifies_single_instance_renamed_wrapper_as_pure_pass_through(tmp_path):
    wrapper = _wrapper_summary(tmp_path, PURE_RENAME_WRAPPER_V)

    assert wrapper["wrapperClass"] == "pure_pass_through"
    assert wrapper["confidence"] == "safe"
    assert "previewCollapse" in wrapper["refactorActions"]


def test_classifies_alias_assign_wrapper_as_pure_pass_through(tmp_path):
    wrapper = _wrapper_summary(tmp_path, PURE_ALIAS_WRAPPER_V)

    assert wrapper["wrapperClass"] == "pure_pass_through"
    assert wrapper["confidence"] == "safe"


def test_classifies_multi_instance_wrapper_as_structural(tmp_path):
    wrapper = _wrapper_summary(tmp_path, STRUCTURAL_WRAPPER_V)

    assert wrapper["wrapperClass"] == "structural_wrapper"
    assert wrapper["confidence"] == "preview"


def test_classifies_behavioral_wrapper_as_unsafe(tmp_path):
    wrapper = _wrapper_summary(tmp_path, BEHAVIORAL_WRAPPER_V)

    assert wrapper["wrapperClass"] == "behavioral_wrapper"
    assert wrapper["confidence"] == "unsafe"
    assert wrapper["diagnostics"][0]["code"] == "behavioral-wrapper"


def test_classifies_wrapper_with_unresolved_child_as_blocked(tmp_path):
    wrapper = _wrapper_summary(tmp_path, UNRESOLVED_CHILD_WRAPPER_V, include_core=False)

    assert wrapper["wrapperClass"] == "unknown_or_unsupported"
    assert wrapper["confidence"] == "blocked"
    assert wrapper["diagnostics"][0]["code"] == "unresolved-child"


def test_hierarchy_wrappers_cli_emits_json_contract(tmp_path, capsys):
    _write_design(tmp_path, PURE_RENAME_WRAPPER_V)

    exit_code = cli_main(["hierarchy", "wrappers", str(tmp_path), "--top", "top", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    result = payload["result"]
    wrapper = result["wrappers"][0]
    assert exit_code == 0
    assert payload["command"] == "hierarchy wrappers"
    assert payload["success"] is True
    assert wrapper["instancePath"] == "top/u_wrap"
    assert wrapper["wrapperClass"] == "pure_pass_through"
    assert wrapper["file"].startswith("file:///")
    assert "instanceRange" in wrapper


def test_hierarchy_graph_cli_emits_roots_and_wrapper_metadata(tmp_path, capsys):
    _write_design(tmp_path, STRUCTURAL_WRAPPER_V)

    exit_code = cli_main(["hierarchy", "graph", str(tmp_path), "--top", "top", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    root = payload["result"]["roots"][0]
    child = root["children"][0]
    assert exit_code == 0
    assert root["name"] == "top"
    assert child["instancePath"] == "top/u_wrap"
    assert child["wrapperClass"] == "structural_wrapper"


def test_dot_visualization_includes_edges_and_wrapper_class(tmp_path):
    design = parse_files(_write_design(tmp_path, PURE_RENAME_WRAPPER_V))
    graph = build_hierarchy_graph(design, top="top")

    dot = hierarchy_graph_to_dot(graph)

    assert dot.startswith("digraph verilog_hierarchy")
    assert '"top" -> "top/u_wrap";' in dot
    assert "pure_pass_through" in dot


def test_mermaid_visualization_includes_edges_and_classes(tmp_path):
    design = parse_files(_write_design(tmp_path, STRUCTURAL_WRAPPER_V))
    graph = build_hierarchy_graph(design, top="top")

    mermaid = hierarchy_graph_to_mermaid(graph)

    assert mermaid.startswith("flowchart TD")
    assert "structural_wrapper" in mermaid
    assert "-->" in mermaid


def test_hierarchy_boundary_move_api_normalizes_pull_up_instance(tmp_path):
    design = parse_files(_write_design(tmp_path, STRUCTURAL_WRAPPER_V))
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_wrap"),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is True
    assert payload["applyReady"] is True
    assert payload["confidence"] == "safe"
    assert payload["source"]["instancePath"] == "top/u_wrap"
    assert payload["parent"]["moduleName"] == "top"
    assert payload["beforeHierarchy"] == {
        "selectedPath": "top/u_wrap",
        "parentPath": "top",
        "selectedModule": "wrapper",
    }
    assert payload["afterHierarchy"] == {
        "removedInstancePath": "top/u_wrap",
        "mergedIntoPath": "top",
        "movedModule": "wrapper",
    }
    assert payload["movedItems"]["instances"] == ["u_first", "u_second"]
    assert payload["movedItems"]["nets"] == ["mid"]
    assert len(payload["edits"]) == 1
    assert "u_wrap__mid" in payload["edits"][0]["replacement"]
    assert "wrapper u_wrap" not in payload["edits"][0]["replacement"]


def test_hierarchy_boundary_move_api_pulls_up_parameterized_output_reg_child(tmp_path):
    top = tmp_path / "top.v"
    child = tmp_path / "param_child.v"
    top.write_text(PARAM_TOP_V, encoding="utf-8")
    child.write_text(PARAM_CHILD_V, encoding="utf-8")
    paths = [top, child]
    design = parse_files(paths)
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_child"),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    replacement = "\n".join(edit["replacement"] for edit in payload["edits"])
    proposed_top = _apply_preview_edits(top.read_text(encoding="utf-8"), payload["edits"])
    instance_edit = payload["edits"][-1]
    assert payload["ok"] is True
    assert payload["applyReady"] is True
    assert len(payload["edits"]) == 2
    assert "param_child #(" in instance_edit["original"]
    assert ") u_child (" in instance_edit["original"]
    assert "reg [7:0] child_out;" in replacement
    assert "reg [8 - 1:0] u_child__hold;" in replacement
    assert "always @(posedge clk)" in replacement
    assert "u_child__hold <= data_i;" in replacement
    assert "child_out <= u_child__hold;" in replacement
    assert "param_child #(" not in proposed_top


def test_hierarchy_pull_up_blocks_multi_instance_statement_minimal_edit(tmp_path):
    top = tmp_path / "top.v"
    core = tmp_path / "core.v"
    top.write_text(MULTI_INSTANCE_TOP_V, encoding="utf-8")
    core.write_text(CORE_V, encoding="utf-8")
    design = parse_files([top, core])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_first"),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is False
    assert payload["applyReady"] is False
    assert payload["diagnostics"][0]["code"] == "multi-instance-statement-unsupported"


def test_hierarchy_pull_up_rewrites_typical_logic_constructs_and_reparses(tmp_path):
    top_path = tmp_path / "top.v"
    child_path = tmp_path / "typical_child.v"
    leaf_path = tmp_path / "typical_leaf.v"
    top_path.write_text(TYPICAL_PARENT_V, encoding="utf-8")
    child_path.write_text(TYPICAL_CHILD_V, encoding="utf-8")
    leaf_path.write_text(TYPICAL_LEAF_V, encoding="utf-8")
    design = parse_files([top_path, child_path, leaf_path])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_logic"),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is True
    assert payload["applyReady"] is True
    assert payload["metadata"]["rewriteStatus"] == "apply-ready"
    replacement = "\n".join(edit["replacement"] for edit in payload["edits"])
    proposed_top = _apply_preview_edits(top_path.read_text(encoding="utf-8"), payload["edits"])
    assert len(payload["edits"]) == 2
    assert "typical_child #(" not in proposed_top
    assert "reg [7:0] data_wire;" in replacement
    assert "localparam u_logic__MASK = 8'hA5;" in replacement
    assert "wire [8 - 1:0] u_logic__masked;" in replacement
    assert "reg [8 - 1:0] u_logic__state;" in replacement
    assert "reg [1:0] u_logic__mode;" in replacement
    assert "typical_leaf u_logic__u_leaf (.din(ctrl), .dout(u_logic__masked));" in replacement
    assert "assign flag_o = |u_logic__masked;" in replacement
    assert "case (ctrl[1:0])" in replacement
    assert "u_logic__state <= {ctrl[8 - 2:0], ctrl[8 - 1]};" in replacement
    assert "data_wire <= u_logic__state;" in replacement

    rewritten_top = tmp_path / "rewritten_top.v"
    rewritten_top.write_text(proposed_top, encoding="utf-8")
    rewritten = parse_files([rewritten_top, leaf_path])
    rewritten_top_module = rewritten.get_module("top")
    assert rewritten_top_module is not None
    assert [inst.instance_name for inst in rewritten_top_module.instances] == ["u_logic__u_leaf"]
    assert rewritten_top_module.get_net("data_wire") is None
    assert rewritten_top_module.get_variable("data_wire") is not None


def test_hierarchy_boundary_move_api_keeps_pull_up_destination(tmp_path):
    design = parse_files(_write_design(tmp_path, STRUCTURAL_WRAPPER_V))
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_wrap"),
        target_parent_path="top",
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is True
    assert payload["targetParentPath"] == "top"
    assert payload["selection"]["instancePath"] == "top/u_wrap"
    assert payload["afterHierarchy"]["mergedIntoPath"] == "top"
    assert payload["movedItems"]["instances"] == ["u_first", "u_second"]


_PULL_UP_CHAIN_DESIGN = """\
module core(
    input clk,
    input [7:0] din,
    output [7:0] dout
);
    assign dout = din;
endmodule

module mid(
    input clk,
    input [7:0] data_i,
    output [7:0] data_o
);
    core u_core(.clk(clk), .din(data_i), .dout(data_o));
endmodule

module outer(
    input clk,
    input [7:0] data_i,
    output [7:0] data_o
);
    mid u_mid(.clk(clk), .data_i(data_i), .data_o(data_o));
endmodule

module top(
    input clk,
    input [7:0] data_i,
    output [7:0] data_o
);
    outer u_outer(.clk(clk), .data_i(data_i), .data_o(data_o));
endmodule
"""


def test_hierarchy_pull_up_multi_level_inlines_into_target_parent(tmp_path):
    path = _write_single(tmp_path, _PULL_UP_CHAIN_DESIGN)
    design = parse_files([path])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_outer/u_mid"),
        target_parent_path="top",
    )

    preview = preview_hierarchy_boundary_move(design, request)
    payload = preview.to_dict()

    assert payload["ok"] is True, payload.get("diagnostics")
    assert payload["applyReady"] is True
    assert payload["targetParentPath"] == "top"
    edits = payload["edits"]
    assert len(edits) == 1
    edit = edits[0]
    assert "outer u_outer" in edit["original"]
    assert "core u_outer__u_mid__u_core" in edit["replacement"]
    assert ".din(data_i)" in edit["replacement"]
    assert ".dout(data_o)" in edit["replacement"]


def test_hierarchy_pull_up_multi_level_inlines_leaf_body(tmp_path):
    path = _write_single(tmp_path, _PULL_UP_CHAIN_DESIGN)
    design = parse_files([path])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_outer/u_mid/u_core"),
        target_parent_path="top",
    )

    preview = preview_hierarchy_boundary_move(design, request)
    payload = preview.to_dict()

    assert payload["ok"] is True, payload.get("diagnostics")
    assert payload["applyReady"] is True
    edit = payload["edits"][0]
    assert "outer u_outer" in edit["original"]
    assert "assign data_o = data_i;" in edit["replacement"]


def test_hierarchy_pull_up_multi_level_blocks_target_parent_off_path(tmp_path):
    path = _write_single(tmp_path, _PULL_UP_CHAIN_DESIGN)
    design = parse_files([path])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_outer/u_mid/u_core"),
        target_parent_path="other_top",
    )

    preview = preview_hierarchy_boundary_move(design, request)
    payload = preview.to_dict()

    assert payload["ok"] is False
    assert any(diag["code"] == "target-parent-not-on-instance-path" for diag in payload["diagnostics"])


def test_hierarchy_pull_up_multi_level_blocks_intermediate_with_sibling_instance(tmp_path):
    bad = _PULL_UP_CHAIN_DESIGN.replace(
        "    core u_core(.clk(clk), .din(data_i), .dout(data_o));\nendmodule\n\nmodule outer(",
        (
            "    wire [7:0] tap;\n"
            "    core u_core(.clk(clk), .din(data_i), .dout(tap));\n"
            "    core u_extra(.clk(clk), .din(tap), .dout(data_o));\n"
            "endmodule\n\nmodule outer("
        ),
    )
    path = _write_single(tmp_path, bad)
    design = parse_files([path])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_outer/u_mid/u_core"),
        target_parent_path="top",
    )

    preview = preview_hierarchy_boundary_move(design, request)
    payload = preview.to_dict()

    assert payload["ok"] is False
    assert any(diag["code"] == "intermediate-wrapper-not-erasable" for diag in payload["diagnostics"])


def test_hierarchy_pull_up_multi_level_blocks_intermediate_with_logic(tmp_path):
    bad = _PULL_UP_CHAIN_DESIGN.replace(
        "    mid u_mid(.clk(clk), .data_i(data_i), .data_o(data_o));\nendmodule\n\nmodule top(",
        (
            "    wire [7:0] gated;\n"
            "    assign gated = data_i & 8'hF0;\n"
            "    mid u_mid(.clk(clk), .data_i(gated), .data_o(data_o));\n"
            "endmodule\n\nmodule top("
        ),
    )
    path = _write_single(tmp_path, bad)
    design = parse_files([path])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_outer/u_mid/u_core"),
        target_parent_path="top",
    )

    preview = preview_hierarchy_boundary_move(design, request)
    payload = preview.to_dict()

    assert payload["ok"] is False
    assert any(diag["code"] == "intermediate-wrapper-not-erasable" for diag in payload["diagnostics"])


def test_hierarchy_pull_up_multi_level_apply_reparses(tmp_path):
    path = _write_single(tmp_path, _PULL_UP_CHAIN_DESIGN)
    design = parse_files([path])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_outer/u_mid"),
        target_parent_path="top",
    )
    preview = preview_hierarchy_boundary_move(design, request)
    payload = preview.to_dict()
    assert payload["applyReady"] is True
    new_text = _apply_preview_edits(path.read_text(encoding="utf-8"), payload["edits"])
    path.write_text(new_text, encoding="utf-8")

    design2 = parse_files([path])
    top2 = design2.get_module("top")
    assert top2 is not None
    assert any(inst.module_name == "core" for inst in top2.instances)
    assert not any(inst.module_name in {"outer", "mid"} for inst in top2.instances)


def test_hierarchy_boundary_move_api_normalizes_push_down_module(tmp_path):
    design = parse_files(_write_design(tmp_path, STRUCTURAL_WRAPPER_V))
    request = BoundaryMoveRequest(
        direction="push_down",
        selection=BoundaryMoveSelection(kind="module", module_name="top"),
        new_module_name="top_partition",
        new_instance_name="u_partition",
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is True
    assert payload["applyReady"] is True
    assert payload["source"]["moduleName"] == "top"
    assert payload["afterHierarchy"] == {
        "createdModule": "top_partition",
        "createdInstance": "u_partition",
        "rewrittenModule": "top",
    }
    assert payload["movedItems"]["instances"] == ["u_wrap"]
    assert payload["metadata"]["rewriteStatus"] == "apply-ready"
    assert payload["edits"]


def test_hierarchy_boundary_move_api_blocks_pull_up_without_parent_context(tmp_path):
    design = parse_files(_write_design(tmp_path, STRUCTURAL_WRAPPER_V))
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="module", module_name="wrapper"),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "parent-context-required"
    assert payload["edits"] == []


def test_hierarchy_boundary_move_api_blocks_ambiguous_file_selection(tmp_path):
    path = tmp_path / "combined.v"
    path.write_text(TOP_V + "\n" + STRUCTURAL_WRAPPER_V + "\n" + CORE_V, encoding="utf-8")
    design = parse_files([path])
    request = BoundaryMoveRequest(
        direction="push_down",
        selection=BoundaryMoveSelection(kind="file", file=str(path)),
        new_module_name="partition",
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "ambiguous-file-selection"


def test_hierarchy_pull_up_cli_emits_json_preview(tmp_path, capsys):
    _write_design(tmp_path, STRUCTURAL_WRAPPER_V)

    exit_code = cli_main(
        [
            "hierarchy",
            "pull-up",
            str(tmp_path),
            "--instance",
            "top/u_wrap",
            "--preview",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    preview = payload["result"]["preview"]
    assert exit_code == 0
    assert payload["command"] == "hierarchy pull-up"
    assert preview["ok"] is True
    assert preview["applyReady"] is True
    assert preview["source"]["instancePath"] == "top/u_wrap"
    assert preview["afterHierarchy"]["mergedIntoPath"] == "top"
    assert preview["movedItems"]["instances"] == ["u_first", "u_second"]
    assert preview["edits"]


def test_hierarchy_push_down_cli_emits_json_preview(tmp_path, capsys):
    _write_design(tmp_path, STRUCTURAL_WRAPPER_V)

    exit_code = cli_main(
        [
            "hierarchy",
            "push-down",
            str(tmp_path),
            "--module",
            "top",
            "--name",
            "top_partition",
            "--instance-name",
            "u_partition",
            "--preview",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    preview = payload["result"]["preview"]
    assert exit_code == 0
    assert payload["command"] == "hierarchy push-down"
    assert preview["ok"] is True
    assert preview["applyReady"] is True
    assert preview["source"]["moduleName"] == "top"
    assert preview["afterHierarchy"] == {
        "createdModule": "top_partition",
        "createdInstance": "u_partition",
        "rewrittenModule": "top",
    }
    assert preview["movedItems"]["instances"] == ["u_wrap"]
    assert preview["edits"]


def test_hierarchy_push_down_cli_blocks_without_new_module_name(tmp_path, capsys):
    _write_design(tmp_path, STRUCTURAL_WRAPPER_V)

    exit_code = cli_main(
        [
            "hierarchy",
            "push-down",
            str(tmp_path),
            "--module",
            "top",
            "--preview",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 2
    assert payload["success"] is False
    assert payload["error"]["type"] == "ArgumentError"


def test_hierarchy_push_down_rewrites_module_and_reparses(tmp_path):
    wrapper_path = tmp_path / "wrapper.v"
    core_path = tmp_path / "core.v"
    wrapper_path.write_text(STRUCTURAL_WRAPPER_V, encoding="utf-8")
    core_path.write_text(CORE_V, encoding="utf-8")
    design = parse_files([wrapper_path, core_path])
    request = BoundaryMoveRequest(
        direction="push_down",
        selection=BoundaryMoveSelection(kind="module", module_name="wrapper"),
        new_module_name="wrapper_core",
        new_instance_name="u_core",
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is True
    assert payload["applyReady"] is True
    assert payload["metadata"]["rewriteStatus"] == "apply-ready"
    assert len(payload["edits"]) == 1
    edit = payload["edits"][0]
    assert os.path.normcase(edit["file"]) == os.path.normcase(str(wrapper_path))
    assert "wrapper_core u_core" in edit["replacement"]
    assert "module wrapper_core" in edit["replacement"]
    assert "core u_first" in edit["replacement"]
    assert "core u_second" in edit["replacement"]

    new_text = _apply_preview_edits(wrapper_path.read_text(encoding="utf-8"), payload["edits"])
    rewritten_wrapper = tmp_path / "rewritten_wrapper.v"
    rewritten_wrapper.write_text(new_text, encoding="utf-8")
    rewritten = parse_files([rewritten_wrapper, core_path])
    wrapper_module = rewritten.get_module("wrapper")
    assert wrapper_module is not None
    assert [inst.module_name for inst in wrapper_module.instances] == ["wrapper_core"]
    assert [inst.instance_name for inst in wrapper_module.instances] == ["u_core"]
    child_module = rewritten.get_module("wrapper_core")
    assert child_module is not None
    assert [inst.instance_name for inst in child_module.instances] == ["u_first", "u_second"]
    assert child_module.get_net("mid") is not None


def test_hierarchy_push_down_blocks_on_module_name_collision(tmp_path):
    design = parse_files(_write_design(tmp_path, STRUCTURAL_WRAPPER_V))
    request = BoundaryMoveRequest(
        direction="push_down",
        selection=BoundaryMoveSelection(kind="module", module_name="wrapper"),
        new_module_name="core",
        new_instance_name="u_core",
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is False
    assert payload["applyReady"] is False
    assert payload["diagnostics"][0]["code"] == "new-module-name-collision"


def test_hierarchy_push_down_blocks_on_instance_name_collision(tmp_path):
    design = parse_files(_write_design(tmp_path, STRUCTURAL_WRAPPER_V))
    request = BoundaryMoveRequest(
        direction="push_down",
        selection=BoundaryMoveSelection(kind="module", module_name="wrapper"),
        new_module_name="wrapper_core",
        new_instance_name="u_first",
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is False
    assert payload["applyReady"] is False
    assert payload["diagnostics"][0]["code"] == "new-instance-name-collision"


def test_hierarchy_push_down_accepts_instance_selection(tmp_path):
    wrapper_path = tmp_path / "wrapper.v"
    core_path = tmp_path / "core.v"
    wrapper_path.write_text(STRUCTURAL_WRAPPER_V, encoding="utf-8")
    core_path.write_text(CORE_V, encoding="utf-8")
    top_path = tmp_path / "top.v"
    top_path.write_text(TOP_V, encoding="utf-8")
    design = parse_files([top_path, wrapper_path, core_path])
    request = BoundaryMoveRequest(
        direction="push_down",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_wrap"),
        new_module_name="wrapper_core",
        new_instance_name="u_core",
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is True
    assert payload["applyReady"] is True
    assert payload["source"]["instancePath"] == "top/u_wrap"
    assert payload["source"]["moduleName"] == "wrapper"
    assert payload["parent"]["moduleName"] == "top"
    assert payload["afterHierarchy"]["rewrittenModule"] == "wrapper"
    assert payload["metadata"]["rewriteStatus"] == "apply-ready"
    assert payload["edits"]
    edit = payload["edits"][0]
    assert "module wrapper_core" in edit["replacement"]

    new_text = _apply_preview_edits(wrapper_path.read_text(encoding="utf-8"), payload["edits"])
    rewritten_wrapper = tmp_path / "rewritten_wrapper.v"
    rewritten_wrapper.write_text(new_text, encoding="utf-8")
    rewritten = parse_files([top_path, rewritten_wrapper, core_path])
    wrapper_module = rewritten.get_module("wrapper")
    assert wrapper_module is not None
    assert [inst.module_name for inst in wrapper_module.instances] == ["wrapper_core"]
    child_module = rewritten.get_module("wrapper_core")
    assert child_module is not None
    assert [inst.instance_name for inst in child_module.instances] == ["u_first", "u_second"]


def test_hierarchy_push_down_subtree_kind_is_alias_for_instance(tmp_path):
    design = parse_files(_write_design(tmp_path, STRUCTURAL_WRAPPER_V))
    request = BoundaryMoveRequest(
        direction="push_down",
        selection=BoundaryMoveSelection(kind="subtree", instance_path="top/u_wrap"),
        new_module_name="wrapper_core",
        new_instance_name="u_core",
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is True
    assert payload["applyReady"] is True
    assert payload["afterHierarchy"]["rewrittenModule"] == "wrapper"


def test_hierarchy_push_down_warns_on_multi_instance_module(tmp_path):
    design = parse_files(_write_design(tmp_path, STRUCTURAL_WRAPPER_V))
    request = BoundaryMoveRequest(
        direction="push_down",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_wrap/u_first"),
        new_module_name="core_wrap",
        new_instance_name="u_inner",
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is True
    assert payload["applyReady"] is True
    assert payload["metadata"]["rewriteStatus"] == "apply-ready"
    assert payload["metadata"]["instanceSiteCount"] == 2
    warnings = [diag for diag in payload["diagnostics"] if diag["severity"] == "warning"]
    assert any(diag["code"] == "push-down-module-multi-instance" for diag in warnings)


def test_hierarchy_push_down_blocks_target_parent_path(tmp_path):
    design = parse_files(_write_design(tmp_path, STRUCTURAL_WRAPPER_V))
    request = BoundaryMoveRequest(
        direction="push_down",
        selection=BoundaryMoveSelection(kind="module", module_name="wrapper"),
        target_parent_path="top",
        new_module_name="wrapper_core",
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is False
    assert payload["applyReady"] is False
    assert payload["diagnostics"][0]["code"] == "push-down-target-not-supported"


def test_hierarchy_push_down_cli_writes_module_when_apply_ready(tmp_path, capsys):
    _write_design(tmp_path, STRUCTURAL_WRAPPER_V)

    exit_code = cli_main(
        [
            "hierarchy",
            "push-down",
            str(tmp_path),
            "--module",
            "wrapper",
            "--name",
            "wrapper_core",
            "--instance-name",
            "u_core",
            "--write",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0, payload
    assert payload["result"]["apply"]["applied"] is True
    wrapper_path = tmp_path / "wrapper.v"
    new_text = wrapper_path.read_text(encoding="utf-8")
    assert "wrapper_core u_core" in new_text
    assert "module wrapper_core" in new_text


def test_hierarchy_graph_cli_emits_dot_format(tmp_path, capsys):
    _write_design(tmp_path, PURE_RENAME_WRAPPER_V)

    exit_code = cli_main(["hierarchy", "graph", str(tmp_path), "--top", "top", "--format", "dot"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "digraph verilog_hierarchy" in captured.out
    assert '"top" -> "top/u_wrap";' in captured.out


def test_hierarchy_graph_cli_emits_mermaid_format(tmp_path, capsys):
    _write_design(tmp_path, PURE_RENAME_WRAPPER_V)

    exit_code = cli_main(["hierarchy", "graph", str(tmp_path), "--top", "top", "--format", "mermaid"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.startswith("flowchart TD")
    assert "pure_pass_through" in captured.out


def test_preview_collapse_replaces_wrapper_with_renamed_child_instance(tmp_path):
    design = parse_files(_write_design(tmp_path, PURE_RENAME_WRAPPER_V))

    preview = preview_collapse_hierarchy(design, "top/u_wrap")

    payload = preview.to_dict()
    replacement = payload["edits"][0]["replacement"]
    assert payload["ok"] is True
    assert payload["confidence"] == "safe"
    assert payload["renames"] == [{"from": "u_wrap/u_core", "to": "u_wrap__u_core"}]
    assert "wrapper u_wrap" not in replacement
    assert "core u_wrap__u_core" in replacement
    assert ".din(data_i)" in replacement
    assert ".dout(data_o)" in replacement
    assert "diff --git" not in payload["diff"]
    assert "-    wrapper u_wrap" in payload["diff"]
    assert "+    core u_wrap__u_core" in payload["diff"]


def test_preview_collapse_remaps_alias_assign_connections(tmp_path):
    design = parse_files(_write_design(tmp_path, PURE_ALIAS_WRAPPER_V))

    preview = preview_collapse_hierarchy(design, "top/u_wrap")

    replacement = preview.to_dict()["edits"][0]["replacement"]
    assert ".dout(data_o)" in replacement


def test_preview_collapse_blocks_non_pure_wrapper(tmp_path):
    design = parse_files(_write_design(tmp_path, STRUCTURAL_WRAPPER_V))

    preview = preview_collapse_hierarchy(design, "top/u_wrap")

    payload = preview.to_dict()
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "unsupported-wrapper-class"
    assert payload["edits"] == []


def test_hierarchy_collapse_cli_emits_json_preview(tmp_path, capsys):
    _write_design(tmp_path, PURE_RENAME_WRAPPER_V)

    exit_code = cli_main(
        [
            "hierarchy",
            "collapse",
            str(tmp_path),
            "--top",
            "top",
            "--instance",
            "top/u_wrap",
            "--preview",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    preview = payload["result"]["preview"]
    assert exit_code == 0
    assert payload["command"] == "hierarchy collapse"
    assert preview["ok"] is True
    assert "core u_wrap__u_core" in preview["edits"][0]["replacement"]


def test_hierarchy_collapse_cli_prints_diff_preview(tmp_path, capsys):
    _write_design(tmp_path, PURE_RENAME_WRAPPER_V)

    exit_code = cli_main(
        [
            "hierarchy",
            "collapse",
            str(tmp_path),
            "--top",
            "top",
            "--instance",
            "top/u_wrap",
            "--preview",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "--- " in captured.out
    assert "-    wrapper u_wrap" in captured.out
    assert "+    core u_wrap__u_core" in captured.out


def test_apply_collapse_preview_writes_parent_file(tmp_path):
    paths = _write_design(tmp_path, PURE_RENAME_WRAPPER_V)
    top_path = paths[0]
    design = parse_files(paths)
    preview = preview_collapse_hierarchy(design, "top/u_wrap")

    result = apply_collapse_preview(preview)

    text = top_path.read_text(encoding="utf-8")
    assert result.applied is True
    assert result.written_files == (str(top_path),)
    assert "wrapper u_wrap" not in text
    assert "core u_wrap__u_core" in text
    reparsed = parse_files(paths)
    top = reparsed.get_module("top")
    assert top is not None
    assert [inst.instance_name for inst in top.instances] == ["u_wrap__u_core"]


def test_apply_collapse_preview_rejects_stale_source(tmp_path):
    paths = _write_design(tmp_path, PURE_RENAME_WRAPPER_V)
    top_path = paths[0]
    design = parse_files(paths)
    preview = preview_collapse_hierarchy(design, "top/u_wrap")
    top_path.write_text(top_path.read_text(encoding="utf-8").replace("u_wrap", "u_changed"), encoding="utf-8")

    result = apply_collapse_preview(preview)

    assert result.applied is False
    assert result.diagnostics[0].code == "stale-preview"
    assert "u_changed" in top_path.read_text(encoding="utf-8")


def test_hierarchy_collapse_cli_write_applies_and_reports_json(tmp_path, capsys):
    paths = _write_design(tmp_path, PURE_RENAME_WRAPPER_V)
    top_path = paths[0]

    exit_code = cli_main(
        [
            "hierarchy",
            "collapse",
            str(tmp_path),
            "--top",
            "top",
            "--instance",
            "top/u_wrap",
            "--write",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    apply_result = payload["result"]["apply"]
    assert exit_code == 0
    assert apply_result["applied"] is True
    assert apply_result["writtenFiles"] == [str(top_path)]
    assert "core u_wrap__u_core" in top_path.read_text(encoding="utf-8")


def test_hierarchy_collapse_cli_write_blocks_non_pure_wrapper(tmp_path, capsys):
    paths = _write_design(tmp_path, STRUCTURAL_WRAPPER_V)
    top_path = paths[0]

    exit_code = cli_main(
        [
            "hierarchy",
            "collapse",
            str(tmp_path),
            "--top",
            "top",
            "--instance",
            "top/u_wrap",
            "--write",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    apply_result = payload["result"]["apply"]
    assert exit_code == 0
    assert apply_result["applied"] is False
    assert apply_result["diagnostics"][0]["code"] == "preview-not-applicable"
    assert "wrapper u_wrap" in top_path.read_text(encoding="utf-8")


def test_extract_submodule_preview_generates_child_module_and_instance(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_CHAIN_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "assign mid"),
        _line_containing(top_path, "assign y"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    child_path = tmp_path / "extracted_logic.v"
    assert payload["ok"] is True
    assert sorted(edits_by_file) == [str(child_path), str(top_path)]
    assert payload["boundary"] == {"inputs": ["a", "b", "c"], "outputs": ["y"], "internals": ["mid"]}
    normalized = payload["metadata"]["selectionNormalization"]
    assert [item["kind"] for item in normalized["items"]] == ["continuous_assign", "continuous_assign"]
    assert all(item["supported"] for item in normalized["items"])
    assert "module extracted_logic" in payload["generatedModule"]
    assert "wire [7:0] mid;" in payload["generatedModule"]
    assert "assign mid = a & b;" in payload["generatedModule"]
    assert "assign y = mid | c;" in payload["generatedModule"]
    assert "extracted_logic u_extracted_logic (.a(a), .b(b), .c(c), .y(y));" in parent_text
    assert "assign mid = a & b;" not in parent_text
    assert payload["generatedModule"] == edits_by_file[str(child_path)][0]["replacement"]
    assert "extracted_logic u_extracted_logic" in payload["diff"]


def test_extract_submodule_preview_moves_selected_helper_declarations(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_WITH_HELPERS_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "localparam"),
        _line_containing(top_path, "assign y"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    child_path = tmp_path / "extracted_logic.v"
    generated = payload["generatedModule"]
    assert payload["ok"] is True
    assert payload["boundary"] == {"inputs": ["a", "b"], "outputs": ["y"], "internals": ["mid"]}
    assert payload["metadata"]["selectedDeclarations"] == {
        "parameters": ["MASK"],
        "nets": ["mid"],
        "variables": [],
    }
    assert "localparam MASK = 8'hf0;" in generated
    assert "wire [7:0] mid;" in generated
    assert ".MASK(" not in parent_text
    assert ".mid(" not in parent_text
    assert "localparam MASK" not in parent_text
    assert "wire [7:0] mid;" not in parent_text
    child_path.write_text(edits_by_file[str(child_path)][0]["replacement"], encoding="utf-8")
    top_path.write_text(parent_text, encoding="utf-8")
    roundtrip = parse_files([top_path, child_path])
    assert roundtrip.get_module("extracted_logic") is not None


def test_extract_submodule_preview_keeps_parent_parameter_and_binds_child_override(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_WITH_PARENT_PARAMETER_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "parameter WIDTH"),
        _line_containing(top_path, "assign y"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    child_path = tmp_path / "extracted_logic.v"
    generated = payload["generatedModule"]
    assert payload["ok"] is True
    assert payload["metadata"]["selectedDeclarations"] == {
        "parameters": ["WIDTH"],
        "nets": ["mid"],
        "variables": [],
    }
    assert "module extracted_logic #(parameter WIDTH = 8)" in generated
    assert "wire [WIDTH - 1:0] mid;" in generated
    assert "parameter WIDTH = 8;" in parent_text
    assert "wire [WIDTH-1:0] mid;" not in parent_text
    assert "extracted_logic #(.WIDTH(WIDTH)) u_extracted_logic" in parent_text

    result = apply_extract_preview(preview)

    assert result.applied is True
    assert result.written_files == (str(top_path), str(child_path))
    parent_written = top_path.read_text(encoding="utf-8")
    child_written = child_path.read_text(encoding="utf-8")
    assert "parameter WIDTH = 8;" in parent_written
    assert "extracted_logic #(.WIDTH(WIDTH)) u_extracted_logic" in parent_written
    assert "module extracted_logic #(parameter WIDTH = 8)" in child_written
    roundtrip = parse_files([top_path, child_path])
    assert roundtrip.get_module("top") is not None
    assert roundtrip.get_module("extracted_logic") is not None


def test_extract_submodule_preview_blocks_selected_parameter_dependency(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_WITH_PARAMETER_DEPENDENCY_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "parameter WIDTH"),
        _line_containing(top_path, "assign y"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "unsupported-parameter-dependencies"
    assert "BASE" in payload["diagnostics"][0]["message"]
    assert payload["edits"] == []


def test_extract_submodule_preview_auto_copies_unselected_localparam(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_AUTO_COPY_LOCALPARAM_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "assign mid"),
        _line_containing(top_path, "assign y = mid"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is True, payload
    generated = payload["generatedModule"]
    assert "localparam W" in generated
    assert "localparam OFFSET" in generated
    assert "input OFFSET" not in generated
    assert "input W" not in generated
    assert "[W - 1:0] mid" in generated or "[W-1:0] mid" in generated


def test_extract_submodule_preview_auto_forwards_unselected_parameter(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_AUTO_FORWARD_PARAMETER_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "assign mid"),
        _line_containing(top_path, "assign y = mid"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is True, payload
    generated = payload["generatedModule"]
    assert "parameter W" in generated
    assert "input W" not in generated
    edit_text = "\n".join(edit.get("replacement", "") for edit in payload["edits"])
    assert ".W(W)" in edit_text


def test_extract_submodule_preview_auto_copies_localparam_chain(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_AUTO_COPY_LOCALPARAM_CHAIN_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "assign mid"),
        _line_containing(top_path, "assign y = mid"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is True, payload
    generated = payload["generatedModule"]
    assert "localparam BASE" in generated
    assert "localparam W" in generated
    assert generated.index("localparam BASE") < generated.index("localparam W")


def test_extract_submodule_preview_blocks_localparam_depending_on_signal(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_AUTO_COPY_LOCALPARAM_DEPENDS_ON_SIGNAL_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "assign y"),
        _line_containing(top_path, "assign y"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is False, payload
    codes = {d["code"] for d in payload["diagnostics"]}
    assert any("depends-on-signal" in c or "parameter-dependencies" in c for c in codes), payload


def test_extract_submodule_preview_auto_copies_localparam_in_procedural_block(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_AUTO_COPY_LOCALPARAM_PROCEDURAL_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "always @(posedge clk)"),
        _line_containing(top_path, "end"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is True, payload
    generated = payload["generatedModule"]
    assert "localparam OFFSET" in generated
    assert "input OFFSET" not in generated


def test_extract_submodule_preview_skips_block_local_var_shadowing_parent_localparam(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_AUTO_COPY_BLOCK_LOCAL_SHADOW_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "always @(posedge clk)"),
        _line_containing(top_path, "end"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    if payload["ok"]:
        generated = payload["generatedModule"]
        # Block-local W shadows parent W; parent localparam W must NOT be auto-copied
        # into the child since the block reference resolves locally.
        assert "localparam W" not in generated, generated


def test_extract_submodule_preview_supports_instance_group_extract(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_INSTANCE_GROUP_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "wire [7:0] mid"),
        _line_containing(top_path, "or_leaf u_or"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    child_path = tmp_path / "extracted_logic.v"
    generated = payload["generatedModule"]
    normalized = payload["metadata"]["selectionNormalization"]
    assert payload["ok"] is True
    assert payload["boundary"] == {"inputs": ["a", "b", "c"], "outputs": ["y"], "internals": ["mid"]}
    assert payload["metadata"]["selectedInstances"] == 2
    assert payload["metadata"]["selectedDeclarations"] == {
        "parameters": [],
        "nets": ["mid"],
        "variables": [],
    }
    assert [item["kind"] for item in normalized["items"]] == ["net", "instance", "instance"]
    assert all(item["supported"] for item in normalized["items"])
    assert "module extracted_logic" in generated
    assert "wire [7:0] mid;" in generated
    assert "and_leaf u_and (.a(a), .b(b), .y(mid));" in generated
    assert "or_leaf u_or (.a(mid), .b(c), .y(y));" in generated
    assert "wire [7:0] mid;" not in parent_text
    assert "and_leaf u_and" not in parent_text
    assert "or_leaf u_or" not in parent_text
    assert "extracted_logic u_extracted_logic (.a(a), .b(b), .c(c), .y(y));" in parent_text

    result = apply_extract_preview(preview)

    assert result.applied is True
    assert result.written_files == (str(top_path), str(child_path))
    roundtrip = parse_files([top_path, child_path])
    assert roundtrip.get_module("top") is not None
    assert roundtrip.get_module("extracted_logic") is not None


def test_extract_submodule_preview_supports_parameterized_instance_group_extract(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_INSTANCE_GROUP_WITH_PARAMETER_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "parameter WIDTH"),
        _line_containing(top_path, "buf_leaf #(.WIDTH(WIDTH)) u_right"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    generated = payload["generatedModule"]
    assert payload["ok"] is True
    assert payload["metadata"]["selectedInstances"] == 2
    assert payload["metadata"]["selectedDeclarations"] == {
        "parameters": ["WIDTH"],
        "nets": ["mid"],
        "variables": [],
    }
    assert "module extracted_logic #(parameter WIDTH = 8)" in generated
    assert "buf_leaf #(.WIDTH(WIDTH)) u_left" in generated
    assert "buf_leaf #(.WIDTH(WIDTH)) u_right" in generated
    assert "parameter WIDTH = 8;" in parent_text
    assert "extracted_logic #(.WIDTH(WIDTH)) u_extracted_logic" in parent_text


def test_extract_submodule_preview_supports_mixed_structural_extract(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_MIXED_STRUCTURAL_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "wire [7:0] a_i"),
        _line_containing(top_path, "assign y = y_i"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    child_path = tmp_path / "extracted_logic.v"
    generated = payload["generatedModule"]
    normalized = payload["metadata"]["selectionNormalization"]
    assert payload["ok"] is True
    assert payload["boundary"] == {"inputs": ["a", "b", "c"], "outputs": ["y"], "internals": ["a_i", "mid", "y_i"]}
    assert payload["metadata"]["selectedAssignments"] == 2
    assert payload["metadata"]["selectedInstances"] == 2
    assert payload["metadata"]["selectedDeclarations"] == {
        "parameters": [],
        "nets": ["a_i", "mid", "y_i"],
        "variables": [],
    }
    assert [item["kind"] for item in normalized["items"]] == [
        "net",
        "net",
        "net",
        "continuous_assign",
        "instance",
        "instance",
        "continuous_assign",
    ]
    assert all(item["supported"] for item in normalized["items"])
    assert "module extracted_logic" in generated
    assert "wire [7:0] a_i;" in generated
    assert "wire [7:0] mid;" in generated
    assert "wire [7:0] y_i;" in generated
    assert "assign a_i = a;" in generated
    assert "assign y = y_i;" in generated
    assert "and_leaf u_and (.a(a_i), .b(b), .y(mid));" in generated
    assert "or_leaf u_or (.a(mid), .b(c), .y(y_i));" in generated
    assert "assign a_i = a;" not in parent_text
    assert "assign y = y_i;" not in parent_text
    assert "and_leaf u_and" not in parent_text
    assert "or_leaf u_or" not in parent_text
    assert "extracted_logic u_extracted_logic (.a(a), .b(b), .c(c), .y(y));" in parent_text

    result = apply_extract_preview(preview)

    assert result.applied is True
    assert result.written_files == (str(top_path), str(child_path))
    roundtrip = parse_files([top_path, child_path])
    assert roundtrip.get_module("top") is not None
    assert roundtrip.get_module("extracted_logic") is not None


def test_extract_submodule_preview_supports_input_expression_connection(tmp_path):
    """Input-side expressions like `.a(a & b)` are accepted and contribute their reads to the boundary."""
    top_path = _write_single(tmp_path, EXTRACT_INSTANCE_GROUP_COMPLEX_CONNECTION_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "and_leaf u_and"),
        _line_containing(top_path, "and_leaf u_and"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is True, payload["diagnostics"]
    assert sorted(payload["boundary"]["inputs"]) == ["a", "b", "c"]
    assert payload["boundary"]["outputs"] == ["y"]
    generated = payload["generatedModule"]
    assert "and_leaf u_and (.a(a & b), .b(c), .y(y));" in generated
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    assert "extracted_logic u_extracted_logic (.a(a), .b(b), .c(c), .y(y));" in parent_text


def test_extract_submodule_preview_supports_broad_input_expression_shapes(tmp_path):
    """Concat, replication, slice, ternary, unary and binary forms are all accepted on input ports."""
    top_path = _write_single(tmp_path, EXTRACT_INSTANCE_GROUP_INPUT_SHAPES_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "and_leaf u_and"),
        _line_containing(top_path, "and_leaf u_and"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is True, payload["diagnostics"]
    # Every parent identifier referenced by any input expression must show up as a boundary input.
    assert set(payload["boundary"]["inputs"]) == {"a", "b", "hi", "lo", "wide", "sel"}
    assert payload["boundary"]["outputs"] == ["y"]


def test_extract_submodule_preview_blocks_function_call_instance_connection(tmp_path):
    """System / user function calls inside an input connection remain rejected."""
    top_path = _write_single(tmp_path, EXTRACT_INSTANCE_GROUP_FUNCALL_CONNECTION_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "and_leaf u_and"),
        _line_containing(top_path, "and_leaf u_and"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is False
    codes = [d["code"] for d in payload["diagnostics"]]
    assert "unsupported-instance-connection" in codes
    assert payload["edits"] == []


def test_extract_submodule_preview_supports_complex_output_instance_connection(tmp_path):
    """Slice/concat output expressions are accepted via a synthesized child output port."""
    top_path = _write_single(tmp_path, EXTRACT_INSTANCE_GROUP_OUTPUT_SLICE_CONNECTION_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "and_leaf u_and"),
        _line_containing(top_path, "and_leaf u_and"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is True, payload["diagnostics"]
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    generated = payload["generatedModule"]
    # Underlying parent signal y is not added as a regular boundary output - it
    # stays declared on the parent and is bound to a synthesized child port.
    assert "y" not in payload["boundary"]["outputs"]
    assert "output [7:0] u_and_y" in generated
    assert "and_leaf u_and (.a(a), .b(b), .y(u_and_y));" in generated
    assert "and_leaf u_and" not in parent_text
    assert "extracted_logic u_extracted_logic" in parent_text
    assert ".u_and_y(y[7:0])" in parent_text


def test_extract_submodule_preview_blocks_parameterized_output_instance_connection(tmp_path):
    """Complex output connections on parameterized inner ports are still rejected."""
    source = """\
module buf_leaf #(parameter WIDTH = 8)(
    input  [WIDTH-1:0] a,
    output [WIDTH-1:0] y
);
    assign y = a;
endmodule

module top #(parameter WIDTH = 8)(
    input  [WIDTH-1:0] a,
    output [2*WIDTH-1:0] y
);
    buf_leaf #(.WIDTH(WIDTH)) u_buf(.a(a), .y(y[WIDTH-1:0]));
endmodule
"""
    top_path = _write_single(tmp_path, source)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "buf_leaf #(.WIDTH(WIDTH)) u_buf"),
        _line_containing(top_path, "buf_leaf #(.WIDTH(WIDTH)) u_buf"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is False
    codes = {d["code"] for d in payload["diagnostics"]}
    # Either a parameter-dependency diagnostic on the output expression or the
    # explicit parameterized-port-width rejection is acceptable; both block apply.
    assert codes & {"unsupported-output-instance-connection", "unsupported-instance-parameter-dependencies"}
    assert payload["edits"] == []


def test_extract_submodule_preview_supports_concat_output_instance_connection(tmp_path):
    """A concat output expression maps to one synthetic port with the inner port's width."""
    source = """\
module split_leaf(
    input  [7:0] a,
    output [7:0] y
);
    assign y = a;
endmodule

module top(
    input  [7:0] a,
    output [3:0] hi,
    output [3:0] lo
);
    split_leaf u_split(.a(a), .y({hi, lo}));
endmodule
"""
    top_path = _write_single(tmp_path, source)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "split_leaf u_split"),
        _line_containing(top_path, "split_leaf u_split"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is True, payload["diagnostics"]
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    generated = payload["generatedModule"]
    assert "output [7:0] u_split_y" in generated
    assert "split_leaf u_split (.a(a), .y(u_split_y));" in generated
    assert ".u_split_y({hi, lo})" in parent_text


def test_extract_submodule_preview_supports_positional_instance_connections(tmp_path):
    """Positional (ordered) port bindings on selected child instances extract cleanly."""
    source = """\
module and_leaf(
    input  [7:0] a,
    input  [7:0] b,
    output [7:0] y
);
    assign y = a & b;
endmodule

module top(
    input  [7:0] a,
    input  [7:0] b,
    output [7:0] y
);
    and_leaf u_and(a, b, y);
endmodule
"""
    top_path = _write_single(tmp_path, source)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "and_leaf u_and"),
        _line_containing(top_path, "and_leaf u_and"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is True, payload["diagnostics"]
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    generated = payload["generatedModule"]
    # Boundary correctly resolves positional ports to a/b/y.
    assert payload["boundary"]["inputs"] == ["a", "b"]
    assert payload["boundary"]["outputs"] == ["y"]
    # Inner positional binding is preserved on the moved instance.
    assert "and_leaf u_and (a, b, y);" in generated
    # New outer instance always emits named bindings, regardless of the source style.
    assert "extracted_logic u_extracted_logic (.a(a), .b(b), .y(y));" in parent_text


def test_extract_submodule_preview_supports_positional_with_complex_output(tmp_path):
    """Positional binding + complex output expression still synthesizes a child output port."""
    source = """\
module split_leaf(
    input  [7:0] a,
    output [7:0] y
);
    assign y = a;
endmodule

module top(
    input  [7:0] a,
    output [3:0] hi,
    output [3:0] lo
);
    split_leaf u_split(a, {hi, lo});
endmodule
"""
    top_path = _write_single(tmp_path, source)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "split_leaf u_split"),
        _line_containing(top_path, "split_leaf u_split"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is True, payload["diagnostics"]
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    generated = payload["generatedModule"]
    assert "output [7:0] u_split_y" in generated
    # Positional rewrite preserved: the second positional arg now references the synthetic port.
    assert "split_leaf u_split (a, u_split_y);" in generated
    assert ".u_split_y({hi, lo})" in parent_text


def test_extract_submodule_preview_supports_positional_with_input_expression(tmp_path):
    """Positional binding + complex input expression participates in input shape acceptance."""
    source = """\
module and_leaf(
    input  [7:0] a,
    input  [7:0] b,
    output [7:0] y
);
    assign y = a & b;
endmodule

module top(
    input  [7:0] a,
    input  [7:0] b,
    input  sel,
    output [7:0] y
);
    and_leaf u_and(sel ? a : 8'hFF, ~b, y);
endmodule
"""
    top_path = _write_single(tmp_path, source)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "and_leaf u_and"),
        _line_containing(top_path, "and_leaf u_and"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is True, payload["diagnostics"]
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    generated = payload["generatedModule"]
    assert set(payload["boundary"]["inputs"]) == {"a", "b", "sel"}
    assert payload["boundary"]["outputs"] == ["y"]
    # Inner positional binding preserved unchanged on the moved instance — the
    # expression itself lives inside the child, computed from the new boundary inputs.
    assert "and_leaf u_and (sel ? a : 8'hFF, ~b, y);" in generated
    # Outer parent instance simply passes through plain identifiers for the
    # promoted boundary inputs, regardless of how complex the inner expression was.
    assert "extracted_logic u_extracted_logic (.a(a), .b(b), .sel(sel), .y(y));" in parent_text


def test_extract_submodule_preview_blocks_hierarchical_reference_in_continuous_assign(tmp_path):
    """A continuous assign that reads through a hierarchical path is rejected."""
    source = """\
module sub(input x, output y);
    assign y = x;
endmodule

module top(input x, output y, output z);
    sub u_sub(.x(x), .y(y));
    assign z = u_sub.y;
endmodule
"""
    top_path = _write_single(tmp_path, source)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "assign z = u_sub.y"),
        _line_containing(top_path, "assign z = u_sub.y"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )
    payload = preview.to_dict()
    assert payload["ok"] is False
    codes = [d["code"] for d in payload["diagnostics"]]
    assert "unsupported-hierarchical-reference" in codes
    assert payload["edits"] == []


def test_extract_submodule_preview_blocks_hierarchical_reference_in_always_block(tmp_path):
    """An always block that reads through a hierarchical path is rejected."""
    source = """\
module sub(input clk, input x, output reg y);
    always @(posedge clk) y <= x;
endmodule

module top(input clk, input x, output y, output reg z);
    sub u_sub(.clk(clk), .x(x), .y(y));
    always @(posedge clk) z <= u_sub.y;
endmodule
"""
    top_path = _write_single(tmp_path, source)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "always @(posedge clk) z <= u_sub.y"),
        _line_containing(top_path, "always @(posedge clk) z <= u_sub.y"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )
    payload = preview.to_dict()
    assert payload["ok"] is False
    codes = [d["code"] for d in payload["diagnostics"]]
    assert "unsupported-hierarchical-reference" in codes
    assert payload["edits"] == []


def test_extract_submodule_preview_blocks_hierarchical_reference_in_input_connection(tmp_path):
    """A selected instance whose input connection reads through a hierarchical path is rejected."""
    source = """\
module sub(input x, output y);
    assign y = x;
endmodule

module sib(output reg q);
endmodule

module top(input x, output y);
    sib u_sib();
    sub u_sub(.x(u_sib.q), .y(y));
endmodule
"""
    top_path = _write_single(tmp_path, source)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "sub u_sub"),
        _line_containing(top_path, "sub u_sub"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )
    payload = preview.to_dict()
    assert payload["ok"] is False
    codes = [d["code"] for d in payload["diagnostics"]]
    assert "unsupported-hierarchical-reference" in codes
    assert payload["edits"] == []


def test_extract_submodule_preview_blocks_hierarchical_reference_in_output_connection(tmp_path):
    """A selected instance whose output drives through a hierarchical path is rejected."""
    source = """\
module sub(input x, output y);
    assign y = x;
endmodule

module sib(input d);
endmodule

module top(input x);
    sib u_sib(.d(1'b0));
    sub u_sub(.x(x), .y(u_sib.d));
endmodule
"""
    top_path = _write_single(tmp_path, source)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "sub u_sub"),
        _line_containing(top_path, "sub u_sub"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )
    payload = preview.to_dict()
    assert payload["ok"] is False
    codes = [d["code"] for d in payload["diagnostics"]]
    # Output-side hierarchical refs now surface the specific hierarchical-ref
    # diagnostic instead of the generic non-writable shape rejection.
    assert "unsupported-hierarchical-reference" in codes
    assert payload["edits"] == []


def test_extract_submodule_preview_blocks_unselected_parameter_in_connection(tmp_path):
    """Unselected parent parameters referenced by lifted instance bindings are
    auto-forwarded as parameter ports on the extracted child; the preview
    succeeds and the child carries an inherited ``WIDTH`` parameter with a
    matching ``#(.WIDTH(WIDTH))`` binding on the new outer instance.
    """
    top_path = _write_single(tmp_path, EXTRACT_INSTANCE_GROUP_PARAM_DEP_CONNECTION_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "buf_leaf #(.WIDTH(WIDTH)) u_buf"),
        _line_containing(top_path, "buf_leaf #(.WIDTH(WIDTH)) u_buf"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is True, payload
    assert "extracted_logic" in payload["generatedModule"]
    assert "parameter WIDTH" in payload["generatedModule"]
    edit_text = "\n".join(edit.get("replacement", "") for edit in payload["edits"])
    assert ".WIDTH(WIDTH)" in edit_text


def test_extract_submodule_preview_keeps_selected_boundary_declaration_in_parent(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_WITH_BOUNDARY_DECL_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "wire"),
        _line_containing(top_path, "assign y_int"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    child_path = tmp_path / "extracted_logic.v"
    generated = payload["generatedModule"]

    assert payload["ok"] is True
    assert payload["boundary"] == {"inputs": ["a"], "outputs": ["y_int"], "internals": []}
    assert payload["metadata"]["selectedDeclarations"] == {"parameters": [], "nets": [], "variables": []}
    assert payload["diagnostics"][0]["code"] == "selected-boundary-declaration"
    assert payload["diagnostics"][0]["severity"] == "info"
    assert "wire [7:0] y_int;" in parent_text
    assert "assign y = y_int;" in parent_text
    assert "extracted_logic u_extracted_logic (.a(a), .y_int(y_int));" in parent_text
    assert "wire [7:0] y_int;" not in generated
    assert "output wire [7:0] y_int" in generated
    child_path.write_text(edits_by_file[str(child_path)][0]["replacement"], encoding="utf-8")


def test_extract_always_preview_keeps_boundary_declaration_and_moves_internal_one(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_ALWAYS_WITH_BOUNDARY_DECL_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "reg tick"),
        _line_containing(top_path, "end"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    generated = payload["generatedModule"]

    assert payload["ok"] is True
    assert payload["boundary"] == {"inputs": ["clk", "a"], "outputs": ["tick"], "internals": ["cnt"]}
    assert payload["metadata"]["selectedDeclarations"] == {"parameters": [], "nets": [], "variables": ["cnt"]}
    assert payload["diagnostics"][0]["code"] == "selected-boundary-declaration"
    assert payload["diagnostics"][0]["severity"] == "info"
    assert "wire tick;" in parent_text
    assert "reg cnt" not in parent_text
    assert "assign y = tick;" in parent_text
    assert "reg cnt = 0;" in generated
    assert "output reg tick" in generated


def test_extract_always_preview_keeps_unrelated_selected_declaration_in_parent(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_ALWAYS_WITH_EXTRA_DECL_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "reg tick"),
        _line_containing(top_path, "unused"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    generated = payload["generatedModule"]
    codes = [diag["code"] for diag in payload["diagnostics"]]
    severities = {diag["code"]: diag["severity"] for diag in payload["diagnostics"]}

    assert payload["ok"] is True
    assert payload["boundary"] == {"inputs": ["clk", "a"], "outputs": ["tick"], "internals": ["cnt"]}
    assert payload["metadata"]["selectedDeclarations"] == {"parameters": [], "nets": [], "variables": ["cnt"]}
    assert "selected-boundary-declaration" in codes
    assert "selected-declaration-not-in-child" in codes
    assert severities["selected-boundary-declaration"] == "info"
    assert severities["selected-declaration-not-in-child"] == "info"
    assert '(* keep = "true" *) reg unused = 0;' in parent_text
    assert "reg unused" not in generated


def test_extract_submodule_preview_blocks_unsupported_lhs(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_UNSUPPORTED_LHS_V)
    design = parse_files([top_path])
    line = _line_containing(top_path, "assign")

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=ExtractSelection(str(top_path), line, line),
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "unsupported-extract-lhs"
    assert payload["edits"] == []


def test_extract_selection_normalization_supports_always_block_preview(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_ALWAYS_V)
    design = parse_files([top_path])
    module = design.get_module("top")
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "always"),
        _last_line_containing(top_path, "    end"),
    )

    normalized = normalize_extract_selection(module, selection)
    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    normalized_payload = normalized.to_dict()
    preview_payload = preview.to_dict()
    edits_by_file = _preview_edits_by_file(preview_payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    child_path = tmp_path / "extracted_logic.v"
    assert normalized_payload["items"][0]["kind"] == "always_block"
    assert normalized_payload["items"][0]["supported"] is True
    assert normalized_payload["diagnostics"] == []
    assert preview_payload["ok"] is True
    assert preview_payload["boundary"] == {"inputs": ["clk", "rst", "d"], "outputs": ["q"], "internals": []}
    assert preview_payload["metadata"]["selectionNormalization"]["items"][0]["kind"] == "always_block"
    assert preview_payload["metadata"]["selectedAlwaysBlocks"] == 1
    assert "module extracted_logic (" in preview_payload["generatedModule"]
    assert "output reg q" in preview_payload["generatedModule"]
    assert "always @(posedge clk) begin" in preview_payload["generatedModule"]
    assert "if (rst)" in preview_payload["generatedModule"]
    assert "output q" in parent_text
    assert "output reg q" not in parent_text
    assert "extracted_logic u_extracted_logic (.clk(clk), .rst(rst), .d(d), .q(q));" in parent_text
    assert "always @(posedge clk)" not in parent_text
    child_path.write_text(edits_by_file[str(child_path)][0]["replacement"], encoding="utf-8")
    top_path.write_text(parent_text, encoding="utf-8")
    roundtrip = parse_files([top_path, child_path])
    assert roundtrip.get_module("top") is not None
    assert roundtrip.get_module("extracted_logic") is not None


def test_extract_selection_normalization_keeps_parent_parameter_for_always_extract(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_ALWAYS_WITH_PARENT_PARAMETER_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "parameter WIDTH"),
        _last_line_containing(top_path, "    end"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    edits_by_file = _preview_edits_by_file(payload)
    parent_text = _apply_preview_edits(top_path.read_text(encoding="utf-8"), edits_by_file[str(top_path)])
    assert payload["ok"] is True
    assert payload["metadata"]["selectedDeclarations"] == {
        "parameters": ["WIDTH"],
        "nets": [],
        "variables": [],
    }
    assert "module extracted_logic #(parameter WIDTH = 4)" in payload["generatedModule"]
    assert "output reg [WIDTH - 1:0] q" in payload["generatedModule"]
    assert "parameter WIDTH = 4;" in parent_text
    assert "extracted_logic #(.WIDTH(WIDTH)) u_extracted_logic" in parent_text


def test_extract_selection_normalization_supports_initial_block_extract(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_INITIAL_V)
    design = parse_files([top_path])
    module = design.get_module("top")
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "initial"),
        _last_line_containing(top_path, "    end"),
    )

    normalized = normalize_extract_selection(module, selection)
    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    normalized_payload = normalized.to_dict()
    preview_payload = preview.to_dict()
    assert normalized_payload["items"][0]["kind"] == "initial_block"
    assert normalized_payload["items"][0]["supported"] is True
    assert normalized_payload["diagnostics"] == []
    assert preview_payload["ok"] is True
    assert preview_payload["boundary"] == {"inputs": ["d"], "outputs": ["q"], "internals": []}
    assert preview_payload["metadata"]["selectionNormalization"]["items"][0]["kind"] == "initial_block"
    assert preview_payload["metadata"]["selectedAlwaysBlocks"] == 0
    assert preview_payload["metadata"]["selectedInitialBlocks"] == 1
    assert "module extracted_logic (" in preview_payload["generatedModule"]
    assert "output reg q" in preview_payload["generatedModule"]
    assert "initial begin" in preview_payload["generatedModule"]

    result = apply_extract_preview(preview)

    child_path = tmp_path / "extracted_logic.v"
    assert result.applied is True
    assert result.written_files == (str(top_path), str(child_path))
    parent_text = top_path.read_text(encoding="utf-8")
    child_text = child_path.read_text(encoding="utf-8")
    assert "output q" in parent_text
    assert "output reg q" not in parent_text
    assert "extracted_logic u_extracted_logic (.d(d), .q(q));" in parent_text
    assert "initial begin" not in parent_text
    assert "initial begin" in child_text
    roundtrip = parse_files([top_path, child_path])
    assert roundtrip.get_module("top") is not None
    assert roundtrip.get_module("extracted_logic") is not None


def test_extract_selection_normalization_blocks_partial_statement_selection(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_ALWAYS_V)
    design = parse_files([top_path])
    module = design.get_module("top")
    line = _line_containing(top_path, "always")
    selection = ExtractSelection(str(top_path), line, line)

    normalized = normalize_extract_selection(module, selection)

    assert normalized.to_dict()["items"] == []
    assert normalized.to_dict()["diagnostics"][0]["code"] == "partial-selection"


def test_extract_always_preview_blocks_complex_procedural_lhs(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_ALWAYS_COMPLEX_LHS_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "always"),
        _last_line_containing(top_path, "    end"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "unsupported-procedural-lhs"
    assert payload["edits"] == []


def test_extract_always_preview_blocks_multiple_procedural_drivers(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_ALWAYS_MULTIPLE_DRIVER_V)
    design = parse_files([top_path])
    line = _line_containing(top_path, "always")
    selection = ExtractSelection(str(top_path), line, line + 2)

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "multiple-procedural-drivers"
    assert payload["edits"] == []


def test_extract_always_preview_blocks_multiple_selected_procedural_drivers_same_signal(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_ALWAYS_MULTIPLE_DRIVER_V)
    design = parse_files([top_path])
    first_line = _line_containing(top_path, "always @(posedge clk)")
    last_line = _line_containing(top_path, "1'b0;") + 1
    selection = ExtractSelection(str(top_path), first_line, last_line)

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    payload = preview.to_dict()
    assert payload["ok"] is False
    codes = [d["code"] for d in payload["diagnostics"]]
    assert "multiple-selected-procedural-drivers" in codes
    race_diag = next(d for d in payload["diagnostics"] if d["code"] == "multiple-selected-procedural-drivers")
    assert "'q'" in race_diag["message"]
    assert "race" in race_diag["message"].lower()
    assert payload["edits"] == []


def test_extract_submodule_preview_blocks_instance_name_collision(tmp_path):
    text = EXTRACT_CHAIN_V.replace("endmodule", "    child u_extracted_logic (.a(a));\nendmodule")
    top_path = _write_single(tmp_path, text)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path), _line_containing(top_path, "assign mid"), _line_containing(top_path, "assign y")
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    assert preview.to_dict()["diagnostics"][0]["code"] == "instance-name-collision"


def test_hierarchy_extract_cli_emits_json_preview(tmp_path, capsys):
    top_path = _write_single(tmp_path, EXTRACT_CHAIN_V)
    start = _line_containing(top_path, "assign mid")
    end = _line_containing(top_path, "assign y")

    exit_code = cli_main(
        [
            "hierarchy",
            "extract",
            str(tmp_path),
            "--module",
            "top",
            "--range",
            f"{top_path}:{start}-{end}",
            "--name",
            "extracted_logic",
            "--preview",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    preview = payload["result"]["preview"]
    assert exit_code == 0
    assert payload["command"] == "hierarchy extract"
    assert preview["ok"] is True
    assert preview["boundary"]["internals"] == ["mid"]
    assert "module extracted_logic" in preview["generatedModule"]


def test_apply_extract_preview_writes_parent_and_child_files(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_CHAIN_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "assign mid"),
        _line_containing(top_path, "assign y"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )

    result = apply_extract_preview(preview)

    child_path = tmp_path / "extracted_logic.v"
    assert result.applied is True
    assert result.written_files == (str(top_path), str(child_path))
    top_text = top_path.read_text(encoding="utf-8")
    child_text = child_path.read_text(encoding="utf-8")
    assert "assign mid = a & b;" not in top_text
    assert "extracted_logic u_extracted_logic" in top_text
    assert "module extracted_logic" in child_text
    roundtrip = parse_files([top_path, child_path])
    assert roundtrip.get_module("top") is not None
    assert roundtrip.get_module("extracted_logic") is not None


def test_apply_extract_preview_rejects_stale_source(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_CHAIN_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "assign mid"),
        _line_containing(top_path, "assign y"),
    )

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted_logic",
    )
    top_path.write_text(top_path.read_text(encoding="utf-8").replace("assign y", "assign z"), encoding="utf-8")

    result = apply_extract_preview(preview)

    assert result.applied is False
    assert result.diagnostics[0].code == "stale-preview"
    assert not (tmp_path / "extracted_logic.v").exists()


def test_hierarchy_extract_cli_write_applies_and_reports_json(tmp_path, capsys):
    top_path = _write_single(tmp_path, EXTRACT_CHAIN_V)
    start = _line_containing(top_path, "assign mid")
    end = _line_containing(top_path, "assign y")

    exit_code = cli_main(
        [
            "hierarchy",
            "extract",
            str(tmp_path),
            "--module",
            "top",
            "--range",
            f"{top_path}:{start}-{end}",
            "--name",
            "extracted_logic",
            "--write",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    apply_result = payload["result"]["apply"]
    child_path = tmp_path / "extracted_logic.v"
    assert exit_code == 0
    assert payload["command"] == "hierarchy extract"
    assert apply_result["applied"] is True
    assert apply_result["writtenFiles"] == [str(top_path), str(child_path)]
    assert "extracted_logic u_extracted_logic" in top_path.read_text(encoding="utf-8")
    assert "module extracted_logic" in child_path.read_text(encoding="utf-8")


def test_hierarchy_extract_cli_write_blocks_invalid_preview(tmp_path, capsys):
    top_path = _write_single(tmp_path, EXTRACT_UNSUPPORTED_LHS_V)
    line = _line_containing(top_path, "assign")

    exit_code = cli_main(
        [
            "hierarchy",
            "extract",
            str(tmp_path),
            "--module",
            "top",
            "--range",
            f"{top_path}:{line}-{line}",
            "--name",
            "extracted_logic",
            "--write",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    apply_result = payload["result"]["apply"]
    assert exit_code == 0
    assert apply_result["applied"] is False
    assert apply_result["diagnostics"][0]["code"] == "preview-not-applicable"
    assert not (tmp_path / "extracted_logic.v").exists()


def test_extract_selection_normalization_emits_expand_to_node_suggestion(tmp_path):
    """Partial selections should emit a structured expand-to-node suggestion."""

    top_path = _write_single(tmp_path, EXTRACT_ALWAYS_V)
    design = parse_files([top_path])
    module = design.get_module("top")
    always_start = _line_containing(top_path, "always")
    always_end = _last_line_containing(top_path, "    end")
    selection = ExtractSelection(str(top_path), always_start, always_start)

    normalized = normalize_extract_selection(module, selection)
    payload = normalized.to_dict()

    assert payload["items"] == []
    assert any(d["code"] == "partial-selection" for d in payload["diagnostics"])
    expand_suggestions = [s for s in payload["suggestions"] if s["kind"] == "expand-to-node"]
    assert len(expand_suggestions) == 1
    suggestion = expand_suggestions[0]
    assert suggestion["startLine"] == always_start
    assert suggestion["endLine"] == always_end
    assert suggestion["nodeKind"] == "always_block"
    assert "expand" in suggestion["label"].lower()
    assert suggestion["range"]["start"]["line"] == always_start - 1
    assert suggestion["range"]["end"]["line"] == always_end - 1
    diag_message = next(d["message"] for d in payload["diagnostics"] if d["code"] == "partial-selection")
    assert f"lines {always_start}-{always_end}" in diag_message
    # Single-node partial overlap: no extra cover-all suggestion is emitted because
    # the union range matches the per-node range exactly.
    assert all(s["kind"] != "expand-to-cover-selection" for s in payload["suggestions"])


def test_extract_selection_normalization_complete_selection_has_no_suggestions(tmp_path):
    """Selections that fully contain their nodes should not emit suggestions."""

    top_path = _write_single(tmp_path, EXTRACT_ALWAYS_V)
    design = parse_files([top_path])
    module = design.get_module("top")
    always_start = _line_containing(top_path, "always")
    always_end = _last_line_containing(top_path, "    end")
    selection = ExtractSelection(str(top_path), always_start, always_end)

    normalized = normalize_extract_selection(module, selection)
    payload = normalized.to_dict()

    assert payload["suggestions"] == []
    assert payload["diagnostics"] == []


def test_extract_selection_normalization_emits_cover_selection_for_multiple_nodes(tmp_path):
    """Selections that partially overlap multiple distinct nodes should also offer a union suggestion."""

    source = """\
module top(
    input clk,
    input a,
    input b,
    output reg x,
    output reg y
);
    always @(posedge clk) begin
        x <= a;
    end
    always @(posedge clk) begin
        y <= b;
    end
endmodule
"""
    top_path = _write_single(tmp_path, source)
    design = parse_files([top_path])
    module = design.get_module("top")
    first_always = _line_containing(top_path, "always @(posedge clk) begin")
    # Select from inside the first always block to inside the second always block.
    selection = ExtractSelection(str(top_path), first_always + 1, first_always + 4)

    normalized = normalize_extract_selection(module, selection)
    payload = normalized.to_dict()

    expand_suggestions = [s for s in payload["suggestions"] if s["kind"] == "expand-to-node"]
    cover_suggestions = [s for s in payload["suggestions"] if s["kind"] == "expand-to-cover-selection"]
    assert len(expand_suggestions) == 2
    assert len(cover_suggestions) == 1
    cover = cover_suggestions[0]
    assert cover["startLine"] == min(s["startLine"] for s in expand_suggestions)
    assert cover["endLine"] == max(s["endLine"] for s in expand_suggestions)


# ---------------------------------------------------------------------------
# Trace-neighborhood selection (signal mode)
# ---------------------------------------------------------------------------

TRACE_CHAIN_V = """\
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


def test_resolve_extract_selection_returns_none_for_range_mode(tmp_path):
    top_path = _write_single(tmp_path, TRACE_CHAIN_V)
    design = parse_files([top_path])
    analyze_design(design)
    selection = ExtractSelection(str(top_path), 1, 10)

    assert resolve_extract_selection(design, selection) is None


def test_resolve_signal_selection_includes_drivers_and_loads(tmp_path):
    top_path = _write_single(tmp_path, TRACE_CHAIN_V)
    design = parse_files([top_path])
    analyze_design(design)
    selection = ExtractSelection("", 0, 0, signal="mid", signal_module="top")

    normalized = resolve_extract_selection(design, selection)
    assert normalized is not None
    payload = normalized.to_dict()
    assert payload["diagnostics"] == []

    names = {item["name"] for item in payload["items"]}
    assert names == {"mid", "y"}
    assert all(item["kind"] == "continuous_assign" for item in payload["items"])
    sel_payload = payload["selection"]
    assert sel_payload["signal"] == "mid"
    assert sel_payload["signalModule"] == "top"


def test_resolve_signal_selection_unknown_signal(tmp_path):
    top_path = _write_single(tmp_path, TRACE_CHAIN_V)
    design = parse_files([top_path])
    analyze_design(design)
    selection = ExtractSelection("", 0, 0, signal="nope", signal_module="top")

    normalized = resolve_extract_selection(design, selection)
    assert normalized is not None
    codes = [d["code"] for d in normalized.to_dict()["diagnostics"]]
    assert "unknown-trace-signal" in codes


def test_resolve_signal_selection_unknown_module(tmp_path):
    top_path = _write_single(tmp_path, TRACE_CHAIN_V)
    design = parse_files([top_path])
    analyze_design(design)
    selection = ExtractSelection("", 0, 0, signal="mid", signal_module="missing")

    normalized = resolve_extract_selection(design, selection)
    assert normalized is not None
    codes = [d["code"] for d in normalized.to_dict()["diagnostics"]]
    assert "unknown-trace-module" in codes


def test_resolve_signal_selection_input_port_only_loads(tmp_path):
    top_path = _write_single(tmp_path, TRACE_CHAIN_V)
    design = parse_files([top_path])
    analyze_design(design)
    selection = ExtractSelection("", 0, 0, signal="a", signal_module="top")

    normalized = resolve_extract_selection(design, selection)
    assert normalized is not None
    payload = normalized.to_dict()
    # `a` is a top-level input → no in-module drivers, only `assign mid = a & b;` loads it.
    assert {item["name"] for item in payload["items"]} == {"mid"}
    # No empty-trace-neighborhood error since there is at least one load.
    error_codes = [d["code"] for d in payload["diagnostics"] if d["severity"] == "error"]
    assert "empty-trace-neighborhood" not in error_codes


def test_resolve_signal_selection_output_port_only_drivers(tmp_path):
    top_path = _write_single(tmp_path, TRACE_CHAIN_V)
    design = parse_files([top_path])
    analyze_design(design)
    selection = ExtractSelection("", 0, 0, signal="y", signal_module="top")

    normalized = resolve_extract_selection(design, selection)
    assert normalized is not None
    payload = normalized.to_dict()
    # `y` is a top-level output → driver `assign y = mid | c;`. No in-module loads.
    assert {item["name"] for item in payload["items"]} == {"y"}


def test_preview_extract_via_signal_matches_range_mode(tmp_path):
    top_path = _write_single(tmp_path, TRACE_CHAIN_V)
    design = parse_files([top_path])
    analyze_design(design)

    assign_mid_line = _line_containing(top_path, "assign mid = a & b;")
    assign_y_line = _line_containing(top_path, "assign y = mid | c;")
    range_selection = ExtractSelection(str(top_path), assign_mid_line, assign_y_line)
    range_preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=range_selection,
        extracted_module_name="extracted_range",
    )

    signal_selection = ExtractSelection("", 0, 0, signal="mid", signal_module="top")
    signal_preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=signal_selection,
        extracted_module_name="extracted_signal",
    )

    assert range_preview.ok is True
    assert signal_preview.ok is True
    range_meta = range_preview.metadata["selectionNormalization"]
    signal_meta = signal_preview.metadata["selectionNormalization"]
    range_items_set = {(i["kind"], i["name"]) for i in range_meta["items"]}
    signal_items_set = {(i["kind"], i["name"]) for i in signal_meta["items"]}
    assert range_items_set == signal_items_set == {("continuous_assign", "mid"), ("continuous_assign", "y")}


def test_preview_extract_via_signal_module_mismatch(tmp_path):
    top_path = _write_single(tmp_path, TRACE_CHAIN_V)
    design = parse_files([top_path])
    analyze_design(design)
    selection = ExtractSelection("", 0, 0, signal="mid", signal_module="other_module")

    preview = preview_extract_submodule(
        design,
        module_name="top",
        selection=selection,
        extracted_module_name="extracted",
    )

    assert preview.ok is False
    codes = [d["code"] for d in preview.to_dict()["diagnostics"]]
    assert "trace-module-mismatch" in codes


def test_resolve_signal_selection_does_not_pull_in_unrelated_node(tmp_path):
    """Sparse-neighborhood: an unrelated assign between driver and load lines must NOT be selected."""

    source = """\
module top(
    input [7:0] a,
    input [7:0] b,
    input [7:0] c,
    input [7:0] d,
    output [7:0] y,
    output [7:0] z
);
    wire [7:0] mid;
    assign mid = a & b;
    assign z = c & d;
    assign y = mid | b;
endmodule
"""
    top_path = _write_single(tmp_path, source)
    design = parse_files([top_path])
    analyze_design(design)
    selection = ExtractSelection("", 0, 0, signal="mid", signal_module="top")

    normalized = resolve_extract_selection(design, selection)
    assert normalized is not None
    names = {item["name"] for item in normalized.to_dict()["items"]}
    # `z` sits between mid's driver and load lines but is NOT in the neighborhood.
    assert names == {"mid", "y"}
    assert "z" not in names


# --- Bullet 7: combinational vs sequential always coverage --------------------


_ALWAYS_COV_FUNCTION_REF_V = """
module top(input wire [7:0] a, output reg [7:0] q);
  function [7:0] inv;
    input [7:0] x;
    inv = ~x;
  endfunction
  always @(*) q = inv(a);
endmodule
""".lstrip()


_ALWAYS_COV_TASK_REF_V = """
module top(input wire clk, input wire [7:0] a, output reg [7:0] q);
  task do_q;
    input [7:0] x;
    begin q <= x; end
  endtask
  always @(posedge clk) do_q(a);
endmodule
""".lstrip()


_ALWAYS_COV_SYSTEM_TASK_V = """
module top(input wire clk, input wire [7:0] a, output reg [7:0] q);
  always @(posedge clk) begin
    q <= a;
    $display("q=%h", q);
  end
endmodule
""".lstrip()


_ALWAYS_COV_LATCH_V = """
module top(input wire en, input wire [7:0] a, output reg [7:0] q);
  always @(*) if (en) q = a;
endmodule
""".lstrip()


_ALWAYS_COV_NO_LATCH_V = """
module top(input wire en, input wire [7:0] a, input wire [7:0] b, output reg [7:0] q);
  always @(*) if (en) q = a; else q = b;
endmodule
""".lstrip()


_ALWAYS_COV_MULTI_CLOCK_V = """
module top(
  input wire clkA, input wire clkB,
  input wire [7:0] a, input wire [7:0] b,
  output reg [7:0] qa, output reg [7:0] qb
);
  always @(posedge clkA) qa <= a;
  always @(posedge clkB) qb <= b;
endmodule
""".lstrip()


_ALWAYS_COV_SAME_CLOCK_BOTH_EDGES_V = """
module top(input wire clk, input wire [7:0] a, output reg [7:0] q);
  always @(posedge clk or negedge clk) q <= a;
endmodule
""".lstrip()


_ALWAYS_COV_ASYNC_RESET_V = """
module top(input wire clk, input wire rst, input wire [7:0] a, output reg [7:0] q);
  always @(posedge clk or posedge rst)
    if (rst) q <= 8'h00; else q <= a;
endmodule
""".lstrip()


def _diagnostic_codes(payload: dict) -> list[str]:
    return [d["code"] for d in payload.get("diagnostics", [])]


def _diagnostics_by_code(payload: dict, code: str) -> list[dict]:
    return [d for d in payload.get("diagnostics", []) if d["code"] == code]


def test_extract_submodule_blocks_function_reference_in_always_block(tmp_path):
    top_path = _write_single(tmp_path, _ALWAYS_COV_FUNCTION_REF_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path), _line_containing(top_path, "always @"), _line_containing(top_path, "always @")
    )
    payload = preview_extract_submodule(
        design, module_name="top", selection=selection, extracted_module_name="extracted_logic"
    ).to_dict()
    assert payload["ok"] is False
    refs = _diagnostics_by_code(payload, "unsupported-procedural-subroutine-reference")
    assert refs and "inv" in refs[0]["message"]


def test_extract_submodule_blocks_task_call_in_always_block(tmp_path):
    top_path = _write_single(tmp_path, _ALWAYS_COV_TASK_REF_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path), _line_containing(top_path, "always @"), _line_containing(top_path, "always @")
    )
    payload = preview_extract_submodule(
        design, module_name="top", selection=selection, extracted_module_name="extracted_logic"
    ).to_dict()
    assert payload["ok"] is False
    codes = _diagnostic_codes(payload)
    assert "unsupported-procedural-subroutine-reference" in codes
    # Should NOT fire the misleading empty-extract diagnostic for a task-only body.
    assert "unsupported-empty-always-extract" not in codes


def test_extract_submodule_allows_system_task_in_always_block(tmp_path):
    top_path = _write_single(tmp_path, _ALWAYS_COV_SYSTEM_TASK_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path), _line_containing(top_path, "always @"), _line_containing(top_path, "end")
    )
    payload = preview_extract_submodule(
        design, module_name="top", selection=selection, extracted_module_name="extracted_logic"
    ).to_dict()
    assert "unsupported-procedural-subroutine-reference" not in _diagnostic_codes(payload)


def test_extract_submodule_warns_on_inferred_latch(tmp_path):
    top_path = _write_single(tmp_path, _ALWAYS_COV_LATCH_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path), _line_containing(top_path, "always @"), _line_containing(top_path, "always @")
    )
    payload = preview_extract_submodule(
        design, module_name="top", selection=selection, extracted_module_name="extracted_logic"
    ).to_dict()
    assert payload["ok"] is True
    latch_diags = _diagnostics_by_code(payload, "procedural-inferred-latch")
    assert latch_diags
    assert all(d["severity"] == "warning" for d in latch_diags)


def test_extract_submodule_no_latch_warning_when_else_present(tmp_path):
    top_path = _write_single(tmp_path, _ALWAYS_COV_NO_LATCH_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path), _line_containing(top_path, "always @"), _line_containing(top_path, "always @")
    )
    payload = preview_extract_submodule(
        design, module_name="top", selection=selection, extracted_module_name="extracted_logic"
    ).to_dict()
    assert payload["ok"] is True
    assert "procedural-inferred-latch" not in _diagnostic_codes(payload)


def test_extract_submodule_warns_on_multi_clock_domain(tmp_path):
    top_path = _write_single(tmp_path, _ALWAYS_COV_MULTI_CLOCK_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path),
        _line_containing(top_path, "always @(posedge clkA)"),
        _line_containing(top_path, "always @(posedge clkB)"),
    )
    payload = preview_extract_submodule(
        design, module_name="top", selection=selection, extracted_module_name="extracted_logic"
    ).to_dict()
    assert payload["ok"] is True
    multi = _diagnostics_by_code(payload, "procedural-multi-clock-domain")
    assert len(multi) == 1
    assert multi[0]["severity"] == "warning"
    assert "clkA" in multi[0]["message"] and "clkB" in multi[0]["message"]


def test_extract_submodule_no_multi_clock_warning_for_same_signal_both_edges(tmp_path):
    top_path = _write_single(tmp_path, _ALWAYS_COV_SAME_CLOCK_BOTH_EDGES_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path), _line_containing(top_path, "always @"), _line_containing(top_path, "always @")
    )
    payload = preview_extract_submodule(
        design, module_name="top", selection=selection, extracted_module_name="extracted_logic"
    ).to_dict()
    assert payload["ok"] is True
    assert "procedural-multi-clock-domain" not in _diagnostic_codes(payload)


def test_extract_submodule_emits_additional_edge_sensitive_input_info(tmp_path):
    top_path = _write_single(tmp_path, _ALWAYS_COV_ASYNC_RESET_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path), _line_containing(top_path, "always @"), _line_containing(top_path, "else q")
    )
    payload = preview_extract_submodule(
        design, module_name="top", selection=selection, extracted_module_name="extracted_logic"
    ).to_dict()
    assert payload["ok"] is True
    extras = _diagnostics_by_code(payload, "procedural-additional-edge-sensitive-input")
    assert len(extras) == 1
    assert extras[0]["severity"] == "info"
    assert "rst" in extras[0]["message"]


def test_extract_submodule_preview_propagates_warnings_and_info_without_blocking(tmp_path):
    top_path = _write_single(tmp_path, _ALWAYS_COV_LATCH_V)
    design = parse_files([top_path])
    selection = ExtractSelection(
        str(top_path), _line_containing(top_path, "always @"), _line_containing(top_path, "always @")
    )
    payload = preview_extract_submodule(
        design, module_name="top", selection=selection, extracted_module_name="extracted_logic"
    ).to_dict()
    assert payload["ok"] is True
    assert payload["edits"], "warning-only diagnostics must not block edit emission"
    severities = {d["severity"] for d in payload["diagnostics"]}
    assert "warning" in severities
    assert "error" not in severities


# ---------------------------------------------------------------------------
# Declaration-boundary follow-up: parent-constant subroutine refs + partial info
# ---------------------------------------------------------------------------

_DECL_BND_LOG2_FN = """
  function integer log2;
    input integer n;
    integer i;
    begin
      log2 = 0;
      for (i = n - 1; i > 0; i = i >> 1) log2 = log2 + 1;
    end
  endfunction
""".strip("\n")


_DECL_BND_LOCALPARAM_LOG2_DEFAULT_V = f"""
module top (
    input wire [7:0] a,
    output wire [7:0] y
);
{_DECL_BND_LOG2_FN}
  localparam W = log2(8);
  wire [W-1:0] mid;
  assign mid = a[W-1:0];
  assign y = {{mid, {{(8-W){{1'b0}}}}}};
endmodule
"""


_DECL_BND_LOCALPARAM_CLOG2_V = """
module top (
    input wire [7:0] a,
    output wire [7:0] y
);
  localparam W = $clog2(8);
  wire [W-1:0] mid;
  assign mid = a[W-1:0];
  assign y = {mid, {(8-W){1'b0}}};
endmodule
"""


_DECL_BND_PARAMETER_LOG2_DEFAULT_V = f"""
module top (
    input wire [7:0] a,
    output wire [7:0] y
);
{_DECL_BND_LOG2_FN}
  parameter W2 = log2(8);
  wire [W2-1:0] mid;
  assign mid = a[W2-1:0];
  assign y = {{mid, {{(8-W2){{1'b0}}}}}};
endmodule
"""


_DECL_BND_COPIED_LP_ALSO_USED_V = """
module top (
    input wire [7:0] a,
    input wire [7:0] b,
    output wire [7:0] y,
    output wire [7:0] z
);
  localparam W = 8;
  wire [W-1:0] mid;
  assign mid = a[W-1:0];
  assign y = mid;
  assign z = b + W;
endmodule
"""


_DECL_BND_COPIED_LP_ONLY_HERE_V = """
module top (
    input wire [7:0] a,
    output wire [7:0] y
);
  localparam W = 8;
  wire [W-1:0] mid;
  assign mid = a;
  assign y = mid;
endmodule
"""


_DECL_BND_COPIED_LP_USED_BY_REMAINING_PARAM_V = """
module top (
    input wire [7:0] a,
    output wire [7:0] y,
    output wire [7:0] z
);
  localparam A = 8;
  localparam B = A + 1;
  wire [A-1:0] mid;
  assign mid = a;
  assign y = mid;
  assign z = B[7:0];
endmodule
"""


def test_extract_submodule_blocks_parent_localparam_subroutine_default(tmp_path):
    top_path = _write_single(tmp_path, _DECL_BND_LOCALPARAM_LOG2_DEFAULT_V)
    design = parse_files([top_path])
    start = _line_containing(top_path, "assign mid")
    end = _line_containing(top_path, "assign y")
    payload = preview_extract_submodule(
        design, module_name="top", selection=ExtractSelection(str(top_path), start, end), extracted_module_name="ext"
    ).to_dict()
    assert payload["ok"] is False
    diags = _diagnostics_by_code(payload, "unsupported-parent-constant-depends-on-subroutine")
    assert diags and "log2" in diags[0]["message"] and "W" in diags[0]["message"]


def test_extract_submodule_allows_parent_localparam_system_function(tmp_path):
    top_path = _write_single(tmp_path, _DECL_BND_LOCALPARAM_CLOG2_V)
    design = parse_files([top_path])
    start = _line_containing(top_path, "assign mid")
    end = _line_containing(top_path, "assign y")
    payload = preview_extract_submodule(
        design, module_name="top", selection=ExtractSelection(str(top_path), start, end), extracted_module_name="ext"
    ).to_dict()
    assert "unsupported-parent-constant-depends-on-subroutine" not in _diagnostic_codes(payload)
    assert payload["ok"] is True


def test_extract_submodule_blocks_forwarded_parameter_subroutine_default(tmp_path):
    top_path = _write_single(tmp_path, _DECL_BND_PARAMETER_LOG2_DEFAULT_V)
    design = parse_files([top_path])
    start = _line_containing(top_path, "assign mid")
    end = _line_containing(top_path, "assign y")
    payload = preview_extract_submodule(
        design, module_name="top", selection=ExtractSelection(str(top_path), start, end), extracted_module_name="ext"
    ).to_dict()
    assert payload["ok"] is False
    diags = _diagnostics_by_code(payload, "unsupported-parent-constant-depends-on-subroutine")
    assert diags and "log2" in diags[0]["message"] and "W2" in diags[0]["message"]


def test_extract_submodule_info_when_copied_localparam_used_by_remaining_logic(tmp_path):
    top_path = _write_single(tmp_path, _DECL_BND_COPIED_LP_ALSO_USED_V)
    design = parse_files([top_path])
    start = _line_containing(top_path, "assign mid")
    end = _line_containing(top_path, "assign y = mid")
    payload = preview_extract_submodule(
        design, module_name="top", selection=ExtractSelection(str(top_path), start, end), extracted_module_name="ext"
    ).to_dict()
    assert payload["ok"] is True
    diags = _diagnostics_by_code(payload, "extracted-localparam-copy-also-used-in-parent")
    assert len(diags) == 1
    assert diags[0]["severity"] == "info"
    assert "W" in diags[0]["message"]


def test_extract_submodule_no_info_when_copied_localparam_only_here(tmp_path):
    top_path = _write_single(tmp_path, _DECL_BND_COPIED_LP_ONLY_HERE_V)
    design = parse_files([top_path])
    start = _line_containing(top_path, "assign mid")
    end = _line_containing(top_path, "assign y")
    payload = preview_extract_submodule(
        design, module_name="top", selection=ExtractSelection(str(top_path), start, end), extracted_module_name="ext"
    ).to_dict()
    assert payload["ok"] is True
    assert "extracted-localparam-copy-also-used-in-parent" not in _diagnostic_codes(payload)


def test_extract_submodule_info_when_remaining_parent_param_references_copied_localparam(tmp_path):
    top_path = _write_single(tmp_path, _DECL_BND_COPIED_LP_USED_BY_REMAINING_PARAM_V)
    design = parse_files([top_path])
    start = _line_containing(top_path, "assign mid")
    end = _line_containing(top_path, "assign y = mid")
    payload = preview_extract_submodule(
        design, module_name="top", selection=ExtractSelection(str(top_path), start, end), extracted_module_name="ext"
    ).to_dict()
    assert payload["ok"] is True
    diags = _diagnostics_by_code(payload, "extracted-localparam-copy-also-used-in-parent")
    assert len(diags) == 1
    assert diags[0]["severity"] == "info"
    assert "'A'" in diags[0]["message"]


def _design_for_validation(tmp_path):
    top_path = _write_single(tmp_path, CORE_V)
    return parse_files([top_path])


def test_boundary_selection_serializes_new_fields():
    selection = BoundaryMoveSelection(
        kind="range",
        file="src/top.v",
        start_line=10,
        end_line=20,
    )
    payload = selection.to_dict()
    assert payload == {
        "kind": "range",
        "file": "src/top.v",
        "startLine": 10,
        "endLine": 20,
    }


def test_boundary_selection_serializes_signal_fields():
    selection = BoundaryMoveSelection(
        kind="signal",
        signal="data",
        signal_module="top",
    )
    assert selection.to_dict() == {
        "kind": "signal",
        "signal": "data",
        "signalModule": "top",
    }


def test_boundary_request_serializes_extracted_module_name():
    request = BoundaryMoveRequest(
        direction="extract",
        selection=BoundaryMoveSelection(kind="range", file="t.v", start_line=1, end_line=2),
        extracted_module_name="new_mod",
    )
    payload = request.to_dict()
    assert payload["direction"] == "extract"
    assert payload["extractedModuleName"] == "new_mod"


def test_boundary_request_rejects_unsupported_direction(tmp_path):
    design = _design_for_validation(tmp_path)
    request = BoundaryMoveRequest(
        direction="sideways",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_a"),
    )
    preview = preview_hierarchy_boundary_move(design, request)
    codes = [d.code for d in preview.diagnostics]
    assert "unsupported-boundary-move-direction" in codes
    assert preview.confidence == "blocked"


def test_boundary_request_rejects_direction_kind_mismatch(tmp_path):
    design = _design_for_validation(tmp_path)
    request = BoundaryMoveRequest(
        direction="push_down",
        selection=BoundaryMoveSelection(kind="range", file="t.v", start_line=1, end_line=2),
    )
    preview = preview_hierarchy_boundary_move(design, request)
    codes = [d.code for d in preview.diagnostics]
    assert "direction-kind-mismatch" in codes


def test_boundary_request_rejects_extract_without_module_name(tmp_path):
    design = _design_for_validation(tmp_path)
    request = BoundaryMoveRequest(
        direction="extract",
        selection=BoundaryMoveSelection(kind="range", file="t.v", start_line=1, end_line=2),
    )
    preview = preview_hierarchy_boundary_move(design, request)
    codes = [d.code for d in preview.diagnostics]
    assert "extracted-module-name-required" in codes


def test_boundary_request_rejects_extract_range_without_lines(tmp_path):
    design = _design_for_validation(tmp_path)
    request = BoundaryMoveRequest(
        direction="extract",
        selection=BoundaryMoveSelection(kind="range", file="t.v"),
        extracted_module_name="new_mod",
    )
    preview = preview_hierarchy_boundary_move(design, request)
    codes = [d.code for d in preview.diagnostics]
    assert "extract-range-lines-required" in codes


def test_boundary_request_rejects_extract_range_with_inverted_lines(tmp_path):
    design = _design_for_validation(tmp_path)
    request = BoundaryMoveRequest(
        direction="extract",
        selection=BoundaryMoveSelection(kind="range", file="t.v", start_line=20, end_line=10),
        extracted_module_name="new_mod",
    )
    preview = preview_hierarchy_boundary_move(design, request)
    codes = [d.code for d in preview.diagnostics]
    assert "extract-range-lines-invalid" in codes


def test_boundary_request_rejects_extract_signal_without_signal(tmp_path):
    design = _design_for_validation(tmp_path)
    request = BoundaryMoveRequest(
        direction="extract",
        selection=BoundaryMoveSelection(kind="signal", signal_module="top"),
        extracted_module_name="new_mod",
    )
    preview = preview_hierarchy_boundary_move(design, request)
    codes = [d.code for d in preview.diagnostics]
    assert "extract-signal-name-required" in codes


def test_boundary_request_rejects_collapse_with_range_kind(tmp_path):
    design = _design_for_validation(tmp_path)
    request = BoundaryMoveRequest(
        direction="collapse",
        selection=BoundaryMoveSelection(kind="range", file="t.v", start_line=1, end_line=2),
    )
    preview = preview_hierarchy_boundary_move(design, request)
    codes = [d.code for d in preview.diagnostics]
    assert "direction-kind-mismatch" in codes


def test_boundary_extract_direction_routes_to_extract_engine_on_validation_failure(tmp_path):
    # After Phase 3 wiring, extract direction routes to the extract engine.
    # Without selection.module_name we now get an extract-side "module-name-required"
    # diagnostic (instead of the Phase 1 "boundary-direction-not-wired" placeholder).
    design = _design_for_validation(tmp_path)
    request = BoundaryMoveRequest(
        direction="extract",
        selection=BoundaryMoveSelection(kind="range", file="t.v", start_line=1, end_line=2),
        extracted_module_name="new_mod",
    )
    preview = preview_hierarchy_boundary_move(design, request)
    codes = [d.code for d in preview.diagnostics]
    assert "extract-module-name-required" in codes
    assert preview.confidence == "blocked"


def test_boundary_collapse_direction_routes_to_collapse_engine_on_validation_failure(tmp_path):
    # After Phase 4 wiring, collapse direction routes to the collapse engine.
    # Without selection.instance_path we now get a collapse-side
    # "instance-path-required" diagnostic (instead of the Phase 1 placeholder).
    design = _design_for_validation(tmp_path)
    request = BoundaryMoveRequest(
        direction="collapse",
        selection=BoundaryMoveSelection(kind="instance"),
    )
    preview = preview_hierarchy_boundary_move(design, request)
    codes = [d.code for d in preview.diagnostics]
    assert "collapse-instance-path-required" in codes


def test_boundary_preview_default_engine_kind_is_boundary():
    selection = BoundaryMoveSelection(kind="instance", instance_path="top/u_a")
    request = BoundaryMoveRequest(direction="pull_up", selection=selection)
    from veriforge.refactor.hierarchy_boundary import BoundaryMovePreview

    p = BoundaryMovePreview(request=request, confidence="blocked")
    assert p.engine_kind == "boundary"
    assert p.engine_preview is None
    payload = p.to_dict()
    assert payload["engineKind"] == "boundary"
    assert "enginePreview" not in payload


def test_boundary_preview_rejects_invalid_engine_kind():
    from veriforge.refactor.hierarchy_boundary import BoundaryMovePreview

    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_a"),
    )
    import pytest

    with pytest.raises(ValueError, match="Invalid engine_kind"):
        BoundaryMovePreview(request=request, confidence="blocked", engine_kind="bogus")


def test_boundary_preview_extract_accessor_returns_inner():
    from veriforge.refactor.hierarchy_boundary import BoundaryMovePreview

    sentinel = object()
    request = BoundaryMoveRequest(
        direction="extract",
        selection=BoundaryMoveSelection(kind="range", file="t.v", start_line=1, end_line=2),
        extracted_module_name="m",
    )
    p = BoundaryMovePreview(
        request=request,
        confidence="apply-ready",
        engine_kind="extract",
        engine_preview=sentinel,
    )
    assert p.as_extract_preview() is sentinel
    import pytest

    with pytest.raises(ValueError):
        p.as_collapse_preview()


def test_pull_up_range_preview_resolves_single_selected_instance(tmp_path):
    top_path = tmp_path / "top.v"
    child_path = tmp_path / "param_child.v"
    top_path.write_text(PARAM_TOP_V, encoding="utf-8")
    child_path.write_text(PARAM_CHILD_V, encoding="utf-8")
    design = parse_files([str(top_path), str(child_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(top_path),
            start_line=_line_containing(top_path, "param_child #("),
            end_line=_last_line_containing(top_path, ");"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.engine_kind == "boundary"
    assert preview.apply_ready is True
    assert preview.source is not None
    assert preview.source.instance_name == "u_child"
    assert preview.parent is not None
    assert preview.parent.module_name == "top"
    assert preview.metadata["rewriteStatus"] == "apply-ready"


def test_pull_up_range_preview_rejects_partial_instance_selection(tmp_path):
    top_path = tmp_path / "top.v"
    child_path = tmp_path / "param_child.v"
    top_path.write_text(PARAM_TOP_V, encoding="utf-8")
    child_path.write_text(PARAM_CHILD_V, encoding="utf-8")
    design = parse_files([str(top_path), str(child_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(top_path),
            start_line=_line_containing(top_path, ".WIDTH(8)"),
            end_line=_line_containing(top_path, ".dout(child_out)"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    codes = [d.code for d in preview.diagnostics]
    assert "partial-instance-selection" in codes


def test_pull_up_range_preview_moves_child_procedural_logic_into_all_parent_sites(tmp_path):
    child_path = tmp_path / "pulse_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "reg toggle;"),
            end_line=_line_containing(child_path, "end"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    assert preview.source is not None
    assert preview.source.module_name == "pulse_child"
    assert preview.metadata["scope"] == "design-wide"
    assert preview.metadata["siteCount"] == 2
    assert preview.metadata["parentModules"] == ["top_a", "top_b"]
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"pulse_child.v", "top_a.v", "top_b.v"}
    assert "input toggle" in edits_by_name["pulse_child.v"]
    assert "reg toggle;" not in edits_by_name["pulse_child.v"]
    assert "always @(posedge clk)" not in edits_by_name["pulse_child.v"]
    assert "reg u_child__toggle;" in edits_by_name["top_a.v"]
    assert ".toggle(u_child__toggle)" in edits_by_name["top_a.v"]
    assert "u_child__toggle <= strobe_a ? ~u_child__toggle : u_child__toggle;" in edits_by_name["top_a.v"]
    assert "reg u_child_b__toggle;" in edits_by_name["top_b.v"]
    assert ".toggle(u_child_b__toggle)" in edits_by_name["top_b.v"]
    assert "u_child_b__toggle <= strobe_b ? ~u_child_b__toggle : u_child_b__toggle;" in edits_by_name["top_b.v"]


def test_pull_up_range_preview_moves_child_output_port_logic_into_parent_sites(tmp_path):
    child_path = tmp_path / "pulse_out_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_OUTPUT_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_OUTPUT_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_OUTPUT_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "always @(posedge clk)"),
            end_line=_line_containing(child_path, "        end"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"pulse_out_child.v", "top_a.v", "top_b.v"}
    assert "input pulse_out" in edits_by_name["pulse_out_child.v"]
    assert "output reg pulse_out" not in edits_by_name["pulse_out_child.v"]
    assert "always @(posedge clk)" not in edits_by_name["pulse_out_child.v"]
    assert "output reg pulse_a" in edits_by_name["top_a.v"]
    assert "pulse_out_child u_child (.clk(clk), .strobe_in(strobe_a), .pulse_out(pulse_a));" in edits_by_name["top_a.v"]
    assert "pulse_a <= strobe_a ? ~pulse_a : pulse_a;" in edits_by_name["top_a.v"]
    assert "reg pulse_mid;" in edits_by_name["top_b.v"]
    assert "assign pulse_b = pulse_mid;" in edits_by_name["top_b.v"]
    assert "pulse_mid <= strobe_b ? ~pulse_mid : pulse_mid;" in edits_by_name["top_b.v"]
    assert "u_child__pulse_out" not in edits_by_name["top_a.v"]
    assert "u_child_b__pulse_out" not in edits_by_name["top_b.v"]


def test_pull_up_range_preview_uses_localized_parent_edits_for_top_level_sites(tmp_path):
    child_path = tmp_path / "pulse_out_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_OUTPUT_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_OUTPUT_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_OUTPUT_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "always @(posedge clk)"),
            end_line=_line_containing(child_path, "        end"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    top_a_edits = [edit for edit in preview.edits if Path(edit.file).name == "top_a.v"]
    top_b_edits = [edit for edit in preview.edits if Path(edit.file).name == "top_b.v"]
    assert len(top_a_edits) >= 2
    assert len(top_b_edits) >= 2
    assert all("endmodule" not in edit.original for edit in top_a_edits)
    assert all("endmodule" not in edit.original for edit in top_b_edits)
    assert any("pulse_out_child u_child" in edit.original for edit in top_a_edits)
    assert any("pulse_out_child u_child_b" in edit.original for edit in top_b_edits)


def test_pull_up_range_preview_moves_child_assigns_into_all_parent_sites(tmp_path):
    child_path = tmp_path / "assign_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_ASSIGN_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_ASSIGN_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_ASSIGN_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "assign mid = en & data_in;"),
            end_line=_line_containing(child_path, "assign mid = en & data_in;"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    assert preview.metadata["selectedAssignments"] == 1
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"assign_child.v", "top_a.v", "top_b.v"}
    assert "input wire mid" in edits_by_name["assign_child.v"]
    assert "wire mid;" not in edits_by_name["assign_child.v"]
    assert "assign mid = en & data_in;" not in edits_by_name["assign_child.v"]
    assert "wire u_child__mid;" in edits_by_name["top_a.v"]
    assert ".mid(u_child__mid)" in edits_by_name["top_a.v"]
    assert "assign u_child__mid = en_a & data_a;" in edits_by_name["top_a.v"]
    assert "wire u_child_b__mid;" in edits_by_name["top_b.v"]
    assert ".mid(u_child_b__mid)" in edits_by_name["top_b.v"]
    assert "assign u_child_b__mid = en_b & data_b;" in edits_by_name["top_b.v"]


def test_pull_up_range_preview_moves_child_assign_output_logic_into_parent_sites(tmp_path):
    child_path = tmp_path / "assign_out_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_ASSIGN_OUTPUT_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_ASSIGN_OUTPUT_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_ASSIGN_OUTPUT_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "assign data_out = en & data_in;"),
            end_line=_line_containing(child_path, "assign data_out = en & data_in;"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"assign_out_child.v", "top_a.v", "top_b.v"}
    assert "input data_out" in edits_by_name["assign_out_child.v"]
    assert "assign data_out = en & data_in;" not in edits_by_name["assign_out_child.v"]
    assert "assign data_out_a = en_a & data_a;" in edits_by_name["top_a.v"]
    assert "assign data_mid = en_b & data_b;" in edits_by_name["top_b.v"]
    assert "u_child__data_out" not in edits_by_name["top_a.v"]
    assert "u_child_b__data_out" not in edits_by_name["top_b.v"]


def test_pull_up_range_preview_moves_child_nested_instance_into_all_parent_sites(tmp_path):
    leaf_path = tmp_path / "leaf_gate.v"
    child_path = tmp_path / "struct_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    leaf_path.write_text(STRUCT_PULLUP_LEAF_V, encoding="utf-8")
    child_path.write_text(CHILD_INSTANCE_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_INSTANCE_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_INSTANCE_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(leaf_path), str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "leaf_gate u_leaf("),
            end_line=_last_line_containing(child_path, ");"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    assert preview.metadata["selectedInstances"] == 1
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"struct_child.v", "top_a.v", "top_b.v"}
    assert "input wire mid" in edits_by_name["struct_child.v"]
    assert "leaf_gate u_leaf" not in edits_by_name["struct_child.v"]
    assert "wire u_child__mid;" in edits_by_name["top_a.v"]
    assert "leaf_gate u_child__u_leaf" in edits_by_name["top_a.v"]
    assert ".data_out(u_child__mid)" in edits_by_name["top_a.v"]
    assert ".mid(u_child__mid)" in edits_by_name["top_a.v"]
    assert "wire u_child_b__mid;" in edits_by_name["top_b.v"]
    assert "leaf_gate u_child_b__u_leaf" in edits_by_name["top_b.v"]
    assert ".data_out(u_child_b__mid)" in edits_by_name["top_b.v"]
    assert ".mid(u_child_b__mid)" in edits_by_name["top_b.v"]


def test_pull_up_range_preview_moves_child_mixed_structural_logic_into_all_parent_sites(tmp_path):
    leaf_path = tmp_path / "leaf_gate.v"
    child_path = tmp_path / "struct_mixed_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    leaf_path.write_text(STRUCT_PULLUP_LEAF_V, encoding="utf-8")
    child_path.write_text(CHILD_MIXED_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_MIXED_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_MIXED_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(leaf_path), str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "leaf_gate u_leaf("),
            end_line=_line_containing(child_path, "assign masked = mid & en;"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    assert preview.metadata["selectedAssignments"] == 1
    assert preview.metadata["selectedInstances"] == 1
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"struct_mixed_child.v", "top_a.v", "top_b.v"}
    assert "input wire masked" in edits_by_name["struct_mixed_child.v"]
    assert "leaf_gate u_leaf" not in edits_by_name["struct_mixed_child.v"]
    assert "assign masked = mid & en;" not in edits_by_name["struct_mixed_child.v"]
    assert "wire u_child__mid;" in edits_by_name["top_a.v"]
    assert "wire u_child__masked;" in edits_by_name["top_a.v"]
    assert "leaf_gate u_child__u_leaf" in edits_by_name["top_a.v"]
    assert "assign u_child__masked = u_child__mid & en_a;" in edits_by_name["top_a.v"]
    assert ".masked(u_child__masked)" in edits_by_name["top_a.v"]
    assert "wire u_child_b__mid;" in edits_by_name["top_b.v"]
    assert "wire u_child_b__masked;" in edits_by_name["top_b.v"]
    assert "leaf_gate u_child_b__u_leaf" in edits_by_name["top_b.v"]
    assert "assign u_child_b__masked = u_child_b__mid & en_b;" in edits_by_name["top_b.v"]
    assert ".masked(u_child_b__masked)" in edits_by_name["top_b.v"]


def test_pull_up_range_preview_moves_child_instance_with_complex_outputs_into_all_parent_sites(tmp_path):
    leaf_path = tmp_path / "leaf_pair.v"
    child_path = tmp_path / "complex_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    leaf_path.write_text(STRUCT_COMPLEX_PULLUP_LEAF_V, encoding="utf-8")
    child_path.write_text(CHILD_COMPLEX_INSTANCE_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_COMPLEX_INSTANCE_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_COMPLEX_INSTANCE_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(leaf_path), str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "leaf_pair u_leaf("),
            end_line=_last_line_containing(child_path, ");"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    assert preview.metadata["selectedInstances"] == 1
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"complex_child.v", "top_a.v", "top_b.v"}
    assert "input wire hi" in edits_by_name["complex_child.v"]
    assert "input wire lo" in edits_by_name["complex_child.v"]
    assert "leaf_pair u_leaf" not in edits_by_name["complex_child.v"]
    assert "wire u_child__hi;" in edits_by_name["top_a.v"]
    assert "wire u_child__lo;" in edits_by_name["top_a.v"]
    assert "leaf_pair u_child__u_leaf" in edits_by_name["top_a.v"]
    assert ".pair_out({u_child__hi, u_child__lo})" in edits_by_name["top_a.v"]
    assert ".hi(u_child__hi)" in edits_by_name["top_a.v"]
    assert ".lo(u_child__lo)" in edits_by_name["top_a.v"]
    assert "wire u_child_b__hi;" in edits_by_name["top_b.v"]
    assert "wire u_child_b__lo;" in edits_by_name["top_b.v"]
    assert "leaf_pair u_child_b__u_leaf" in edits_by_name["top_b.v"]
    assert ".pair_out({u_child_b__hi, u_child_b__lo})" in edits_by_name["top_b.v"]
    assert ".hi(u_child_b__hi)" in edits_by_name["top_b.v"]
    assert ".lo(u_child_b__lo)" in edits_by_name["top_b.v"]


def test_pull_up_range_preview_moves_child_mixed_structural_complex_outputs_into_all_parent_sites(tmp_path):
    leaf_path = tmp_path / "leaf_pair.v"
    child_path = tmp_path / "complex_mixed_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    leaf_path.write_text(STRUCT_COMPLEX_PULLUP_LEAF_V, encoding="utf-8")
    child_path.write_text(CHILD_COMPLEX_MIXED_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_COMPLEX_MIXED_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_COMPLEX_MIXED_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(leaf_path), str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "leaf_pair u_leaf("),
            end_line=_line_containing(child_path, "assign data_out = pair;"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    assert preview.metadata["selectedAssignments"] == 1
    assert preview.metadata["selectedInstances"] == 1
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"complex_mixed_child.v", "top_a.v", "top_b.v"}
    assert "input [1:0] data_out" in edits_by_name["complex_mixed_child.v"]
    assert "leaf_pair u_leaf" not in edits_by_name["complex_mixed_child.v"]
    assert "assign data_out = pair;" not in edits_by_name["complex_mixed_child.v"]
    assert "wire [1:0] u_child__pair;" in edits_by_name["top_a.v"]
    assert "leaf_pair u_child__u_leaf" in edits_by_name["top_a.v"]
    assert ".pair_out({u_child__pair[1], u_child__pair[0]})" in edits_by_name["top_a.v"]
    assert "assign data_out_a = u_child__pair;" in edits_by_name["top_a.v"]
    assert ".data_out(data_out_a)" in edits_by_name["top_a.v"]
    assert "wire [1:0] u_child_b__pair;" in edits_by_name["top_b.v"]
    assert "leaf_pair u_child_b__u_leaf" in edits_by_name["top_b.v"]
    assert ".pair_out({u_child_b__pair[1], u_child_b__pair[0]})" in edits_by_name["top_b.v"]
    assert "assign data_out_b = u_child_b__pair;" in edits_by_name["top_b.v"]
    assert ".data_out(data_out_b)" in edits_by_name["top_b.v"]


def test_pull_up_range_preview_allows_selected_parameter_with_child_assign(tmp_path):
    child_path = tmp_path / "shift_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(PARAM_ASSIGN_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(PARAM_ASSIGN_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(PARAM_ASSIGN_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "parameter SHIFT = 1"),
            end_line=_line_containing(child_path, "assign data_out = data_in << SHIFT;"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"shift_child.v", "top_a.v", "top_b.v"}
    assert "parameter SHIFT = 1" in edits_by_name["shift_child.v"]
    assert "assign data_out = data_in << SHIFT;" not in edits_by_name["shift_child.v"]
    assert "assign data_out_a = data_a << 1;" in edits_by_name["top_a.v"]
    assert "assign data_out_b = data_b << 2;" in edits_by_name["top_b.v"]


def test_pull_up_range_moves_selected_localparam_with_child_procedural_logic(tmp_path):
    child_path = tmp_path / "reset_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(LOCALPARAM_PROC_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(LOCALPARAM_PROC_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(LOCALPARAM_PROC_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "localparam RESET_VALUE = 1'b0;"),
            end_line=_line_containing(child_path, "end"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"reset_child.v", "top_a.v", "top_b.v"}
    assert "localparam RESET_VALUE = 1'b0;" not in edits_by_name["reset_child.v"]
    assert "always @(posedge clk)" not in edits_by_name["reset_child.v"]
    assert "output reg pulse_a" in edits_by_name["top_a.v"]
    assert "pulse_a <= strobe_a ? ~pulse_a : 1'b0;" in edits_by_name["top_a.v"]
    assert "reg pulse_mid;" in edits_by_name["top_b.v"]
    assert "pulse_mid <= strobe_b ? ~pulse_mid : 1'b0;" in edits_by_name["top_b.v"]


def test_pull_up_range_moves_child_procedural_logic_with_functions_into_all_parent_sites(tmp_path):
    child_path = tmp_path / "func_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(FUNCTION_PROC_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(FUNCTION_PROC_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(FUNCTION_PROC_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "pulse_out <= calc_next(pulse_out, strobe_in);"),
            end_line=_line_containing(child_path, "pulse_out <= calc_next(pulse_out, strobe_in);"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"func_child.v", "top_a.v", "top_b.v"}
    assert "function calc_next;" in edits_by_name["func_child.v"]
    assert "always @(posedge clk)" not in edits_by_name["func_child.v"]
    assert "function u_child__calc_next;" in edits_by_name["top_a.v"]
    assert "pulse_a <= u_child__calc_next(pulse_a, strobe_a);" in edits_by_name["top_a.v"]
    assert "function u_child_b__calc_next;" in edits_by_name["top_b.v"]
    assert "pulse_mid <= u_child_b__calc_next(pulse_mid, strobe_b);" in edits_by_name["top_b.v"]


def test_pull_up_instance_supports_child_functions(tmp_path):
    top_path = tmp_path / "top.v"
    child_path = tmp_path / "local_func_child.v"
    top_path.write_text(LOCAL_FUNCTION_PULLUP_TOP_V, encoding="utf-8")
    child_path.write_text(LOCAL_FUNCTION_PULLUP_CHILD_V, encoding="utf-8")
    design = parse_files([str(top_path), str(child_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_child"),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    assert len(preview.edits) == 1
    replacement = preview.edits[0].replacement
    assert "function [3:0]" in replacement
    assert "u_child__add_one" in replacement
    assert "assign data_out = u_child__add_one(data_in);" in replacement


def test_pull_up_range_allows_unselected_generate_blocks_in_child_module(tmp_path):
    child_path = tmp_path / "gen_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(GENERATE_TOLERANT_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(GENERATE_TOLERANT_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(GENERATE_TOLERANT_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "assign mid = en & data_in;"),
            end_line=_line_containing(child_path, "assign mid = en & data_in;"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"gen_child.v", "top_a.v", "top_b.v"}
    assert "begin : keep_logic" in edits_by_name["gen_child.v"]
    assert "assign keep_tap = data_in;" in edits_by_name["gen_child.v"]
    assert "assign mid = en & data_in;" not in edits_by_name["gen_child.v"]
    assert "wire u_child__mid;" in edits_by_name["top_a.v"]
    assert "assign u_child__mid = en_a & data_a;" in edits_by_name["top_a.v"]
    assert ".mid(u_child__mid)" in edits_by_name["top_a.v"]
    assert "wire u_child_b__mid;" in edits_by_name["top_b.v"]
    assert "assign u_child_b__mid = en_b & data_b;" in edits_by_name["top_b.v"]
    assert ".mid(u_child_b__mid)" in edits_by_name["top_b.v"]


def test_pull_up_range_preview_preserves_explicit_child_generate_wrappers_for_localized_edits(tmp_path):
    child_path = tmp_path / "gen_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(GENERATE_TOLERANT_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(GENERATE_TOLERANT_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(GENERATE_TOLERANT_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "assign mid = en & data_in;"),
            end_line=_line_containing(child_path, "assign mid = en & data_in;"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    child_text = _preview_texts_by_name(preview)["gen_child.v"]
    assert "generate" in child_text
    assert "endgenerate" in child_text
    assert child_text.index("generate") < child_text.index("endgenerate")
    assert child_text.index("endgenerate") < child_text.index("assign data_out = mid;")


def test_pull_up_range_preview_preserves_unrelated_parent_function_order_for_localized_edits(tmp_path):
    child_path = tmp_path / "pulse_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(PARENT_ORDER_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "reg toggle;"),
            end_line=_line_containing(child_path, "end"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    top_a_text = _preview_texts_by_name(preview)["top_a.v"]
    assert "function keep_fn;" in top_a_text
    assert top_a_text.index("function keep_fn;") < top_a_text.index("reg u_child__toggle;")


def test_pull_up_range_preview_preserves_unrelated_parent_generate_wrappers_for_localized_edits(tmp_path):
    child_path = tmp_path / "pulse_out_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_OUTPUT_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(PARENT_GENERATE_PRESERVE_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_OUTPUT_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "always @(posedge clk)"),
            end_line=_line_containing(child_path, "        end"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    top_a_text = _preview_texts_by_name(preview)["top_a.v"]
    assert "generate" in top_a_text
    assert "endgenerate" in top_a_text
    assert "begin : g_keep" in top_a_text
    assert "assign keep_tap = strobe_a;" in top_a_text
    assert top_a_text.index("pulse_out_child u_child") < top_a_text.index("generate")


def test_pull_up_range_preview_preserves_unrelated_parent_comments_attributes_and_initializers(tmp_path):
    child_path = tmp_path / "pulse_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(PARENT_NOISE_PRESERVE_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "reg toggle;"),
            end_line=_line_containing(child_path, "end"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    top_a_text = _preview_texts_by_name(preview)["top_a.v"]
    assert "// keep parameter comment" in top_a_text
    assert '    (* keep = "true" *) wire preserved_wire = 1\'b0;\n' in top_a_text
    assert "reg u_child__toggle;" in top_a_text


def test_pull_up_range_supports_generate_nested_parent_sites(tmp_path):
    child_path = tmp_path / "gen_site_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(GENERATE_SITE_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(GENERATE_SITE_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(GENERATE_SITE_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "assign data_out = en & data_in;"),
            end_line=_line_containing(child_path, "assign data_out = en & data_in;"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"gen_site_child.v", "top_a.v", "top_b.v"}
    assert "assign data_out = en & data_in;" not in edits_by_name["gen_site_child.v"]
    assert "assign data_out_a = en_a & data_a;" in edits_by_name["top_a.v"]
    assert "begin : g_child" in edits_by_name["top_b.v"]
    assert "assign data_out_b = en_b & data_b;" in edits_by_name["top_b.v"]
    assert "gen_site_child u_child_b" in edits_by_name["top_b.v"]


def test_pull_up_range_supports_child_generate_assign_selection(tmp_path):
    child_path = tmp_path / "gen_inner_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_GENERATE_ASSIGN_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_GENERATE_ASSIGN_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_GENERATE_ASSIGN_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "assign data_out = en & data_in;"),
            end_line=_line_containing(child_path, "assign data_out = en & data_in;"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"gen_inner_child.v", "top_a.v", "top_b.v"}
    assert "assign data_out = en & data_in;" not in edits_by_name["gen_inner_child.v"]
    assert "begin : g_logic" in edits_by_name["top_a.v"]
    assert "assign data_out_a = en_a & data_a;" in edits_by_name["top_a.v"]
    assert "begin : g_child" in edits_by_name["top_b.v"]
    assert "begin : g_logic" in edits_by_name["top_b.v"]
    assert "assign data_out_b = en_b & data_b;" in edits_by_name["top_b.v"]


def test_pull_up_range_supports_child_generate_procedural_selection(tmp_path):
    child_path = tmp_path / "gen_proc_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(CHILD_GENERATE_PROC_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(CHILD_GENERATE_PROC_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(CHILD_GENERATE_PROC_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(
                child_path, "always @(posedge clk) pulse_out <= strobe_in ? ~pulse_out : pulse_out;"
            ),
            end_line=_line_containing(
                child_path, "always @(posedge clk) pulse_out <= strobe_in ? ~pulse_out : pulse_out;"
            ),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"gen_proc_child.v", "top_a.v", "top_b.v"}
    assert (
        "always @(posedge clk) pulse_out <= strobe_in ? ~pulse_out : pulse_out;"
        not in edits_by_name["gen_proc_child.v"]
    )
    assert "output reg pulse_a" in edits_by_name["top_a.v"]
    assert "begin : g_logic" in edits_by_name["top_a.v"]
    assert "pulse_a <= strobe_a ? ~pulse_a : pulse_a;" in edits_by_name["top_a.v"]
    assert "reg pulse_mid;" in edits_by_name["top_b.v"]
    assert "begin : g_child" in edits_by_name["top_b.v"]
    assert "begin : g_logic" in edits_by_name["top_b.v"]
    assert "pulse_mid <= strobe_b ? ~pulse_mid : pulse_mid;" in edits_by_name["top_b.v"]


def test_pull_up_range_allows_unconnected_irrelevant_child_ports(tmp_path):
    child_path = tmp_path / "async_like_child.v"
    top_a_path = tmp_path / "top_a.v"
    top_b_path = tmp_path / "top_b.v"
    child_path.write_text(UNCONNECTED_PORT_PULLUP_CHILD_V, encoding="utf-8")
    top_a_path.write_text(UNCONNECTED_PORT_PULLUP_TOP_A_V, encoding="utf-8")
    top_b_path.write_text(UNCONNECTED_PORT_PULLUP_TOP_B_V, encoding="utf-8")
    design = parse_files([str(child_path), str(top_a_path), str(top_b_path)])
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(child_path),
            start_line=_line_containing(child_path, "always @(posedge clk)"),
            end_line=_line_containing(child_path, "sync_vector_out <= async_vector_in;"),
        ),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    assert preview.ok is True, preview.diagnostics
    assert preview.apply_ready is True
    assert not any(diag.code == "unconnected-port-unsupported" for diag in preview.diagnostics)
    edits_by_name = _preview_texts_by_name(preview)
    assert set(edits_by_name) == {"async_like_child.v", "top_a.v", "top_b.v"}
    assert (
        "output sync_vector_out" in edits_by_name["async_like_child.v"]
        or "input sync_vector_out" in edits_by_name["async_like_child.v"]
    )
    assert "always @(posedge clk)" not in edits_by_name["async_like_child.v"]
    assert "output reg sync_a" in edits_by_name["top_a.v"]
    assert "sync_a <= async_a;" in edits_by_name["top_a.v"]
    assert "output reg sync_b" in edits_by_name["top_b.v"]
    assert "sync_b <= async_b;" in edits_by_name["top_b.v"]
    assert ".changing()" in edits_by_name["top_b.v"]


def test_boundary_preview_collapse_accessor_returns_inner():
    from veriforge.refactor.hierarchy_boundary import BoundaryMovePreview

    sentinel = object()
    request = BoundaryMoveRequest(
        direction="collapse",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_a"),
    )
    p = BoundaryMovePreview(
        request=request,
        confidence="apply-ready",
        engine_kind="collapse",
        engine_preview=sentinel,
    )
    assert p.as_collapse_preview() is sentinel
    import pytest

    with pytest.raises(ValueError):
        p.as_extract_preview()


def test_unified_extract_route_wraps_extract_preview(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_CHAIN_V)
    design = parse_files([top_path])
    request = BoundaryMoveRequest(
        direction="extract",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(top_path),
            module_name="top",
            start_line=_line_containing(top_path, "assign mid"),
            end_line=_line_containing(top_path, "assign y"),
        ),
        extracted_module_name="extracted_logic",
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is True
    assert payload["engineKind"] == "extract"
    assert payload["metadata"]["origin"] == "extract"
    assert payload["metadata"]["extractedModuleName"] == "extracted_logic"
    assert payload["metadata"]["moduleName"] == "top"
    assert payload["target"]["moduleName"] == "extracted_logic"
    assert payload["target"]["instanceName"] == "u_extracted_logic"
    assert payload["source"]["moduleName"] == "top"
    assert payload["source"]["range"]["startLine"] == request.selection.start_line
    assert payload["edits"]
    # engine_preview carries the original ExtractPreview
    inner = preview.as_extract_preview()
    inner_payload = inner.to_dict()
    assert inner_payload["operation"] == "extractSubmodule"
    assert inner_payload["extractedModuleName"] == "extracted_logic"
    assert inner_payload["boundary"]["outputs"] == ["y"]


def test_unified_extract_route_blocks_when_module_name_missing(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_CHAIN_V)
    design = parse_files([top_path])
    request = BoundaryMoveRequest(
        direction="extract",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(top_path),
            start_line=_line_containing(top_path, "assign mid"),
            end_line=_line_containing(top_path, "assign y"),
        ),
        extracted_module_name="extracted_logic",
    )

    preview = preview_hierarchy_boundary_move(design, request)
    codes = [d.code for d in preview.diagnostics]
    assert "extract-module-name-required" in codes


def test_unified_extract_route_uses_signal_module(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_CHAIN_V)
    design = parse_files([top_path])
    request = BoundaryMoveRequest(
        direction="extract",
        selection=BoundaryMoveSelection(
            kind="signal",
            signal="mid",
            signal_module="top",
        ),
        extracted_module_name="extracted_logic",
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    # We don't assert ok here (signal/trace selection may or may not produce
    # an apply-ready preview for this fixture), but we verify the routing
    # reached the extract engine and produced an extract-shaped payload.
    assert payload["engineKind"] == "extract"
    assert payload["metadata"]["origin"] == "extract"
    assert payload["metadata"]["moduleName"] == "top"
    inner = preview.as_extract_preview()
    assert inner.module_name == "top"
    assert inner.extracted_module_name == "extracted_logic"
    assert inner.selection.signal == "mid"
    assert inner.selection.signal_module == "top"


def test_unified_collapse_route_wraps_collapse_preview(tmp_path):
    design = parse_files(_write_design(tmp_path, PURE_RENAME_WRAPPER_V))
    request = BoundaryMoveRequest(
        direction="collapse",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_wrap"),
    )

    preview = preview_hierarchy_boundary_move(design, request)

    payload = preview.to_dict()
    assert payload["ok"] is True
    assert payload["engineKind"] == "collapse"
    assert payload["metadata"]["origin"] == "collapse"
    assert payload["metadata"]["instancePath"] == "top/u_wrap"
    assert payload["source"]["instancePath"] == "top/u_wrap"
    assert payload["source"]["instanceName"] == "u_wrap"
    assert payload["edits"]
    inner = preview.as_collapse_preview()
    assert inner.instance_path == "top/u_wrap"
    assert inner.confidence == "safe"
    assert inner.renames == ({"from": "u_wrap/u_core", "to": "u_wrap__u_core"},)


def test_unified_collapse_route_blocks_when_instance_path_missing(tmp_path):
    design = parse_files(_write_design(tmp_path, PURE_RENAME_WRAPPER_V))
    request = BoundaryMoveRequest(
        direction="collapse",
        selection=BoundaryMoveSelection(kind="instance"),
    )
    preview = preview_hierarchy_boundary_move(design, request)
    codes = [d.code for d in preview.diagnostics]
    assert "collapse-instance-path-required" in codes


def test_unified_collapse_route_propagates_engine_diagnostics(tmp_path):
    design = parse_files(_write_design(tmp_path, STRUCTURAL_WRAPPER_V))
    request = BoundaryMoveRequest(
        direction="collapse",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_wrap"),
    )
    preview = preview_hierarchy_boundary_move(design, request)
    codes = [d.code for d in preview.diagnostics]
    assert "unsupported-wrapper-class" in codes
    assert preview.engine_kind == "collapse"


def test_unified_apply_dispatches_extract_engine_and_reports_created_files(tmp_path):
    top_path = _write_single(tmp_path, EXTRACT_CHAIN_V)
    design = parse_files([top_path])
    request = BoundaryMoveRequest(
        direction="extract",
        selection=BoundaryMoveSelection(
            kind="range",
            file=str(top_path),
            module_name="top",
            start_line=_line_containing(top_path, "assign mid"),
            end_line=_line_containing(top_path, "assign y"),
        ),
        extracted_module_name="extracted_logic",
    )
    preview = preview_hierarchy_boundary_move(design, request)
    assert preview.engine_kind == "extract"

    child_path = tmp_path / "extracted_logic.v"
    assert not child_path.exists()

    result = apply_hierarchy_boundary_move_preview(preview)
    assert result.applied is True, result.diagnostics
    assert str(top_path) in result.written_files
    assert str(child_path) in result.written_files
    assert str(child_path) in result.created_files
    assert str(top_path) not in result.created_files
    assert child_path.exists()

    payload = result.to_dict()
    assert str(child_path) in payload["createdFiles"]
    assert str(top_path) in payload["writtenFiles"]


def test_unified_apply_dispatches_collapse_engine(tmp_path):
    paths = _write_design(tmp_path, PURE_RENAME_WRAPPER_V)
    design = parse_files(paths)
    request = BoundaryMoveRequest(
        direction="collapse",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_wrap"),
    )
    preview = preview_hierarchy_boundary_move(design, request)
    assert preview.engine_kind == "collapse"
    assert preview.as_collapse_preview() is not None

    result = apply_hierarchy_boundary_move_preview(preview)
    assert result.applied is True, result.diagnostics
    # Collapse rewrites the parent module file in place.
    assert any(str(p) in result.written_files for p in paths)
    assert result.created_files == ()


def test_unified_apply_blocks_when_native_preview_not_ready():
    request = BoundaryMoveRequest(
        direction="pull_up",
        selection=BoundaryMoveSelection(kind="instance", instance_path="top/u_a"),
    )
    from veriforge.refactor.hierarchy_boundary import BoundaryMovePreview

    preview = BoundaryMovePreview(request=request, confidence="blocked")
    result = apply_hierarchy_boundary_move_preview(preview)
    assert result.applied is False
    codes = [d.code for d in result.diagnostics]
    assert "preview-not-applicable" in codes


def test_unified_apply_blocks_extract_when_engine_preview_missing():
    request = BoundaryMoveRequest(
        direction="extract",
        selection=BoundaryMoveSelection(kind="range", file="t.v", start_line=1, end_line=2),
        extracted_module_name="m",
    )
    from veriforge.refactor.hierarchy_boundary import BoundaryMovePreview

    preview = BoundaryMovePreview(
        request=request,
        confidence="apply-ready",
        engine_kind="extract",
        engine_preview=None,
    )
    result = apply_hierarchy_boundary_move_preview(preview)
    assert result.applied is False
    codes = [d.code for d in result.diagnostics]
    assert "engine-preview-missing" in codes


def test_node_source_and_range_uses_exact_loc(tmp_path):
    from veriforge.refactor import hierarchy_boundary

    top_path = _write_single(
        tmp_path,
        """\
module top(
    input a,
    output y
);
    wire mid;
    assign mid = a;
    assign y = mid;
endmodule
""",
    )

    design = parse_files([top_path])
    top = design.get_module("top")
    assert top is not None
    net = top.get_net("mid")
    assert net is not None

    source, source_range = hierarchy_boundary._node_source_and_range(
        net,
        error_code="test-location-required",
        description="test net",
    )

    assert source == "wire mid;"
    assert source_range["start"]["line"] == _line_containing(top_path, "wire mid") - 1
    assert source_range["end"]["line"] == _line_containing(top_path, "wire mid") - 1


def test_node_source_and_range_can_include_leading_attribute_lines(tmp_path):
    from veriforge.refactor import hierarchy_boundary

    top_path = _write_single(
        tmp_path,
        """\
module top(
    input a,
    output y
);
    (* keep = "true" *)
    wire mid;
    assign y = a;
endmodule
""",
    )

    design = parse_files([top_path])
    top = design.get_module("top")
    assert top is not None
    net = top.get_net("mid")
    assert net is not None

    source, _source_range = hierarchy_boundary._node_source_and_range(
        net,
        error_code="test-location-required",
        description="test net",
        whole_lines=True,
        include_leading_attribute_lines=True,
    )

    assert source == '    (* keep = "true" *)\n    wire mid;\n'


def test_instance_source_and_range_preserves_leading_attribute_lines(tmp_path):
    from veriforge.refactor import hierarchy_boundary

    top_path = _write_single(
        tmp_path,
        """\
module child(
    input a,
    output y
);
    assign y = a;
endmodule

module top(
    input a,
    output y
);
    (* keep = "true" *)
    child u_child (
        .a(a),
        .y(y)
    );
endmodule
""",
    )

    design = parse_files([top_path])
    top = design.get_module("top")
    assert top is not None

    source, _source_range = hierarchy_boundary._instance_source_and_range(top.instances[0])

    assert source == '    (* keep = "true" *)\n    child u_child (\n        .a(a),\n        .y(y)\n    );\n'
