// Verilator C++ testbench driver for Ibex RISC-V core.
//
// Drives clock and reset, runs until halt or timeout,
// dumps full VCD trace for cross-simulation validation.
//
// Usage (from WSL, in this directory):
//   make
//   ./obj_dir/Vtb_verilator
//   # produces ibex_trace.vcd

#include <verilated.h>
#include <verilated_vcd_c.h>
#include "Vtb_verilator.h"

#include <cstdio>
#include <cstdlib>

// Simulation parameters
static const int RESET_CYCLES  = 10;     // Hold reset for 10 cycles
static const int MAX_CYCLES    = 11000;  // Max cycles before timeout
static const int CLK_PERIOD_NS = 10;     // 10ns period (100MHz)

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);

    // Create model
    Vtb_verilator* top = new Vtb_verilator;

    // VCD tracing
    Verilated::traceEverOn(true);
    VerilatedVcdC* vcd = new VerilatedVcdC;
    top->trace(vcd, 99);  // trace depth 99 levels
    vcd->open("ibex_trace.vcd");

    // Initialize
    top->clk   = 0;
    top->rst_n = 0;

    vluint64_t sim_time = 0;
    int cycle = 0;
    bool done = false;

    printf("Ibex Verilator simulation starting...\n");
    printf("  Reset cycles:  %d\n", RESET_CYCLES);
    printf("  Max cycles:    %d\n", MAX_CYCLES);
    printf("  VCD output:    ibex_trace.vcd\n\n");

    while (!done && cycle < MAX_CYCLES && !Verilated::gotFinish()) {
        // Rising edge
        top->clk = 1;

        // Release reset after RESET_CYCLES
        if (cycle == RESET_CYCLES) {
            top->rst_n = 1;
            printf("  Reset released at cycle %d\n", cycle);
        }

        top->eval();
        vcd->dump(sim_time);
        sim_time += CLK_PERIOD_NS / 2;

        // Check halt (on rising edge, after eval)
        if (top->halted) {
            printf("\n  Halted at cycle %d (halt_code = %u)\n", cycle, top->halt_code);
            done = true;
        }

        // Falling edge
        top->clk = 0;
        top->eval();
        vcd->dump(sim_time);
        sim_time += CLK_PERIOD_NS / 2;

        cycle++;
    }

    if (!done) {
        printf("\n  TIMEOUT after %d cycles\n", MAX_CYCLES);
    }

    // Final eval and flush
    top->eval();
    vcd->dump(sim_time);
    vcd->close();

    printf("\n  Total cycles:  %d\n", cycle);
    printf("  Sim time:      %lu ns\n", (unsigned long)sim_time);
    printf("  VCD written:   ibex_trace.vcd\n");

    delete top;
    delete vcd;
    return done ? 0 : 1;
}
