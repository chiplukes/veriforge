// Copyright 2018 ETH Zurich and University of Bologna.
//
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License. You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.
//
// Fabian Schuiki <fschuiki@iis.ee.ethz.ch>

module gray_to_binary (
    input  logic [3:0] A,
    output logic [3:0] Z
);
    assign Z[3] = A[3];
    assign Z[2] = A[3] ^ A[2];
    assign Z[1] = A[3] ^ A[2] ^ A[1];
    assign Z[0] = A[3] ^ A[2] ^ A[1] ^ A[0];
endmodule
