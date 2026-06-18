# Verilog Grammar Support Status

This table is auto-generated from `verilog.lark` metadata tags.
## Summary Statistics

| Metric | Count |
|--------|-------|
| Total Rules | 370 |
| HIGH Priority | 133 |
| MEDIUM Priority | 53 |
| LOW Priority | 175 |
| Synthesizable (YES) | 178 |
| Synthesizable (NO) | 168 |
| Synthesizable (PARTIAL) | 11 |
| Supported | 5 |


## A.1.1 Library source text

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `library_text` | 14 | 🟢 LOW | - | ❌ | `library mylib "path/to/lib";` |
| `library_description` | 23 | 🟢 LOW | ❌ | - | `library work "designs/*.v";` |
| `library_declaration` | 30 | 🟢 LOW | ❌ | - | `library mylib "src/*.v", "lib/*.v" -incdir "inc";` |
| `include_statement` | 35 | 🟢 LOW | ❌ | - | `include "header.vh";` |

## A.1.2 Verilog source text

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `source_text` | 42 | 🔴 HIGH | - | ✅ | `module foo(); endmodule module bar(); endmodule` |
| `description` | 50 | 🔴 HIGH | - | ✅ | `module counter(clk, rst, count); endmodule` |
| `module_declaration` | 66 | 🔴 HIGH | ✅ | ✅ | `module adder #(parameter W=8) (input [W-1:0] a, b, output [W-1:0] sum); assign sum = a + b; endmodule` |

## A.1.2a SystemVerilog source text extensions

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `interface_declaration` | 78 | 🔴 HIGH | ✅ | - | `interface my_bus; logic [7:0] data; modport master(output data); endinterface` |
| `interface_item` | 84 | 🔴 HIGH | ✅ | - | `logic [7:0] data;` |
| `modport_declaration` | 91 | 🔴 HIGH | ✅ | - | `modport master(output data);` |
| `modport_port_declaration` | 97 | 🔴 HIGH | ✅ | - | `output data` |
| `package_declaration` | 104 | 🔴 HIGH | ✅ | - | `package my_pkg; localparam WIDTH = 8; endpackage` |
| `package_item` | 110 | 🔴 HIGH | ✅ | - | `localparam WIDTH = 8;` |
| `import_declaration` | 121 | 🔴 HIGH | ✅ | - | `import my_pkg::WIDTH;` |
| `import_item` | 127 | 🔴 HIGH | ✅ | - | `my_pkg::WIDTH` |

## A.1.3 Module parameters and ports

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `module_parameter_port_list` | 136 | 🔴 HIGH | ✅ | - | `#(parameter WIDTH=8, parameter DEPTH=16)` |
| `list_of_ports` | 141 | 🔴 HIGH | ✅ | - | `(clk, rst, data_in, data_out)` |
| `list_of_port_declarations` | 148 | 🔴 HIGH | ✅ | - | `(input clk, input rst, output [7:0] data)` |
| `port` | 156 | 🔴 HIGH | ✅ | - | `.data_in(internal_data)` |
| `port_expression` | 164 | 🔴 HIGH | ✅ | - | `{a, b, c}` |
| `port_reference` | 171 | 🔴 HIGH | ✅ | - | `data[7:0]` |
| `port_declaration` | 180 | 🔴 HIGH | ✅ | - | `input wire [7:0] data` |

## A.1.4 Module items

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `module_item` | 192 | 🔴 HIGH | ✅ | - | `input clk;` |
| `module_or_generate_item` | 210 | 🔴 HIGH | ✅ | - | `wire [7:0] data;` |
| `module_or_generate_item_declaration` | 241 | 🔴 HIGH | ⚠️ | - | `wire [7:0] data;` |
| `non_port_module_item` | 268 | 🔴 HIGH | ⚠️ | - | `always @(posedge clk) q <= d;` |
| `parameter_override` | 278 | 🟡 MED | ✅ | - | `defparam u1.WIDTH = 16;` |

## A.1.5 Configuration source text

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `config_declaration` | 290 | 🟢 LOW | ❌ | ❌ | `config cfg1; design work.top; endconfig` |
| `design_statement` | 296 | - | - | - |  |
| `config_rule_statement` | 306 | 🟢 LOW | ❌ | - | `default liblist work;` |
| `default_clause` | 311 | 🟢 LOW | ❌ | - | `default` |
| `inst_clause` | 316 | 🟢 LOW | ❌ | - | `instance top.u1` |
| `inst_name` | 321 | 🟢 LOW | ❌ | - | `top.u1.u2` |
| `cell_clause` | 326 | 🟢 LOW | ❌ | - | `cell work.counter` |
| `liblist_clause` | 331 | 🟢 LOW | ❌ | - | `liblist work rtl` |
| `use_clause` | 336 | 🟢 LOW | ❌ | - | `use work.new_cell` |

## A.2.1.1 Module parameter declarations

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `local_parameter_declaration` | 350 | 🔴 HIGH | ✅ | - | `localparam WIDTH = 8` |
| `parameter_declaration` | 360 | 🔴 HIGH | ✅ | - | `parameter WIDTH = 8` |
| `specparam_declaration` | 367 | 🟢 LOW | ❌ | - | `specparam tRISE = 1.5;` |
| `parameter_type` | 373 | 🔴 HIGH | ✅ | - | `integer` |

