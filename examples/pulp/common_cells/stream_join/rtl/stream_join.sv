// Copyright 2020 ETH Zurich and University of Bologna.
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License. You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.

module stream_join #(
    parameter int unsigned N_INP = 32'd0
) (
    input  logic [N_INP-1:0] inp_valid_i,
    output logic [N_INP-1:0] inp_ready_o,
    output logic             oup_valid_o,
    input  logic             oup_ready_i
);

    stream_join_dynamic #(
        .N_INP(N_INP)
    ) i_stream_join_dynamic (
        .inp_valid_i(inp_valid_i),
        .inp_ready_o(inp_ready_o),
        .sel_i({N_INP{1'b1}}),
        .oup_valid_o(oup_valid_o),
        .oup_ready_i(oup_ready_i)
    );

endmodule
