module trip_counter_tb_local;

    logic       clk;
    logic       rst_n;
    logic       en_i;
    logic [3:0] delta_i;
    logic [3:0] bound_i;
    logic [3:0] q_o;
    logic       last_o;
    logic       trip_o;

    integer errors;

    trip_counter #(
        .WIDTH(4)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .en_i(en_i),
        .delta_i(delta_i),
        .bound_i(bound_i),
        .q_o(q_o),
        .last_o(last_o),
        .trip_o(trip_o)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        en_i = 1'b0;
        delta_i = 4'd1;
        bound_i = 4'd3;
        errors = 0;

        #1;
        if (q_o != 4'd0 || last_o != 1'b0 || trip_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL reset state: q=%0d last=%0d trip=%0d", q_o, last_o, trip_o);
        end

        #9;
        rst_n = 1'b1;

        en_i = 1'b1;
        #10;
        if (q_o != 4'd1 || last_o != 1'b0 || trip_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL step to 1: q=%0d last=%0d trip=%0d", q_o, last_o, trip_o);
        end
        #10;
        if (q_o != 4'd2 || last_o != 1'b0 || trip_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL step to 2: q=%0d last=%0d trip=%0d", q_o, last_o, trip_o);
        end
        #10;
        if (q_o != 4'd3 || last_o != 1'b1 || trip_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL bound hit: q=%0d last=%0d trip=%0d", q_o, last_o, trip_o);
        end
        #10;
        if (q_o != 4'd0 || last_o != 1'b0 || trip_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL auto reset after trip: q=%0d last=%0d trip=%0d", q_o, last_o, trip_o);
        end

        en_i = 1'b0;
        delta_i = 4'd2;
        bound_i = 4'd4;
        #10;
        if (q_o != 4'd0 || last_o != 1'b0 || trip_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL idle hold before second run: q=%0d last=%0d trip=%0d", q_o, last_o, trip_o);
        end

        en_i = 1'b1;
        #10;
        if (q_o != 4'd2 || last_o != 1'b0 || trip_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL step to 2 with delta2: q=%0d last=%0d trip=%0d", q_o, last_o, trip_o);
        end
        #10;
        if (q_o != 4'd4 || last_o != 1'b1 || trip_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL bound 4 hit: q=%0d last=%0d trip=%0d", q_o, last_o, trip_o);
        end
        #10;
        if (q_o != 4'd0 || last_o != 1'b0 || trip_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL auto reset after second trip: q=%0d last=%0d trip=%0d", q_o, last_o, trip_o);
        end

        if (errors == 0)
            $display("PASS trip_counter deterministic checks");
        else
            $display("FAIL trip_counter deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
