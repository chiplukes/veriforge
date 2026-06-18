"""Example: Testbench using system tasks, delays, and event control.

Demonstrates:
- Initial block with delays (#10, #100)
- System tasks ($display, $finish, $readmemh)
- Event control (@(posedge clk))
- with m.delay(...): block form
- Testbench module pattern
"""

from veriforge.dsl import Module, posedge
from veriforge.codegen.verilog_emitter import emit_module

# -- Build a simple DUT (device under test) --

dut = Module("counter")
clk = dut.input("clk")
rst = dut.input("rst")
en = dut.input("en").comment("Count enable")
count = dut.output_reg("count", width=8)

with dut.always(posedge(clk)):
    with dut.if_(rst):
        count <<= 0
    with dut.elif_(en):
        count <<= count + 1

# -- Build the testbench --

tb = Module("tb")
tb_clk = tb.reg("clk")
tb_rst = tb.reg("rst")
tb_en = tb.reg("en")
tb_count = tb.wire("count", width=8)

# Instantiate the DUT
tb.instance(
    "counter",
    "dut",
    ports={
        "clk": tb_clk,
        "rst": tb_rst,
        "en": tb_en,
        "count": tb_count,
    },
)

# Clock generation: 10ns period
tb.comment("Clock generation")
with tb.initial():
    tb_clk @= 0
    # In real Verilog: forever #5 clk = ~clk;
    # (forever loop not yet in DSL — use simulation Clock instead)

# Stimulus
tb.comment("Test stimulus")
with tb.initial():
    tb.display("=== Counter Testbench ===")

    # Initialize
    tb_rst @= 1
    tb_en @= 0
    tb.display("Time 0: Asserting reset")

    # Wait 20ns, release reset
    tb.delay(20)
    tb_rst @= 0
    tb.display("Time 20: Releasing reset")

    # Enable counting
    tb.delay(10)
    tb_en @= 1
    tb.display("Time 30: Enabling counter")

    # Let it count for a while
    tb.delay(100)
    tb.display("Time 130: count = %d", tb_count)

    # Disable and check
    tb_en @= 0
    tb.delay(20)
    tb.display("Time 150: count (held) = %d", tb_count)

    tb.delay(10)
    tb.display("=== Test Complete ===")
    tb.finish()

# -- Emit both modules --

print("// ===== DUT =====")
print(emit_module(dut.build()))
print()
print("// ===== Testbench =====")
print(emit_module(tb.build()))
