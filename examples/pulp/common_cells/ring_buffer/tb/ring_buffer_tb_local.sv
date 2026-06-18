module ring_buffer_tb_local;

    logic       clk;
    logic       rst_n;
    logic       wvalid_i;
    logic [7:0] wdata_i;
    logic       wready_o;
    logic       rvalid_i;
    logic [1:0] raddr_i;
    logic       rready_o;
    logic [7:0] rdata_o;
    logic       advance_i;
    logic [2:0] step_i;
    logic [1:0] wptr_o;
    logic [1:0] rptr_o;
    logic       full_o;
    logic       empty_o;

    integer errors;

    ring_buffer dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .wvalid_i(wvalid_i),
        .wready_o(wready_o),
        .wdata_i(wdata_i),
        .rvalid_i(rvalid_i),
        .rready_o(rready_o),
        .raddr_i(raddr_i),
        .rdata_o(rdata_o),
        .advance_i(advance_i),
        .step_i(step_i),
        .wptr_o(wptr_o),
        .rptr_o(rptr_o),
        .full_o(full_o),
        .empty_o(empty_o)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        wvalid_i = 1'b0;
        wdata_i = 8'h00;
        rvalid_i = 1'b0;
        raddr_i = 2'b00;
        advance_i = 1'b0;
        step_i = 3'b000;
        errors = 0;

        #10;
        if (wptr_o != 2'b00 || rptr_o != 2'b00 || full_o != 1'b0 || empty_o != 1'b1 || wready_o != 1'b1) begin
            errors = errors + 1;
            $display(
                "FAIL reset state: wptr=%0d rptr=%0d full=%0d empty=%0d wready=%0d",
                wptr_o,
                rptr_o,
                full_o,
                empty_o,
                wready_o
            );
        end

        rst_n = 1'b1;

        wvalid_i = 1'b1;
        wdata_i = 8'h11;
        #10;
        if (wptr_o != 2'b01 || empty_o != 1'b0 || full_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL first write state: wptr=%0d empty=%0d full=%0d", wptr_o, empty_o, full_o);
        end

        wdata_i = 8'h22;
        #10;
        if (wptr_o != 2'b10) begin
            errors = errors + 1;
            $display("FAIL second write pointer: wptr=%0d", wptr_o);
        end

        wdata_i = 8'h33;
        #10;
        if (wptr_o != 2'b11) begin
            errors = errors + 1;
            $display("FAIL third write pointer: wptr=%0d", wptr_o);
        end

        wvalid_i = 1'b0;
        rvalid_i = 1'b1;
        raddr_i = 2'b10;
        #1;
        if (rready_o != 1'b1 || rdata_o != 8'h33) begin
            errors = errors + 1;
            $display("FAIL valid read before advance: rready=%0d rdata=%0h", rready_o, rdata_o);
        end

        raddr_i = 2'b11;
        #1;
        if (rready_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL invalid read before advance: rready=%0d", rready_o);
        end

        rvalid_i = 1'b0;
        advance_i = 1'b1;
        step_i = 3'b001;
        #10;
        advance_i = 1'b0;
        step_i = 3'b000;
        if (rptr_o != 2'b01 || empty_o != 1'b0 || full_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL first advance state: rptr=%0d empty=%0d full=%0d", rptr_o, empty_o, full_o);
        end

        rvalid_i = 1'b1;
        raddr_i = 2'b00;
        #1;
        if (rready_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL invalid read after advance: rready=%0d", rready_o);
        end

        raddr_i = 2'b01;
        #1;
        if (rready_o != 1'b1 || rdata_o != 8'h22) begin
            errors = errors + 1;
            $display("FAIL valid read after advance: rready=%0d rdata=%0h", rready_o, rdata_o);
        end

        rvalid_i = 1'b0;
        wvalid_i = 1'b1;
        wdata_i = 8'h44;
        #10;
        if (wptr_o != 2'b00 || full_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL first wrap write: wptr=%0d full=%0d", wptr_o, full_o);
        end

        wdata_i = 8'h55;
        #10;
        wvalid_i = 1'b0;
        if (wptr_o != 2'b01 || full_o != 1'b1 || wready_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL full state after wrap: wptr=%0d full=%0d wready=%0d", wptr_o, full_o, wready_o);
        end

        rvalid_i = 1'b1;
        raddr_i = 2'b00;
        #1;
        if (rready_o != 1'b1 || rdata_o != 8'h55) begin
            errors = errors + 1;
            $display("FAIL full-state wrapped read 0: rready=%0d rdata=%0h", rready_o, rdata_o);
        end

        raddr_i = 2'b10;
        #1;
        if (rready_o != 1'b1 || rdata_o != 8'h33) begin
            errors = errors + 1;
            $display("FAIL full-state wrapped read 2: rready=%0d rdata=%0h", rready_o, rdata_o);
        end

        rvalid_i = 1'b0;
        advance_i = 1'b1;
        step_i = 3'b010;
        #10;
        advance_i = 1'b0;
        step_i = 3'b000;
        if (rptr_o != 2'b11 || full_o != 1'b0 || empty_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL second advance state: rptr=%0d full=%0d empty=%0d", rptr_o, full_o, empty_o);
        end

        rvalid_i = 1'b1;
        raddr_i = 2'b11;
        #1;
        if (rready_o != 1'b1 || rdata_o != 8'h44) begin
            errors = errors + 1;
            $display("FAIL wrapped range read 3: rready=%0d rdata=%0h", rready_o, rdata_o);
        end

        raddr_i = 2'b00;
        #1;
        if (rready_o != 1'b1 || rdata_o != 8'h55) begin
            errors = errors + 1;
            $display("FAIL wrapped range read 0: rready=%0d rdata=%0h", rready_o, rdata_o);
        end

        raddr_i = 2'b01;
        #1;
        if (rready_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL wrapped invalid read 1: rready=%0d", rready_o);
        end

        rvalid_i = 1'b0;
        advance_i = 1'b1;
        step_i = 3'b010;
        #10;
        advance_i = 1'b0;
        step_i = 3'b000;
        if (rptr_o != 2'b01 || empty_o != 1'b1 || full_o != 1'b0 || wready_o != 1'b1) begin
            errors = errors + 1;
            $display(
                "FAIL final drain state: rptr=%0d empty=%0d full=%0d wready=%0d",
                rptr_o,
                empty_o,
                full_o,
                wready_o
            );
        end

        rvalid_i = 1'b1;
        raddr_i = 2'b00;
        #1;
        if (rready_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL empty read blocked: rready=%0d", rready_o);
        end

        if (errors == 0)
            $display("PASS ring_buffer deterministic checks");
        else
            $display("FAIL ring_buffer deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
