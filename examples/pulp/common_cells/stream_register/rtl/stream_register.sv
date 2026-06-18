// Copyright 2022 ETH Zurich and University of Bologna.
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License. You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.

/// Register with a simple stream-like ready/valid handshake.
/// This register does not cut combinatorial paths on all control signals; if
/// you need a complete cut, use `spill_register`.
module stream_register (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic clr_i,
    input  logic testmode_i,
    input  logic valid_i,
    output logic ready_o,
    input  logic [7:0] data_i,
    output logic valid_o,
    input  logic ready_i,
    output logic [7:0] data_o
);

    logic reg_ena;

    assign ready_o = ready_i | ~valid_o;
    assign reg_ena = valid_i & ready_o;

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            valid_o <= 1'b0;
            data_o <= '0;
        end else if (clr_i) begin
            valid_o <= 1'b0;
            data_o <= '0;
        end else begin
            if (ready_o) begin
                valid_o <= valid_i;
            end
            if (reg_ena) begin
                data_o <= data_i;
            end
        end
    end

endmodule
