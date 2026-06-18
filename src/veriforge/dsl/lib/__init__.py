"""Reusable hardware component library.

Pre-built modules for common patterns — FIFOs, clock domain crossings,
encoders/decoders, and AXI protocol interfaces.

All factory functions return a ``Module`` builder.  Call ``.build()`` to get
the model, or pass to ``emit_module()`` for Verilog output.

Usage::

    from veriforge.dsl.lib import sync_fifo, axi_stream, axis_register
    from veriforge.codegen import emit_module

    fifo = sync_fifo(data_width=8, depth=16)
    print(emit_module(fifo.build()))
"""

from .fifo import sync_fifo
from .cdc import edge_detector, synchronizer
from .codec import binary_decoder, priority_encoder
from .axi_stream import axi_stream, axis_register
from .axi import axi4_lite
from .dsp import fir_filter, mac, pipelined_mult
from .xilinx import lutram, shift_register_srl

# Re-export RAM patterns for convenience
from ..ram import rom, simple_dual_port_ram, single_port_ram, true_dual_port_ram

__all__ = [  # noqa: RUF022  # grouped by category, alphabetical order within each group
    # FIFO
    "sync_fifo",
    # Clock domain crossing
    "synchronizer",
    "edge_detector",
    # Encoders / decoders
    "priority_encoder",
    "binary_decoder",
    # AXI Stream
    "axi_stream",
    "axis_register",
    # AXI4-Lite
    "axi4_lite",
    # DSP inference
    "mac",
    "pipelined_mult",
    "fir_filter",
    # Xilinx inference
    "shift_register_srl",
    "lutram",
    # RAM (re-exported)
    "single_port_ram",
    "simple_dual_port_ram",
    "true_dual_port_ram",
    "rom",
]
