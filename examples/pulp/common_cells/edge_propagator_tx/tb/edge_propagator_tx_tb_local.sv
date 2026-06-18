module edge_propagator_tx_tb_local;

    logic clk;
    logic rstn;
    logic valid_i;
    logic ack_i;
    logic valid_o;

    integer errors;

    edge_propagator_tx dut (
        .clk_i(clk),
        .rstn_i(rstn),
        .valid_i(valid_i),
        .ack_i(ack_i),
        .valid_o(valid_o)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rstn = 1'b0;
        valid_i = 1'b0;
        ack_i = 1'b0;
        errors = 0;

        #20;
        if (valid_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL reset idle: valid_o=%0d", valid_o);
        end

        rstn = 1'b1;
        #20;
        if (valid_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL idle after reset: valid_o=%0d", valid_o);
        end

        valid_i = 1'b1;
        #10;
        valid_i = 1'b0;
        #10;
        if (valid_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL first request latch: valid_o=%0d", valid_o);
        end

        #20;
        if (valid_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL hold before ack: valid_o=%0d", valid_o);
        end

        ack_i = 1'b1;
        #10;
        ack_i = 1'b0;
        #10;
        if (valid_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL ack pipeline latency: valid_o=%0d", valid_o);
        end

        #20;
        if (valid_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL clear after ack: valid_o=%0d", valid_o);
        end

        valid_i = 1'b1;
        #10;
        valid_i = 1'b0;
        #10;
        if (valid_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL second request latch: valid_o=%0d", valid_o);
        end

        ack_i = 1'b1;
        #10;
        ack_i = 1'b0;
        #30;
        if (valid_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL second clear after ack: valid_o=%0d", valid_o);
        end

        if (errors == 0)
            $display("PASS edge_propagator_tx deterministic checks");
        else
            $display("FAIL edge_propagator_tx deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
