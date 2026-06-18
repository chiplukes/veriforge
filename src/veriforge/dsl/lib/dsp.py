"""DSP inference patterns — multiply-accumulate and FIR filters.

Usage::

    from veriforge.dsl.lib import mac, fir_filter
    from veriforge.codegen import emit_module

    m = mac(a_width=18, b_width=18)
    print(emit_module(m.build()))

    f = fir_filter(data_width=16, coeff_width=16, num_taps=4)
    print(emit_module(f.build()))

Generates Verilog that FPGA synthesis tools infer as DSP48 slices
(Xilinx) or equivalent hard multiplier-accumulator blocks.
"""

from __future__ import annotations

import math

from .. import Module, posedge


def mac(
    a_width: int = 18,
    b_width: int = 18,
    acc_width: int | None = None,
    *,
    name: str = "mac",
    use_dsp: str | None = "yes",
) -> Module:
    """Build a multiply-accumulate (MAC) unit.

    Implements the classic DSP48 inference pattern::

        always @(posedge clk)
            if (clr)
                acc <= 0;
            else if (en)
                acc <= acc + a * b;

    Synthesis tools map this to DSP48 slices when input widths fit
    (typically 18x18 or 25x18 for Xilinx, 18x18 for Intel).

    Ports:
        clk          — clock
        rst          — synchronous reset (clears accumulator)
        en           — enable (accumulate when high)
        clr          — clear accumulator (takes priority over accumulate)
        a [A-1:0]    — multiplicand
        b [B-1:0]    — multiplier
        p [P-1:0]    — product/accumulator output (registered)

    Args:
        a_width: Width of input A (default 18 for DSP48).
        b_width: Width of input B (default 18 for DSP48).
        acc_width: Width of accumulator. Defaults to a_width + b_width + 1
                   for headroom.
        name: Module name.
        use_dsp: Synthesis attribute. ``"yes"`` forces DSP inference,
                 ``"no"`` forces fabric logic. ``None`` omits the attribute.

    Returns:
        Module builder.

    Raises:
        ValueError: If widths are less than 1.
    """
    if a_width < 1:
        raise ValueError(f"a_width must be >= 1, got {a_width}")
    if b_width < 1:
        raise ValueError(f"b_width must be >= 1, got {b_width}")

    if acc_width is None:
        acc_width = a_width + b_width + 1

    m = Module(name)
    clk = m.input("clk")
    rst = m.input("rst")
    en = m.input("en").comment("Accumulate enable")
    clr = m.input("clr").comment("Clear accumulator")
    a = m.input("a", width=a_width)
    b = m.input("b", width=b_width)
    p = m.output_reg("p", width=acc_width).comment("Accumulated product")

    if use_dsp is not None:
        p.attr("use_dsp", use_dsp)

    with m.always(posedge(clk)):
        with m.if_(rst):
            p <<= 0
        with m.else_():
            with m.if_(clr):
                p <<= 0
            with m.elif_(en):
                p <<= p + a * b

    return m


def pipelined_mult(
    a_width: int = 18,
    b_width: int = 18,
    *,
    stages: int = 3,
    name: str = "pipelined_mult",
    use_dsp: str | None = "yes",
) -> Module:
    """Build a pipelined multiplier for optimal DSP48 timing.

    Registers both inputs and the product, matching the DSP48 internal
    pipeline structure (A-reg, B-reg, M-reg / P-reg)::

        // Stage 1: register inputs
        always @(posedge clk) begin a_r <= a; b_r <= b; end
        // Stage 2: multiply (registered)
        always @(posedge clk) p_r <= a_r * b_r;
        // Stage 3: output register
        always @(posedge clk) p <= p_r;

    Args:
        a_width: Width of input A.
        b_width: Width of input B.
        stages: Pipeline depth (2 = in+out, 3 = in+mult+out). Must be >= 2.
        name: Module name.
        use_dsp: Synthesis attribute.

    Returns:
        Module builder.

    Raises:
        ValueError: If stages < 2 or widths < 1.
    """
    if a_width < 1:
        raise ValueError(f"a_width must be >= 1, got {a_width}")
    if b_width < 1:
        raise ValueError(f"b_width must be >= 1, got {b_width}")
    if stages < 2:
        raise ValueError(f"stages must be >= 2, got {stages}")

    product_width = a_width + b_width

    m = Module(name)
    clk = m.input("clk")
    a = m.input("a", width=a_width)
    b = m.input("b", width=b_width)
    p = m.output_reg("p", width=product_width).comment("Pipelined product")

    if use_dsp is not None:
        p.attr("use_dsp", use_dsp)

    m.comment("Input registers")
    a_r = m.reg("a_r", width=a_width)
    b_r = m.reg("b_r", width=b_width)

    # Build pipeline chain: multiply result → N-2 extra stages → output
    pipe_regs = []
    for i in range(stages - 2):
        r = m.reg(f"p_stage{i}", width=product_width)
        pipe_regs.append(r)

    with m.always(posedge(clk)):
        m.comment("Stage 1: register inputs")
        a_r <<= a
        b_r <<= b

        m.comment("Stage 2: multiply")
        if pipe_regs:
            pipe_regs[0] <<= a_r * b_r
        else:
            # stages == 2: multiply directly to output
            p <<= a_r * b_r

        # Additional pipeline stages
        for i in range(1, len(pipe_regs)):
            pipe_regs[i] <<= pipe_regs[i - 1]

        # Final output stage
        if pipe_regs:
            m.comment(f"Stage {stages}: output register")
            p <<= pipe_regs[-1]

    return m


