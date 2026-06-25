// axis_skid_buf.v -- AXI-Stream register slice (pipeline register / skid buffer).
//
// Adds exactly one cycle of registered pipeline latency between s_axis and m_axis.
// s_axis_tready is driven combinatorially so the source sees zero-latency backpressure:
//   s_axis_tready = !m_axis_tvalid | m_axis_tready
// The output register is held when downstream stalls (m_axis_tready = 0, m_axis_tvalid = 1).
//
// This is a common first DUT for AXI-Stream testbenches because it has both a slave
// and a master port, is simple enough to reason about by inspection, and requires
// back-pressure testing to verify tready propagation.

module axis_skid_buf #(
    parameter DATA_W = 8
) (
    input  wire              clk,
    input  wire              rst,

    // Slave (input) port
    input  wire              s_axis_tvalid,
    output wire              s_axis_tready,
    input  wire [DATA_W-1:0] s_axis_tdata,
    input  wire              s_axis_tlast,

    // Master (output) port
    output reg               m_axis_tvalid,
    input  wire              m_axis_tready,
    output reg  [DATA_W-1:0] m_axis_tdata,
    output reg               m_axis_tlast
);
    // Accept input when the output register is free or the downstream is consuming this cycle.
    assign s_axis_tready = !m_axis_tvalid || m_axis_tready;

    always @(posedge clk) begin
        if (rst) begin
            m_axis_tvalid <= 1'b0;
            m_axis_tdata  <= {DATA_W{1'b0}};
            m_axis_tlast  <= 1'b0;
        end else if (!m_axis_tvalid || m_axis_tready) begin
            // Output slot is free (or being consumed): latch whatever the source is driving.
            m_axis_tvalid <= s_axis_tvalid;
            m_axis_tdata  <= s_axis_tdata;
            m_axis_tlast  <= s_axis_tlast;
        end
        // else: output full and not consumed — hold the output register unchanged.
    end
endmodule
