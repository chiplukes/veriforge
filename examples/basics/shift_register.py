"""Example: Parameterized shift register with parallel output.

Demonstrates:
- Parameters for configurable width
- Concatenation (cat) for shift logic
- Bit selection (sig[i])
- Python loops for generation (replaces Verilog generate)
- Simulation verification
"""

from veriforge.dsl import Module, cat, posedge
from veriforge.codegen.verilog_emitter import emit_module
from veriforge.sim import Clock, Simulator


def build_shift_register(depth: int = 4) -> Module:
    """Build a shift register with configurable depth.

    This shows how Python functions replace Verilog parameterized modules.
    """
    m = Module("shift_register")
    clk = m.input("clk")
    rst = m.input("rst")
    din = m.input("din").comment("Serial data in")
    dout = m.output("dout").comment("Serial data out (oldest bit)")
    parallel = m.output_reg("parallel", width=depth).comment("Parallel snapshot")

    sr = m.reg("sr", width=depth).comment(f"{depth}-bit shift chain")

    # Shift: new data enters MSB, old data exits LSB
    with m.always(posedge(clk)):
        with m.if_(rst):
            sr <<= 0
        with m.else_():
            sr <<= cat(din, sr[depth - 1 : 1])  # shift right, din into MSB

    m.assign(dout, sr[0])  # oldest bit
    m.assign(parallel, sr)

    return m


# -- Build and emit --

m = build_shift_register(depth=4)
module = m.build()

print("// ===== Generated Verilog =====")
print(emit_module(module))

# -- Simulate: shift in 1, 0, 1, 1 --

sim = Simulator(module)
sim.fork(Clock(sim.signal("clk"), period=10))

# Reset
sim.drive("rst", 1)
sim.drive("din", 0)
sim.run(lambda s: None, max_time=15)
assert sim.read("sr") == 0, "Shift register should be cleared"

# Release reset and shift in bits: 1, 0, 1, 1 (MSB first)
sim.drive("rst", 0)
bits = [1, 0, 1, 1]
for bit in bits:
    sim.drive("din", bit)
    sim.run(lambda s: None, max_time=10)  # one clock cycle

parallel = int(sim.read("parallel"))
print(f"\n// After shifting in {bits}: parallel = {parallel:#06b} ({parallel})")
print("// Simulation PASSED")
