// Multi-interface top-level — structural wrapper exercising three distinct
// AXI/AXI-Stream protocol variants on a single shared clock domain.
//
// Sub-modules
// -----------
//   u_loopback  axis_loopback  — combinational AXI-Stream pass-through
//   u_regfile   axil_regfile   — 4 × 32-bit AXI-Lite slave register file
//   u_ram       axi4_ram       — 8 × 32-bit AXI4 single-beat slave RAM
//
// Port-prefix → protocol detection (verilog-tools auto-detect)
// -------------------------------------------------------------
//   m_axis_*   AXI-Stream slave  (bench source → DUT)   — no awlen  →  stream
//   s_axis_*   AXI-Stream master (DUT source  → bench)  — no awlen  →  stream
//   axil_*     AXI-Lite slave    (bench master → DUT)   — no awlen  →  axi_lite
//   ram_*      AXI4 slave        (bench master → DUT)   — has awlen →  axi4
//
// Clock / reset
// -------------
//   clk    — shared positive-edge clock
//   rst_n  — shared active-low asynchronous reset
//
// The top module is purely structural (no always blocks).  Clock/reset
// detection relies on verilog-tools extract_clocks_resets_hier(), which
// promotes the clock/reset found in sub-module always blocks up through
// the port-connection map.  The fallback name heuristic (clk / rst_n) also
// resolves these signals correctly.
module multi_iface_top (
    input  wire        clk,
    input  wire        rst_n,

    // -------------------------------------------------------------------
    // AXI-Stream slave port  (bench → DUT; prefix: m_axis)
    // -------------------------------------------------------------------
    input  wire        m_axis_tvalid,
    output wire        m_axis_tready,
    input  wire [7:0]  m_axis_tdata,
    input  wire        m_axis_tlast,

    // -------------------------------------------------------------------
    // AXI-Stream master port (DUT → bench; prefix: s_axis)
    // -------------------------------------------------------------------
    output wire        s_axis_tvalid,
    input  wire        s_axis_tready,
    output wire [7:0]  s_axis_tdata,
    output wire        s_axis_tlast,

    // -------------------------------------------------------------------
    // AXI-Lite slave port (bench master → DUT slave; prefix: axil)
    // axil_awlen is absent → auto-detected as AXI-Lite (not AXI4)
    // -------------------------------------------------------------------
    input  wire [3:0]  axil_awaddr,
    input  wire [2:0]  axil_awprot,
    input  wire        axil_awvalid,
    output wire        axil_awready,

    input  wire [31:0] axil_wdata,
    input  wire [3:0]  axil_wstrb,
    input  wire        axil_wvalid,
    output wire        axil_wready,

    output wire [1:0]  axil_bresp,
    output wire        axil_bvalid,
    input  wire        axil_bready,

    input  wire [3:0]  axil_araddr,
    input  wire [2:0]  axil_arprot,
    input  wire        axil_arvalid,
    output wire        axil_arready,

    output wire [31:0] axil_rdata,
    output wire [1:0]  axil_rresp,
    output wire        axil_rvalid,
    input  wire        axil_rready,

    // -------------------------------------------------------------------
    // AXI4 slave port (bench master → DUT slave; prefix: ram)
    // ram_awlen is present → auto-detected as AXI4 (not AXI-Lite)
    // -------------------------------------------------------------------
    input  wire [3:0]  ram_awid,
    input  wire [4:0]  ram_awaddr,
    input  wire [7:0]  ram_awlen,
    input  wire [2:0]  ram_awsize,
    input  wire [1:0]  ram_awburst,
    input  wire        ram_awvalid,
    output wire        ram_awready,

    input  wire [31:0] ram_wdata,
    input  wire [3:0]  ram_wstrb,
    input  wire        ram_wlast,
    input  wire        ram_wvalid,
    output wire        ram_wready,

    output wire [3:0]  ram_bid,
    output wire [1:0]  ram_bresp,
    output wire        ram_bvalid,
    input  wire        ram_bready,

    input  wire [3:0]  ram_arid,
    input  wire [4:0]  ram_araddr,
    input  wire [7:0]  ram_arlen,
    input  wire [2:0]  ram_arsize,
    input  wire [1:0]  ram_arburst,
    input  wire        ram_arvalid,
    output wire        ram_arready,

    output wire [3:0]  ram_rid,
    output wire [31:0] ram_rdata,
    output wire [1:0]  ram_rresp,
    output wire        ram_rlast,
    output wire        ram_rvalid,
    input  wire        ram_rready
);

    // -------------------------------------------------------------------
    // u_loopback — AXI-Stream combinational pass-through
    // -------------------------------------------------------------------
    axis_loopback u_loopback (
        .clk           (clk),
        .rst_n         (rst_n),
        .m_axis_tvalid (m_axis_tvalid),
        .m_axis_tready (m_axis_tready),
        .m_axis_tdata  (m_axis_tdata),
        .m_axis_tlast  (m_axis_tlast),
        .s_axis_tvalid (s_axis_tvalid),
        .s_axis_tready (s_axis_tready),
        .s_axis_tdata  (s_axis_tdata),
        .s_axis_tlast  (s_axis_tlast)
    );

    // -------------------------------------------------------------------
    // u_regfile — AXI-Lite 4-register slave
    // Top-level axil_* ports map to submodule s_axi_* ports.
    // -------------------------------------------------------------------
    axil_regfile u_regfile (
        .clk           (clk),
        .rst_n         (rst_n),
        .s_axi_awaddr  (axil_awaddr),
        .s_axi_awprot  (axil_awprot),
        .s_axi_awvalid (axil_awvalid),
        .s_axi_awready (axil_awready),
        .s_axi_wdata   (axil_wdata),
        .s_axi_wstrb   (axil_wstrb),
        .s_axi_wvalid  (axil_wvalid),
        .s_axi_wready  (axil_wready),
        .s_axi_bresp   (axil_bresp),
        .s_axi_bvalid  (axil_bvalid),
        .s_axi_bready  (axil_bready),
        .s_axi_araddr  (axil_araddr),
        .s_axi_arprot  (axil_arprot),
        .s_axi_arvalid (axil_arvalid),
        .s_axi_arready (axil_arready),
        .s_axi_rdata   (axil_rdata),
        .s_axi_rresp   (axil_rresp),
        .s_axi_rvalid  (axil_rvalid),
        .s_axi_rready  (axil_rready)
    );

    // -------------------------------------------------------------------
    // u_ram — AXI4 single-beat 8-word slave RAM
    // Top-level ram_* ports map to submodule s_axi_* ports.
    // -------------------------------------------------------------------
    axi4_ram u_ram (
        .clk           (clk),
        .rst_n         (rst_n),
        .s_axi_awid    (ram_awid),
        .s_axi_awaddr  (ram_awaddr),
        .s_axi_awlen   (ram_awlen),
        .s_axi_awsize  (ram_awsize),
        .s_axi_awburst (ram_awburst),
        .s_axi_awvalid (ram_awvalid),
        .s_axi_awready (ram_awready),
        .s_axi_wdata   (ram_wdata),
        .s_axi_wstrb   (ram_wstrb),
        .s_axi_wlast   (ram_wlast),
        .s_axi_wvalid  (ram_wvalid),
        .s_axi_wready  (ram_wready),
        .s_axi_bid     (ram_bid),
        .s_axi_bresp   (ram_bresp),
        .s_axi_bvalid  (ram_bvalid),
        .s_axi_bready  (ram_bready),
        .s_axi_arid    (ram_arid),
        .s_axi_araddr  (ram_araddr),
        .s_axi_arlen   (ram_arlen),
        .s_axi_arsize  (ram_arsize),
        .s_axi_arburst (ram_arburst),
        .s_axi_arvalid (ram_arvalid),
        .s_axi_arready (ram_arready),
        .s_axi_rid     (ram_rid),
        .s_axi_rdata   (ram_rdata),
        .s_axi_rresp   (ram_rresp),
        .s_axi_rlast   (ram_rlast),
        .s_axi_rvalid  (ram_rvalid),
        .s_axi_rready  (ram_rready)
    );

endmodule
