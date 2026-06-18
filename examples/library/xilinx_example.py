"""Example: Xilinx-specific inference patterns.

Demonstrates SRL shift registers and LUTRAM patterns that Vivado
maps to dedicated primitives.
"""

from veriforge.codegen import emit_module
from veriforge.dsl.lib import lutram, shift_register_srl


def main():
    # SRL16-style shift register (depth ≤ 16 → SRL16E primitive)
    srl16 = shift_register_srl(width=1, depth=16)
    print("=== SRL16 Shift Register ===")
    print(emit_module(srl16.build()))

    # SRL32-style shift register (depth ≤ 32 → SRLC32E primitive)
    srl32 = shift_register_srl(width=8, depth=32, name="srl32_delay")
    print("\n=== SRL32 Delay Line (8-bit) ===")
    print(emit_module(srl32.build()))

    # Distributed LUTRAM (async read → RAM32X1S / RAM64X1S)
    lut = lutram(data_width=8, depth=32)
    print("\n=== Distributed LUTRAM 8x32 ===")
    print(emit_module(lut.build()))

    # Larger LUTRAM
    lut64 = lutram(data_width=16, depth=64, name="lutram_16x64")
    print("\n=== Distributed LUTRAM 16x64 ===")
    print(emit_module(lut64.build()))


if __name__ == "__main__":
    main()
