module rr_arb_tree_tb_vm_local;

    localparam int unsigned NumIn = 32'd4;
    localparam int unsigned DataWidth = 32'd8;
    localparam int unsigned IdxWidth = 32'd2;

    logic clk;
    logic rst_n;
    logic flush;
    logic [IdxWidth-1:0] rr;
    logic [NumIn-1:0] req;
    logic [NumIn-1:0] gnt;
    logic [NumIn*DataWidth-1:0] data_bus;
    logic req_oup;
    logic gnt_oup;
    logic [DataWidth-1:0] data_oup;
    logic [IdxWidth-1:0] idx_oup;

    rr_arb_tree #(
        .NumIn(NumIn),
        .DataWidth(DataWidth),
        .ExtPrio(1'b0),
        .AxiVldRdy(1'b1),
        .LockIn(1'b1),
        .FairArb(1'b1)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .rr_i(rr),
        .req_i(req),
        .gnt_o(gnt),
        .data_i(data_bus),
        .req_o(req_oup),
        .gnt_i(gnt_oup),
        .data_o(data_oup),
        .idx_o(idx_oup)
    );

endmodule
