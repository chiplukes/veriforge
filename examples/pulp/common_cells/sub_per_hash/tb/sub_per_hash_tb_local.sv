module sub_per_hash_tb_local;

    localparam int unsigned DataWidth = 32'd11;
    localparam int unsigned HashWidth = 32'd5;
    localparam int unsigned NoRounds = 32'd1;

    logic [DataWidth-1:0] data;

    logic [HashWidth-1:0] hash0;
    logic [HashWidth-1:0] hash1;
    logic [HashWidth-1:0] hash2;

    logic [2**HashWidth-1:0] onehot0;
    logic [2**HashWidth-1:0] onehot1;
    logic [2**HashWidth-1:0] onehot2;

    integer errors;

    sub_per_hash #(
        .InpWidth(DataWidth),
        .HashWidth(HashWidth),
        .NoRounds(NoRounds),
        .PermuteKey(32'd299034753),
        .XorKey(32'd4094834)
    ) dut0 (
        .data_i(data),
        .hash_o(hash0),
        .hash_onehot_o(onehot0)
    );

    sub_per_hash #(
        .InpWidth(DataWidth),
        .HashWidth(HashWidth),
        .NoRounds(NoRounds),
        .PermuteKey(32'd19921030),
        .XorKey(32'd995713)
    ) dut1 (
        .data_i(data),
        .hash_o(hash1),
        .hash_onehot_o(onehot1)
    );

    sub_per_hash #(
        .InpWidth(DataWidth),
        .HashWidth(HashWidth),
        .NoRounds(NoRounds),
        .PermuteKey(32'd294388),
        .XorKey(32'd65146511)
    ) dut2 (
        .data_i(data),
        .hash_o(hash2),
        .hash_onehot_o(onehot2)
    );

    initial begin
        errors = 0;

        data = 11'd0;
        #1;
        if (hash0 != 5'd0 || onehot0 != 32'h00000001) begin
            errors = errors + 1;
            $display("FAIL seed0 data0: hash=%0d onehot=%h", hash0, onehot0);
        end
        if (hash1 != 5'd0 || onehot1 != 32'h00000001) begin
            errors = errors + 1;
            $display("FAIL seed1 data0: hash=%0d onehot=%h", hash1, onehot1);
        end
        if (hash2 != 5'd0 || onehot2 != 32'h00000001) begin
            errors = errors + 1;
            $display("FAIL seed2 data0: hash=%0d onehot=%h", hash2, onehot2);
        end

        data = 11'd1;
        #1;
        if (hash0 != 5'd0 || onehot0 != 32'h00000001) begin
            errors = errors + 1;
            $display("FAIL seed0 data1: hash=%0d onehot=%h", hash0, onehot0);
        end
        if (hash1 != 5'd0 || onehot1 != 32'h00000001) begin
            errors = errors + 1;
            $display("FAIL seed1 data1: hash=%0d onehot=%h", hash1, onehot1);
        end
        if (hash2 != 5'd0 || onehot2 != 32'h00000001) begin
            errors = errors + 1;
            $display("FAIL seed2 data1: hash=%0d onehot=%h", hash2, onehot2);
        end

        data = 11'd341;
        #1;
        if (hash0 != 5'd16 || onehot0 != 32'h00010000) begin
            errors = errors + 1;
            $display("FAIL seed0 data341: hash=%0d onehot=%h", hash0, onehot0);
        end
        if (hash1 != 5'd0 || onehot1 != 32'h00000001) begin
            errors = errors + 1;
            $display("FAIL seed1 data341: hash=%0d onehot=%h", hash1, onehot1);
        end
        if (hash2 != 5'd4 || onehot2 != 32'h00000010) begin
            errors = errors + 1;
            $display("FAIL seed2 data341: hash=%0d onehot=%h", hash2, onehot2);
        end

        data = 11'd1023;
        #1;
        if (hash0 != 5'd8 || onehot0 != 32'h00000100) begin
            errors = errors + 1;
            $display("FAIL seed0 data1023: hash=%0d onehot=%h", hash0, onehot0);
        end
        if (hash1 != 5'd26 || onehot1 != 32'h04000000) begin
            errors = errors + 1;
            $display("FAIL seed1 data1023: hash=%0d onehot=%h", hash1, onehot1);
        end
        if (hash2 != 5'd17 || onehot2 != 32'h00020000) begin
            errors = errors + 1;
            $display("FAIL seed2 data1023: hash=%0d onehot=%h", hash2, onehot2);
        end

        if (errors == 0)
            $display("PASS sub_per_hash deterministic checks");
        else
            $display("FAIL sub_per_hash deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
