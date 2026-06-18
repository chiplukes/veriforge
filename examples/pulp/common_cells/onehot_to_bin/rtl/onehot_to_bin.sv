// Copyright 2018 ETH Zurich and University of Bologna.
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License. You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.

module onehot_to_bin (
    input  logic [7:0] onehot,
    output logic [2:0] bin
);
    assign bin[0] = |(onehot & 8'b10101010);
    assign bin[1] = |(onehot & 8'b11001100);
    assign bin[2] = |(onehot & 8'b11110000);
endmodule
