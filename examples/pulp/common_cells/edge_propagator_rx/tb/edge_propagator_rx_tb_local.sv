module edge_propagator_rx_tb_local;

    logic clk;
    logic rstn;
    logic valid_i;
    logic ack_o;
    logic valid_o;

    integer errors;
    integer valid_count;
    integer ack_count;

    edge_propagator_rx dut (
        .clk_i(clk),
        .rstn_i(rstn),
        .valid_i(valid_i),
        .ack_o(ack_o),
        .valid_o(valid_o)
    );

    always #5 clk = ~clk;

    always @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            valid_count <= 0;
            ack_count <= 0;
        end else begin
            if (valid_o) begin
                valid_count <= valid_count + 1;
            end
            if (ack_o) begin
                ack_count <= ack_count + 1;
            end
        end
    end

    initial begin
        clk = 1'b0;
        rstn = 1'b0;
        valid_i = 1'b0;
        errors = 0;

        #20;
        if (ack_o != 1'b0 || valid_o != 1'b0 || valid_count != 0 || ack_count != 0) begin
            errors = errors + 1;
            $display(
                "FAIL reset idle: ack=%0d valid=%0d valid_count=%0d ack_count=%0d",
                ack_o,
                valid_o,
                valid_count,
                ack_count
            );
        end

        rstn = 1'b1;
        #20;
        if (valid_count != 0 || ack_count != 0) begin
            errors = errors + 1;
            $display("FAIL idle before first request: valid_count=%0d ack_count=%0d", valid_count, ack_count);
        end

        valid_i = 1'b1;
        #10;
        valid_i = 1'b0;

        #40;
        if (valid_count != 1 || ack_count != 1 || ack_o != 1'b0 || valid_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL first request sequence: valid_count=%0d ack_count=%0d ack=%0d valid=%0d",
                valid_count,
                ack_count,
                ack_o,
                valid_o
            );
        end

        #20;
        if (ack_o != 1'b0 || valid_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL idle after first request: ack=%0d valid=%0d", ack_o, valid_o);
        end

        valid_i = 1'b1;
        #10;
        valid_i = 1'b0;

        #40;
        if (valid_count != 2 || ack_count != 2 || ack_o != 1'b0 || valid_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL second request sequence: valid_count=%0d ack_count=%0d ack=%0d valid=%0d",
                valid_count,
                ack_count,
                ack_o,
                valid_o
            );
        end

        if (errors == 0)
            $display("PASS edge_propagator_rx deterministic checks");
        else
            $display("FAIL edge_propagator_rx deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
