// PicoRV32 RV32I instruction test testbench.
// Loads firmware from firmware.hex, runs until trap (EBREAK),
// then reads test results and prints pass/fail per test group.
// Adapted from PicoRV32 testbench_ez.v (public domain).

`timescale 1 ns / 1 ps

module testbench;
	reg clk = 1;
	reg resetn = 0;
	wire trap;

	always #5 clk = ~clk;

	// Reset release after 100 clock cycles (counter-based, no @posedge in initial)
	reg [15:0] cycle_count = 0;
	always @(posedge clk) begin
		cycle_count <= cycle_count + 1;
		if (cycle_count == 99)
			resetn <= 1;
	end

	// Wait for trap (EBREAK) or timeout
	always @(posedge clk) begin
		if (resetn && trap) begin
			$display("");
			$display("==============================");
			$display("RV32I Instruction Test Results");
			$display("==============================");
			$display("  [%s] LUI/AUIPC",           memory[384] ? "PASS" : "FAIL");
			$display("  [%s] JAL/JALR",             memory[385] ? "PASS" : "FAIL");
			$display("  [%s] BRANCH",               memory[386] ? "PASS" : "FAIL");
			$display("  [%s] LOAD/STORE (word)",     memory[387] ? "PASS" : "FAIL");
			$display("  [%s] ALU immediate",         memory[388] ? "PASS" : "FAIL");
			$display("  [%s] ALU register",          memory[389] ? "PASS" : "FAIL");
			$display("  [%s] SHIFT",                 memory[390] ? "PASS" : "FAIL");
			$display("  [%s] COMPARE (SLT)",         memory[391] ? "PASS" : "FAIL");
			$display("  [%s] LOGICAL",               memory[392] ? "PASS" : "FAIL");
			$display("  [%s] LOAD/STORE (byte)",     memory[393] ? "PASS" : "FAIL");
			$display("  [%s] LOAD/STORE (half)",     memory[394] ? "PASS" : "FAIL");
			$display("==============================");
			$display("Completed in %0d cycles", uut.count_cycle);
			$finish;
		end
	end

	// Timeout after 5000 cycles
	always @(posedge clk) begin
		if (cycle_count == 5100) begin
			$display("TIMEOUT - trap not reached after 5000 cycles");
			$finish;
		end
	end

	wire mem_valid;
	wire mem_instr;
	reg mem_ready;
	wire [31:0] mem_addr;
	wire [31:0] mem_wdata;
	wire [3:0] mem_wstrb;
	reg  [31:0] mem_rdata;

	picorv32 uut (
		.clk         (clk        ),
		.resetn      (resetn     ),
		.trap        (trap       ),
		.mem_valid   (mem_valid  ),
		.mem_instr   (mem_instr  ),
		.mem_ready   (mem_ready  ),
		.mem_addr    (mem_addr   ),
		.mem_wdata   (mem_wdata  ),
		.mem_wstrb   (mem_wstrb  ),
		.mem_rdata   (mem_rdata  )
	);

	reg [31:0] memory [0:1023];

	initial begin
		$dumpfile("dump.vcd");
		$dumpvars(0, testbench);
		$readmemh("firmware.hex", memory);
	end

	always @(posedge clk) begin
		mem_ready <= 0;
		if (mem_valid && !mem_ready) begin
			if (mem_addr < 4096) begin
				mem_ready <= 1;
				mem_rdata <= memory[mem_addr >> 2];
				if (mem_wstrb[0]) memory[mem_addr >> 2][ 7: 0] <= mem_wdata[ 7: 0];
				if (mem_wstrb[1]) memory[mem_addr >> 2][15: 8] <= mem_wdata[15: 8];
				if (mem_wstrb[2]) memory[mem_addr >> 2][23:16] <= mem_wdata[23:16];
				if (mem_wstrb[3]) memory[mem_addr >> 2][31:24] <= mem_wdata[31:24];
			end
		end
	end
endmodule
