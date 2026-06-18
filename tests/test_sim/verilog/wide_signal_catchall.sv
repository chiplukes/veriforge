module wide_signal_catchall (
    input  logic         clk,
    input  logic         sel,
    input  logic         mem_sel,
    input  logic [7:0]   plus_base,
    input  logic [7:0]   minus_base,
    input  logic [7:0]   bit_index,
    input  logic [3:0]   shamt,
    input  logic [15:0]  narrow_a,
    input  logic [15:0]  narrow_b,
    input  logic [129:0] wide_a,
    input  logic [129:0] wide_b,
    input  logic [129:0] wide_mask,
    output logic [15:0]  narrow_logic,
    output logic [15:0]  narrow_concat,
    output logic [31:0]  narrow_repl,
    output logic         narrow_eq,
    output logic [129:0] wide_mux,
    output logic [64:0]  wide_slice_mux,
    output logic [129:0] wide_concat_mix,
    output logic [129:0] wide_replication,
    output logic [129:0] wide_mask_merge,
    output logic [129:0] wide_add,
    output logic [129:0] wide_sub,
    output logic [129:0] wide_shl,
    output logic [129:0] wide_shr,
    output logic [129:0] wide_ashr,
    output logic [129:0] wide_tree_mix,
    output logic         wide_tree_truth,
    output logic [64:0]  wide_plus_slice,
    output logic [64:0]  wide_minus_slice,
    output logic         wide_selected_bit,
    output logic         wide_eq,
    output logic         wide_neq,
    output logic         wide_any,
    output logic         wide_all,
    output logic         wide_parity,
    output logic [129:0] struct_mix,
    output logic [129:0] mem_word,
    output logic [64:0]  mem_slice,
    output logic [129:0] seq_stage,
    output logic [129:0] seq_mem_word
);
    typedef struct packed {
        logic [127:0] data;
        logic         valid;
        logic         last;
    } wide_bus_t;

    wide_bus_t pkt_a;
    wide_bus_t pkt_b;
    wide_bus_t mem[0:1];
    logic signed [129:0] wide_b_signed;

    assign narrow_logic = (narrow_a & narrow_b) ^ (narrow_a | 16'h00F0);
    assign narrow_concat = {narrow_a[15:8], narrow_b[7:0]};
    assign narrow_repl = {2{narrow_a[7:0], narrow_b[7:0]}};
    assign narrow_eq = (narrow_a == narrow_b);

    assign wide_mux = sel ? wide_a : wide_b;
    assign wide_slice_mux = sel ? wide_a[128:64] : wide_b[128:64];
    assign wide_concat_mix = {wide_a[129:65], wide_b[64:0]};
    assign wide_replication = {65{sel, ~sel}};
    assign wide_eq = (wide_a[128:64] == wide_b[128:64]);
    assign wide_neq = (wide_a[64:0] != wide_b[64:0]);
    assign wide_any = |wide_a[128:64];
    assign wide_all = &wide_b[64:0];
    assign wide_parity = ^wide_a[64:0];

    always_comb begin
        pkt_a.data = wide_a[129:2];
        pkt_a.valid = wide_a[1];
        pkt_a.last = wide_a[0];

        pkt_b.data = wide_b[129:2];
        pkt_b.valid = wide_b[1];
        pkt_b.last = wide_b[0];

        wide_mask_merge = (wide_a & wide_mask) | (wide_b & ~wide_mask);
        wide_add = wide_a + wide_b;
        wide_sub = wide_a - wide_b;
        wide_shl = wide_a << shamt;
        wide_shr = wide_b >> shamt;
        wide_b_signed = wide_b;
        wide_ashr = $signed(wide_b_signed) >>> shamt;
        wide_plus_slice = wide_a[plus_base +: 65];
        wide_minus_slice = wide_b[minus_base -: 65];
        wide_selected_bit = wide_a[bit_index];
        wide_tree_mix = ((sel ? wide_a : wide_b) ^ wide_mask) + wide_concat_mix;
        wide_tree_truth = (((sel ? wide_a : wide_b) & wide_mask) != 130'b0);
        struct_mix = {pkt_a.data[127:64], mem[mem_sel].data[63:0], pkt_a.valid, mem[mem_sel].last};
        mem_word = {mem[mem_sel].data, mem[mem_sel].valid, mem[mem_sel].last};
        mem_slice = mem[mem_sel].data[plus_base +: 65];
    end

    initial begin
        mem[0].data = 128'h112233445566778899AABBCCDDEEFF00;
        mem[0].valid = 1'b1;
        mem[0].last = 1'b0;
        mem[1].data = 128'hFFEEDDCCBBAA99887766554433221100;
        mem[1].valid = 1'b0;
        mem[1].last = 1'b1;
        seq_stage = 130'b0;
        seq_mem_word = 130'b0;
    end

    always @(posedge clk) begin
        seq_stage <= sel ? wide_mask_merge : wide_concat_mix;
        mem[mem_sel].data[127:64] <= wide_mux[129:66];
        mem[mem_sel].data[63:0] <= wide_plus_slice[63:0];
        mem[mem_sel].valid <= sel;
        mem[mem_sel].last <= wide_selected_bit;
        seq_mem_word <= {mem[mem_sel].data, mem[mem_sel].valid, mem[mem_sel].last};
    end
endmodule
