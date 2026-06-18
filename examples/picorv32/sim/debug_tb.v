`timescale 1 ns / 1 ps
module testbench;
    reg clk = 1;
    reg resetn = 0;
    wire trap;
    always #5 clk = ~clk;
    initial begin
        repeat (100) @(posedge clk);
        resetn <= 1;
        repeat (5000) @(posedge clk);
        $display("TIMEOUT");
        $finish;
    end
    wire mem_valid, mem_instr;
    reg mem_ready;
    wire [31:0] mem_addr, mem_wdata;
    wire [3:0] mem_wstrb;
    reg [31:0] mem_rdata;
    picorv32 uut (.clk(clk), .resetn(resetn), .trap(trap),
        .mem_valid(mem_valid), .mem_instr(mem_instr), .mem_ready(mem_ready),
        .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb),
        .mem_rdata(mem_rdata));
    reg [31:0] memory [0:1023];
    initial $readmemh("firmware.hex", memory);
    always @(posedge clk) begin
        if (resetn && uut.decoder_trigger && uut.cpu_state == 8'b01000000)
            $display("t=%0d PC=%08x insn=%08x", $time, uut.reg_pc, uut.mem_rdata_q);
        if (trap) begin
            $display("TRAP at PC=%08x", uut.reg_pc);
            $finish;
        end
    end
    always @(posedge clk) begin
        mem_ready <= 0;
        if (mem_valid && !mem_ready && mem_addr < 4096) begin
            mem_ready <= 1;
            mem_rdata <= memory[mem_addr >> 2];
            if (mem_wstrb[0]) memory[mem_addr >> 2][ 7: 0] <= mem_wdata[ 7: 0];
            if (mem_wstrb[1]) memory[mem_addr >> 2][15: 8] <= mem_wdata[15: 8];
            if (mem_wstrb[2]) memory[mem_addr >> 2][23:16] <= mem_wdata[23:16];
            if (mem_wstrb[3]) memory[mem_addr >> 2][31:24] <= mem_wdata[31:24];
        end
    end
endmodule