## A.2.1.2 Port declarations

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `inout_declaration` | 382 | 🔴 HIGH | ✅ | - | `inout wire signed [7:0] data` |
| `input_declaration` | 388 | 🔴 HIGH | ✅ | - | `input wire signed [7:0] data` |
| `output_declaration` | 401 | 🔴 HIGH | ✅ | - | `output reg [7:0] result` |

## A.2.1.3 Type declarations

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `event_declaration` | 410 | 🟢 LOW | ❌ | - | `event done;` |
| `integer_declaration` | 415 | 🟡 MED | ✅ | - | `integer count;` |
| `net_declaration` | 438 | 🔴 HIGH | ✅ | - | `wire [7:0] data;` |
| `real_declaration` | 450 | 🟢 LOW | ❌ | - | `real voltage;` |
| `realtime_declaration` | 455 | 🟢 LOW | ❌ | - | `realtime sim_time;` |
| `reg_declaration` | 461 | 🔴 HIGH | ✅ | - | `reg [7:0] counter;` |
| `logic_declaration` | 466 | 🔴 HIGH | ✅ | - | `logic [7:0] data;` |
| `bit_declaration` | 471 | 🔴 HIGH | ✅ | - | `bit [3:0] nibble;` |
| `byte_declaration` | 476 | 🟡 MED | ✅ | - | `byte status;` |
| `shortint_declaration` | 481 | 🟡 MED | ✅ | - | `shortint offset;` |
| `int_declaration` | 486 | 🟡 MED | ✅ | - | `int count;` |
| `longint_declaration` | 491 | 🟡 MED | ✅ | - | `longint timestamp;` |
| `typedef_declaration` | 500 | 🔴 HIGH | ✅ | - | `typedef enum logic [1:0] { IDLE, RUN, DONE } state_t;` |
| `enum_declaration` | 508 | 🔴 HIGH | ✅ | - | `enum logic [1:0] { IDLE, RUN, DONE }` |
| `enum_base_type` | 513 | 🔴 HIGH | ✅ | - | `logic [1:0]` |
| `enum_name_declaration` | 519 | 🔴 HIGH | ✅ | - | `IDLE = 2'b00` |
| `struct_declaration` | 524 | 🔴 HIGH | ✅ | - | `struct packed { logic [7:0] data; logic valid; }` |
| `union_declaration` | 529 | 🔴 HIGH | ✅ | - | `union packed { logic [31:0] word; logic [7:0] byte_val; }` |
| `struct_member` | 534 | 🔴 HIGH | ✅ | - | `logic [7:0] data;` |
| `time_declaration` | 539 | 🟢 LOW | ❌ | - | `time start_time;` |

## A.2.2.1 Net and variable types

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `net_type` | 550 | 🔴 HIGH | ✅ | - | `wire` |
| `output_variable_type` | 555 | 🟡 MED | ✅ | - | `integer` |
| `real_type` | 562 | 🟢 LOW | ❌ | - | `voltage = 3.3` |
| `variable_type` | 570 | 🔴 HIGH | ✅ | - | `count = 0` |

## A.2.2.2 Strengths

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `drive_strength` | 585 | 🟢 LOW | ❌ | - | `(strong1, weak0)` |
| `strength0` | 596 | 🟢 LOW | ❌ | - | `strong0` |
| `strength1` | 601 | 🟢 LOW | ❌ | - | `strong1` |
| `charge_strength` | 606 | 🟢 LOW | ❌ | - | `(medium)` |

## A.2.2.3 Delays

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `delay3` | 615 | 🟡 MED | ❌ | - | `#(1, 2, 3)` |
| `delay2` | 624 | 🟡 MED | ❌ | - | `#(5, 10)` |
| `delay_value` | 634 | 🟡 MED | ❌ | - | `10` |

## A.2.3 Declaration lists

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `list_of_defparam_assignments` | 643 | 🟡 MED | ✅ | - | `u1.WIDTH = 16, u2.WIDTH = 32` |
| `list_of_event_identifiers` | 649 | 🟢 LOW | ❌ | - | `evt1, evt2[3:0]` |
| `list_of_net_decl_assignments` | 654 | 🔴 HIGH | ✅ | - | `x = a & b, y = c | d` |
| `list_of_net_identifiers` | 660 | 🔴 HIGH | ✅ | - | `clk, rst, data[7:0]` |
| `list_of_param_assignments` | 665 | 🔴 HIGH | ✅ | - | `WIDTH = 8, DEPTH = 16` |
| `list_of_port_identifiers` | 670 | 🔴 HIGH | ✅ | - | `a, b, c` |
| `list_of_real_identifiers` | 675 | 🟢 LOW | ❌ | - | `voltage, current = 0.1` |
| `list_of_specparam_assignments` | 680 | 🟢 LOW | ❌ | - | `tRISE = 1.0, tFALL = 1.5` |
| `list_of_variable_identifiers` | 685 | 🔴 HIGH | ✅ | - | `count, index = 0` |
| `list_of_variable_port_identifiers` | 691 | 🔴 HIGH | ✅ | - | `result = 0, carry = 1'b0` |

