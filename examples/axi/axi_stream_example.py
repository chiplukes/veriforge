"""Example: AXI-Stream interface and pipeline register.

Shows how to use the AXI-Stream interface in custom modules and
the pre-built pipeline register for timing closure.
"""

from veriforge.codegen import emit_module
from veriforge.dsl import Module, posedge
from veriforge.dsl.lib import axi_stream, axis_register


def data_source():
    """Simple AXI-Stream master that outputs incrementing data."""
    m = Module("data_source")
    clk = m.input("clk")
    rst = m.input("rst")

    # Bind as master: tvalid/tdata/tlast are outputs, tready is input
    out = m.interface("m_axis", axi_stream(data_width=8), role="master", reg=True)
    counter = m.reg("counter", width=8)

    with m.always(posedge(clk)):
        with m.if_(rst):
            out.tvalid <<= 0
            counter <<= 0
        with m.else_():
            out.tvalid <<= 1
            out.tdata <<= counter
            out.tlast <<= counter == 255
            with m.if_(out.tready & out.tvalid):
                counter <<= counter + 1

    return m


def data_sink():
    """Simple AXI-Stream slave that accepts everything."""
    m = Module("data_sink")
    clk = m.input("clk")
    rst = m.input("rst")

    # Bind as slave: tvalid/tdata/tlast are inputs, tready is output
    inp = m.interface("s_axis", axi_stream(data_width=8), role="slave")
    m.assign(inp.tready, 1)  # always ready

    received = m.output_reg("received", width=8)
    with m.always(posedge(clk)):
        with m.if_(rst):
            received <<= 0
        with m.else_():
            with m.if_(inp.tvalid & inp.tready):
                received <<= inp.tdata

    return m


def main():
    print("=== AXI-Stream Data Source (Master) ===")
    print(emit_module(data_source().build()))

    print("\n=== AXI-Stream Data Sink (Slave) ===")
    print(emit_module(data_sink().build()))

    print("\n=== AXI-Stream Pipeline Register ===")
    reg = axis_register(data_width=8)
    print(emit_module(reg.build()))

    print("\n=== 32-bit Pipeline Register ===")
    reg32 = axis_register(data_width=32, name="axis_reg_32")
    print(emit_module(reg32.build()))


if __name__ == "__main__":
    main()
