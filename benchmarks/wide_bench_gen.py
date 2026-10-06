"""Synthetic Verilog generator for the work-queue delta-engine investigation.

See `notes/plans/work_queue_delta_engine.md` ("New Synthetic Reproducer DUT").

Produces a self-contained `module bench(...)` source with two independently
tunable knobs:

- `n_lanes` — total design size (`N_SIGS`/`N_cont` scale ~linearly with it;
  each lane contributes one register, three short-chain continuous assigns,
  and one enable comparison).
- `active_lanes` — how many of the `n_lanes` lanes actually change state on
  any given edge, via a rotating round-robin enable window. This is the
  real per-cycle activity, held independent of `n_lanes`.

Lanes are explicitly instantiated in a Python loop (no generate/genvar), to
match how a flattened hierarchy (e.g. `gfwx-fpga`'s) produces its many
`cont` processes -- not a single generate-block construct that might
elaborate differently.
"""

from __future__ import annotations


def make_wide_bench(n_lanes: int, active_lanes: int) -> str:
    """Self-contained `module bench(...)` Verilog source.

    `n_lanes` independent lanes (counter behind a 3-hop combinational
    chain), of which only `active_lanes` are enabled on any given edge.
    When a lane is NOT enabled, its counter holds its value, so its
    combinational chain recomputes to the SAME value as before -- no
    `dirty[]` bit gets set for it, so the engine correctly never calls its
    `cont_N()` bodies. The *gating checks* for those lanes still run every
    delta iteration regardless of activity; that fixed cost is what this
    reproducer isolates.
    """
    if n_lanes <= 0:
        raise ValueError("n_lanes must be positive")
    if not (0 <= active_lanes <= n_lanes):
        raise ValueError("active_lanes must be in [0, n_lanes]")

    lines = [
        "module bench(",
        "    input clk,",
        "    input rst,",
        f"    output [{n_lanes * 16 - 1}:0] q_flat",
        ");",
        "  reg [31:0] rr_base_reg;",
        "  always @(posedge clk) begin",
        "    if (rst) rr_base_reg <= 0;",
        f"    else rr_base_reg <= (rr_base_reg + {active_lanes}) % {n_lanes};",
        "  end",
    ]
    for i in range(n_lanes):
        lines += [
            f"  reg [15:0] ctr_{i};",
            f"  reg [15:0] q_{i};",
            f"  wire en_{i} = ((({i} + {n_lanes} - rr_base_reg) % {n_lanes}) < {active_lanes});",
            f"  wire [15:0] a_{i} = ctr_{i} ^ 16'hABCD;",
            f"  wire [15:0] b_{i} = a_{i} + 16'h1111;",
            f"  wire [15:0] c_{i} = b_{i} ^ (b_{i} >> 3);",
            "  always @(posedge clk) begin",
            f"    if (rst) begin ctr_{i} <= {i}; q_{i} <= 0; end",
            f"    else if (en_{i}) begin ctr_{i} <= ctr_{i} + 1; q_{i} <= c_{i}; end",
            "  end",
            f"  assign q_flat[{i * 16 + 15}:{i * 16}] = q_{i};",
        ]
    lines.append("endmodule")
    return "\n".join(lines)