## A.2.4 Declaration assignments

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `defparam_assignment` | 698 | 🟡 MED | ✅ | - | `u1.WIDTH = 16` |
| `net_decl_assignment` | 703 | 🔴 HIGH | ✅ | - | `data = in_a & in_b` |
| `param_assignment` | 708 | 🔴 HIGH | ✅ | - | `WIDTH = 8` |
| `specparam_assignment` | 715 | 🟢 LOW | ❌ | - | `tRISE = 1.5` |
| `pulse_control_specparam` | 725 | 🟢 LOW | ❌ | - | `PATHPULSE$ = (1, 2)` |
| `error_limit_value` | 732 | 🟢 LOW | ❌ | - | `2` |
| `reject_limit_value` | 737 | 🟢 LOW | ❌ | - | `1` |
| `limit_value` | 742 | 🟢 LOW | ❌ | - | `1:2:3` |

## A.2.5 Declaration ranges

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `dimension` | 749 | 🔴 HIGH | ✅ | - | `[0:15]` |
| `range` | 754 | 🔴 HIGH | ✅ | - | `[7:0]` |

## A.2.6 Function declarations

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `function_declaration` | 769 | 🟡 MED | ✅ | - | `function automatic [7:0] add; input [7:0] a, b; begin add = a + b; end endfunction` |
| `function_item_declaration` | 778 | 🟡 MED | ✅ | - | `input [7:0] a;` |
| `function_port_list` | 786 | 🟡 MED | ✅ | - | `input [7:0] a, input [7:0] b` |
| `function_range_or_type` | 797 | 🟡 MED | ✅ | - | `[7:0]` |

## A.2.7 Task declarations

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `task_declaration` | 816 | 🟡 MED | ❌ | - | `task automatic delay_10; #10; endtask` |
| `task_item_declaration` | 827 | 🟡 MED | ❌ | - | `input [7:0] data;` |
| `task_port_list` | 836 | 🟡 MED | ❌ | - | `input a, output b` |
| `task_port_item` | 844 | 🟡 MED | ❌ | - | `input [7:0] data` |
| `tf_input_declaration` | 855 | 🟡 MED | ⚠️ | - | `input [7:0] data` |
| `tf_output_declaration` | 865 | 🟡 MED | ❌ | - | `output [7:0] result` |
| `tf_inout_declaration` | 874 | 🟡 MED | ❌ | - | `inout [7:0] bus` |
| `task_port_type` | 882 | 🟡 MED | ❌ | - | `integer` |

## A.2.8 Block item declarations

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `block_item_declaration` | 899 | 🔴 HIGH | ⚠️ | - | `reg signed [7:0] temp;` |
| `list_of_block_variable_identifiers` | 912 | 🔴 HIGH | ✅ | - | `a, b, c[7:0]` |
| `list_of_block_real_identifiers` | 917 | 🟢 LOW | ❌ | - | `voltage, current` |
| `block_variable_type` | 922 | 🔴 HIGH | ✅ | - | `temp[7:0]` |
| `block_real_type` | 927 | 🟢 LOW | ❌ | - | `voltage` |

## A.3.1 Primitive instantiation and instances

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `gate_instantiation` | 956 | 🟢 LOW | ✅ | ❌ | `and g1(out, in1, in2);` |
| `cmos_switch_instance` | 971 | 🟢 LOW | ✅ | - | `g1(out, in, nctrl, pctrl)` |
| `enable_gate_instance` | 977 | 🟢 LOW | ✅ | - | `g1(out, in, en)` |
| `mos_switch_instance` | 982 | 🟢 LOW | ✅ | - | `m1(out, in, gate)` |
| `n_input_gate_instance` | 987 | 🟢 LOW | ✅ | - | `g1(out, in1, in2, in3)` |
| `n_output_gate_instance` | 992 | 🟢 LOW | ✅ | - | `g1(out1, out2, in)` |
| `pass_switch_instance` | 997 | 🟢 LOW | ✅ | - | `p1(a, b)` |
| `pass_enable_switch_instance` | 1003 | 🟢 LOW | ✅ | - | `p1(a, b, en)` |
| `pull_gate_instance` | 1008 | 🟢 LOW | ✅ | - | `g1(out)` |
| `name_of_gate_instance` | 1013 | 🟢 LOW | ✅ | - | `g1[3:0]` |

## A.3.2 Primitive strengths

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `pulldown_strength` | 1023 | 🟢 LOW | ❌ | - | `(strong0, weak1)` |
| `pullup_strength` | 1034 | 🟢 LOW | ❌ | - | `(weak1)` |

## A.3.3 Primitive terminals

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `enable_terminal` | 1044 | 🟢 LOW | ✅ | - | `en` |
| `inout_terminal` | 1049 | 🟢 LOW | ✅ | - | `data` |
| `input_terminal` | 1054 | 🟢 LOW | ✅ | - | `a & b` |
| `ncontrol_terminal` | 1059 | 🟢 LOW | ✅ | - | `nctrl` |
| `output_terminal` | 1064 | 🟢 LOW | ✅ | - | `out` |
| `pcontrol_terminal` | 1069 | 🟢 LOW | ✅ | - | `pctrl` |

## A.3.4 Primitive gate and switch types

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `cmos_switchtype` | 1076 | 🟢 LOW | ✅ | - | `cmos` |
| `enable_gatetype` | 1081 | 🟢 LOW | ✅ | - | `bufif1` |
| `mos_switchtype` | 1086 | 🟢 LOW | ✅ | - | `nmos` |
| `n_input_gatetype` | 1091 | 🟢 LOW | ✅ | - | `and` |
| `n_output_gatetype` | 1096 | 🟢 LOW | ✅ | - | `buf` |
| `pass_en_switchtype` | 1101 | 🟢 LOW | ✅ | - | `tranif1` |
| `pass_switchtype` | 1106 | 🟢 LOW | ✅ | - | `tran` |

