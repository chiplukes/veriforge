# Examples

This directory contains runnable examples and integration targets for the
Verilog tooling stack. Some examples are short Python DSL demonstrations; others
are imported RTL designs used to validate parser, model, analysis, and execution
behavior on realistic source trees.

## Quick-start examples

These examples are intended as the first place to look when learning the APIs.

| Directory | Contents | Notes |
| --- | --- | --- |
| `basics` | Counter, shift register, FSM, ALU, and testbench examples. | Small standalone DSL examples. |
| `library` | FIFO, CDC, codec, DSP, and Xilinx-oriented component examples. | Demonstrates reusable DSL component helpers. |
| `axi` | AXI-Stream and AXI4-Lite examples. | Demonstrates bus-oriented DSL patterns. |
| `composability` | Pipeline generator, register bank, and design exploration examples. | Demonstrates building designs from Python configuration and reusable generators. |

Run a quick-start example with `uv run`, for example:

```powershell
uv run python examples\basics\counter.py
```

## Real-world RTL targets

These directories contain imported or adapted RTL designs used as larger
validation targets. They may require optional tooling such as Cython, a C
compiler, Icarus Verilog, or Verilator depending on the script being run.

| Directory | Purpose |
| --- | --- |
| `darkriscv` | DarkRISCV RISC-V SoC integration target. |
| `femtorv` | FemtoRV-based RISC-V integration target. |
| `picorv32` | PicoRV32 integration target. |
| `serv` | SERV bit-serial RISC-V integration target. |
| `ibex` | Ibex-related validation assets, including Verilator-oriented files. |
| `pulp` | Focused imports from the `pulp-platform` ecosystem, including common_cells and AXI examples. |

Before running a larger target, check its local README or script comments for
tool requirements and expected pass conditions.

## Documentation conventions

New examples should:

1. State the API or design behavior being demonstrated.
2. Include a minimal command to run the example.
3. List optional external tool requirements.
4. Keep imported RTL examples focused and reproducible rather than copying full
   upstream verification environments.
