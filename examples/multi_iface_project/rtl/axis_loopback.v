// AXI-Stream combinational loopback — passes m_axis → s_axis without buffering.
//
// Detected interface:
//   m_axis  (axi_stream, slave)  — bench drives tvalid/tdata/tlast; DUT drives tready
//   s_axis  (axi_stream, master) — DUT drives tvalid/tdata/tlast; bench drives tready
//
// The heartbeat register gives the design an always block so the Verilog-tools
// clock/reset detector can find the active-low asynchronous reset without having
// to rely on port-name heuristics.
module axis_loopback (
    input  wire        clk,
    input  wire        rst_n,

    // AXI-Stream slave port (bench → DUT)
    input  wire        m_axis_tvalid,
    output wire        m_axis_tready,
    input  wire [7:0]  m_axis_tdata,
    input  wire        m_axis_tlast,

    // AXI-Stream master port (DUT → bench)
    output wire        s_axis_tvalid,
    input  wire        s_axis_tready,
    output wire [7:0]  s_axis_tdata,
    output wire        s_axis_tlast
);
    // Combinational pass-through — no pipeline latency.
    assign s_axis_tvalid = m_axis_tvalid;
    assign s_axis_tdata  = m_axis_tdata;
    assign s_axis_tlast  = m_axis_tlast;
    assign m_axis_tready = s_axis_tready;

    // Heartbeat counter — sole purpose is to give the parser an always block
    // so that clock/reset detection is unambiguous (active-low async reset).
    reg [7:0] tick;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) tick <= 8'h00;
        else        tick <= tick + 8'd1;
endmodule