## A.4.1 Module instantiation

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `module_instantiation` | 1116 | 🔴 HIGH | ✅ | - | `counter #(.WIDTH(8)) u1 (.clk(clk), .rst(rst));` |
| `parameter_value_assignment` | 1121 | 🔴 HIGH | ✅ | - | `#(.WIDTH(8), .DEPTH(16))` |
| `list_of_parameter_assignments` | 1128 | 🔴 HIGH | ✅ | - | `.WIDTH(8), .DEPTH(16)` |
| `ordered_parameter_assignment` | 1134 | 🔴 HIGH | ✅ | - | `8` |
| `named_parameter_assignment` | 1139 | 🔴 HIGH | ✅ | - | `.WIDTH(8)` |
| `module_instance` | 1144 | 🔴 HIGH | ✅ | - | `u1(.clk(clk), .data(data))` |
| `name_of_module_instance` | 1149 | 🔴 HIGH | ✅ | - | `u1[3:0]` |
| `list_of_port_connections` | 1156 | 🔴 HIGH | ✅ | - | `.clk(clk), .rst(rst)` |
| `ordered_port_connection` | 1163 | 🔴 HIGH | ✅ | - | `clk` |
| `named_port_connection` | 1168 | 🔴 HIGH | ✅ | - | `.clk(sys_clk)` |

## A.4.2 Generate construct

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `generate_region` | 1176 | 🟡 MED | ✅ | - | `generate for (i=0; i<4; i=i+1) begin : gen_loop wire x; end endgenerate` |
| `genvar_declaration` | 1182 | 🟡 MED | ✅ | - | `genvar i, j;` |
| `list_of_genvar_identifiers` | 1188 | 🟡 MED | ✅ | - | `i, j` |
| `loop_generate_construct` | 1195 | 🟡 MED | ✅ | - | `for (i=0; i<4; i=i+1) begin : gen_blk wire x; end` |
| `genvar_initialization` | 1205 | 🟡 MED | ✅ | - | `i = 0` |
| `genvar_expression` | 1216 | 🟡 MED | ✅ | - | `i < 4` |
| `genvar_iteration` | 1231 | 🟡 MED | ✅ | - | `i = i + 1` |
| `genvar_primary` | 1247 | 🟡 MED | ✅ | - | `i` |
| `conditional_generate_construct` | 1256 | 🟡 MED | ✅ | - | `if (WIDTH > 8) begin : wide_path wire x; end else begin : narrow_path wire y; end` |
| `if_generate_construct` | 1264 | 🟡 MED | ✅ | - | `if (ENABLE) begin : gen_en wire x; end` |
| `case_generate_construct` | 1275 | 🟡 MED | ✅ | - | `case (MODE) 0: begin : m0 wire a; end 1: begin : m1 wire b; end endcase` |
| `case_generate_item` | 1282 | 🟡 MED | ✅ | - | `0: begin : case0 wire x; end` |
| `generate_block` | 1290 | 🟡 MED | ✅ | - | `begin : blk wire x; end` |
| `generate_block_or_null` | 1299 | 🟡 MED | ✅ | - | `begin : blk wire x; end` |

## A.5.1 UDP declaration

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `udp_declaration` | 1316 | 🟢 LOW | ✅ | ✅ | `primitive mux(out, sel, a, b); output out; input sel, a, b; table 0 0 ? : 0; 0 1 ? : 1; 1 ? 0 : 0; 1 ? 1 : 1; endtable endprimitive` |

## A.5.2 UDP ports

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `udp_port_list` | 1324 | 🟢 LOW | ❌ | - | `out, sel, a, b` |
| `udp_declaration_port_list` | 1330 | 🟢 LOW | ❌ | - | `output out, input a, input b` |
| `udp_port_declaration` | 1338 | 🟢 LOW | ❌ | - | `output out;` |
| `udp_output_declaration` | 1347 | 🟢 LOW | ❌ | - | `output reg q = 1'b0` |
| `udp_input_declaration` | 1353 | 🟢 LOW | ❌ | - | `input a, b, c` |
| `udp_reg_declaration` | 1358 | 🟢 LOW | ❌ | - | `reg q` |

## A.5.3 UDP body

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `udp_body` | 1365 | 🟢 LOW | ❌ | - | `table 00:0; 01:0; 10:0; 11:1; endtable` |
| `combinational_body` | 1370 | 🟢 LOW | ❌ | - | `table 00:0; 01:0; 10:0; 11:1; endtable` |
| `combinational_entry` | 1375 | 🟢 LOW | ❌ | - | `00 : 0 ;` |
| `sequential_body` | 1380 | 🟢 LOW | ❌ | - | `table ?r:0:1; endtable` |
| `udp_initial_statement` | 1385 | 🟢 LOW | ❌ | - | `initial q = 1'b0;` |
| `init_val` | 1390 | 🟢 LOW | ❌ | - | `1'b0` |
| `sequential_entry` | 1395 | 🟢 LOW | ❌ | - | `?r : 0 : 1 ;` |
| `seq_input_list` | 1400 | 🟢 LOW | ❌ | - | `?r` |
| `level_input_list` | 1405 | 🟢 LOW | ❌ | - | `01` |
| `edge_input_list` | 1410 | 🟢 LOW | ❌ | - | `?r?` |
| `edge_indicator` | 1415 | 🟢 LOW | ❌ | - | `(01)` |
| `current_state` | 1420 | 🟢 LOW | ❌ | - | `0` |
| `next_state` | 1425 | 🟢 LOW | ❌ | - | `1` |
| `output_symbol` | 1430 | 🟢 LOW | ❌ | - | `1` |
| `level_symbol` | 1435 | 🟢 LOW | ❌ | - | `?` |
| `edge_symbol` | 1440 | 🟢 LOW | ❌ | - | `r` |

