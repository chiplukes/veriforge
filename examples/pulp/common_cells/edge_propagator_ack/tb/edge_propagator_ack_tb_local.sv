module edge_propagator_ack_tb_local;

    logic clk_tx;
    logic clk_rx;
    logic rstn_tx;
    logic rstn_rx;
    logic edge_i;
    logic ack_tx_o;
    logic edge_o;

    integer errors;
    integer edge_count;
    integer ack_count;

    edge_propagator_ack dut (
        .clk_tx_i(clk_tx),
        .rstn_tx_i(rstn_tx),
        .edge_i(edge_i),
        .ack_tx_o(ack_tx_o),
        .clk_rx_i(clk_rx),
        .rstn_rx_i(rstn_rx),
        .edge_o(edge_o)
    );

    always #5 clk_tx = ~clk_tx;
    always #6 clk_rx = ~clk_rx;

    always @(posedge clk_rx or negedge rstn_rx) begin
        if (!rstn_rx) begin
            edge_count <= 0;
        end else if (edge_o) begin
            edge_count <= edge_count + 1;
        end
    end

    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx) begin
            ack_count <= 0;
        end else if (ack_tx_o) begin
            ack_count <= ack_count + 1;
        end
    end

    initial begin
        clk_tx = 1'b0;
        clk_rx = 1'b0;
        rstn_tx = 1'b0;
        rstn_rx = 1'b0;
        edge_i = 1'b0;
        errors = 0;

        #24;
        if (ack_tx_o != 1'b0 || edge_o != 1'b0 || edge_count != 0 || ack_count != 0) begin
            errors = errors + 1;
            $display(
                "FAIL reset idle: ack=%0d edge=%0d edge_count=%0d ack_count=%0d",
                ack_tx_o,
                edge_o,
                edge_count,
                ack_count
            );
        end

        rstn_tx = 1'b1;
        rstn_rx = 1'b1;

        #40;
        if (edge_count != 0 || ack_count != 0) begin
            errors = errors + 1;
            $display("FAIL idle before first edge: edge_count=%0d ack_count=%0d", edge_count, ack_count);
        end

        edge_i = 1'b1;
        #10;
        edge_i = 1'b0;

        #150;
        if (edge_count != 1 || ack_count == 0 || ack_tx_o != 1'b0 || edge_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL first round trip: edge_count=%0d ack_count=%0d ack=%0d edge=%0d",
                edge_count,
                ack_count,
                ack_tx_o,
                edge_o
            );
        end

        edge_i = 1'b1;
        #10;
        edge_i = 1'b0;

        #150;
        if (edge_count != 2 || ack_count < 2 || ack_tx_o != 1'b0 || edge_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL second round trip: edge_count=%0d ack_count=%0d ack=%0d edge=%0d",
                edge_count,
                ack_count,
                ack_tx_o,
                edge_o
            );
        end

        if (errors == 0)
            $display("PASS edge_propagator_ack deterministic checks");
        else
            $display("FAIL edge_propagator_ack deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
