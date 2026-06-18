module lossy_valid_to_stream #(
    parameter int DATA_WIDTH = 8
) (
    input  logic                  clk_i,
    input  logic                  rst_ni,
    input  logic                  valid_i,
    input  logic [DATA_WIDTH-1:0] data_i,
    output logic                  valid_o,
    input  logic                  ready_i,
    output logic [DATA_WIDTH-1:0] data_o,
    output logic                  busy_o
);

    logic read_ptr_d;
    logic read_ptr_q;
    logic write_ptr_d;
    logic write_ptr_q;
    logic [1:0] pending_tx_counter_d;
    logic [1:0] pending_tx_counter_q;
    logic [DATA_WIDTH-1:0] mem_d [1:0];
    logic [DATA_WIDTH-1:0] mem_q [1:0];

    assign valid_o = (pending_tx_counter_q != 0) || valid_i;

    always @(*) begin : write_logic
        write_ptr_d = write_ptr_q;
        mem_d = mem_q;
        if (valid_i) begin
            if ((pending_tx_counter_q != 0) || !ready_i) begin
                if ((pending_tx_counter_q == 2) && !ready_i) begin
                    mem_d[write_ptr_q - 1'b1] = data_i;
                end else begin
                    mem_d[write_ptr_q] = data_i;
                    write_ptr_d = write_ptr_q + 1'b1;
                end
            end
        end
    end

    always @(*) begin : read_logic
        read_ptr_d = read_ptr_q;
        data_o = mem_q[read_ptr_q];
        if ((pending_tx_counter_q == 0) && valid_i) begin
            data_o = data_i;
        end else if (valid_o && ready_i) begin
            read_ptr_d = read_ptr_q + 1'b1;
        end
    end

    always @(*) begin : count_transactions
        pending_tx_counter_d = pending_tx_counter_q;
        if (valid_i && valid_o && ready_i) begin
            pending_tx_counter_d = pending_tx_counter_q;
        end else if (valid_i && !(valid_o && ready_i)) begin
            if (pending_tx_counter_q != 2) begin
                pending_tx_counter_d = pending_tx_counter_q + 1'b1;
            end
        end else if (!valid_i && (valid_o && ready_i)) begin
            pending_tx_counter_d = pending_tx_counter_q - 1'b1;
        end
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            read_ptr_q <= '0;
            write_ptr_q <= '0;
            pending_tx_counter_q <= '0;
            mem_q[0] <= '0;
            mem_q[1] <= '0;
        end else begin
            read_ptr_q <= read_ptr_d;
            write_ptr_q <= write_ptr_d;
            pending_tx_counter_q <= pending_tx_counter_d;
            mem_q[0] <= mem_d[0];
            mem_q[1] <= mem_d[1];
        end
    end

    assign busy_o = (pending_tx_counter_q != 0);

endmodule
