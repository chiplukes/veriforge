"""Example: Simple 8-bit ALU with 4 operations.

Demonstrates:
- Case statement for operation decoding
- Combinational always block
- Multiple output signals
- Concatenation for carry/result packing
- Mux helper function
- Localparam operation encoding
- Simulation of all operations
"""

from veriforge.dsl import Module, cat, mux
from veriforge.codegen.verilog_emitter import emit_module
from veriforge.sim import Simulator

# -- Build the ALU --

m = Module("alu")
a = m.input("a", width=8).comment("Operand A")
b = m.input("b", width=8).comment("Operand B")
op = m.input("op", width=2).comment("Operation select")
result = m.output_reg("result", width=8).comment("ALU result")
carry = m.output_reg("carry").comment("Carry/borrow out")
zero = m.output("zero").comment("Result is zero")

# Operation encoding — Python constants used for simulation;
# localparams emitted in Verilog output.
OP_ADD = 0
OP_SUB = 1
OP_AND = 2
OP_OR = 3
m.localparam("OP_ADD", value=OP_ADD)
m.localparam("OP_SUB", value=OP_SUB)
m.localparam("OP_AND", value=OP_AND)
m.localparam("OP_OR", value=OP_OR)

# Internal 9-bit sum/diff for carry detection
tmp = m.reg("tmp", width=9)

m.comment("ALU operation decode")
with m.always():
    tmp @= 0
    carry @= 0
    result @= 0
    with m.case(op) as c:
        with c.when(OP_ADD):
            tmp @= a + b
            result @= tmp[7:0]
            carry @= tmp[8]
        with c.when(OP_SUB):
            tmp @= a - b
            result @= tmp[7:0]
            carry @= tmp[8]
        with c.when(OP_AND):
            result @= a & b
        with c.when(OP_OR):
            result @= a | b

# Zero flag (continuous)
m.assign(zero, result == 0)

module = m.build()

# -- Emit Verilog --

print("// ===== Generated Verilog =====")
print(emit_module(module))

# -- Simulate all operations --

sim = Simulator(module)

test_cases = [
    # (a, b, op, expected_result, description)
    (10, 20, 0, 30, "ADD: 10 + 20 = 30"),
    (100, 50, 1, 50, "SUB: 100 - 50 = 50"),
    (0xFF, 0x0F, 2, 0x0F, "AND: 0xFF & 0x0F = 0x0F"),
    (0xA0, 0x05, 3, 0xA5, "OR:  0xA0 | 0x05 = 0xA5"),
    (0, 0, 0, 0, "ADD: 0 + 0 = 0 (zero flag)"),
]

print("\n// ===== Simulation Results =====")
all_passed = True
for a_val, b_val, op_val, expected, desc in test_cases:
    sim.drive("a", a_val)
    sim.drive("b", b_val)
    sim.drive("op", op_val)
    sim.run(lambda s: None, max_time=10)
    actual = int(sim.read("result"))
    zero_flag = int(sim.read("zero"))
    passed = actual == expected
    status = "PASS" if passed else "FAIL"
    print(f"//   [{status}] {desc} → got {actual}, zero={zero_flag}")
    if not passed:
        all_passed = False

if all_passed:
    print("// All ALU tests PASSED")
else:
    print("// Some ALU tests FAILED")
    raise AssertionError("ALU simulation failed")
