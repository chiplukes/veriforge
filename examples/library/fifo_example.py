"""Example: Using the sync_fifo from the library.

Builds a FIFO, emits Verilog, and shows how to customize parameters.
"""

from veriforge.codegen import emit_module
from veriforge.dsl.lib import sync_fifo


def main():
    # Default 8-bit, 16-deep FIFO
    fifo = sync_fifo()
    print("=== Default FIFO ===")
    print(emit_module(fifo.build()))

    # Custom: 32-bit data, 64-deep, block RAM style
    fifo32 = sync_fifo(data_width=32, depth=64, name="fifo_32x64", style="block")
    print("\n=== 32-bit Block RAM FIFO ===")
    print(emit_module(fifo32.build()))

    # Small distributed FIFO for control signals
    ctrl_fifo = sync_fifo(data_width=4, depth=4, name="ctrl_fifo", style="distributed")
    print("\n=== Small Distributed FIFO ===")
    print(emit_module(ctrl_fifo.build()))


if __name__ == "__main__":
    main()