## A.5.4 UDP instantiation

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `udp_instantiation` | 1448 | 🟢 LOW | ❌ | - | `my_udp u1(out, in1, in2);` |
| `udp_instance` | 1454 | 🟢 LOW | ❌ | - | `u1(out, in1, in2)` |
| `name_of_udp_instance` | 1459 | 🟢 LOW | ❌ | - | `u1[3:0]` |

## A.6.1 Continuous assignment statements

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `continuous_assign` | 1467 | 🔴 HIGH | ✅ | - | `assign y = a & b;` |
| `list_of_net_assignments` | 1472 | 🔴 HIGH | ✅ | - | `y = a & b, z = c | d` |
| `net_assignment` | 1477 | 🔴 HIGH | ✅ | - | `y = a & b` |

## A.6.2 Procedural blocks and assignments

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `initial_construct` | 1484 | 🟡 MED | ❌ | - | `initial begin clk = 0; end` |
| `always_construct` | 1489 | 🔴 HIGH | ✅ | - | `always @(posedge clk) q <= d;` |
| `always_comb_construct` | 1494 | 🔴 HIGH | ✅ | - | `always_comb begin y = a & b; end` |
| `always_ff_construct` | 1499 | 🔴 HIGH | ✅ | - | `always_ff @(posedge clk or negedge rst_n) begin q <= d; end` |
| `always_latch_construct` | 1504 | 🔴 HIGH | ✅ | - | `always_latch begin if (en) q <= d; end` |
| `blocking_assignment` | 1509 | 🔴 HIGH | ✅ | - | `x = a + b` |
| `nonblocking_assignment` | 1514 | 🔴 HIGH | ✅ | - | `q <= d` |
| `procedural_continuous_assignments` | 1527 | 🟢 LOW | ❌ | - | `force data = 8'hFF` |
| `variable_assignment` | 1538 | 🔴 HIGH | ✅ | - | `data = value` |

## A.6.3 Parallel and sequential blocks

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `par_block` | 1546 | 🟢 LOW | ❌ | - | `fork : par_blk #10 a = 1; #20 b = 2; join` |
| `seq_block` | 1552 | 🔴 HIGH | ✅ | - | `begin : blk reg [7:0] temp; x = 1; y = 2; end` |

## A.6.4 Statements

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `statement` | 1574 | 🔴 HIGH | ⚠️ | - | `if (sel) y = a; else y = b;` |
| `statement_or_null` | 1595 | 🔴 HIGH | ⚠️ | - | `;` |
| `function_statement` | 1601 | 🟡 MED | ✅ | - | `begin add = a + b; end` |

## A.6.5 Timing control statements

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `delay_control` | 1610 | 🟡 MED | ❌ | - | `#10` |
| `delay_or_event_control` | 1619 | 🟡 MED | ❌ | - | `@(posedge clk)` |
| `disable_statement` | 1628 | 🟡 MED | ❌ | - | `disable my_task;` |
| `event_control` | 1639 | 🔴 HIGH | ✅ | - | `@(posedge clk or negedge rst)` |
| `event_trigger` | 1649 | 🟢 LOW | ❌ | - | `-> done_event;` |
| `event_expression` | 1659 | 🔴 HIGH | ✅ | - | `posedge clk or negedge rst` |
| `procedural_timing_control` | 1671 | 🔴 HIGH | ⚠️ | - | `@(posedge clk)` |
| `procedural_timing_control_statement` | 1678 | 🔴 HIGH | ⚠️ | - | `@(posedge clk) q <= d;` |
| `wait_statement` | 1684 | 🟢 LOW | ❌ | - | `wait (ready) data = bus;` |

## A.6.6 Conditional statements

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `conditional_statement` | 1694 | 🔴 HIGH | ✅ | - | `if (sel) y = a; else y = b;` |
| `if_else_if_statement` | 1703 | 🔴 HIGH | ✅ | - | `if (a) x=1; else if (b) x=2; else x=3;` |

## A.6.7 Case statements

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `case_statement` | 1717 | 🔴 HIGH | ✅ | - | `case (sel) 2'b00: y=a; 2'b01: y=b; default: y=0; endcase` |
| `case_qualifier` | 1721 | - | - | - |  |
| `case_item` | 1728 | 🔴 HIGH | ✅ | - | `2'b00: y = a;` |

## A.6.8 Looping statements

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `loop_statement` | 1743 | 🟡 MED | ⚠️ | - | `for (i=0; i<8; i=i+1) mem[i] = 0;` |

## A.6.9 Task enable statements

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `system_task_enable` | 1754 | 🟡 MED | ❌ | - | `$display("value=%d", val);` |
| `task_enable` | 1759 | 🟡 MED | ❌ | - | `my_task(arg1, arg2);` |

