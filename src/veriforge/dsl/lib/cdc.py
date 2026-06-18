"""Clock domain crossing and edge detection components.

Usage::

    from veriforge.dsl.lib import synchronizer, edge_detector
    from veriforge.codegen import emit_module

    sync = synchronizer(width=1, stages=2)
    print(emit_module(sync.build()))

    det = edge_detector("rising")
    print(emit_module(det.build()))
"""

from __future__ import annotations

from .. import Module, posedge


def synchronizer(
    width: int = 1,
    stages: int = 2,
    *,
    name: str = "synchronizer",
) -> Module:
    """Build a multi-flip-flop synchronizer for clock domain crossing.

    Creates a chain of ``stages`` registers with ``(* async_reg = "true" *)``
    synthesis attributes for proper FPGA placement.

    Ports:
        clk  — destination domain clock
        din  [width-1:0] — asynchronous input
        dout [width-1:0] — synchronized output

    Args:
        width: Signal width in bits (default 1).
        stages: Number of synchronizer flip-flop stages (default 2, minimum 2).
        name: Module name.

    Returns:
        Module builder.

    Raises:
        ValueError: If *stages* < 2.
    """
    if stages < 2:
        raise ValueError(f"stages must be >= 2, got {stages}")

    m = Module(name)
    clk = m.input("clk")
    din = m.input("din", width=width)
    dout = m.output("dout", width=width)

    regs = []
    for i in range(stages):
        r = m.reg(f"sync_r{i}", width=width).attr("async_reg", "true")
        regs.append(r)

    with m.always(posedge(clk)):
        regs[0] <<= din
        for i in range(1, stages):
            regs[i] <<= regs[i - 1]

    m.assign(dout, regs[-1])

    return m


def edge_detector(
    edge_type: str = "rising",
    *,
    name: str | None = None,
) -> Module:
    """Build a single-cycle pulse generator for signal edge detection.

    Captures the previous value of ``din`` in a register and compares
    with the current value to detect transitions.

    Ports:
        clk   — clock
        din   — input signal (1 bit)
        pulse — output pulse (high for one clock cycle on detected edge)

    Args:
        edge_type: ``"rising"``, ``"falling"``, or ``"any"``.
        name: Module name (default: ``"{edge_type}_edge_det"``).

    Returns:
        Module builder.

    Raises:
        ValueError: If *edge_type* is not valid.
    """
    if edge_type not in ("rising", "falling", "any"):
        raise ValueError(f"edge_type must be 'rising', 'falling', or 'any', got {edge_type!r}")

    m = Module(name or f"{edge_type}_edge_det")
    clk = m.input("clk")
    din = m.input("din")
    pulse = m.output("pulse")
    din_r = m.reg("din_r").comment("Previous value of din")

    with m.always(posedge(clk)):
        din_r <<= din

    if edge_type == "rising":
        m.assign(pulse, din & ~din_r)
    elif edge_type == "falling":
        m.assign(pulse, ~din & din_r)
    else:  # any
        m.assign(pulse, din ^ din_r)

    return m
