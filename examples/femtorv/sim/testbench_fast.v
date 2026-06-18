// FemtoRV32 Quark — no timing controls.
// Clock and reset driven from Python via batch_run().

`define BENCH

module testbench;
	reg clk = 0;
	reg reset_n = 0;  // Active-low reset

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

	reg [31:0] memory [0:1023];

	initial begin
		$readmemh("firmware.hex", memory);
	end

	// Combinatorial read
	assign mem_rdata = memory[mem_addr[11:2]];

	// Synchronous write
	always @(posedge clk) begin
		if (mem_wmask[0]) memory[mem_addr[11:2]][ 7: 0] <= mem_wdata[ 7: 0];
		if (mem_wmask[1]) memory[mem_addr[11:2]][15: 8] <= mem_wdata[15: 8];
		if (mem_wmask[2]) memory[mem_addr[11:2]][23:16] <= mem_wdata[23:16];
		if (mem_wmask[3]) memory[mem_addr[11:2]][31:24] <= mem_wdata[31:24];
	end
endmodule