def fir_filter(
    data_width: int = 16,
    coeff_width: int = 16,
    num_taps: int = 4,
    *,
    name: str = "fir_filter",
    use_dsp: str | None = "yes",
) -> Module:
    """Build a direct-form FIR filter.

    Implements a transposed FIR structure that maps well to DSP48 chains::

        // Tap chain: each tap multiplies input by coefficient and adds
        always @(posedge clk)
            for each tap i:
                acc[i] <= din * coeff[i] + acc[i+1]
        dout <= acc[0]

    Coefficients are stored in registers (loaded via ``load`` signal).
    The transposed form allows each multiply-add to map to one DSP48.

    Ports:
        clk                     — clock
        rst                     — synchronous reset
        din  [data_width-1:0]   — input sample
        dout [out_width-1:0]    — filtered output (registered)
        coeff_in [coeff_width-1:0] — coefficient input data
        coeff_addr [addr-1:0]   — coefficient write address
        coeff_we                — coefficient write enable

    Args:
        data_width: Input sample width.
        coeff_width: Coefficient width.
        num_taps: Number of filter taps (>= 2).
        name: Module name.
        use_dsp: Synthesis attribute for multiply-add inference.

    Returns:
        Module builder.

    Raises:
        ValueError: If num_taps < 2 or widths < 1.
    """
    if data_width < 1:
        raise ValueError(f"data_width must be >= 1, got {data_width}")
    if coeff_width < 1:
        raise ValueError(f"coeff_width must be >= 1, got {coeff_width}")
    if num_taps < 2:
        raise ValueError(f"num_taps must be >= 2, got {num_taps}")

    product_width = data_width + coeff_width
    # Output width: product + bits for accumulation of num_taps values
    out_width = product_width + math.ceil(math.log2(num_taps))
    addr_width = max(1, math.ceil(math.log2(num_taps)))

    m = Module(name)
    clk = m.input("clk")
    rst = m.input("rst")
    din = m.input("din", width=data_width).comment("Input sample")
    dout = m.output_reg("dout", width=out_width).comment("Filtered output")

    if use_dsp is not None:
        dout.attr("use_dsp", use_dsp)

    m.comment("Coefficient loading interface")
    coeff_in = m.input("coeff_in", width=coeff_width)
    coeff_addr = m.input("coeff_addr", width=addr_width)
    coeff_we = m.input("coeff_we")

    m.comment("Coefficient registers")
    coeffs = []
    for i in range(num_taps):
        c = m.reg(f"coeff{i}", width=coeff_width)
        coeffs.append(c)

    m.comment("Accumulator chain (transposed form)")
    accs = []
    for i in range(num_taps):
        a = m.reg(f"acc{i}", width=out_width)
        accs.append(a)

    m.comment("Coefficient write logic")
    with m.always(posedge(clk)):
        with m.if_(coeff_we):
            with m.case(coeff_addr) as c:
                for i in range(num_taps):
                    with c.when(i):
                        coeffs[i] <<= coeff_in

    m.comment("Transposed FIR — each tap is a multiply-add")
    with m.always(posedge(clk)):
        with m.if_(rst):
            for i in range(num_taps):
                accs[i] <<= 0
            dout <<= 0
        with m.else_():
            # Last tap: just multiply
            accs[num_taps - 1] <<= din * coeffs[num_taps - 1]
            # Chain: acc[i] = din * coeff[i] + acc[i+1]
            for i in range(num_taps - 2, -1, -1):
                accs[i] <<= din * coeffs[i] + accs[i + 1]
            # Output is first accumulator
            dout <<= accs[0]

    return m
