"""Composability showcase: Per-stage pipeline generator.

Demonstrates something IMPOSSIBLE in plain Verilog: generating a pipeline
where each stage performs a different operation, defined by a Python callable.

In Verilog, `generate for` can replicate *identical* stages, but cannot
parameterize the *operation* at each stage.  You'd need to manually write
out every stage or use fragile `ifdef` chains.

With the DSL, you just pass a Python list of lambdas.
"""

from veriforge.dsl import Module, posedge, mux
from veriforge.codegen import emit_module


# ---------------------------------------------------------------------------
# The pipeline generator — a reusable Python function
# ---------------------------------------------------------------------------


def pipeline(name, data_width, stages, *, enable=True):
    """Generate a pipelined datapath where each stage has a different operation.

    Args:
        name:       Module name.
        data_width: Bit width of the data path.
        stages:     List of (description, callable) pairs.
                    Each callable: (input_signal) -> output_expression.
        enable:     If True, add a global pipeline-enable/stall signal.

    Returns:
        Module builder (call .build() to get model Module).

    Generated ports::

        clk                    — pipeline clock
        rst                    — synchronous reset
        en                     — pipeline enable (only when enable=True)
        din  [data_width-1:0]  — pipeline input data
        valid_in               — input valid flag
        dout [data_width-1:0]  — pipeline output data
        valid_out              — output valid (delayed by len(stages) cycles)
    """
    m = Module(name)
    clk = m.input("clk").comment("Pipeline clock")
    rst = m.input("rst").comment("Synchronous reset")
    en = m.input("en").comment("Pipeline enable (stall when low)") if enable else None

    din = m.input("din", width=data_width).comment("Pipeline input data")
    valid_in = m.input("valid_in").comment("Input valid")
    dout = m.output_reg("dout", width=data_width).comment("Pipeline output data")
    valid_out = m.output_reg("valid_out").comment("Output valid")

    prev_data = din
    prev_valid = valid_in

    for i, (desc, stage_fn) in enumerate(stages):
        is_last = i == len(stages) - 1
        stage_data = dout if is_last else m.reg(f"stage{i}_data", width=data_width)
        stage_valid = valid_out if is_last else m.reg(f"stage{i}_valid")

        m.comment(f"Stage {i}: {desc}")
        with m.always(posedge(clk)):
            with m.if_(rst):
                stage_data <<= 0
                stage_valid <<= 0
            if en is not None:
                with m.elif_(en):
                    stage_data <<= stage_fn(prev_data)
                    stage_valid <<= prev_valid
            else:
                with m.else_():
                    stage_data <<= stage_fn(prev_data)
                    stage_valid <<= prev_valid

        prev_data = stage_data
        prev_valid = stage_valid

    return m


# ---------------------------------------------------------------------------
# Example 1: Image processing pipeline (scale, offset, clamp)
# ---------------------------------------------------------------------------

print("=" * 70)
print("Example 1: Image pixel processing pipeline")
print("    Stage 0: multiply by 2 (brightness boost)")
print("    Stage 1: add bias of 16")
print("    Stage 2: saturate to 255 (clamp)")
print("=" * 70)

pixel_pipeline = pipeline(
    "pixel_pipe",
    data_width=16,
    stages=[
        ("multiply by 2", lambda x: x << 1),
        ("add bias 16", lambda x: x + 16),
        ("saturate to 255", lambda x: mux(x > 255, 255, x)),
    ],
)

print(emit_module(pixel_pipeline.build()))
print()


# ---------------------------------------------------------------------------
# Example 2: DSP accumulation pipeline
# ---------------------------------------------------------------------------

print("=" * 70)
print("Example 2: DSP accumulation pipeline")
print("    Stage 0: square the input (x * x)")
print("    Stage 1: scale by 3")
print("    Stage 2: mask to lower 16 bits")
print("=" * 70)

dsp_pipe = pipeline(
    "dsp_pipe",
    data_width=32,
    stages=[
        ("square", lambda x: x * x),
        ("scale by 3", lambda x: x + (x << 1)),
        ("mask lower 16", lambda x: x & 0xFFFF),
    ],
    enable=False,
)

print(emit_module(dsp_pipe.build()))
print()


# ---------------------------------------------------------------------------
# Example 3: Dynamically build stages from a Python list
# ---------------------------------------------------------------------------

print("=" * 70)
print("Example 3: Programmatic pipeline — N shift-and-mask stages")
print("    Generated from a Python loop, not hand-coded")
print("=" * 70)

# Use a Python loop to generate N stages — each shifts by (i+1) bits
num_stages = 4
shift_stages = []
for i in range(num_stages):
    shift_amount = i + 1
    # Use default argument to capture loop variable
    shift_stages.append((f"shift right by {shift_amount}", lambda x, s=shift_amount: x >> s))

shift_pipe = pipeline("shift_pipe", data_width=16, stages=shift_stages)
print(emit_module(shift_pipe.build()))
