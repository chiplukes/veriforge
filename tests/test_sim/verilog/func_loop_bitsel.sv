module func_loop_bitsel (
    input  logic        clk,
    input  logic [3:0]  idx,
    output logic [4:0]  r1
);
// bit length of a value read from an unpacked array, via a loop with a bit-select inside a function
logic [15:0] cnt [0:23];
initial for (int i = 0; i < 24; i++) cnt[i] = i;

function automatic logic [4:0] bitlen16(input logic [15:0] n);
    logic [4:0] r;
    begin
        r = 5'd0;
        for (int i = 0; i < 16; i++)
            if (n[i]) r = i + 1;
        bitlen16 = r;
    end
endfunction

assign r1 = bitlen16(cnt[idx]);
endmodule