## A.7.1 Specify block declaration

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `specify_block` | 1767 | 🟢 LOW | ❌ | - | `specify (a => b) = 1; endspecify` |
| `specify_item` | 1777 | 🟢 LOW | ❌ | - | `(a => b) = 1;` |
| `pulsestyle_declaration` | 1788 | 🟢 LOW | ❌ | - | `pulsestyle_onevent out;` |
| `showcancelled_declaration` | 1796 | 🟢 LOW | ❌ | - | `showcancelled out;` |

## A.7.2 Specify path declarations

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `path_declaration` | 1807 | 🟢 LOW | ❌ | - | `(a => b) = 1;` |
| `simple_path_declaration` | 1816 | 🟢 LOW | ❌ | - | `(a => b) = 1` |
| `parallel_path_description` | 1823 | 🟢 LOW | ❌ | - | `(a => b)` |
| `full_path_description` | 1829 | 🟢 LOW | ❌ | - | `(a, b *> c, d)` |
| `list_of_path_inputs` | 1835 | 🟢 LOW | ❌ | - | `a, b` |
| `list_of_path_outputs` | 1841 | 🟢 LOW | ❌ | - | `c, d` |

## A.7.3 Specify block terminals

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `specify_input_terminal_descriptor` | 1850 | 🟢 LOW | ❌ | - | `a[7:0]` |
| `specify_output_terminal_descriptor` | 1856 | 🟢 LOW | ❌ | - | `q[7:0]` |
| `input_identifier` | 1861 | 🟢 LOW | ❌ | - | `clk` |
| `output_identifier` | 1866 | 🟢 LOW | ❌ | - | `q` |

## A.7.4 Specify path delays

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `path_delay_value` | 1875 | 🟢 LOW | ❌ | - | `(1, 2, 3)` |
| `list_of_path_delay_expressions` | 1890 | 🟢 LOW | ❌ | - | `1, 2` |
| `t_path_delay_expression` | 1900 | 🟢 LOW | ❌ | - | `1` |
| `trise_path_delay_expression` | 1905 | 🟢 LOW | ❌ | - | `2` |
| `tfall_path_delay_expression` | 1910 | 🟢 LOW | ❌ | - | `3` |
| `tz_path_delay_expression` | 1915 | 🟢 LOW | ❌ | - | `4` |
| `t01_path_delay_expression` | 1920 | 🟢 LOW | ❌ | - | `1` |
| `t10_path_delay_expression` | 1925 | 🟢 LOW | ❌ | - | `2` |
| `t0z_path_delay_expression` | 1930 | 🟢 LOW | ❌ | - | `3` |
| `tz1_path_delay_expression` | 1935 | 🟢 LOW | ❌ | - | `4` |
| `t1z_path_delay_expression` | 1940 | 🟢 LOW | ❌ | - | `5` |
| `tz0_path_delay_expression` | 1945 | 🟢 LOW | ❌ | - | `6` |
| `t0x_path_delay_expression` | 1950 | 🟢 LOW | ❌ | - | `7` |
| `tx1_path_delay_expression` | 1955 | 🟢 LOW | ❌ | - | `8` |
| `t1x_path_delay_expression` | 1960 | 🟢 LOW | ❌ | - | `9` |
| `tx0_path_delay_expression` | 1965 | 🟢 LOW | ❌ | - | `10` |
| `txz_path_delay_expression` | 1970 | 🟢 LOW | ❌ | - | `11` |
| `tzx_path_delay_expression` | 1975 | 🟢 LOW | ❌ | - | `12` |
| `path_delay_expression` | 1980 | 🟢 LOW | ❌ | - | `1:2:3` |
| `edge_sensitive_path_declaration` | 1987 | 🟢 LOW | ❌ | - | `(posedge clk => (q +: d)) = 1` |
| `parallel_edge_sensitive_path_description` | 1995 | 🟢 LOW | ❌ | - | `(posedge clk => (q +: d))` |
| `full_edge_sensitive_path_description` | 2003 | 🟢 LOW | ❌ | - | `(posedge clk *> (q, r +: d))` |
| `data_source_expression` | 2007 | 🟢 LOW | ❌ | - | `d` |
| `edge_identifier` | 2012 | 🟢 LOW | ❌ | - | `posedge` |
| `state_dependent_path_declaration` | 2020 | 🟢 LOW | ❌ | - | `if (enable) (a => b) = 1` |
| `polarity_operator` | 2027 | 🟢 LOW | ❌ | - | `+` |

## A.7.5.1 System timing check commands

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `system_timing_check` | 2047 | 🟢 LOW | ❌ | - | `$setup(data, posedge clk, 1);` |
| `setup_timing_check` | 2065 | 🟢 LOW | ❌ | - | `$setup(d, posedge clk, 1);` |
| `hold_timing_check` | 2071 | 🟢 LOW | ❌ | - | `$hold(posedge clk, d, 1);` |
| `setuphold_timing_check` | 2079 | 🟢 LOW | ❌ | - | `$setuphold(posedge clk, d, 1, 2);` |
| `recovery_timing_check` | 2085 | 🟢 LOW | ❌ | - | `$recovery(posedge rst, d, 1);` |
| `removal_timing_check` | 2091 | 🟢 LOW | ❌ | - | `$removal(posedge rst, d, 1);` |
| `recrem_timing_check` | 2099 | 🟢 LOW | ❌ | - | `$recrem(posedge rst, d, 1, 2);` |
| `skew_timing_check` | 2105 | 🟢 LOW | ❌ | - | `$skew(posedge clk1, posedge clk2, 1);` |
| `timeskew_timing_check` | 2112 | 🟢 LOW | ❌ | - | `$timeskew(posedge clk1, posedge clk2, 1);` |
| `fullskew_timing_check` | 2119 | 🟢 LOW | ❌ | - | `$fullskew(posedge clk1, posedge clk2, 1, 2);` |
| `period_timing_check` | 2125 | 🟢 LOW | ❌ | - | `$period(posedge clk, 10);` |
| `width_timing_check` | 2132 | 🟢 LOW | ❌ | - | `$width(posedge clk, 5);` |
| `nochange_timing_check` | 2139 | 🟢 LOW | ❌ | - | `$nochange(posedge clk, d, 1, 2);` |

