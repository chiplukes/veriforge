module popcount #(
    parameter int unsigned INPUT_WIDTH = 256,
    parameter int unsigned PopcountWidth = $clog2(INPUT_WIDTH) + 1
) (
    input logic [INPUT_WIDTH-1:0] data_i,
    output logic [PopcountWidth-1:0] popcount_o
);

    always_comb begin
        popcount_o = 0;
        for (int i = 0; i < INPUT_WIDTH; i++) begin
            popcount_o = popcount_o + data_i[i];
        end
    end

endmodule
