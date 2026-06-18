"""Encoder and decoder components.

Usage::

    from veriforge.dsl.lib import priority_encoder, binary_decoder
    from veriforge.codegen import emit_module

    enc = priority_encoder(width=8)
    print(emit_module(enc.build()))

    dec = binary_decoder(width=3)
    print(emit_module(dec.build()))
"""

from __future__ import annotations

import math

from .. import Module


def priority_encoder(
    width: int = 8,
    *,
    name: str = "priority_encoder",
) -> Module:
    """Build a priority encoder (MSB-first priority).

    Outputs the index of the highest-priority (highest-numbered) set bit.
    If no input bit is set, ``valid`` is low and ``out`` is zero.

    Ports:
        din   [width-1:0]             — input vector
        out   [ceil(log2(width))-1:0] — encoded index of highest set bit
        valid                          — high when any input bit is set

    Args:
        width: Input width (number of input bits, >= 2).
        name: Module name.

    Returns:
        Module builder.

    Raises:
        ValueError: If *width* < 2.
    """
    if width < 2:
        raise ValueError(f"width must be >= 2, got {width}")

    out_width = max(1, math.ceil(math.log2(width)))

    m = Module(name)
    din = m.input("din", width=width)
    out = m.output_reg("out", width=out_width)
    valid = m.output_reg("valid")

    with m.always():
        out @= 0
        valid @= 0
        # Lower indices checked first; higher indices overwrite → MSB priority
        for i in range(width):
            with m.if_(din[i]):
                out @= i
                valid @= 1

    return m


def binary_decoder(
    width: int = 3,
    *,
    name: str = "binary_decoder",
) -> Module:
    """Build a binary-to-one-hot decoder with enable.

    When ``en`` is high, sets exactly one bit of ``out`` corresponding
    to the binary value on ``din``.  When ``en`` is low, ``out`` is
    all zeros.

    Ports:
        din [width-1:0]       — binary input
        en                     — enable
        out [2**width - 1 : 0] — one-hot output

    Args:
        width: Input width in bits (>= 1).  Output is 2**width bits.
        name: Module name.

    Returns:
        Module builder.

    Raises:
        ValueError: If *width* < 1.
    """
    if width < 1:
        raise ValueError(f"width must be >= 1, got {width}")

    n_outputs = 2**width

    m = Module(name)
    din = m.input("din", width=width)
    en = m.input("en")
    out = m.output_reg("out", width=n_outputs)

    with m.always():
        out @= 0
        with m.if_(en):
            with m.case(din) as c:
                for i in range(n_outputs):
                    with c.when(i):
                        out @= 1 << i

    return m
