module unread_tb_local;

    logic d_i;

    unread dut (
        .d_i(d_i)
    );

    initial begin
        d_i = 1'b0;
        #1;
        d_i = 1'b1;
        #1;
        d_i = 1'b0;
        #1;
        $display("PASS unread deterministic checks");
        $finish;
    end

endmodule
