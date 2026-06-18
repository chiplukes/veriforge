// Copyright 2018 ETH Zurich and University of Bologna.
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License. You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.

module stream_mux (
    input  logic [23:0] inp_data_i,
    input  logic [2:0]  inp_valid_i,
    output logic [2:0]  inp_ready_o,
    input  logic [1:0]  inp_sel_i,
    output logic [7:0]  oup_data_o,
    output logic        oup_valid_o,
    input  logic        oup_ready_i
);

    always_comb begin
        inp_ready_o = 3'b000;
        oup_data_o = 8'h00;
        oup_valid_o = 1'b0;

        case (inp_sel_i)
            2'd0: begin
                inp_ready_o[0] = oup_ready_i;
                oup_data_o = inp_data_i[7:0];
                oup_valid_o = inp_valid_i[0];
            end
            2'd1: begin
                inp_ready_o[1] = oup_ready_i;
                oup_data_o = inp_data_i[15:8];
                oup_valid_o = inp_valid_i[1];
            end
            default: begin
                inp_ready_o[2] = oup_ready_i;
                oup_data_o = inp_data_i[23:16];
                oup_valid_o = inp_valid_i[2];
            end
        endcase
    end

endmodule
