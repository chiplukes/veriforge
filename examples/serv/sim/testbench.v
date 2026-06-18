// SERV bit-serial RISC-V CPU — RV32I instruction test testbench.
// Uses servile convenience wrapper with external RF RAM.
// Loads firmware from firmware.hex, runs until done flag is set,
// then reads test results and prints pass/fail per test group.

`timescale 1 ns / 1 ps

module testbench;
	reg clk = 1;
	reg rst = 1;  // Active HIGH reset (1 = reset, 0 = run)

	always #5 clk = ~clk;

	// Reset release after 100 clock cycles
	reg [31:0] cycle_count = 0;
	always @(posedge clk) begin
		cycle_count <= cycle_count + 1;
		if (cycle_count == 99)
			rst <= 0;
	end

	// Memory Wishbone interface
	wire [31:0] wb_mem_adr;
	wire [31:0] wb_mem_dat;
	wire [3:0]  wb_mem_sel;
	wire        wb_mem_we;
	wire        wb_mem_stb;
	wire [31:0] wb_mem_rdt;
	reg         wb_mem_ack;

	// Extension Wishbone interface (unused — directly tied off in port map)
	wire [31:0] wb_ext_adr;
	wire [31:0] wb_ext_dat;
	wire [3:0]  wb_ext_sel;
	wire        wb_ext_we;
	wire        wb_ext_stb;

	// RF SRAM interface — rf_width=2, depth=512, $clog2(512)=9
	wire [8:0]  rf_waddr;
	wire [1:0]  rf_wdata;
	wire        rf_wen;
	wire [8:0]  rf_raddr;
	wire [1:0]  rf_rdata;
	wire        rf_ren;

	servile #(
		.width         (1),
		.reset_pc      (32'h00000000),
		.reset_strategy("MINI"),
		.sim           (1'b0),
		.debug         (1'b0),
		.with_c        (1'b0),
		.with_csr      (1'b0),
		.with_mdu      (1'b0)
	) uut (
		.i_clk        (clk),
		.i_rst        (rst),
		.i_timer_irq  (1'b0),
		// Memory bus
		.o_wb_mem_adr (wb_mem_adr),
		.o_wb_mem_dat (wb_mem_dat),
		.o_wb_mem_sel (wb_mem_sel),
		.o_wb_mem_we  (wb_mem_we),
		.o_wb_mem_stb (wb_mem_stb),
		.i_wb_mem_rdt (wb_mem_rdt),
		.i_wb_mem_ack (wb_mem_ack),
		// Extension bus (unused)
		.o_wb_ext_adr (wb_ext_adr),
		.o_wb_ext_dat (wb_ext_dat),
		.o_wb_ext_sel (wb_ext_sel),
		.o_wb_ext_we  (wb_ext_we),
		.o_wb_ext_stb (wb_ext_stb),
		.i_wb_ext_rdt (32'h00000000),
		.i_wb_ext_ack (1'b0),
		// RF SRAM interface
		.o_rf_waddr   (rf_waddr),
		.o_rf_wdata   (rf_wdata),
		.o_rf_wen     (rf_wen),
		.o_rf_raddr   (rf_raddr),
		.i_rf_rdata   (rf_rdata),
		.o_rf_ren     (rf_ren)
	);

	serv_rf_ram #(
		.width    (2),
		.csr_regs (0)
	) rf_ram (
		.i_clk   (clk),
		.i_waddr (rf_waddr),
		.i_wdata (rf_wdata),
		.i_wen   (rf_wen),
		.i_raddr (rf_raddr),
		.i_ren   (rf_ren),
		.o_rdata (rf_rdata)
	);

	// 2048 words (8KB) of memory, byte-addressed 0x0000 - 0x1FFF
	reg [31:0] memory [0:2047];

	initial begin
		$readmemh("firmware.hex", memory);
		$dumpfile("dump.vcd");
		$dumpvars(0, testbench);
	end

	// Wishbone single-cycle ack
	always @(posedge clk) begin
		wb_mem_ack <= wb_mem_stb & !wb_mem_ack;
		if (rst)
			wb_mem_ack <= 1'b0;
	end

	// Combinatorial read
	assign wb_mem_rdt = memory[wb_mem_adr[12:2]];

	// Synchronous write with byte enables (on first cycle of transaction)
	always @(posedge clk) begin
		if (wb_mem_stb & wb_mem_we & !wb_mem_ack) begin
			if (wb_mem_sel[0]) memory[wb_mem_adr[12:2]][ 7: 0] <= wb_mem_dat[ 7: 0];
			if (wb_mem_sel[1]) memory[wb_mem_adr[12:2]][15: 8] <= wb_mem_dat[15: 8];
			if (wb_mem_sel[2]) memory[wb_mem_adr[12:2]][23:16] <= wb_mem_dat[23:16];
			if (wb_mem_sel[3]) memory[wb_mem_adr[12:2]][31:24] <= wb_mem_dat[31:24];
		end
	end

	// Completion detection: firmware writes 1 to word 511 (address 0x7FC)
	always @(posedge clk) begin
		if (!rst && memory[511] == 32'h00000001) begin
			$display("");
			$display("==============================");
			$display("RV32I Instruction Test Results");
			$display("==============================");
			$display("  [%s] LUI/AUIPC",           memory[384] ? "PASS" : "FAIL");
			$display("  [%s] JAL/JALR",             memory[385] ? "PASS" : "FAIL");
			$display("  [%s] BRANCH",               memory[386] ? "PASS" : "FAIL");
			$display("  [%s] LOAD/STORE (word)",    memory[387] ? "PASS" : "FAIL");
			$display("  [%s] ALU immediate",        memory[388] ? "PASS" : "FAIL");
			$display("  [%s] ALU register",         memory[389] ? "PASS" : "FAIL");
			$display("  [%s] SHIFT",                memory[390] ? "PASS" : "FAIL");
			$display("  [%s] COMPARE (SLT)",        memory[391] ? "PASS" : "FAIL");
			$display("  [%s] LOGICAL",              memory[392] ? "PASS" : "FAIL");
			$display("  [%s] LOAD/STORE (byte)",    memory[393] ? "PASS" : "FAIL");
			$display("  [%s] LOAD/STORE (half)",    memory[394] ? "PASS" : "FAIL");
			$display("==============================");
			$finish;
		end
	end

	// Timeout after 500000 cycles
	always @(posedge clk) begin
		if (cycle_count >= 500000) begin
			$display("TIMEOUT - done flag not set after 500000 cycles");
			$finish;
		end
	end
endmodule
