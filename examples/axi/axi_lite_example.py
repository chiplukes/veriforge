"""Example: AXI4-Lite interface usage.

Shows how to create an AXI4-Lite slave peripheral with a simple
register file.
"""

from veriforge.codegen import emit_module
from veriforge.dsl import Module, posedge
from veriforge.dsl.lib import axi4_lite


def axi_lite_slave():
    """Minimal AXI4-Lite slave with 4 read/write registers."""
    m = Module("axi_lite_regs")
    clk = m.input("clk")
    rst = m.input("rst")

    # Bind as slave: master-driven signals become inputs
    s = m.interface("s_axi", axi4_lite(data_width=32, addr_width=4), role="slave", reg=True)

    # Internal register file (4 x 32-bit)
    reg0 = m.reg("reg0", width=32)
    reg1 = m.reg("reg1", width=32)
    reg2 = m.reg("reg2", width=32)
    reg3 = m.reg("reg3", width=32)

    # Write logic
    with m.always(posedge(clk)):
        with m.if_(rst):
            s.awready <<= 0
            s.wready <<= 0
            s.bvalid <<= 0
            s.bresp <<= 0
            reg0 <<= 0
            reg1 <<= 0
            reg2 <<= 0
            reg3 <<= 0
        with m.else_():
            # Accept write address
            with m.if_(s.awvalid & ~s.awready):
                s.awready <<= 1
            with m.else_():
                s.awready <<= 0

            # Accept write data
            with m.if_(s.wvalid & ~s.wready):
                s.wready <<= 1
            with m.else_():
                s.wready <<= 0

            # Write response
            with m.if_(s.bready & s.bvalid):
                s.bvalid <<= 0

    # Read logic
    with m.always(posedge(clk)):
        with m.if_(rst):
            s.arready <<= 0
            s.rvalid <<= 0
            s.rdata <<= 0
            s.rresp <<= 0
        with m.else_():
            with m.if_(s.arvalid & ~s.arready):
                s.arready <<= 1
                s.rvalid <<= 1
                s.rresp <<= 0
                with m.case(s.araddr[3:2]) as c:
                    with c.when(0):
                        s.rdata <<= reg0
                    with c.when(1):
                        s.rdata <<= reg1
                    with c.when(2):
                        s.rdata <<= reg2
                    with c.when(3):
                        s.rdata <<= reg3
            with m.else_():
                s.arready <<= 0
                with m.if_(s.rready & s.rvalid):
                    s.rvalid <<= 0

    return m


def main():
    print("=== AXI4-Lite Slave Register File ===")
    print(emit_module(axi_lite_slave().build()))


if __name__ == "__main__":
    main()
