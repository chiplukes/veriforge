// FemtoRV32 Quark RV32I instruction test testbench.
// Loads firmware from firmware.hex, runs until done flag is set,
// then reads test results and prints pass/fail per test group.

`timescale 1 ns / 1 ps

`define BENCH

module testbench;
	reg clk = 1;
	reg reset_n = 0;  // Active-low reset (0 = reset, 1 = run)

	always #5 clk = ~clk;

	// Reset release after 100 clock cycles
	reg [15:0] cycle_count = 0;
	always @(posedge clk) begin
		cycle_count <= cycle_count + 1;
		if (cycle_count == 99)
			reset_n <= 1;
	end

	// Memory interface
	wire [31:0] mem_addr;
	wire [31:0] mem_wdata;
	wire [3:0]  mem_wmask;
	wire [31:0] mem_rdata;
	wire        mem_rstrb;

	FemtoRV32 #(
		.RESET_ADDR(32'h00000000),
		.ADDR_WIDTH(24)
	) uut (
		.clk       (clk),
		.mem_addr  (mem_addr),
		.mem_wdata (mem_wdata),
		.mem_wmask (mem_wmask),
		.mem_rdata (mem_rdata),
		.mem_rstrb (mem_rstrb),
		.mem_rbusy (1'b0),
		.mem_wbusy (1'b0),
		.reset     (reset_n)
	);

	// 1024 words (4KB) of memory, byte-addressed 0x000 - 0xFFF
	reg [31:0] memory [0:1023];

	initial begin
		$dumpfile("dump.vcd");
		$dumpvars(0, testbench);
		$readmemh("firmware.hex", memory);
	end

	// Combinatorial read (no wait states)
	assign mem_rdata = memory[mem_addr[11:2]];

	// Synchronous write
	always @(posedge clk) begin
		if (mem_wmask[0]) memory[mem_addr[11:2]][ 7: 0] <= mem_wdata[ 7: 0];
		if (mem_wmask[1]) memory[mem_addr[11:2]][15: 8] <= mem_wdata[15: 8];
		if (mem_wmask[2]) memory[mem_addr[11:2]][23:16] <= mem_wdata[23:16];
		if (mem_wmask[3]) memory[mem_addr[11:2]][31:24] <= mem_wdata[31:24];
	end

	// Completion detection: firmware writes 1 to word 511 (address 0x7FC)
	always @(posedge clk) begin
		if (reset_n && memory[511] == 32'h00000001) begin
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
			$display("Completed in %0d cycles", uut.cycles);
			$finish;
		end
	end

	// Timeout after 10000 cycles
	always @(posedge clk) begin
		if (cycle_count == 10100) begin
			$display("TIMEOUT - done flag not set after 10000 cycles");
			$finish;
		end
	end
endmodule
