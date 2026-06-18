module plru_tree_tb_local;

    logic       clk;
    logic       rst_n;
    logic [3:0] used_i;
    logic [3:0] plru_o;

    integer errors;

    plru_tree #(
        .ENTRIES(4)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .used_i(used_i),
        .plru_o(plru_o)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        used_i = 4'b0000;
        errors = 0;

        #10;
        if (plru_o != 4'b0001) begin
            errors = errors + 1;
            $display("FAIL reset replacement: plru=%b", plru_o);
        end

        rst_n = 1'b1;

        used_i = 4'b0001;
        #10;
        if (plru_o != 4'b0100) begin
            errors = errors + 1;
            $display("FAIL use entry0 -> entry2: plru=%b", plru_o);
        end

        used_i = 4'b0100;
        #10;
        if (plru_o != 4'b0010) begin
            errors = errors + 1;
            $display("FAIL use entry2 -> entry1: plru=%b", plru_o);
        end

        used_i = 4'b0010;
        #10;
        if (plru_o != 4'b1000) begin
            errors = errors + 1;
            $display("FAIL use entry1 -> entry3: plru=%b", plru_o);
        end

        used_i = 4'b1000;
        #10;
        if (plru_o != 4'b0001) begin
            errors = errors + 1;
            $display("FAIL use entry3 -> entry0: plru=%b", plru_o);
        end

        used_i = 4'b0000;
        #10;
        if (plru_o != 4'b0001) begin
            errors = errors + 1;
            $display("FAIL idle hold: plru=%b", plru_o);
        end

        if (errors == 0)
            $display("PASS plru_tree deterministic checks");
        else
            $display("FAIL plru_tree deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
