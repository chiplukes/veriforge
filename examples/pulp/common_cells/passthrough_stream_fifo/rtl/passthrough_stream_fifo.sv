module passthrough_stream_fifo #(
    parameter bit SAME_CYCLE_RW = 1'b1
) (
    input  logic       clk_i,
    input  logic       rst_ni,
    input  logic       flush_i,
    input  logic       testmode_i,
    input  logic [7:0] data_i,
    input  logic       valid_i,
    output logic       ready_o,
    output logic [7:0] data_o,
    output logic       valid_o,
    input  logic       ready_i
);

    localparam int unsigned DEPTH = 3;
    localparam int unsigned POINTER_WIDTH = 3;

    logic [POINTER_WIDTH-1:0] read_ptr_d;
    logic [POINTER_WIDTH-1:0] read_ptr_q;
    logic [POINTER_WIDTH-1:0] write_ptr_d;
    logic [POINTER_WIDTH-1:0] write_ptr_q;
    logic [7:0] data_d [2:0];
    logic [7:0] data_q [2:0];
    logic load_data;

    assign data_o = data_q[read_ptr_q[POINTER_WIDTH-2:0]];

    always @(*) begin
        load_data = 1'b0;
        read_ptr_d = read_ptr_q;
        write_ptr_d = write_ptr_q;
        data_d = data_q;

        if (flush_i) begin
            read_ptr_d = '0;
            write_ptr_d = '0;
            valid_o = 1'b0;
            ready_o = 1'b0;
        end else begin
            valid_o = (read_ptr_q[POINTER_WIDTH-1] == write_ptr_q[POINTER_WIDTH-1]) ?
                (read_ptr_q[POINTER_WIDTH-2:0] != write_ptr_q[POINTER_WIDTH-2:0]) : 1'b1;

            if (ready_i && valid_o) begin
                if (read_ptr_q[POINTER_WIDTH-2:0] == (DEPTH - 1)) begin
                    read_ptr_d[POINTER_WIDTH-2:0] = '0;
                    read_ptr_d[POINTER_WIDTH-1] = !read_ptr_q[POINTER_WIDTH-1];
                end else begin
                    read_ptr_d = read_ptr_q + 1'b1;
                end
            end

            ready_o = (
                (read_ptr_q[POINTER_WIDTH-1] == write_ptr_q[POINTER_WIDTH-1]) ?
                    1'b1 : (write_ptr_q[POINTER_WIDTH-2:0] != read_ptr_q[POINTER_WIDTH-2:0])
            ) || (SAME_CYCLE_RW && ready_i && valid_o);

            if (valid_i && ready_o) begin
                load_data = 1'b1;
                data_d[write_ptr_q[POINTER_WIDTH-2:0]] = data_i;

                if (write_ptr_q[POINTER_WIDTH-2:0] == (DEPTH - 1)) begin
                    write_ptr_d[POINTER_WIDTH-2:0] = '0;
                    write_ptr_d[POINTER_WIDTH-1] = !write_ptr_q[POINTER_WIDTH-1];
                end else begin
                    write_ptr_d = write_ptr_q + 1'b1;
                end
            end
        end
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            read_ptr_q <= '0;
            write_ptr_q <= '0;
            data_q[0] <= '0;
            data_q[1] <= '0;
            data_q[2] <= '0;
        end else begin
            read_ptr_q <= read_ptr_d;
            write_ptr_q <= write_ptr_d;
            if (load_data) begin
                data_q[0] <= data_d[0];
                data_q[1] <= data_d[1];
                data_q[2] <= data_d[2];
            end
        end
    end

endmodule