## A.7.5.2 System timing check command arguments

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `checktime_condition` | 2146 | 🟢 LOW | ❌ | - | `1` |
| `controlled_reference_event` | 2151 | 🟢 LOW | ❌ | - | `posedge clk` |
| `data_event` | 2156 | 🟢 LOW | ❌ | - | `d` |
| `delayed_data` | 2163 | 🟢 LOW | ❌ | - | `d_delayed` |
| `delayed_reference` | 2171 | 🟢 LOW | ❌ | - | `clk_delayed` |
| `end_edge_offset` | 2177 | 🟢 LOW | ❌ | - | `5` |
| `event_based_flag` | 2182 | 🟢 LOW | ❌ | - | `1` |
| `notifier` | 2187 | 🟢 LOW | ❌ | - | `notif` |
| `reference_event` | 2192 | 🟢 LOW | ❌ | - | `posedge clk` |
| `remain_active_flag` | 2197 | 🟢 LOW | ❌ | - | `1` |
| `stamptime_condition` | 2202 | 🟢 LOW | ❌ | - | `1` |
| `start_edge_offset` | 2207 | 🟢 LOW | ❌ | - | `2` |
| `threshold` | 2212 | 🟢 LOW | ❌ | - | `0.5` |
| `timing_check_limit` | 2217 | 🟢 LOW | ❌ | - | `10` |

## A.7.5.3 System timing check event definitions

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `timing_check_event` | 2225 | 🟢 LOW | ❌ | - | `posedge clk &&& enable` |
| `controlled_timing_check_event` | 2231 | 🟢 LOW | ❌ | - | `posedge clk` |
| `timing_check_event_control` | 2239 | 🟢 LOW | ❌ | - | `posedge` |
| `specify_terminal_descriptor` | 2248 | 🟢 LOW | ❌ | - | `clk` |
| `edge_control_specifier` | 2254 | 🟢 LOW | ❌ | - | `edge[01, 10]` |
| `edge_descriptor` | 2264 | 🟢 LOW | ❌ | - | `01` |
| `zero_or_one` | 2271 | 🟢 LOW | ❌ | - | `0` |
| `z_or_x` | 2276 | 🟢 LOW | ❌ | - | `x` |
| `timing_check_condition` | 2283 | 🟢 LOW | ❌ | - | `(enable)` |
| `scalar_timing_check_condition` | 2295 | 🟢 LOW | ❌ | - | `enable == 1'b1` |
| `scalar_constant` | 2306 | 🟢 LOW | ❌ | - | `1'b1` |

## A.8.1 Concatenations

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `concatenation` | 2314 | 🔴 HIGH | ✅ | - | `{a, b, c}` |
| `constant_concatenation` | 2319 | 🔴 HIGH | ✅ | - | `{4'b0, 4'b1}` |
| `constant_multiple_concatenation` | 2324 | 🔴 HIGH | ✅ | - | `{4{4'b0}}` |
| `module_path_concatenation` | 2329 | 🟢 LOW | ❌ | - | `{clk, reset}` |
| `module_path_multiple_concatenation` | 2334 | 🟢 LOW | ❌ | - | `{2{clk, reset}}` |
| `multiple_concatenation` | 2339 | 🔴 HIGH | ✅ | - | `{4{data}}` |

## A.8.2 Function calls

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `constant_function_call` | 2347 | 🟡 MED | ✅ | - | `clog2(WIDTH)` |
| `constant_system_function_call` | 2353 | 🟡 MED | ❌ | - | `$clog2(8)` |
| `function_call` | 2359 | 🟡 MED | ✅ | - | `add(a, b)` |

## A.8.3 Expressions

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `base_expression` | 2373 | 🔴 HIGH | ✅ | - | `$random` |
| `conditional_expression` | 2378 | 🔴 HIGH | ✅ | - | `sel ? a : b` |
| `constant_base_expression` | 2383 | 🔴 HIGH | ✅ | - | `WIDTH` |
| `constant_expression` | 2393 | 🔴 HIGH | ✅ | - | `WIDTH - 1` |
| `constant_mintypmax_expression` | 2403 | 🔴 HIGH | ⚠️ | - | `5:10:15` |
| `constant_range_expression` | 2413 | 🔴 HIGH | ✅ | - | `7:0` |
| `dimension_constant_expression` | 2421 | 🔴 HIGH | ✅ | - | `8` |
| `expression` | 2431 | 🔴 HIGH | ✅ | - | `~a & b` |
| `lsb_constant_expression` | 2442 | 🔴 HIGH | ✅ | - | `0` |
| `mintypmax_expression` | 2449 | 🔴 HIGH | ⚠️ | - | `1:2:3` |
| `module_path_conditional_expression` | 2456 | 🟢 LOW | ❌ | - | `en ? a : b` |
| `module_path_expression` | 2466 | 🟢 LOW | ❌ | - | `clk && en` |
| `module_path_mintypmax_expression` | 2476 | 🟢 LOW | ❌ | - | `1:2:3` |
| `msb_constant_expression` | 2482 | 🔴 HIGH | ✅ | - | `7` |
| `range_expression` | 2491 | 🔴 HIGH | ✅ | - | `7:0` |
| `width_constant_expression` | 2499 | 🔴 HIGH | ✅ | - | `8` |

