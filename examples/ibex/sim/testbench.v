// Ibex RISC-V core testbench.
// Wraps ibex_core with memory, register file, and bus logic.
// Loads firmware from firmware.hex, runs until halt.
//
// Memory map (byte addressed):
//   0x0000_0000 - 0x0000_3FFF  RAM (16 KB = 4096 words)
//
// Boot address: 0x00000000
// The core fetches its first instruction from boot_addr + 0x80 = 0x80.

`include "prim_assert.sv"
`include "dv_fcov_macros.svh"

module testbench;

	// ── Clock and reset ─────────────────────────────────────────────
	reg clk = 1;
	reg rst_n = 0;

	always #5 clk = ~clk;

	// Release reset to match Verilator timing.
	// Verilator C++ driver sets rst_n=1 simultaneously with the rising edge
	// at cycle 10 (t=100).  Our NBA-based release must happen one cycle
	// earlier so that rst_n is already 1 when the posedge at t=100 fires.
	reg [15:0] cycle_count = 0;
	always @(posedge clk) begin
		cycle_count <= cycle_count + 1;
		if (cycle_count == 8)
			rst_n <= 1;
	end

	// ── Timeout ─────────────────────────────────────────────────────
	always @(posedge clk) begin
		if (cycle_count == 10100) begin
			$display("TIMEOUT after 10000 cycles");
			$finish;
		end
	end

	// ── Memory ──────────────────────────────────────────────────────
	reg [31:0] memory [0:4095];  // 16 KB

	initial begin
		$readmemh("firmware.hex", memory);
	end

	// ── Instruction bus ─────────────────────────────────────────────
	// OBI protocol: gnt is combinational (same cycle as req),
	//               rvalid + rdata arrive one cycle after gnt.
	wire        instr_req;
	wire [31:0] instr_addr;
	wire        instr_gnt;      // combinational: always grant immediately
	reg         instr_rvalid;
	reg  [31:0] instr_rdata;

	// Grant immediately when request comes in
	assign instr_gnt = instr_req;

	// Respond with data one cycle after grant
	reg         instr_req_r;    // registered copy of req (tracks gnt)
	reg  [31:0] instr_addr_r;   // registered address for data lookup

	always @(posedge clk) begin
		if (!rst_n) begin
			instr_req_r  <= 0;
			instr_addr_r <= 0;
			instr_rvalid <= 0;
			instr_rdata  <= 0;
		end else begin
			instr_req_r  <= instr_req;
			instr_addr_r <= instr_addr;
			instr_rvalid <= instr_req_r;
			instr_rdata  <= instr_req_r ? memory[instr_addr_r[15:2]] : instr_rdata;
		end
	end

	// ── Data bus ────────────────────────────────────────────────────
	// OBI protocol: gnt is combinational, rvalid one cycle after gnt.
	wire        data_req;
	wire        data_we;
	wire [3:0]  data_be;
	wire [31:0] data_addr;
	wire [31:0] data_wdata;
	wire        data_gnt;       // combinational: always grant immediately
	reg         data_rvalid;
	reg  [31:0] data_rdata;

	// Grant immediately
	assign data_gnt = data_req;

	// One-cycle pipelined response
	reg         data_req_r;
	reg         data_we_r;
	reg  [3:0]  data_be_r;
	reg  [31:0] data_addr_r;
	reg  [31:0] data_wdata_r;

	always @(posedge clk) begin
		if (!rst_n) begin
			data_req_r   <= 0;
			data_we_r    <= 0;
			data_be_r    <= 0;
			data_addr_r  <= 0;
			data_wdata_r <= 0;
			data_rvalid  <= 0;
			data_rdata   <= 0;
		end else begin
			data_req_r   <= data_req;
			data_we_r    <= data_we;
			data_be_r    <= data_be;
			data_addr_r  <= data_addr;
			data_wdata_r <= data_wdata;
			data_rvalid  <= data_req_r;

			// Execute write on response cycle
			if (data_req_r && data_we_r) begin
				if (data_be_r[0]) memory[data_addr_r[15:2]][ 7: 0] <= data_wdata_r[ 7: 0];
				if (data_be_r[1]) memory[data_addr_r[15:2]][15: 8] <= data_wdata_r[15: 8];
				if (data_be_r[2]) memory[data_addr_r[15:2]][23:16] <= data_wdata_r[23:16];
				if (data_be_r[3]) memory[data_addr_r[15:2]][31:24] <= data_wdata_r[31:24];
				data_rdata <= 0;
			end else if (data_req_r) begin
				data_rdata <= memory[data_addr_r[15:2]];
			end
		end
	end

	// ── Halt detection ──────────────────────────────────────────────
	// Convention: SW to address 0x3000 = halt with exit code in data.
	always @(posedge clk) begin
		if (rst_n && data_req && data_we && data_addr == 32'h00003000) begin
			if (data_wdata == 32'h00000001) begin
				$display("");
				$display("==============================");
				$display("Ibex Test Results");
				$display("==============================");
				$display("  [%s] LUI/AUIPC",         memory[512] ? "PASS" : "FAIL");
				$display("  [%s] ADDI",              memory[513] ? "PASS" : "FAIL");
				$display("  [%s] ADD/SUB",           memory[514] ? "PASS" : "FAIL");
				$display("  [%s] LOGIC (AND/OR/XOR)", memory[515] ? "PASS" : "FAIL");
				$display("  [%s] SHIFT",             memory[516] ? "PASS" : "FAIL");
				$display("  [%s] COMPARE (SLT)",     memory[517] ? "PASS" : "FAIL");
				$display("  [%s] BRANCH",            memory[518] ? "PASS" : "FAIL");
				$display("  [%s] JAL/JALR",          memory[519] ? "PASS" : "FAIL");
				$display("  [%s] LOAD/STORE",        memory[520] ? "PASS" : "FAIL");
				$display("==============================");
				$display("Completed in %0d cycles", cycle_count);
				$finish;
			end else begin
				$display("FAIL: exit code %0d", data_wdata);
				$finish;
			end
		end
	end

	// ── Tie-off signals (explicit assigns, no init-in-declaration) ──
	wire [31:0] hart_id;
	wire [31:0] boot_addr;
	wire        zero;
	wire        one;
	wire [14:0] zero15;
	wire [3:0]  fetch_en;

	assign hart_id   = 32'h0;
	assign boot_addr = 32'h0;
	assign zero      = 1'b0;
	assign one       = 1'b1;
	assign zero15    = 15'h0;
	assign fetch_en  = 4'b0101;  // IbexMuBiOn

	// ── Register file (external, looped back) ───────────────────────
	wire        dummy_instr_id;
	wire        dummy_instr_wb;
	wire [4:0]  rf_raddr_a;
	wire [4:0]  rf_raddr_b;
	wire [4:0]  rf_waddr_wb;
	wire        rf_we_wb;
	wire [31:0] rf_wdata_wb_ecc;
	wire [31:0] rf_rdata_a_ecc;
	wire [31:0] rf_rdata_b_ecc;

	ibex_register_file_ff #(
		.RV32E            (0),
		.DataWidth        (32),
		.DummyInstructions(0)
	) regfile (
		.clk_i            (clk),
		.rst_ni           (rst_n),
		.test_en_i        (zero),
		.dummy_instr_id_i (dummy_instr_id),
		.dummy_instr_wb_i (dummy_instr_wb),
		.raddr_a_i        (rf_raddr_a),
		.rdata_a_o        (rf_rdata_a_ecc),
		.raddr_b_i        (rf_raddr_b),
		.rdata_b_o        (rf_rdata_b_ecc),
		.waddr_a_i        (rf_waddr_wb),
		.wdata_a_i        (rf_wdata_wb_ecc),
		.we_a_i           (rf_we_wb)
	);

	// ── Ibex core ───────────────────────────────────────────────────
	ibex_core #(
		.PMPEnable        (0),
		.PMPNumRegions    (4),
		.MHPMCounterNum   (0),
		.MHPMCounterWidth (40),
		.RV32E            (0),
		.RV32M            (3),    // RV32MSingleCycle
		.RV32B            (0),    // RV32BNone
		.BranchTargetALU  (0),
		.WritebackStage   (0),
		.ICache           (0),
		.ICacheECC        (0),
		.BranchPredictor  (0),
		.DbgTriggerEn     (0),
		.DbgHwBreakNum    (1),
		.SecureIbex       (0),
		.DummyInstructions(0),
		.RegFileECC       (0),
		.RegFileDataWidth (32),
		.MemECC           (0),
		.DmHaltAddr       (32'h1A110800),
		.DmExceptionAddr  (32'h1A110808)
	) core (
		.clk_i              (clk),
		.rst_ni             (rst_n),

		.hart_id_i          (hart_id),
		.boot_addr_i        (boot_addr),

		// Instruction memory
		.instr_req_o        (instr_req),
		.instr_gnt_i        (instr_gnt),
		.instr_rvalid_i     (instr_rvalid),
		.instr_addr_o       (instr_addr),
		.instr_rdata_i      (instr_rdata),
		.instr_err_i        (zero),

		// Data memory
		.data_req_o         (data_req),
		.data_gnt_i         (data_gnt),
		.data_rvalid_i      (data_rvalid),
		.data_we_o          (data_we),
		.data_be_o          (data_be),
		.data_addr_o        (data_addr),
		.data_wdata_o       (data_wdata),
		.data_rdata_i       (data_rdata),
		.data_err_i         (zero),

		// Register file
		.dummy_instr_id_o   (dummy_instr_id),
		.dummy_instr_wb_o   (dummy_instr_wb),
		.rf_raddr_a_o       (rf_raddr_a),
		.rf_raddr_b_o       (rf_raddr_b),
		.rf_waddr_wb_o      (rf_waddr_wb),
		.rf_we_wb_o         (rf_we_wb),
		.rf_wdata_wb_ecc_o  (rf_wdata_wb_ecc),
		.rf_rdata_a_ecc_i   (rf_rdata_a_ecc),
		.rf_rdata_b_ecc_i   (rf_rdata_b_ecc),

		// ICache RAMs (unused, ICache=0) — omit unpacked array ports
		.ic_tag_req_o       (),
		.ic_tag_write_o     (),
		.ic_tag_addr_o      (),
		.ic_tag_wdata_o     (),
		.ic_data_req_o      (),
		.ic_data_write_o    (),
		.ic_data_addr_o     (),
		.ic_data_wdata_o    (),
		.ic_scr_key_valid_i (one),

		// Interrupts (none)
		.irq_software_i     (zero),
		.irq_timer_i        (zero),
		.irq_external_i     (zero),
		.irq_fast_i         (zero15),
		.irq_nm_i           (zero),
		.irq_pending_o      (),

		// Debug (none)
		.debug_req_i        (zero),
		.crash_dump_o       (),
		.double_fault_seen_o(),

		// Control
		.fetch_enable_i             (fetch_en),
		.alert_minor_o              (),
		.alert_major_internal_o     (),
		.alert_major_bus_o          (),
		.core_busy_o                ()
	);

endmodule
