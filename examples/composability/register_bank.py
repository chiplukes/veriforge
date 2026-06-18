"""Composability showcase: Register bank generated from a Python dictionary.

Demonstrates something IMPRACTICAL in plain Verilog: generating a complete
memory-mapped register file from a declarative Python configuration.

In Verilog, register files require manually writing:
  - One `reg` per register
  - An address-decode case statement for writes
  - An address-decode case statement for reads
  - Reset values for every register
  - Port declarations matching the register set

Any change to the register map requires touching 4+ places in the HDL.

With the DSL, the register map is a single Python dict — add a register
in one place and the write logic, read mux, reset values, and ports are
all generated automatically.
"""

from veriforge.dsl import Module, posedge
from veriforge.codegen import emit_module


# ---------------------------------------------------------------------------
# The register bank generator
# ---------------------------------------------------------------------------


def register_bank(name, registers, *, data_width=32, addr_width=8):
    """Generate a memory-mapped register file from a Python config dict.

    Args:
        name:       Module name.
        registers:  Ordered dict of register_name -> config.
                    Config keys:
                      offset   (int)  — byte address
                      width    (int)  — bit width (default: data_width)
                      default  (int)  — reset value (default: 0)
                      readonly (bool) — if True, exposed as input port
        data_width: Bus data width (default 32).
        addr_width: Address bus width (default 8).

    Returns:
        Module builder.

    Generated ports::

        clk, rst       — clock and synchronous reset
        addr           — address bus
        wdata          — write data bus
        we             — write enable
        rdata          — read data bus (registered output)
        <name>_out     — one output per writable register (current value)
        <name>_in      — one input per read-only register (external value)
    """
    m = Module(name)
    clk = m.input("clk")
    rst = m.input("rst")
    addr = m.input("addr", width=addr_width)
    wdata = m.input("wdata", width=data_width)
    we = m.input("we")
    rdata = m.output_reg("rdata", width=data_width)

    reg_signals = {}  # name -> Signal

    # --- Declare registers and ports ---
    for reg_name, cfg in registers.items():
        w = cfg.get("width", data_width)
        readonly = cfg.get("readonly", False)
        lname = reg_name.lower()

        if readonly:
            # Read-only: external input feeds the read mux
            sig = m.input(f"{lname}_in", width=w).comment(f"{reg_name} (read-only)")
        else:
            # Writable: internal reg + output port for current value
            sig = m.reg(lname, width=w).comment(f"{reg_name} register")
            out_port = m.output(f"{lname}_out", width=w).comment(f"{reg_name} current value")
            m.assign(out_port, sig)

        reg_signals[reg_name] = sig

    # --- Write logic ---
    writable = {k: v for k, v in registers.items() if not v.get("readonly", False)}

    if writable:
        m.comment("Write logic — address decode")
        with m.always(posedge(clk)):
            with m.if_(rst):
                for rname, cfg in writable.items():
                    reg_signals[rname] <<= cfg.get("default", 0)
            with m.elif_(we):
                with m.case(addr) as c:
                    for rname, cfg in writable.items():
                        with c.when(cfg["offset"]):
                            w = cfg.get("width", data_width)
                            if w < data_width:
                                # Mask to register width
                                mask = (1 << w) - 1
                                reg_signals[rname] <<= wdata & mask
                            else:
                                reg_signals[rname] <<= wdata

    # --- Read logic (combinational) ---
    m.comment("Read logic — address decode")
    with m.always():
        with m.case(addr) as c:
            for rname, cfg in registers.items():
                with c.when(cfg["offset"]):
                    rdata.set(reg_signals[rname])
            with c.default():
                rdata.set(0)

    return m


# ---------------------------------------------------------------------------
# Example 1: Simple peripheral register set
# ---------------------------------------------------------------------------

print("=" * 70)
print("Example 1: Simple peripheral (4 registers)")
print("=" * 70)

peripheral_regs = {
    "CTRL": {"offset": 0x00, "default": 0x01},
    "STATUS": {"offset": 0x04, "readonly": True},
    "TX_DATA": {"offset": 0x08},
    "RX_DATA": {"offset": 0x0C, "readonly": True},
}

periph = register_bank("periph_regs", peripheral_regs)
print(emit_module(periph.build()))
print()


# ---------------------------------------------------------------------------
# Example 2: PWM controller register set
# ---------------------------------------------------------------------------

print("=" * 70)
print("Example 2: PWM controller (6 registers, mixed widths)")
print("=" * 70)

pwm_regs = {
    "PWM_CTRL": {"offset": 0x00, "width": 8, "default": 0},
    "PWM_PERIOD": {"offset": 0x04, "width": 16, "default": 1000},
    "PWM_DUTY0": {"offset": 0x08, "width": 16, "default": 500},
    "PWM_DUTY1": {"offset": 0x0C, "width": 16, "default": 500},
    "PWM_DUTY2": {"offset": 0x10, "width": 16, "default": 500},
    "PWM_STATUS": {"offset": 0x14, "width": 8, "readonly": True},
}

pwm = register_bank("pwm_regs", pwm_regs, addr_width=5)
print(emit_module(pwm.build()))
print()


# ---------------------------------------------------------------------------
# Example 3: Programmatically generate a large register set
# ---------------------------------------------------------------------------

print("=" * 70)
print("Example 3: 16-channel DMA register set (generated from loop)")
print("=" * 70)

dma_regs = {
    "DMA_CTRL": {"offset": 0x00, "default": 0},
    "DMA_STATUS": {"offset": 0x04, "readonly": True},
}

# Programmatically add per-channel registers
for ch in range(4):
    base = 0x10 + ch * 0x10
    dma_regs[f"DMA_CH{ch}_SRC"] = {"offset": base + 0x00}
    dma_regs[f"DMA_CH{ch}_DST"] = {"offset": base + 0x04}
    dma_regs[f"DMA_CH{ch}_LEN"] = {"offset": base + 0x08, "width": 16}
    dma_regs[f"DMA_CH{ch}_CTRL"] = {"offset": base + 0x0C, "width": 8, "default": 0}

dma = register_bank("dma_regs", dma_regs, addr_width=8)
verilog = emit_module(dma.build())

# Show stats instead of full Verilog (it's big)
lines = verilog.strip().splitlines()
print(f"Generated {len(lines)} lines of Verilog for {len(dma_regs)} registers")
print(f"Registers: {', '.join(dma_regs.keys())}")
print()
print("First 40 lines:")
for line in lines[:40]:
    print(line)
print("...")