## A.8.4 Primaries

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `constant_primary` | 2517 | 🔴 HIGH | ✅ | - | `8'hFF` |
| `module_path_primary` | 2538 | 🟢 LOW | ❌ | - | `clk` |
| `primary` | 2561 | 🔴 HIGH | ✅ | - | `data[7:0]` |

## A.8.5 Expression left-side values

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `net_lvalue` | 2579 | 🔴 HIGH | ✅ | - | `{cout, sum}` |
| `variable_lvalue` | 2588 | 🔴 HIGH | ✅ | - | `data[7:0]` |

## A.8.6 Operators

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `unary_operator` | 2657 | 🔴 HIGH | ✅ | - | `~` |
| `binary_operator` | 2675 | 🔴 HIGH | ✅ | - | `+` |
| `unary_module_path_operator` | 2707 | 🟢 LOW | ❌ | - | `!` |
| `binary_module_path_operator` | 2722 | 🟢 LOW | ❌ | - | `&&` |

## A.8.7 Numbers

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `number` | 2772 | 🔴 HIGH | ✅ | - | `8'hFF` |
| `real_number` | 2784 | 🟢 LOW | ❌ | - | `3.14` |
| `exp` | 2790 | 🟢 LOW | ❌ | - | `e` |
| `decimal_number` | 2800 | 🔴 HIGH | ✅ | - | `42` |
| `binary_number` | 2809 | 🔴 HIGH | ✅ | - | `4'b1010` |
| `octal_number` | 2814 | 🔴 HIGH | ✅ | - | `8'o77` |
| `hex_number` | 2819 | 🔴 HIGH | ✅ | - | `8'hFF` |
| `sign` | 2824 | 🔴 HIGH | ✅ | - | `-` |
| `size` | 2829 | 🔴 HIGH | ✅ | - | `8` |
| `non_zero_unsigned_number` | 2834 | 🔴 HIGH | ✅ | - | `123` |
| `unsigned_number` | 2839 | 🔴 HIGH | ✅ | - | `42` |
| `binary_value` | 2844 | 🔴 HIGH | ✅ | - | `1010` |
| `octal_value` | 2849 | 🔴 HIGH | ✅ | - | `77` |
| `hex_value` | 2854 | 🔴 HIGH | ✅ | - | `FF` |
| `decimal_base` | 2859 | 🔴 HIGH | ✅ | - | `'d` |
| `binary_base` | 2865 | 🔴 HIGH | ✅ | - | `'b` |
| `octal_base` | 2871 | 🔴 HIGH | ✅ | - | `'o` |
| `hex_base` | 2877 | 🔴 HIGH | ✅ | - | `'h` |
| `non_zero_decimal_digit` | 2883 | 🔴 HIGH | ✅ | - | `1` |
| `decimal_digit` | 2888 | 🔴 HIGH | ✅ | - | `5` |
| `binary_digit` | 2893 | 🔴 HIGH | ✅ | - | `1` |
| `octal_digit` | 2897 | 🔴 HIGH | ✅ | - | `7` |
| `hex_digit` | 2904 | 🔴 HIGH | ✅ | - | `F` |
| `x_digit` | 2909 | 🔴 HIGH | ✅ | - | `x` |
| `z_digit` | 2914 | 🔴 HIGH | ✅ | - | `z` |

## A.8.8 Strings

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `string` | 2922 | 🟡 MED | ❌ | - | `"Hello World"` |

## A.9.1 Attributes

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `attribute_instance` | 2930 | 🟡 MED | ✅ | - | `(* full_case *)` |
| `attr_spec` | 2936 | 🟡 MED | ✅ | - | `full_case = 1` |
| `attr_name` | 2941 | 🟡 MED | ✅ | - | `full_case` |

## A.9.3 Identifiers

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `hierarchical_block_identifier` | 2965 | - | - | - |  |
| `hierarchical_event_identifier` | 2966 | - | - | - |  |
| `hierarchical_function_identifier` | 2967 | - | - | - |  |
| `hierarchical_identifier` | 2972 | 🔴 HIGH | ✅ | - | `top.u1.counter` |
| `hierarchical_net_identifier` | 2973 | - | - | - |  |
| `hierarchical_parameter_identifier` | 2974 | - | - | - |  |
| `hierarchical_variable_identifier` | 2975 | - | - | - |  |
| `hierarchical_task_identifier` | 2976 | - | - | - |  |

## Uncategorized

| Rule | Line | Priority | Synth | Support | Example |
|------|------|----------|-------|---------|---------|
| `verilog` | 5 | 🔴 HIGH | - | ✅ | `module top(); endmodule` |
