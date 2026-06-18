// Copyright 2018 ETH Zurich and University of Bologna.
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License. You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.

module stream_demux #(
    parameter int unsigned N_OUP = 32'd1,
    parameter int unsigned LOG_N_OUP = (N_OUP > 32'd1) ? $clog2(N_OUP) : 32'd1
) (
    input  logic                 inp_valid_i,
    output logic                 inp_ready_o,
    input  logic [LOG_N_OUP-1:0] oup_sel_i,
    output logic [N_OUP-1:0]     oup_valid_o,
    input  logic [N_OUP-1:0]     oup_ready_i
);

    always_comb begin
        oup_valid_o = '0;
        oup_valid_o[oup_sel_i] = inp_valid_i;
    end

    assign inp_ready_o = oup_ready_i[oup_sel_i];

endmodule
