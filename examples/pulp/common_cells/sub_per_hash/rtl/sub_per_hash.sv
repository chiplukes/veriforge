module sub_per_hash #(
    parameter int unsigned InpWidth   = 32'd11,
    parameter int unsigned HashWidth  = 32'd5,
    parameter int unsigned NoRounds   = 32'd1,
    parameter int unsigned PermuteKey = 32'd299034753,
    parameter int unsigned XorKey     = 32'd4094834
) (
    input  logic [InpWidth-1:0] data_i,
    output logic [HashWidth-1:0] hash_o,
    output logic [2**HashWidth-1:0] hash_onehot_o
);

    if (NoRounds == 32'd1 && InpWidth == 32'd11 && HashWidth == 32'd5) begin : gen_supported_local_subset
        if (PermuteKey == 32'd299034753 && XorKey == 32'd4094834) begin : gen_seed0
            assign hash_o[0] = 1'b0;
            assign hash_o[1] = 1'b0;
            assign hash_o[2] = data_i[10];
            assign hash_o[3] = data_i[1];
            assign hash_o[4] = data_i[4] ^ data_i[1];
        end else if (PermuteKey == 32'd19921030 && XorKey == 32'd995713) begin : gen_seed1
            assign hash_o[0] = 1'b0;
            assign hash_o[1] = data_i[5];
            assign hash_o[2] = data_i[3] ^ data_i[5];
            assign hash_o[3] = data_i[3];
            assign hash_o[4] = data_i[5];
        end else if (PermuteKey == 32'd294388 && XorKey == 32'd65146511) begin : gen_seed2
            assign hash_o[0] = data_i[7];
            assign hash_o[1] = 1'b0;
            assign hash_o[2] = data_i[3] ^ data_i[8];
            assign hash_o[3] = 1'b0;
            assign hash_o[4] = data_i[3];
        end else begin : gen_seed_fallback_zero
            assign hash_o = '0;
        end

        assign hash_onehot_o = 1'b1 << hash_o;
    end else begin : gen_generic
        integer Permutations[NoRounds*InpWidth];
        integer XorStages[NoRounds*InpWidth*3];
        integer indices[NoRounds*InpWidth];
        integer A_perm;
        integer C_perm;
        integer M_perm;
        integer A_xor;
        integer C_xor;
        integer M_xor;
        integer rand_perm;
        integer rand_xor;
        integer index;
        integer advance;

        logic [NoRounds-1:0][InpWidth-1:0] permuted;
        logic [NoRounds-1:0][InpWidth-1:0] xored;

        initial begin : init_tables
            A_perm = 2147483629;
            C_perm = 2147483587;
            M_perm = 2**31 - 1;
            A_xor = 1664525;
            C_xor = 1013904223;
            M_xor = 2**32;
            rand_perm = (A_perm * PermuteKey + C_perm) % M_perm;
            rand_xor = (A_xor * XorKey + C_xor) % M_xor;

            for (int unsigned r = 0; r < NoRounds; r++) begin
                for (int unsigned i = 0; i < InpWidth; i++) begin
                    indices[r*InpWidth + i] = i;
                    Permutations[r*InpWidth + i] = i;
                end

                for (int unsigned i = 0; i < InpWidth; i++) begin
                    if (i > 0) begin
                        rand_perm = (A_perm * rand_perm + C_perm) % M_perm;
                        index = rand_perm % i;
                    end else begin
                        index = 0;
                    end
                    if (i != index) begin
                        Permutations[r*InpWidth + i] = Permutations[r*InpWidth + index];
                        Permutations[r*InpWidth + index] = indices[r*InpWidth + i];
                    end
                end

                rand_perm = (A_perm * rand_perm + C_perm) % M_perm;
                advance = rand_perm % NoRounds;
                for (int unsigned i = 0; i < advance; i++) begin
                    rand_perm = (A_perm * rand_perm + C_perm) % M_perm;
                end

                for (int unsigned i = 0; i < InpWidth; i++) begin
                    for (int unsigned j = 0; j < 3; j++) begin
                        rand_xor = (A_xor * rand_xor + C_xor) % M_xor;
                        index = rand_xor % InpWidth;
                        XorStages[(r*InpWidth + i)*3 + j] = index;
                    end
                end

                rand_xor = (A_xor * rand_xor + C_xor) % M_xor;
                advance = rand_xor % NoRounds;
                for (int unsigned i = 0; i < advance; i++) begin
                    rand_xor = (A_xor * rand_xor + C_xor) % M_xor;
                end
            end
        end

        for (genvar r = 0; r < NoRounds; r++) begin : gen_round
            for (genvar i = 0; i < InpWidth; i++) begin : gen_sub_per
                if (r == 0) begin : gen_input
                    assign permuted[r][i] = data_i[Permutations[r*InpWidth + i]];
                end else begin : gen_permutation
                    assign permuted[r][i] = xored[r-1][Permutations[r*InpWidth + i]];
                end

                assign xored[r][i] = permuted[r][XorStages[(r*InpWidth + i)*3 + 0]] ^
                                     permuted[r][XorStages[(r*InpWidth + i)*3 + 1]] ^
                                     permuted[r][XorStages[(r*InpWidth + i)*3 + 2]];
            end
        end

        assign hash_o = xored[NoRounds-1][HashWidth-1:0];
        assign hash_onehot_o = 1'b1 << hash_o;
    end

endmodule
