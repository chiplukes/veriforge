"""Example: 8-bit counter with synchronous reset.

Demonstrates:
- Port declarations (input, output_reg)
- Always block with posedge sensitivity
- If/else control flow
- Non-blocking assignment (<<=)
- Simulation with clock and reset
"""

from veriforge.dsl import Module, posedge
from veriforge.codegen.verilog_emitter import emit_module
from veriforge.sim import Clock, Simulator

# -- Build the counter module --

m = Module("counter")
clk = m.input("clk").comment("System clock")
rst = m.input("rst").comment("Synchronous reset, active high")
count = m.output_reg("count", width=8).comment("Free-running counter")

with m.always(posedge(clk)):
    with m.if_(rst):
        count <<= 0
    with m.else_():
        count <<= count + 1

module = m.build()

# -- Emit Verilog --

print("// ===== Generated Verilog =====")
print(emit_module(module))

# -- Simulate --

sim = Simulator(module)
sim.fork(Clock(sim.signal("clk"), period=10))


def test(s):
    s.drive("rst", 1)


sim.run(test, max_time=5)  # Hold reset for one half-cycle
assert sim.read("count") == 0, "Counter should be zero under reset"

# Release reset and run a few clock cycles
sim.drive("rst", 0)
sim.run(lambda s: None, max_time=80)
count_val = int(sim.read("count"))
print(f"\n// After ~8 clock cycles: count = {count_val}")
assert count_val > 0, "Counter should have incremented"
print("// Simulation PASSED")
